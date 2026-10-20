"""Verify all public Funnel relay addresses, without using private Tailscale DNS."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import ipaddress
import json
from pathlib import Path
import socket
import ssl
import time

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--wait-seconds", type=int, default=60)
    args = parser.parse_args()
    url = httpx.URL(args.origin)
    if url.scheme != "https" or url.path != "/" or url.query or url.fragment or url.userinfo:
        parser.error("Provide an HTTPS origin")
    if not 0 <= args.wait_seconds <= 300:
        parser.error("Wait must be between zero and 300 seconds")
    port = url.port or 443
    report = {"origin": args.origin, "started_at": datetime.now(timezone.utc).isoformat(),
              "tls_certificate_verification": True, "passed": False, "attempts": []}
    context = ssl.create_default_context()

    def probe(address):
        started = time.monotonic()
        result = {"ip": address, "passed": False}
        try:
            with socket.create_connection((address, port), timeout=8) as raw:
                with context.wrap_socket(raw, server_hostname=url.host) as connection:
                    authority = url.host if port == 443 else f"{url.host}:{port}"
                    connection.sendall((f"GET /api/ready HTTP/1.1\r\nHost: {authority}\r\n"
                                        "Connection: close\r\n\r\n").encode())
                    chunks = []
                    while True:
                        chunk = connection.recv(8192)
                        if not chunk:
                            break
                        chunks.append(chunk)
                        if sum(map(len, chunks)) > 65536:
                            raise RuntimeError("Unexpectedly large readiness response")
            response = b"".join(chunks)
            head, body = response.split(b"\r\n\r\n", 1)
            result["status"] = int(head.split(b"\r\n", 1)[0].split()[1])
            # This deployment's Nginx readiness response uses Content-Length.
            result["passed"] = result["status"] == 200 and json.loads(body)["status"] == "ready"
        except Exception as exc:
            result["error"] = str(exc)
        result["seconds"] = round(time.monotonic() - started, 2)
        return result

    deadline = time.monotonic() + args.wait_seconds
    try:
        while True:
            with httpx.Client(trust_env=False, timeout=15) as client:
                response = client.get("https://cloudflare-dns.com/dns-query",
                                      params={"name": url.host, "type": "A"},
                                      headers={"accept": "application/dns-json"})
                response.raise_for_status()
                addresses = sorted({a["data"] for a in response.json().get("Answer", [])
                                    if a["type"] == 1 and ipaddress.ip_address(a["data"]).is_global})
            if not addresses:
                raise RuntimeError("Public DNS returned no public IPv4 relays")
            with ThreadPoolExecutor(max_workers=min(8, len(addresses))) as pool:
                results = list(pool.map(probe, addresses))
            report["attempts"].append({"checked_at": datetime.now(timezone.utc).isoformat(),
                                       "relays": results})
            good = sum(result["passed"] for result in results)
            print(f"Public HTTPS readiness: {good}/{len(results)} relays passed", flush=True)
            if good == len(results):
                report["passed"] = True
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(min(5, max(0, deadline - time.monotonic())))
    except Exception as exc:
        report["error"] = str(exc)
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
