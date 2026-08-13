import pytest

from app.history import PrivateScope, PublicScope
from app.history_sqlite import SqliteConversationStore


def _store(tmp_path, max_personal=40, max_public=500):
    return SqliteConversationStore(max_personal, max_public, tmp_path / "m.sqlite")


def test_append_history_and_cap(tmp_path):
    s = _store(tmp_path, max_personal=4)
    scope = PrivateScope(100, 1)
    s.append_turn(scope, "q1", "a1")
    assert s.history(scope) == [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
    ]
    s.append_turn(scope, "q2", "a2")
    s.append_turn(scope, "q3", "a3")  # exceeds cap of 4 messages
    h = s.history(scope)
    assert len(h) == 4
    assert h[0] == {"role": "user", "content": "q2"}  # q1/a1 evicted


def test_scopes_isolated_and_clear(tmp_path):
    s = _store(tmp_path)
    priv = PrivateScope(100, 1)
    pub = PublicScope(100)
    s.append_turn(priv, "p", "pa")
    s.append_turn(pub, "u", "ua")
    assert len(s.history(priv)) == 2
    assert len(s.history(pub)) == 2
    s.clear(priv)
    assert s.history(priv) == []
    assert len(s.history(pub)) == 2  # clear is scoped


def test_persistence_across_restart(tmp_path):
    """A brand-new store pointing at the same db file hydrates prior history."""
    p = tmp_path / "m.sqlite"
    s1 = SqliteConversationStore(40, 500, p)
    s1.append_turn(PrivateScope(7, 7), "hi", "yo")
    s1.append_turn(PrivateScope(7, 7), "bye", "yo2")
    s1.close()

    s2 = SqliteConversationStore(40, 500, p)  # simulate restart
    assert s2.history(PrivateScope(7, 7)) == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "yo"},
        {"role": "user", "content": "bye"},
        {"role": "assistant", "content": "yo2"},
    ]


def test_persistence_respects_cap(tmp_path):
    p = tmp_path / "m.sqlite"
    s1 = SqliteConversationStore(4, 500, p)  # 4 messages = 2 turns
    for n in (1, 2, 3):
        s1.append_turn(PrivateScope(1, 1), f"q{n}", f"a{n}")
    s1.close()

    s2 = SqliteConversationStore(4, 500, p)
    h = s2.history(PrivateScope(1, 1))
    assert len(h) == 4  # hydrated within cap
    assert h[0] == {"role": "user", "content": "q2"}  # q1/a1 pruned


def test_clear_persists(tmp_path):
    p = tmp_path / "m.sqlite"
    s1 = SqliteConversationStore(40, 500, p)
    s1.append_turn(PrivateScope(1, 1), "q", "a")
    s1.clear(PrivateScope(1, 1))
    s1.close()

    s2 = SqliteConversationStore(40, 500, p)
    assert s2.history(PrivateScope(1, 1)) == []


async def test_locked_re_read_invariant_holds(tmp_path):
    """Sqlite store still reads history inside locked() (re-read-after-acquire)."""
    s = _store(tmp_path)
    scope = PrivateScope(1, 1)
    async with s.locked(scope):
        s.append_turn(scope, "q", "a")
        assert s.history(scope) == [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "a"},
        ]
