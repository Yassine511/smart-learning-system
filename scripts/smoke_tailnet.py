"""Check an HTTPS deployment without printing credentials or session tokens."""

from __future__ import annotations

import argparse
import json
from getpass import getpass
from pathlib import Path
import time
from uuid import uuid4

import httpx


def check(response: httpx.Response, expected: int, name: str) -> None:
    if response.status_code != expected:
        raise RuntimeError(f"{name}: expected HTTP {expected}, received {response.status_code}")
    print(f"{name}: HTTP {response.status_code}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("origin", help="Exact HTTPS origin, without trailing slash")
    parser.add_argument("email")
    parser.add_argument("--chat", action="store_true", help="Also verify one real Ollama-backed reply")
    parser.add_argument("--pdf", action="store_true", help="Also verify PDF indexing and deletion")
    args = parser.parse_args()
    origin = args.origin.rstrip("/")
    password = getpass("Student password: ")
    with httpx.Client(base_url=origin, timeout=30, trust_env=False) as client:
        check(client.get("/api/health"), 200, "Health")
        check(client.get("/api/ready"), 200, "Readiness")
        login = client.post("/api/login", headers={"Origin": origin},
                            json={"email": args.email, "password": password})
        check(login, 200, "Sign in")
        token = login.json()["csrf_token"]
        check(client.get("/api/me"), 200, "Current student")
        headers = {"Origin": origin, "X-CSRF-Token": token}
        created = client.post("/api/conversations", headers=headers,
                              json={"title": f"Deployment check {uuid4().hex[:8]}"})
        check(created, 201, "Create conversation")
        conversation_id = created.json()["id"]
        try:
            listed = client.get("/api/conversations")
            check(listed, 200, "List conversations")
            if conversation_id not in {item["id"] for item in listed.json()}:
                raise RuntimeError("Created conversation did not appear in the list")
            if args.pdf:
                fixture = Path(__file__).resolve().parents[1] / "backend/tests/fixtures/networking_notes.pdf"
                uploaded = client.post(
                    "/api/documents", headers=headers,
                    files={"file": ("networking_notes.pdf", fixture.read_bytes(), "application/pdf")},
                )
                check(uploaded, 202, "Upload PDF")
                document_id = uploaded.json()["id"]
                try:
                    for _ in range(60):
                        document = client.get(f"/api/documents/{document_id}")
                        if document.status_code != 200:
                            check(document, 200, "PDF status")
                        status = document.json()["status"]
                        if status == "indexed":
                            print("PDF indexing: completed")
                            break
                        if status == "failed":
                            raise RuntimeError("PDF indexing failed: " + str(document.json()["error_code"]))
                        time.sleep(2)
                    else:
                        raise RuntimeError("PDF indexing did not finish within two minutes")
                finally:
                    check(client.delete(f"/api/documents/{document_id}", headers=headers),
                          204, "Delete test PDF")
                    for _ in range(60):
                        if client.get(f"/api/documents/{document_id}").status_code == 404:
                            print("PDF cleanup: completed")
                            break
                        time.sleep(2)
                    else:
                        raise RuntimeError("PDF cleanup did not finish within two minutes")
            if args.chat:
                reply = client.post(
                    f"/api/conversations/{conversation_id}/messages",
                    headers=headers,
                    json={"request_id": str(uuid4()), "content": "Reply with one word: READY."},
                    timeout=240,
                )
                check(reply, 200, "Chat stream")
                if "event: complete" not in reply.text or "event: failed" in reply.text:
                    codes = []
                    for block in reply.text.split("\n\n"):
                        if "event: failed" not in block:
                            continue
                        data = next((line[6:] for line in block.splitlines()
                                     if line.startswith("data: ")), "{}")
                        codes.append(json.loads(data).get("error_code", "unknown"))
                    raise RuntimeError("Chat stream failed: " + ", ".join(codes or ["no terminal event"]))
                history = client.get(f"/api/conversations/{conversation_id}/messages")
                check(history, 200, "Chat history")
                if not any(message["role"] == "assistant" and message["status"] == "completed"
                           for message in history.json()):
                    raise RuntimeError("Completed assistant reply is missing from history")
                print("Ollama-backed reply: completed and saved")
        finally:
            check(client.delete(f"/api/conversations/{conversation_id}", headers=headers),
                  204, "Delete test conversation")
            check(client.post("/api/logout", headers=headers), 204, "Sign out")


if __name__ == "__main__":
    main()
