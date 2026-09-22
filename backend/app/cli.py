import argparse
from getpass import getpass

from sqlalchemy import select

from app.db import get_session_factory
from app.models import User
from app.security import hash_password, normalize_email


def create_user(email: str, display_name: str) -> int:
    normalized_email = normalize_email(email)
    clean_name = display_name.strip()
    if "@" not in normalized_email or not clean_name:
        print("A valid email and non-blank display name are required.")
        return 2

    password = getpass("Password (minimum 12 characters): ")
    confirmation = getpass("Confirm password: ")
    if len(password) < 12:
        print("Password must contain at least 12 characters.")
        return 2
    if password != confirmation:
        print("Passwords do not match.")
        return 2

    with get_session_factory()() as db:
        if db.scalar(select(User).where(User.email == normalized_email)) is not None:
            print("A user with that email already exists.")
            return 1
        db.add(
            User(
                email=normalized_email,
                display_name=clean_name,
                password_hash=hash_password(password),
            )
        )
        db.commit()
    print(f"Created student account: {normalized_email}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Smart Learning System administration")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create_parser = subparsers.add_parser(
        "create-user",
        help="Create an administrator-managed student account",
    )
    create_parser.add_argument("--email", required=True)
    create_parser.add_argument("--name", required=True)
    args = parser.parse_args()
    if args.command == "create-user":
        return create_user(args.email, args.name)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
