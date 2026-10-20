"""Seed two demo accounts through HTTPS and record checks without saving secrets."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import ipaddress
import json
from pathlib import Path
import re
import socket
import threading
import time
from uuid import uuid4

import httpx


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--credentials-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--public-ip", help="Public Funnel relay IP; keep the origin hostname for TLS verification")
    parser.add_argument("--pdf", type=Path, default=Path(__file__).resolve().parents[1]
                        / "backend/tests/fixtures/networking_notes.pdf")
    args = parser.parse_args()
    origin = args.origin.rstrip("/")
    url = httpx.URL(origin)
    if url.scheme != "https" or url.path != "/" or url.query or url.fragment or url.userinfo:
        parser.error("Provide an HTTPS origin without a path, credentials, query, or fragment")
    if args.public_ip:
        if not ipaddress.ip_address(args.public_ip).is_global:
            parser.error("The relay IP must be a public internet address")
        original_resolver = socket.getaddrinfo

        def public_resolver(host, *resolver_args, **resolver_kwargs):
            if host in (url.host, url.host.encode()):
                host = args.public_ip
            return original_resolver(host, *resolver_args, **resolver_kwargs)

        socket.getaddrinfo = public_resolver
    accounts = json.loads(args.credentials_file.read_text(encoding="utf-8-sig"))
    if len(accounts) != 2 or accounts[0]["email"] == accounts[1]["email"]:
        parser.error("Provide exactly two distinct demo accounts")
    pdf_bytes = args.pdf.read_bytes()
    clients = [httpx.Client(base_url=origin, timeout=300, trust_env=False) for _ in accounts]
    headers = []
    documents = []
    conversations = []
    report = {"origin": origin, "started_at": datetime.now(timezone.utc).isoformat(),
              "passed": False, "checks": [], "connected_public_ip": args.public_ip,
              "tls_certificate_verification": True}
    chat_barrier = threading.Barrier(2)

    def check(response, expected, label):
        report["checks"].append({"name": label, "status": response.status_code,
                                 "expected": expected})
        if response.status_code != expected:
            raise RuntimeError(f"{label}: expected HTTP {expected}, got {response.status_code}")
        return response

    def chat(index):
        request_id = str(uuid4())
        chat_barrier.wait(timeout=15)
        started = time.monotonic()
        started_at = datetime.now(timezone.utc).isoformat()
        question = ("According to the attached notes, how does TCP provide reliable delivery?"
                    if index == 0 else "According to the attached notes, does UDP guarantee packet delivery?")
        response = clients[index].post(
            f"/api/conversations/{conversations[index]}/messages", headers=headers[index],
            json={"request_id": request_id,
                  "content": question + " Answer in one sentence with a source marker."})
        check(response, 200, f"Professor {index + 1} chat stream")
        events = []
        for block in response.text.replace("\r\n", "\n").split("\n\n"):
            name = next((line[7:] for line in block.splitlines() if line.startswith("event: ")), "")
            data = next((line[6:] for line in block.splitlines() if line.startswith("data: ")), "{}")
            if name:
                events.append((name, json.loads(data)))
        failures = [data.get("error_code", "unknown") for name, data in events if name == "failed"]
        if failures or not any(name == "complete" for name, _ in events):
            raise RuntimeError("Model stream failed: " + ",".join(failures or ["no completion"]))
        if any(data.get("request_id", request_id) != request_id for _, data in events):
            raise RuntimeError("Chat response request ID mismatch")
        history = check(clients[index].get(
            f"/api/conversations/{conversations[index]}/messages"), 200, "Saved history").json()
        saved = next((m for m in history if m["role"] == "assistant"
                      and m["request_id"] == request_id), None)
        if not saved or saved["status"] != "completed" or not saved["content"].strip():
            raise RuntimeError("Completed assistant reply was not saved")
        sources = saved.get("sources", [])
        # Structured answers carry citations separately; SourceList renders them
        # below the answer. Inline markers are only required in free-text mode.
        if not sources or any(s["document_id"] != documents[index] or s["page_number"] not in (1, 2)
                              or not re.fullmatch(r"S[1-9]\d*", s["marker"])
                              or not s["quote"].strip() or s.get("unavailable", False)
                              or s["filename"] != "professor_demo_networking_notes.pdf" for s in sources):
            raise RuntimeError("Reply is missing valid citations to the professor's PDF")
        return {"name": f"Professor {index + 1} simultaneous cited chat",
                "passed": True, "seconds": round(time.monotonic() - started, 2),
                "source_count": len(sources), "started_at": started_at,
                "request_id": request_id}

    try:
        check(clients[0].get("/"), 200, "Frontend")
        check(clients[0].get("/api/health"), 200, "Health")
        check(clients[0].get("/api/ready"), 200, "Readiness")
        check(clients[0].get("/api/me"), 401, "Login required")
        for index, (client, account) in enumerate(zip(clients, accounts, strict=True)):
            login = check(client.post("/api/login", headers={"Origin": origin},
                          json={k: account[k] for k in ("email", "password")}),
                          200, f"Professor {index + 1} login")
            if not any("smart_learning_session=" in cookie and "secure" in cookie.lower()
                       and "httponly" in cookie.lower() for cookie in login.headers.get_list("set-cookie")):
                raise RuntimeError("Session cookie lacks Secure or HttpOnly")
            headers.append({"Origin": origin, "X-CSRF-Token": login.json()["csrf_token"]})
            me = check(client.get("/api/me"), 200, "Account identity").json()
            if me["email"] != account["email"]:
                raise RuntimeError("Wrong account identity")
            filename = "professor_demo_networking_notes.pdf"
            listed = check(client.get("/api/documents"), 200, "List PDFs").json()
            document = next((d for d in listed if d["original_filename"] == filename
                             and d["status"] in ("pending", "indexing", "indexed")), None)
            if document is None:
                document = check(client.post("/api/documents", headers=headers[index],
                                 files={"file": (filename, pdf_bytes, "application/pdf")}),
                                 202, "Seed sample PDF").json()
            document_id = document["id"]
            documents.append(document_id)
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline:
                state = check(client.get(f"/api/documents/{document_id}"), 200, "PDF status").json()
                if state["status"] == "indexed":
                    break
                if state["status"] == "failed":
                    raise RuntimeError("Sample PDF indexing failed: " + str(state.get("error_code")))
                time.sleep(2)
            else:
                raise RuntimeError("Sample PDF did not index within five minutes")
            downloaded = check(client.get(f"/api/documents/{document_id}/file"), 200, "Download own PDF")
            if downloaded.content != pdf_bytes:
                raise RuntimeError("Sample PDF download does not match the fixture")
            title = "Professor demo: networking notes"
            listed = check(client.get("/api/conversations"), 200, "List conversations").json()
            conversation = next((c for c in listed if c["title"] == title), None)
            if conversation is None:
                conversation = check(client.post("/api/conversations", headers=headers[index],
                                     json={"title": title}), 201, "Seed demo conversation").json()
            conversations.append(conversation["id"])
            check(client.put(f"/api/conversations/{conversation['id']}/documents/{document_id}",
                             headers=headers[index]), 204, "Attach sample PDF")
        for index in (0, 1):
            other = 1 - index
            check(clients[index].get(f"/api/conversations/{conversations[other]}"),
                  404, "Other professor's conversation is private")
            check(clients[index].get(f"/api/documents/{documents[other]}/file"),
                  404, "Other professor's PDF is private")
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(chat, index) for index in (0, 1)]
            failures = []
            for future in futures:
                try:
                    report["checks"].append(future.result())
                except Exception as exc:
                    failures.append(str(exc))
            if failures:
                raise RuntimeError("; ".join(failures))
        report["passed"] = True
    except Exception as exc:
        report["error"] = str(exc)
    finally:
        for index, client in enumerate(clients):
            try:
                if index < len(headers):
                    check(client.post("/api/logout", headers=headers[index]), 204, "Logout")
                    check(client.get("/api/me"), 401, "Session invalidated")
            except Exception as exc:
                report["passed"] = False
                report.setdefault("cleanup_errors", []).append(str(exc))
            client.close()
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
