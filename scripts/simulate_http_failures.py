"""Exercise six real HTTP failures on an isolated local FastAPI server.

Run from the repository root. With --capture, Wireshark dumpcap records only
this temporary loopback port. The database, student, and password are synthetic.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import httpx  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
import uvicorn  # noqa: E402

from app import main  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import Base, get_db  # noqa: E402
from app.models import User  # noqa: E402
from app.security import hash_password  # noqa: E402
from app.services.readiness import ComponentStatus  # noqa: E402


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def capture_tool() -> str:
    found = shutil.which("dumpcap")
    if found:
        return found
    windows = Path(r"C:\Program Files\Wireshark\dumpcap.exe")
    if windows.exists():
        return str(windows)
    raise RuntimeError("Wireshark dumpcap is unavailable. Run without --capture for HTTP simulation.")


def run(output: Path, capture: Path | None) -> list[dict[str, object]]:
    settings = get_settings()
    previous = (settings.session_cookie_secure, settings.max_upload_bytes, settings.allowed_origins)
    port = free_port()
    origin = f"http://127.0.0.1:{port}"
    settings.session_cookie_secure = False  # Only the disposable local server uses HTTP.
    settings.max_upload_bytes = 1024
    settings.allowed_origins += "," + origin

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    password = secrets.token_urlsafe(24)
    with factory() as db:
        db.add(User(email="capture-student@example.test", display_name="Capture Student", password_hash=hash_password(password)))
        db.commit()

    def local_db():
        with factory() as db:
            yield db

    original_probe = main.probe_dependencies
    main.app.dependency_overrides[get_db] = local_db
    main.probe_dependencies = lambda: {
        "postgres": ComponentStatus(True, "reachable"),
        "qdrant": ComponentStatus(True, "reachable"),
        "ollama": ComponentStatus(False, "model unavailable in isolated simulation"),
    }
    server = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=port, log_level="error", access_log=False))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    recorder: subprocess.Popen[bytes] | None = None
    results: list[dict[str, object]] = []
    try:
        with httpx.Client(base_url=origin, timeout=10, trust_env=False) as client:
            for _ in range(100):
                try:
                    if client.get("/api/health").status_code == 200:
                        break
                except httpx.ConnectError:
                    pass
                time.sleep(0.05)
            else:
                raise RuntimeError("Local API did not start")

            login = client.post(
                "/api/login", headers={"Origin": origin},
                json={"email": "capture-student@example.test", "password": password},
            )
            if login.status_code != 200:
                raise RuntimeError(f"Synthetic login failed: {login.status_code}")
            csrf = login.json()["csrf_token"]

            if capture:
                capture.parent.mkdir(parents=True, exist_ok=True)
                recorder = subprocess.Popen(
                    [capture_tool(), "-i", r"\Device\NPF_Loopback", "-f", f"tcp port {port}",
                     "-a", "duration:8", "-w", str(capture)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                )
                time.sleep(1.2)
                if recorder.poll() is not None:
                    raise RuntimeError("Wireshark capture stopped before the HTTP simulation")

            def check(name: str, method: str, path: str, expected: int, response: httpx.Response) -> None:
                results.append({"case": name, "method": method, "path": path,
                                "expected": expected, "observed": response.status_code})
                if response.status_code != expected:
                    raise AssertionError(f"{name}: expected {expected}, received {response.status_code}")

            # The six requests below are sent only after capture starts.
            check("session required", "GET", "/api/me", 401,
                  httpx.get(origin + "/api/me", timeout=10, trust_env=False))
            check("CSRF blocked", "POST", "/api/conversations", 403,
                  client.post("/api/conversations", json={"title": "Blocked"}))
            missing = "/api/conversations/00000000-0000-0000-0000-000000000000"
            check("missing private conversation", "GET", missing, 404, client.get(missing))
            headers = {"Origin": origin, "X-CSRF-Token": csrf}
            check("oversized PDF", "POST", "/api/documents", 413,
                  client.post("/api/documents", headers=headers,
                              files={"file": ("large.pdf", b"%PDF-" + b"a" * 2048, "application/pdf")}))
            check("wrong PDF format", "POST", "/api/documents", 415,
                  client.post("/api/documents", headers=headers,
                              files={"file": ("notes.txt", b"plain text", "text/plain")}))
            check("dependency unavailable", "GET", "/api/ready", 503, client.get("/api/ready"))

        if recorder:
            _, stderr = recorder.communicate(timeout=15)
            if recorder.returncode != 0:
                raise RuntimeError(f"Wireshark capture failed: {stderr.decode(errors='replace')}")
            if not capture or not capture.exists() or capture.stat().st_size == 0:
                raise RuntimeError("Wireshark did not save any packets")

        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps({"origin": origin, "capture": str(capture) if capture else None,
                                      "cases": results}, indent=2) + "\n", encoding="utf-8")
        return results
    finally:
        if recorder and recorder.poll() is None:
            recorder.terminate()
            recorder.wait(timeout=5)
        server.should_exit = True
        thread.join(timeout=5)
        main.app.dependency_overrides.clear()
        main.probe_dependencies = original_probe
        settings.session_cookie_secure, settings.max_upload_bytes, settings.allowed_origins = previous
        Base.metadata.drop_all(engine)
        engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "evidence" / "http-six-cases.json")
    parser.add_argument("--capture", type=Path, help="Write a Wireshark pcapng of the six cases")
    args = parser.parse_args()
    for item in run(args.output, args.capture):
        print(f"{item['observed']} {item['method']} {item['path']} — {item['case']}")
    print(f"Results: {args.output}")
    if args.capture:
        print(f"Capture: {args.capture}")
