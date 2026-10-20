#!/usr/bin/env python3
"""Reset only the two professor demo accounts in the existing local prototype."""
import argparse
import json
import os
from pathlib import Path
import pwd
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("credentials_file", type=Path)
    args = parser.parse_args()
    if os.geteuid() != 0 or not os.environ.get("SUDO_USER"):
        parser.error("Run from the normal VM1 account with sudo python3")
    if os.uname().nodename != "smart-learning-server":
        parser.error("Expected VM1 smart-learning-server")
    user = pwd.getpwnam(os.environ["SUDO_USER"])
    guest_home = Path(user.pw_dir)
    source = args.credentials_file.resolve()
    if source != guest_home / ".professor-demo-reset.json":
        parser.error("Expected the scoped reset request in the guest user's home")
    accounts = json.loads(source.read_text())
    expected = {f"professor{i}@demo.example.test" for i in (1, 2)}
    if (len(accounts) != 2 or {a["email"] for a in accounts} != expected
            or any(not isinstance(a.get("password"), str) or not a["password"] for a in accounts)):
        parser.error("Provide exactly the two existing professor demo accounts and nonempty passwords")
    code = '''import json, sys
from sqlalchemy import delete, select
from app.config import get_settings
from app.db import get_session_factory
from app.models import LoginSession, User
from app.security import hash_password, verify_password
settings = get_settings()
if (settings.ollama_chat_model != 'qwen2.5:0.5b'
        or settings.qdrant_collection_prefix != 'smart_learning_local_chunks'):
    raise SystemExit('Refusing to change accounts outside the local prototype')
accounts = json.load(sys.stdin)
by_email = {a['email']: a for a in accounts}
with get_session_factory()() as db:
    users = list(db.scalars(select(User).where(User.email.in_(by_email))))
    if len(users) != 2 or not all(u.is_active for u in users):
        raise SystemExit('Both existing professor demo accounts must be active')
    for user in users:
        user.password_hash = hash_password(by_email[user.email]['password'])
        if not verify_password(by_email[user.email]['password'], user.password_hash):
            raise RuntimeError('Password hash verification failed')
    result = db.execute(delete(LoginSession).where(LoginSession.user_id.in_([u.id for u in users])))
    db.commit()
print(json.dumps({'changed': sorted(by_email), 'sessions_invalidated': result.rowcount}))
'''
    stack = ["bash", "/opt/smart-learning-local/deploy/local-prototype/stack.sh"]
    result = subprocess.run(stack + ["exec", "-T", "backend", "python", "-c", code],
                            input=json.dumps(accounts), text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "The backend password update failed")
    report = json.loads(result.stdout)
    destination = guest_home / ".professor-demo-credentials.json"
    if destination.exists():
        saved = json.loads(destination.read_text())
        for account in saved:
            if account["email"] in expected:
                account["password"] = next(a["password"] for a in accounts if a["email"] == account["email"])
    else:
        saved = accounts
    temporary = destination.with_name(destination.name + ".new")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(saved, stream, indent=2)
        stream.write("\n")
    os.chmod(temporary, 0o600)
    os.chown(temporary, user.pw_uid, user.pw_gid)
    temporary.replace(destination)
    report_file = guest_home / ".professor-password-reset-result.json"
    report_file.write_text(json.dumps(report, indent=2) + "\n")
    os.chown(report_file, user.pw_uid, user.pw_gid)
    os.chmod(report_file, 0o600)
    source.unlink()
    print("Both professor passwords updated; existing sessions invalidated.")


if __name__ == "__main__":
    main()
