"""Conversation store. The lock-and-reread invariant lives in ONE place: `locked()`.

History is in-memory and clears on process restart (persistence is a later phase).
"""

from __future__ import annotations

import asyncio
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Protocol

MessageItem = dict[str, str]


@dataclass(frozen=True)
class PrivateScope:
    """A (group, user) personal conversation."""

    group_id: int
    user_id: int


@dataclass(frozen=True)
class PublicScope:
    """A group-wide shared `公共` conversation."""

    group_id: int


Scope = PrivateScope | PublicScope


class ConversationStore(Protocol):
    """Minimal store surface used by the service layer."""

    @asynccontextmanager
    async def locked(self, scope: Scope):  # pragma: no cover - protocol
        yield self

    def history(self, scope: Scope) -> list[MessageItem]:  # pragma: no cover - protocol
        ...

    def append_turn(self, scope: Scope, user: str, assistant: str) -> None:  # pragma: no cover
        ...

    def clear(self, scope: Scope) -> None:  # pragma: no cover - protocol
        ...


class InMemoryStore:
    """dict-of-deques + per-scope locks. Reads happen inside `locked()` (re-read invariant)."""

    def __init__(self, max_personal_messages: int, max_public_messages: int) -> None:
        self._maxlen = {
            PrivateScope: max_personal_messages,
            PublicScope: max_public_messages,
        }
        self._histories: dict[Scope, deque[MessageItem]] = {}
        self._locks: dict[Scope, asyncio.Lock] = {}

    def _lock_for(self, scope: Scope) -> asyncio.Lock:
        lock = self._locks.get(scope)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[scope] = lock
        return lock

    @asynccontextmanager
    async def locked(self, scope: Scope):
        """Acquire the per-scope lock; callers MUST re-read history inside this block."""
        async with self._lock_for(scope):
            yield self

    def _deque_for(self, scope: Scope) -> deque[MessageItem]:
        history = self._histories.get(scope)
        if history is None:
            history = deque(maxlen=self._maxlen[type(scope)])
            self._histories[scope] = history
        return history

    def history(self, scope: Scope) -> list[MessageItem]:
        """Snapshot copy of the current history (call inside `locked()`)."""
        return list(self._deque_for(scope))

    def append_turn(self, scope: Scope, user: str, assistant: str) -> None:
        deque_ = self._deque_for(scope)
        deque_.append({"role": "user", "content": user})
        deque_.append({"role": "assistant", "content": assistant})

    def clear(self, scope: Scope) -> None:
        self._histories.pop(scope, None)
