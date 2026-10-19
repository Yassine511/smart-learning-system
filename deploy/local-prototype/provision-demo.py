"""Run inside the prototype backend; never print synthetic passwords."""
import json
import os
from pathlib import Path
import secrets

from sqlalchemy import select
from app.config import get_settings
from app.db import get_session_factory
from app.models import User
from app.security import hash_password

settings = get_settings()
if settings.ollama_chat_model != "qwen2.5:0.5b" or settings.qdrant_collection_prefix != "smart_learning_local_chunks":
    raise SystemExit("Demo provisioning is restricted to the local prototype configuration")
path = Path(settings.upload_root) / ".demo-credentials.json"
if path.exists():
    accounts = json.loads(path.read_text())
else:
    accounts = [
        {"email": f"student{i}@example.test", "name": f"Student {i}", "password": secrets.token_urlsafe(24)}
        for i in (1, 2)
    ]
    # Check for a conflicting administrator-created account before saving secrets.
    with get_session_factory()() as db:
        if db.scalar(select(User).where(User.email.in_([a["email"] for a in accounts]))):
            raise SystemExit("Demo account exists without its credential file; preserve it and choose a repair")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as file:
        json.dump(accounts, file)
with get_session_factory()() as db:
    for account in accounts:
        if db.scalar(select(User).where(User.email == account["email"])) is None:
            db.add(User(email=account["email"], display_name=account["name"], password_hash=hash_password(account["password"])))
    db.commit()
print("Two private synthetic demo accounts are provisioned.")
