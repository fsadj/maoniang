"""SQLite-backed conversation store: write-through durability + hydrate on restart.

Subclasses InMemoryStore (same locked/history/append_turn/clear interface). Keeps
in-memory deques for the hot read path, writes every append/clear through to SQLite,
and lazily hydrates a scope's deque from SQLite on first access — so history survives
a process restart.

Thread-safety (per the design verification): connection opened with
check_same_thread=False, every SQLite access serialized by a threading.Lock, WAL
journal mode. SQLite ops are local + sub-ms, so they run synchronously on the
event-loop thread (fine at this scale); the asyncio.Lock in locked() still serializes
coroutines per scope (the re-read-after-acquire invariant is unchanged).
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from .history import InMemoryStore, PrivateScope, PublicScope, Scope

_SUMMARIES_SCHEMA = """CREATE TABLE IF NOT EXISTS summaries (
    scope_type TEXT NOT NULL,
    group_id INTEGER NOT NULL,
    user_id INTEGER,
    summary TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (scope_type, group_id, user_id)
)"""

_SCHEMA = """CREATE TABLE IF NOT EXISTS turns (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_type TEXT NOT NULL,
    group_id INTEGER NOT NULL,
    user_id INTEGER,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
)"""


def _scope_key(scope: Scope) -> tuple[str, int, int | None]:
    if isinstance(scope, PrivateScope):
        return ("private", scope.group_id, scope.user_id)
    if isinstance(scope, PublicScope):
        return ("public", scope.group_id, None)
    raise TypeError(f"unknown scope: {scope!r}")


class SqliteConversationStore(InMemoryStore):
    def __init__(
        self, max_personal_messages: int, max_public_messages: int, path: str | Path
    ) -> None:
        super().__init__(max_personal_messages, max_public_messages)
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(self._path), check_same_thread=False)
        self._db_lock = threading.Lock()
        self._hydrated: set[Scope] = set()
        with self._db_lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=NORMAL")
            self._db.execute(_SCHEMA)
            self._db.execute(_SUMMARIES_SCHEMA)
            self._db.execute(
                "CREATE INDEX IF NOT EXISTS idx_turns_scope "
                "ON turns(scope_type, group_id, user_id, seq)"
            )
            self._db.commit()

    def _deque_for(self, scope: Scope):
        deque_ = super()._deque_for(scope)
        if scope not in self._hydrated:
            self._hydrated.add(scope)
            self._hydrate(deque_, scope)
        return deque_

    def _hydrate(self, deque_, scope: Scope) -> None:
        stype, gid, uid = _scope_key(scope)
        cap = self._maxlen[type(scope)]
        with self._db_lock:
            cur = self._db.execute(
                "SELECT role, content FROM turns "
                "WHERE scope_type=? AND group_id=? AND user_id IS ? "
                "ORDER BY seq DESC LIMIT ?",
                (stype, gid, uid, cap),
            )
            rows = list(reversed(cur.fetchall()))
        for role, content in rows:
            deque_.append({"role": role, "content": content})

    def append_turn(self, scope: Scope, user: str, assistant: str) -> None:
        super().append_turn(scope, user, assistant)
        stype, gid, uid = _scope_key(scope)
        now = datetime.now(timezone.utc).isoformat()
        cap = self._maxlen[type(scope)]
        with self._db_lock:
            self._db.executemany(
                "INSERT INTO turns(scope_type, group_id, user_id, role, content, created_at) "
                "VALUES(?,?,?,?,?,?)",
                [
                    (stype, gid, uid, "user", user, now),
                    (stype, gid, uid, "assistant", assistant, now),
                ],
            )
            # prune: keep only the most recent `cap` rows for this scope
            self._db.execute(
                "DELETE FROM turns WHERE scope_type=? AND group_id=? AND user_id IS ? "
                "AND seq NOT IN (SELECT seq FROM turns WHERE scope_type=? AND group_id=? "
                "AND user_id IS ? ORDER BY seq DESC LIMIT ?)",
                (stype, gid, uid, stype, gid, uid, cap),
            )
            self._db.commit()

    def clear(self, scope: Scope) -> None:
        super().clear(scope)
        stype, gid, uid = _scope_key(scope)
        with self._db_lock:
            self._db.execute(
                "DELETE FROM turns WHERE scope_type=? AND group_id=? AND user_id IS ?",
                (stype, gid, uid),
            )
            self._db.execute(
                "DELETE FROM summaries WHERE scope_type=? AND group_id=? AND user_id IS ?",
                (stype, gid, uid),
            )
            self._db.commit()

    def get_summary(self, scope: Scope) -> str:
        stype, gid, uid = _scope_key(scope)
        with self._db_lock:
            row = self._db.execute(
                "SELECT summary FROM summaries WHERE scope_type=? AND group_id=? AND user_id IS ?",
                (stype, gid, uid),
            ).fetchone()
        return row[0] if row else ""

    def set_summary(self, scope: Scope, summary: str) -> None:
        stype, gid, uid = _scope_key(scope)
        now = datetime.now(timezone.utc).isoformat()
        with self._db_lock:
            self._db.execute(
                "INSERT INTO summaries(scope_type, group_id, user_id, summary, updated_at) "
                "VALUES(?,?,?,?,?) ON CONFLICT(scope_type, group_id, user_id) "
                "DO UPDATE SET summary=excluded.summary, updated_at=excluded.updated_at",
                (stype, gid, uid, summary, now),
            )
            self._db.commit()

    def clear_summary(self, scope: Scope) -> None:
        super().clear_summary(scope)
        stype, gid, uid = _scope_key(scope)
        with self._db_lock:
            self._db.execute(
                "DELETE FROM summaries WHERE scope_type=? AND group_id=? AND user_id IS ?",
                (stype, gid, uid),
            )
            self._db.commit()

    def drop_oldest(self, scope: Scope, n: int) -> None:
        super().drop_oldest(scope, n)  # popleft from the in-memory deque
        stype, gid, uid = _scope_key(scope)
        with self._db_lock:
            self._db.execute(
                "DELETE FROM turns WHERE rowid IN (SELECT rowid FROM turns "
                "WHERE scope_type=? AND group_id=? AND user_id IS ? ORDER BY seq ASC LIMIT ?)",
                (stype, gid, uid, n),
            )
            self._db.commit()

    def close(self) -> None:
        with self._db_lock:
            self._db.close()
