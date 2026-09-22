from hashlib import sha256

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.models import LoginSession
from tests.conftest import TEST_ORIGIN, TEST_PASSWORD, csrf_headers, login


def test_login_stores_only_token_hash_and_logout_revokes_session(
    client: TestClient,
    users,
    session_factory: sessionmaker[Session],
) -> None:
    login_response = client.post(
        "/api/login",
        headers={"Origin": TEST_ORIGIN},
        json={
            "email": " Alice@Example.Test ",
            "password": TEST_PASSWORD,
        },
    )
    assert login_response.status_code == 200
    csrf_token = login_response.json()["csrf_token"]
    settings = get_settings()
    raw_session_token = client.cookies.get(settings.session_cookie_name)

    assert raw_session_token is not None
    assert client.cookies.get(settings.csrf_cookie_name) == csrf_token
    session_cookie = next(
        value
        for value in login_response.headers.get_list("set-cookie")
        if value.startswith(f"{settings.session_cookie_name}=")
    )
    assert "HttpOnly" in session_cookie
    assert "Secure" in session_cookie
    assert "SameSite=strict" in session_cookie

    with session_factory() as db:
        stored = db.scalar(select(LoginSession))
        assert stored is not None
        assert stored.token_hash == sha256(raw_session_token.encode()).hexdigest()
        assert raw_session_token not in stored.token_hash

    me_response = client.get("/api/me")
    assert me_response.status_code == 200
    assert me_response.json()["email"] == "alice@example.test"

    logout_response = client.post(
        "/api/logout",
        headers=csrf_headers(csrf_token),
    )
    assert logout_response.status_code == 204
    assert client.get("/api/me").status_code == 401

    with session_factory() as db:
        assert db.scalar(select(LoginSession)) is None


def test_login_failure_is_generic_for_unknown_user(client: TestClient) -> None:
    response = client.post(
        "/api/login",
        headers={"Origin": TEST_ORIGIN},
        json={"email": "nobody@example.test", "password": TEST_PASSWORD},
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid email or password"}


def test_passwords_are_argon2id_hashes(users) -> None:
    assert users["alice"].password_hash.startswith("$argon2id$")
    assert TEST_PASSWORD not in users["alice"].password_hash


def test_state_changes_require_matching_csrf_token_and_allowed_origin(
    client: TestClient,
    users,
) -> None:
    csrf_token = login(client, "alice@example.test")

    missing = client.post("/api/conversations", json={"title": "Blocked"})
    assert missing.status_code == 403

    wrong_token = client.post(
        "/api/conversations",
        headers=csrf_headers("wrong-token"),
        json={"title": "Blocked"},
    )
    assert wrong_token.status_code == 403

    wrong_origin = client.post(
        "/api/conversations",
        headers={
            "Origin": "https://attacker.example",
            "X-CSRF-Token": csrf_token,
        },
        json={"title": "Blocked"},
    )
    assert wrong_origin.status_code == 403

    allowed = client.post(
        "/api/conversations",
        headers=csrf_headers(csrf_token),
        json={"title": "Allowed"},
    )
    assert allowed.status_code == 201
