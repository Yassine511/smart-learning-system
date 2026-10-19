"""Real HTTPS/model checks from a named VM; save sanitized evidence only."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from getpass import getpass
import json
from pathlib import Path
import socket
import ssl
import subprocess
import time
from uuid import uuid4

import httpx


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", choices=["server", "client1", "client2"], required=True)
    parser.add_argument("--origin", default="https://smart-learning.test")
    parser.add_argument("--credentials-file", type=Path)
    parser.add_argument("--ca-file", type=Path)
    parser.add_argument("--pdf", type=Path, default=Path(__file__).resolve().parents[1] / "backend/tests/fixtures/networking_notes.pdf")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--skip-rag", action="store_true", help="Diagnostic run only; never counts as full acceptance")
    args = parser.parse_args()
    if not args.origin.startswith("https://"):
        parser.error("HTTPS is required")
    expected_ip = {"server": "192.168.50.10", "client1": "192.168.50.11", "client2": "192.168.50.12"}[args.role]
    adapters = json.loads(subprocess.check_output(["ip", "-j", "-4", "addr"], text=True))
    ips = [address["local"] for adapter in adapters for address in adapter.get("addr_info", [])]
    if expected_ip not in ips:
        parser.error(f"Run inside {args.role}; its required address {expected_ip} is missing")
    credentials = json.loads(args.credentials_file.read_text()) if args.credentials_file else [
        {"email": f"student{i}@example.test", "password": getpass(f"Student {i} password: ")} for i in (1, 2)
    ]
    if len(credentials) != 2:
        parser.error("Provide exactly two synthetic demonstration accounts")
    verify = ssl.create_default_context(cafile=str(args.ca_file) if args.ca_file else None)
    clients = [httpx.Client(base_url=args.origin.rstrip('/'), verify=verify, timeout=240, trust_env=False) for _ in credentials]
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "role": args.role,
              "hostname": socket.gethostname(), "ips": ips, "origin": args.origin,
              "full_acceptance": False, "checks": []}
    conversations: list[str] = []
    document_id = None
    headers: list[dict[str, str]] = []

    def check(response, expected, label):
        report["checks"].append({"name": label, "http_status": response.status_code, "expected": expected})
        if response.status_code != expected:
            raise RuntimeError(f"{label}: expected {expected}, received {response.status_code}")
        return response

    def chat(index, question):
        request_id = str(uuid4())
        started = time.monotonic()
        response = clients[index].post(f"/api/conversations/{conversations[index]}/messages", headers=headers[index],
                                      json={"request_id": request_id, "content": question})
        check(response, 200, f"Student {index + 1} chat stream")
        events = []
        for block in response.text.split("\n\n"):
            name = next((line[7:] for line in block.splitlines() if line.startswith("event: ")), "")
            data = next((line[6:] for line in block.splitlines() if line.startswith("data: ")), "{}")
            if name:
                events.append((name, json.loads(data)))
        failures = [data.get("error_code", "unknown") for name, data in events if name == "failed"]
        if failures or not any(name == "complete" for name, _ in events):
            raise RuntimeError("Model stream failed: " + ",".join(failures or ["no completion"]))
        if any(data.get("request_id", request_id) != request_id for _, data in events):
            raise RuntimeError("Response routed to an incorrect request")
        history = check(clients[index].get(f"/api/conversations/{conversations[index]}/messages"), 200, "Saved history").json()
        saved = next((message for message in history if message["request_id"] == request_id and message["role"] == "assistant"), None)
        if not saved or saved["status"] != "completed" or not saved["content"].strip():
            raise RuntimeError("Completed reply was not persisted")
        report["checks"].append({"name": "Real inference and response routing", "request_id": request_id,
                                  "student": index + 1, "seconds": round(time.monotonic() - started, 3),
                                  "queued": any(name == "status" and data.get("status") == "queued" for name, data in events)})
        return saved

    try:
        check(clients[0].get("/api/health"), 200, "Health")
        check(clients[0].get("/api/ready"), 200, "Dependencies and models ready")
        check(clients[0].get("/api/me"), 401, "Unauthenticated access")
        for index, (client, account) in enumerate(zip(clients, credentials, strict=True)):
            login = check(client.post("/api/login", headers={"Origin": args.origin}, json=account), 200, f"Student {index + 1} login")
            cookies = login.headers.get_list("set-cookie")
            if not any("smart_learning_session=" in value and "secure" in value.lower() and "httponly" in value.lower() for value in cookies):
                raise RuntimeError("Session cookie is missing required security flags")
            headers.append({"Origin": args.origin, "X-CSRF-Token": login.json()["csrf_token"]})
            check(client.get("/api/me"), 200, "Current student")
            created = check(client.post("/api/conversations", headers=headers[index], json={"title": f"VM acceptance {uuid4().hex[:8]}"}), 201, "Create conversation")
            conversations.append(created.json()["id"])
        check(clients[0].patch(f"/api/conversations/{conversations[0]}", headers={"Origin": args.origin}, json={"title": "CSRF probe"}), 403, "Missing CSRF")
        check(clients[1].get(f"/api/conversations/{conversations[0]}"), 404, "Cross-student conversation read")
        check(clients[1].patch(f"/api/conversations/{conversations[0]}", headers=headers[1], json={"title": "Ownership probe"}), 404, "Cross-student conversation write")
        chat(0, "Reply in one short sentence: what is a computer network?")
        with ThreadPoolExecutor(max_workers=2) as pool:
            requests = [pool.submit(chat, index, f"Reply briefly: explain {'TCP' if index == 0 else 'UDP'}.") for index in (0, 1)]
            for future in requests: future.result()
        check(clients[0].post("/api/documents", headers=headers[0], files={"file": ("wrong.txt", b"not a PDF", "text/plain")}), 415, "Invalid upload type")
        check(clients[0].post("/api/documents", headers=headers[0], files={"file": ("large.pdf", b"%PDF-" + b"x" * (20 * 1024 * 1024), "application/pdf")}), 413, "Oversized upload")
        uploaded = check(clients[0].post("/api/documents", headers=headers[0], files={"file": ("networking_notes.pdf", args.pdf.read_bytes(), "application/pdf")}), 202, "PDF upload")
        document_id = uploaded.json()["id"]
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            status = check(clients[0].get(f"/api/documents/{document_id}"), 200, "PDF status").json()
            if status["status"] == "indexed": break
            if status["status"] == "failed": raise RuntimeError("PDF indexing failed: " + str(status.get("error_code")))
            time.sleep(2)
        else: raise RuntimeError("PDF indexing exceeded five minutes")
        report["checks"].append({"name": "PDF indexed by real embedding model", "passed": True})
        check(clients[1].get(f"/api/documents/{document_id}/file"), 404, "Cross-student PDF download")
        check(clients[1].put(f"/api/conversations/{conversations[1]}/documents/{document_id}", headers=headers[1]), 404, "Cross-student attachment")
        downloaded = check(clients[0].get(f"/api/documents/{document_id}/file"), 200, "Owned PDF download")
        if downloaded.content != args.pdf.read_bytes(): raise RuntimeError("PDF download differs from upload")
        check(clients[0].put(f"/api/conversations/{conversations[0]}/documents/{document_id}", headers=headers[0]), 204, "Attach indexed PDF")
        if not args.skip_rag:
            answer = chat(0, "How does TCP deliver data reliably? Answer in one sentence with its source marker.")
            if not answer["sources"] or any(source["filename"] != "networking_notes.pdf" or source["page_number"] not in (1, 2) for source in answer["sources"]):
                raise RuntimeError("Weak model did not return valid document citations; RAG acceptance remains incomplete")
            report["checks"].append({"name": "Document-grounded answer and valid page citations", "passed": True})
        check(clients[0].delete(f"/api/documents/{document_id}", headers=headers[0]), 204, "Delete owned PDF")
        check(clients[0].get(f"/api/documents/{document_id}/file"), 404, "Deleted PDF unavailable")
        for _ in range(150):
            if clients[0].get(f"/api/documents/{document_id}").status_code == 404: break
            time.sleep(2)
        else: raise RuntimeError("PDF cleanup did not finish")
        document_id = None
        for index, client in enumerate(clients):
            check(client.delete(f"/api/conversations/{conversations[index]}", headers=headers[index]), 204, "Delete test conversation")
            check(client.post("/api/logout", headers=headers[index]), 204, "Logout")
            check(client.get("/api/me"), 401, "Session invalidated")
        conversations.clear()
        report["full_acceptance"] = not args.skip_rag
        report["result"] = "passed" if not args.skip_rag else "diagnostic_only"
    except Exception as exc:
        report["result"] = "failed"
        report["error"] = str(exc)
    finally:
        if document_id is not None and headers:
            try: clients[0].delete(f"/api/documents/{document_id}", headers=headers[0])
            except httpx.HTTPError: pass
        for index, conversation in enumerate(conversations):
            try: clients[index].delete(f"/api/conversations/{conversation}", headers=headers[index])
            except httpx.HTTPError: pass
        for client in clients: client.close()
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(f"{args.role}: {report['result']}; sanitized evidence: {args.output}")
    if report.get("error"): print(report["error"])
    return 0 if report["full_acceptance"] else 1


if __name__ == "__main__": raise SystemExit(main())
