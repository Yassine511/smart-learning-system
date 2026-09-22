from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from argon2.low_level import Type
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.db import get_db
from app.models import LoginSession, User

password_hasher = PasswordHasher(type=Type.ID)
dummy_password_hash = password_hasher.hash("not-a-real-user-password")


def normalize_email(email: str) -> str:
    return email.strip().casefold()


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_opaque_token() -> str:
    return secrets.token_urlsafe(32)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class AuthContext:
    session: LoginSession
    user: User


def unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
    )


def require_allowed_origin(request: Request) -> None:
    origin = request.headers.get("origin", "").rstrip("/")
    if not origin or origin not in get_settings().allowed_origin_set:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Origin is not allowed",
        )


def get_current_auth(
    request: Request,
    db: Session = Depends(get_db),
) -> AuthContext:
    settings = get_settings()
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise unauthorized()

    login_session = db.scalar(
        select(LoginSession)
        .options(joinedload(LoginSession.user))
        .where(
            LoginSession.token_hash == hash_token(token),
            LoginSession.expires_at > utc_now(),
        )
    )
    if login_session is None or not login_session.user.is_active:
        raise unauthorized()
    return AuthContext(session=login_session, user=login_session.user)


def require_csrf(
    request: Request,
    auth: AuthContext = Depends(get_current_auth),
) -> AuthContext:
    require_allowed_origin(request)
    settings = get_settings()
    header_token = request.headers.get("x-csrf-token")
    cookie_token = request.cookies.get(settings.csrf_cookie_name)
    if (
        not header_token
        or not cookie_token
        or not hmac.compare_digest(header_token, cookie_token)
        or not hmac.compare_digest(
            hash_token(header_token), auth.session.csrf_token_hash
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF validation failed",
        )
    return auth


def create_login_session(user: User) -> tuple[LoginSession, str, str]:
    settings = get_settings()
    session_token = new_opaque_token()
    csrf_token = new_opaque_token()
    expires_at = utc_now() + timedelta(hours=settings.session_ttl_hours)
    return (
        LoginSession(
            user_id=user.id,
            token_hash=hash_token(session_token),
            csrf_token_hash=hash_token(csrf_token),
            expires_at=expires_at,
        ),
        session_token,
        csrf_token,
    )
