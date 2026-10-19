"""Bound password verification work in the supported single API process."""

from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
import hashlib
import math
from threading import Lock
from time import monotonic

from app.config import get_settings


class LoginThrottled(Exception):
    def __init__(self, retry_after: int) -> None:
        self.retry_after = retry_after
        super().__init__("Too many sign-in attempts")


class LoginThrottle:
    def __init__(self, clock: Callable[[], float] = monotonic) -> None:
        self._clock = clock
        self._lock = Lock()
        self._attempts: deque[tuple[float, str]] = deque()
        self._active = 0

    @contextmanager
    def attempt(self, normalized_email: str) -> Iterator[None]:
        settings = get_settings()
        account = hashlib.sha256(normalized_email.encode("utf-8")).hexdigest()
        with self._lock:
            now = self._clock()
            cutoff = now - settings.login_window_seconds
            while self._attempts and self._attempts[0][0] <= cutoff:
                self._attempts.popleft()
            account_attempts = [at for at, key in self._attempts if key == account]
            blocked_until = []
            if len(self._attempts) >= settings.login_global_attempts:
                blocked_until.append(self._attempts[0][0] + settings.login_window_seconds)
            if len(account_attempts) >= settings.login_account_attempts:
                blocked_until.append(account_attempts[0] + settings.login_window_seconds)
            if blocked_until:
                raise LoginThrottled(max(1, math.ceil(max(blocked_until) - now)))
            if self._active >= settings.login_max_concurrent:
                raise LoginThrottled(1)
            # Global admission bounds the number of retained account hashes.
            # Count successful and unknown-account attempts identically, and do
            # not trust proxy-supplied IP headers as an identity.
            self._attempts.append((now, account))
            self._active += 1
        try:
            yield
        finally:
            with self._lock:
                self._active -= 1


login_throttle = LoginThrottle()
