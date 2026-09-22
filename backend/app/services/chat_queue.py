"""One active Ollama generation with bounded FIFO admission per API process."""

from collections import deque
from threading import Condition
from time import monotonic
from uuid import UUID, uuid4


class ChatQueue:
    def __init__(self) -> None:
        self._condition = Condition()
        self._waiting: deque[UUID] = deque()
        self._active: UUID | None = None

    def admit(self, capacity: int) -> UUID | None:
        with self._condition:
            if len(self._waiting) + int(self._active is not None) >= capacity:
                return None
            ticket = uuid4()
            self._waiting.append(ticket)
            self._condition.notify_all()
            return ticket

    def wait_turn(self, ticket: UUID, seconds: float) -> bool:
        deadline = monotonic() + seconds
        with self._condition:
            while True:
                if self._active is None and self._waiting and self._waiting[0] == ticket:
                    self._waiting.popleft()
                    self._active = ticket
                    return True
                remaining = deadline - monotonic()
                if remaining <= 0:
                    self.release(ticket)
                    return False
                self._condition.wait(remaining)

    def release(self, ticket: UUID) -> None:
        with self._condition:
            if self._active == ticket:
                self._active = None
            else:
                try:
                    self._waiting.remove(ticket)
                except ValueError:
                    pass
            self._condition.notify_all()


chat_queue = ChatQueue()
