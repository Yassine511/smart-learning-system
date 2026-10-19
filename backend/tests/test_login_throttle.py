from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from app.api import auth
from app.config import get_settings
from app.services.login_throttle import LoginThrottle, LoginThrottled
from tests.conftest import TEST_ORIGIN, TEST_PASSWORD


def test_login_limits_normalized_accounts_before_password_work(client, users, monkeypatch):
    now = [100.0]
    monkeypatch.setattr(auth, "login_throttle", LoginThrottle(clock=lambda: now[0]))
    monkeypatch.setattr(get_settings(), "login_account_attempts", 2)
    checks = []
    monkeypatch.setattr(auth, "verify_password", lambda *args: checks.append(True) or False)

    def attempt(email):
        return client.post("/api/login", headers={"Origin": TEST_ORIGIN},
                           json={"email": email, "password": "wrong"})

    assert attempt("alice@example.test").status_code == 401
    assert attempt(" Alice@Example.Test ").status_code == 401
    blocked = attempt("alice@example.test")
    assert blocked.status_code == 429
    assert blocked.headers["Retry-After"] == "60"
    assert len(checks) == 2
    assert "set-cookie" not in blocked.headers
    assert attempt("bob@example.test").status_code == 401
    now[0] += 60
    assert attempt("alice@example.test").status_code == 401


def test_global_limit_bounds_rotating_unknown_accounts(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "login_global_attempts", 2)
    checks = []
    monkeypatch.setattr(auth, "verify_password", lambda *args: checks.append(True) or False)
    for index in range(3):
        response = client.post("/api/login", headers={"Origin": TEST_ORIGIN},
                               json={"email": f"unknown{index}@example.test", "password": "wrong"})
        assert response.status_code == (401 if index < 2 else 429)
    assert len(checks) == 2


def test_successful_logins_are_also_limited(client, users, monkeypatch):
    monkeypatch.setattr(get_settings(), "login_account_attempts", 1)
    payload = {"email": "alice@example.test", "password": TEST_PASSWORD}
    first = client.post("/api/login", headers={"Origin": TEST_ORIGIN}, json=payload)
    assert first.status_code == 200
    second = client.post("/api/login", headers={"Origin": TEST_ORIGIN}, json=payload)
    assert second.status_code == 429


def test_password_verification_concurrency_is_bounded_and_released(monkeypatch):
    monkeypatch.setattr(get_settings(), "login_max_concurrent", 1)
    throttle = LoginThrottle()
    barrier = Barrier(2)

    def hold_attempt():
        with throttle.attempt("alice@example.test"):
            barrier.wait(timeout=5)
            barrier.wait(timeout=5)

    with ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(hold_attempt)
        barrier.wait(timeout=5)
        try:
            with pytest.raises(LoginThrottled):
                with throttle.attempt("bob@example.test"):
                    pytest.fail("Concurrent password work was admitted")
        finally:
            barrier.wait(timeout=5)
        task.result(timeout=5)
    with pytest.raises(RuntimeError):
        with throttle.attempt("bob@example.test"):
            raise RuntimeError("verification failed")
    with throttle.attempt("bob@example.test"):
        pass
