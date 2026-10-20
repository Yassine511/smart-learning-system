from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.api.messages import get_chat_session_factory
from app.api.documents import get_search_session_factory
from app.models import User
from app.security import hash_password

TEST_ORIGIN = "https://smart-learning.test"
TEST_PASSWORD = "correct-horse-battery-staple"


@pytest.fixture(autouse=True)
def isolated_login_throttle(monkeypatch) -> None:
    from app.api import auth
    from app.services.login_throttle import LoginThrottle

    monkeypatch.setattr(auth, "login_throttle", LoginThrottle())


@pytest.fixture
def session_factory() -> Generator[sessionmaker[Session], None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_get_db() -> Generator[Session, None, None]:
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_chat_session_factory] = lambda: factory
    app.dependency_overrides[get_search_session_factory] = lambda: factory
    try:
        yield factory
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def users(session_factory: sessionmaker[Session]) -> dict[str, User]:
    with session_factory() as db:
        alice = User(
            email="alice@example.test",
            display_name="Alice",
            password_hash=hash_password(TEST_PASSWORD),
        )
        bob = User(
            email="bob@example.test",
            display_name="Bob",
            password_hash=hash_password(TEST_PASSWORD),
        )
        db.add_all([alice, bob])
        db.commit()
        return {"alice": alice, "bob": bob}


@pytest.fixture
def client(session_factory: sessionmaker[Session]) -> Generator[TestClient, None, None]:
    with TestClient(app, base_url=TEST_ORIGIN) as test_client:
        yield test_client


def login(client: TestClient, email: str) -> str:
    response = client.post(
        "/api/login",
        headers={"Origin": TEST_ORIGIN},
        json={"email": email, "password": TEST_PASSWORD},
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


def csrf_headers(token: str) -> dict[str, str]:
    return {"Origin": TEST_ORIGIN, "X-CSRF-Token": token}
