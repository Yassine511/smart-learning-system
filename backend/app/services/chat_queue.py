"""One active Ollama generation with bounded FIFO admission per API process."""

from collections import deque
from collections.abc import Callable
from threading import Condition
from time import monotonic
from uuid import UUID, uuid4


class QueueFull(Exception):
    """Admission refused; ``scope`` is "global" or "owner"."""

    def __init__(self, scope: str) -> None:
        self.scope = scope
        super().__init__(scope)


class ChatQueue:
    def __init__(
        self,
        attach_seconds: float = 15.0,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._condition = Condition()
        self._waiting: deque[UUID] = deque()
        self._active: UUID | None = None
        # Every admitted ticket (waiting or active) and the student it belongs to.
        self._owners: dict[UUID, str | None] = {}
        # Admitted tickets whose response stream has not started yet. A client
        # can disconnect before the server iterates the body; the generator then
        # never runs its cleanup, so such tickets expire instead of blocking the
        # head of the queue for every later request.
        self._unattached: dict[UUID, float] = {}
        self._attach_seconds = attach_seconds
        self._clock = clock

    def admit(
        self,
        capacity: int,
        owner: str | None = None,
        owner_capacity: int | None = None,
    ) -> UUID:
        with self._condition:
            self._purge_unattached()
            if len(self._owners) >= capacity:
                raise QueueFull("global")
            if owner is not None and owner_capacity is not None and sum(
                1 for value in self._owners.values() if value == owner
            ) >= owner_capacity:
                raise QueueFull("owner")
            ticket = uuid4()
            self._waiting.append(ticket)
            self._owners[ticket] = owner
            self._unattached[ticket] = self._clock()
            self._condition.notify_all()
            return ticket

    def attach(self, ticket: UUID) -> None:
        """Mark a ticket's response stream as started; it no longer expires."""
        with self._condition:
            self._unattached.pop(ticket, None)

    def wait_turn(self, ticket: UUID, seconds: float) -> bool:
        deadline = self._clock() + seconds
        with self._condition:
            self._unattached.pop(ticket, None)
            while True:
                self._purge_unattached()
                if ticket not in self._owners:
                    return False
                if self._active is None and self._waiting and self._waiting[0] == ticket:
                    self._waiting.popleft()
                    self._active = ticket
                    return True
                remaining = deadline - self._clock()
                if remaining <= 0:
                    self._discard(ticket)
                    self._condition.notify_all()
                    return False
                # Wake periodically so an abandoned head ticket is purged.
                self._condition.wait(min(remaining, 1.0))

    def release(self, ticket: UUID) -> None:
        with self._condition:
            self._discard(ticket)
            self._condition.notify_all()

    def _purge_unattached(self) -> None:
        cutoff = self._clock() - self._attach_seconds
        stale = [ticket for ticket, admitted in self._unattached.items() if admitted <= cutoff]
        for ticket in stale:
            self._discard(ticket)
        if stale:
            self._condition.notify_all()

    def _discard(self, ticket: UUID) -> None:
        self._unattached.pop(ticket, None)
        self._owners.pop(ticket, None)
        if self._active == ticket:
            self._active = None
        else:
            try:
                self._waiting.remove(ticket)
            except ValueError:
                pass


chat_queue = ChatQueue()
