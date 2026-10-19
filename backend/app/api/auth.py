from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import LoginSession, User
from app.schemas import LoginRequest, LoginResponse, UserRead
from app.services.login_throttle import LoginThrottled, login_throttle
from app.security import (
    AuthContext,
    create_login_session,
    dummy_password_hash,
    get_current_auth,
    normalize_email,
    require_allowed_origin,
    require_csrf,
    utc_now,
    verify_password,
)

router = APIRouter(prefix="/api", tags=["authentication"])


def _set_auth_cookies(
    response: Response,
    session_token: str,
    csrf_token: str,
) -> None:
    settings = get_settings()
    max_age = settings.session_ttl_hours * 60 * 60
    response.set_cookie(
        key=settings.session_cookie_name,
        value=session_token,
        max_age=max_age,
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite="strict",
        path="/",
    )
    response.set_cookie(
        key=settings.csrf_cookie_name,
        value=csrf_token,
        max_age=max_age,
        secure=settings.session_cookie_secure,
        httponly=False,
        samesite="strict",
        path="/",
    )


def _clear_auth_cookies(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(
        settings.session_cookie_name,
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite="strict",
        path="/",
    )
    response.delete_cookie(
        settings.csrf_cookie_name,
        secure=settings.session_cookie_secure,
        httponly=False,
        samesite="strict",
        path="/",
    )


@router.post("/login", response_model=LoginResponse)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> LoginResponse:
    require_allowed_origin(request)
    email = normalize_email(payload.email)
    try:
        with login_throttle.attempt(email):
            user = db.scalar(select(User).where(User.email == email))
            password_hash = user.password_hash if user is not None else dummy_password_hash
            password_valid = verify_password(payload.password, password_hash)
    except LoginThrottled as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many sign-in attempts",
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc
    if user is None or not password_valid or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    login_session, session_token, csrf_token = create_login_session(user)
    # Sessions are only created here, so pruning expired rows on sign-in keeps
    # the table bounded without a separate cleanup job.
    db.execute(delete(LoginSession).where(LoginSession.expires_at <= utc_now()))
    db.add(login_session)
    db.commit()
    _set_auth_cookies(response, session_token, csrf_token)
    return LoginResponse(user=UserRead.model_validate(user), csrf_token=csrf_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> None:
    db.delete(auth.session)
    db.commit()
    _clear_auth_cookies(response)


@router.get("/me", response_model=UserRead)
def me(auth: AuthContext = Depends(get_current_auth)) -> UserRead:
    return UserRead.model_validate(auth.user)
