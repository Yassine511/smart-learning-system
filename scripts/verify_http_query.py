"""Exercise semantic QUERY through the real lab proxies using a synthetic PDF."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import socket
import ssl
import subprocess
import time

import httpx


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", choices=("server", "client1", "client2"), required=True)
    parser.add_argument("--credentials-file", type=Path, default=Path.home() / ".demo-credentials.json")
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--http-loopback", action="store_true", help="Also test nginx on VM1 loopback")
    parser.add_argument("--keep-document", action="store_true", help="Retain the synthetic PDF for UI screenshots")
    args = parser.parse_args()
    expected_ip = {"server": "192.168.50.10", "client1": "192.168.50.11", "client2": "192.168.50.12"}[args.role]
    adapters = json.loads(subprocess.check_output(["ip", "-j", "-4", "addr"], text=True))
    ips = [a["local"] for adapter in adapters for a in adapter.get("addr_info", [])]
    if expected_ip not in ips or (args.http_loopback and args.role != "server"):
        parser.error("Run in the named VM; --http-loopback requires VM1")
    accounts = json.loads(args.credentials_file.read_text())
    if len(accounts) != 2:
        parser.error("Expected two synthetic lab accounts")
    if args.role == "client2":
        accounts.reverse()
    origin, path = "https://smart-learning.test", "/api/documents/search"
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "role": args.role,
              "hostname": socket.gethostname(), "ip": expected_ip, "passed": False, "checks": []}
    clients = [httpx.Client(base_url=origin, verify=ssl.create_default_context(), timeout=240, trust_env=False)
               for _ in accounts]
    headers = []
    document_id = None

    def check(response, expected, label):
        report["checks"].append({"name": label, "status": response.status_code, "expected": expected})
        if response.status_code != expected:
            raise RuntimeError(f"{label}: expected {expected}, got {response.status_code}")
        return response

    try:
        check(clients[0].request("QUERY", path, json={"query": "TCP"}), 401, "Unauthenticated QUERY")
        for client, account in zip(clients, accounts, strict=True):
            logged_in = check(client.post("/api/login", headers={"Origin": origin}, json=account), 200, "Login")
            headers.append({"Origin": origin, "X-CSRF-Token": logged_in.json()["csrf_token"]})
        document_id = check(clients[0].post("/api/documents", headers=headers[0], files={
            "file": ("query_networking_notes.pdf", args.pdf.read_bytes(), "application/pdf"),
        }), 202, "Upload synthetic PDF").json()["id"]
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            document = check(clients[0].get("/api/documents/" + document_id), 200, "Index status").json()
            if document["status"] == "indexed":
                break
            if document["status"] == "failed":
                raise RuntimeError("Synthetic PDF failed to index")
            time.sleep(2)
        else:
            raise RuntimeError("Synthetic PDF indexing timed out")
        body = {"query": "How does TCP deliver data reliably?", "document_ids": [document_id], "limit": 5}
        result = check(clients[0].request("QUERY", path, json=body), 200, "HTTPS QUERY through Caddy and nginx")
        hits = result.json()
        if not hits or any(hit["document_id"] != document_id or not hit["quote"] or hit["page_number"] < 1 for hit in hits):
            raise RuntimeError("Expected verified passages from the synthetic PDF")
        if result.headers.get("cache-control") != "no-store" or result.headers.get("vary") != "Cookie":
            raise RuntimeError("Missing private response cache headers")
        repeated = check(clients[0].request("QUERY", path, json=body), 200, "Repeat QUERY")
        if repeated.json() != hits:
            raise RuntimeError("Repeated search changed without a document change")
        other = check(clients[1].request("QUERY", path, json=body), 200, "Cross-student guessed document ID")
        if other.json() != []:
            raise RuntimeError("Cross-student search returned private passages")
        for method in ("GET", "POST"):
            wrong = check(clients[0].request(method, path), 405, method + " rejected")
            if wrong.headers.get("allow") != "QUERY" or wrong.headers.get("accept-query") != "application/json":
                raise RuntimeError("Missing search discovery headers")
        if args.http_loopback:
            # Secure cookies normally stay on HTTPS. This explicit copy is only
            # for the isolated VM1 loopback hop already used by Caddy.
            cookie = "; ".join(f"{key}={value}" for key, value in clients[0].cookies.items())
            with httpx.Client(base_url="http://127.0.0.1:8080", timeout=240, trust_env=False) as local:
                plain = check(local.request("QUERY", path, headers={"Cookie": cookie}, json=body), 200, "nginx loopback QUERY")
                if plain.json() != hits:
                    raise RuntimeError("Loopback and HTTPS search disagree")
        report.update(passed=True, document_id=document_id, hit_count=len(hits),
                      page_numbers=[hit["page_number"] for hit in hits], kept_document=args.keep_document)
    except Exception as exc:
        report["error"] = type(exc).__name__ + ": " + str(exc)
    finally:
        try:
            if document_id and headers and not args.keep_document:
                check(clients[0].delete("/api/documents/" + document_id, headers=headers[0]), 204, "Delete synthetic PDF")
            for client, auth_headers in zip(clients, headers):
                check(client.post("/api/logout", headers=auth_headers), 204, "Logout")
        except Exception as exc:
            report.update(passed=False, cleanup_error=type(exc).__name__ + ": " + str(exc))
        for client in clients:
            client.close()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
