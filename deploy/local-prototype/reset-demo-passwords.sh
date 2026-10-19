#!/usr/bin/env bash
# Explicit, local-prototype-only reset; normal account password policy is unchanged.
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Run with sudo.' >&2; exit 1; }
[[ $# == 1 && -n $1 ]] || { echo 'Usage: sudo bash reset-demo-passwords.sh PASSWORD' >&2; exit 2; }
[[ $(cat /var/lib/smart-learning-local-setup/role) == server ]] || exit 2
stack=/opt/smart-learning-local/deploy/local-prototype/stack.sh
DEMO_PASSWORD=$1 bash "$stack" exec -T -e DEMO_PASSWORD backend python - <<'PY'
import json
import os
from pathlib import Path

from sqlalchemy import delete, select
from app.config import get_settings
from app.db import get_session_factory
from app.models import LoginSession, User
from app.security import hash_password, verify_password

settings = get_settings()
if (settings.ollama_chat_model != "qwen2.5:0.5b"
        or settings.qdrant_collection_prefix != "smart_learning_local_chunks"):
    raise SystemExit("Refusing to change accounts outside the local prototype")
path = Path(settings.upload_root) / ".demo-credentials.json"
accounts = json.loads(path.read_text())
expected = {"student1@example.test", "student2@example.test"}
if len(accounts) != 2 or {a["email"] for a in accounts} != expected:
    raise SystemExit("Expected exactly the two local demo accounts")
password = os.environ.pop("DEMO_PASSWORD")
with get_session_factory()() as db:
    users = list(db.scalars(select(User).where(User.email.in_(expected))))
    if len(users) != 2 or not all(u.is_active for u in users):
        raise SystemExit("Both existing demo accounts must be active")
    for user in users:
        user.password_hash = hash_password(password)
        assert verify_password(password, user.password_hash)
    db.execute(delete(LoginSession).where(LoginSession.user_id.in_([u.id for u in users])))
    db.commit()
for account in accounts:
    account["password"] = password
temporary = path.with_name(path.name + ".new")
fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as output:
    json.dump(accounts, output)
os.chmod(temporary, 0o600)
temporary.replace(path)
print("Both local demo passwords updated and verified; previous sessions signed out.")
PY
guest_user=$(cat /var/lib/smart-learning-local-setup/guest-user)
guest_home=$(getent passwd "$guest_user" | cut -d: -f6)
target=/opt/smart-learning-local/.demo-credentials.json
bash "$stack" exec -T backend cat /data/uploads/.demo-credentials.json > "$target"
chmod 0600 "$target"
chown "$guest_user:$(id -gn "$guest_user")" "$target"
install -o "$guest_user" -g "$(id -gn "$guest_user")" -m 0600 "$target" "$guest_home/.demo-credentials.json"
