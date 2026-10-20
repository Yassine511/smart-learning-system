#!/usr/bin/env python3
"""Run with guest sudo to allow one public origin and provision two demo users."""
import argparse
import json
import os
from pathlib import Path
import pwd
import secrets
import shutil
import subprocess
import time
from urllib.parse import urlsplit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("origin")
    args = parser.parse_args()
    origin = args.origin.rstrip("/")
    url = urlsplit(origin)
    if (url.scheme != "https" or not url.hostname or not url.hostname.endswith(".ts.net")
            or url.path or url.query or url.fragment or url.username or url.password):
        parser.error("An HTTPS Tailscale origin is required")
    if os.geteuid() != 0 or not os.environ.get("SUDO_USER"):
        parser.error("Run from the normal VM1 user with sudo python3")
    if os.uname().nodename != "smart-learning-server":
        parser.error("This helper is only for VM1 smart-learning-server")
    root = Path("/opt/smart-learning-local")
    env_file = root / ".env"
    original = env_file.read_text()
    settings = dict(line.split("=", 1) for line in original.splitlines()
                    if line.strip() and not line.lstrip().startswith("#") and "=" in line)
    if (settings.get("OLLAMA_CHAT_MODEL") != "qwen2.5:0.5b"
            or settings.get("APP_ORIGIN") != "https://smart-learning.test"):
        parser.error("Expected the separate local Qwen prototype profile")
    user = pwd.getpwnam(os.environ["SUDO_USER"])
    credentials_file = Path(user.pw_dir) / ".professor-demo-credentials.json"
    state_dir = Path("/var/lib/smart-learning-professor-demo")
    state_dir.mkdir(mode=0o700, exist_ok=True)
    backup = state_dir / "env-before-demo"
    if not backup.exists():
        shutil.copy2(env_file, backup)
        backup.chmod(0o600)
    allowed = [x.strip() for x in settings.get("ALLOWED_ORIGINS", "").split(",") if x.strip()]
    for value in ("https://smart-learning.test", origin):
        if value not in allowed:
            allowed.append(value)
    replacements = {"ALLOWED_ORIGINS": ",".join(allowed), "SESSION_COOKIE_SECURE": "true"}
    lines = []
    for line in original.splitlines():
        key = line.split("=", 1)[0]
        if key in replacements:
            lines.append(key + "=" + replacements.pop(key))
        else:
            lines.append(line)
    lines.extend(key + "=" + value for key, value in replacements.items())
    updated = "\n".join(lines) + "\n"
    stack = ["bash", str(root / "deploy/local-prototype/stack.sh")]
    if updated != original:
        env_file.write_text(updated)
        env_file.chmod(0o600)
        try:
            subprocess.run(stack + ["config", "--quiet"], check=True)
            subprocess.run(stack + ["up", "-d", "--no-deps", "backend"], check=True)
        except Exception:
            env_file.write_text(original)
            subprocess.run(stack + ["up", "-d", "--no-deps", "backend"], check=False)
            raise
    for _ in range(60):
        if subprocess.run(["curl", "-fsS", "http://127.0.0.1:8080/api/ready"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
            break
        time.sleep(2)
    else:
        raise RuntimeError("App did not become ready; Funnel has not been enabled by this helper")
    emails = [f"professor{i}@demo.example.test" for i in (1, 2)]
    if credentials_file.exists():
        accounts = json.loads(credentials_file.read_text())
        if [a["email"] for a in accounts] != emails:
            raise RuntimeError("Existing professor credential file contains unexpected accounts")
    else:
        accounts = [{"email": email, "password": secrets.token_urlsafe(24),
                     "display_name": f"Professor {i}"} for i, email in enumerate(emails, 1)]
        # Save before the DB operation so a rerun can finish without losing credentials.
        fd = os.open(credentials_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(accounts, stream, indent=2)
            stream.write("\n")
        os.chown(credentials_file, user.pw_uid, user.pw_gid)
    code = '''import json, sys
from sqlalchemy import select
from app.db import get_session_factory
from app.models import User
from app.security import hash_password, verify_password
accounts = json.load(sys.stdin)
with get_session_factory()() as db:
    for account in accounts:
        user = db.scalar(select(User).where(User.email == account['email']))
        if user is not None:
            if not user.is_active or not verify_password(account['password'], user.password_hash):
                raise RuntimeError('Existing demo account does not match saved credentials; refusing to reset it')
        else:
            db.add(User(email=account['email'], display_name=account['display_name'],
                        password_hash=hash_password(account['password'])))
    db.commit()
print('Both professor accounts are ready.')
'''
    subprocess.run(stack + ["exec", "-T", "backend", "python", "-c", code],
                   input=json.dumps(accounts), text=True, check=True)
    (state_dir / "origin").write_text(origin + "\n")
    print("Local HTTPS remains allowed; added demo origin: " + origin)
    print("Private credentials saved to " + str(credentials_file))
    print("Settings backup: " + str(backup))


if __name__ == "__main__":
    main()
