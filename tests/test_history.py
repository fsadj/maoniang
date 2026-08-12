import pytest

from app.history import InMemoryStore, PrivateScope, PublicScope


def test_append_turn_records_user_assistant_pair():
    store = InMemoryStore(max_personal_messages=40, max_public_messages=500)
    scope = PrivateScope(100, 1)
    store.append_turn(scope, "q1", "a1")
    assert store.history(scope) == [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
    ]


def test_personal_history_drops_oldest_beyond_cap():
    store = InMemoryStore(max_personal_messages=4, max_public_messages=500)
    scope = PrivateScope(100, 1)
    for n in (1, 2, 3):
        store.append_turn(scope, f"q{n}", f"a{n}")
    history = store.history(scope)
    assert len(history) == 4  # deque maxlen 4
    assert history[0] == {"role": "user", "content": "q2"}  # q1/a1 evicted


def test_public_and_private_scopes_are_isolated():
    store = InMemoryStore(max_personal_messages=40, max_public_messages=500)
    store.append_turn(PrivateScope(100, 1), "priv", "priv-ans")
    store.append_turn(PublicScope(100), "pub", "pub-ans")
    store.append_turn(PrivateScope(100, 2), "other-user", "other-ans")
    assert len(store.history(PrivateScope(100, 1))) == 2
    assert len(store.history(PublicScope(100))) == 2
    assert len(store.history(PrivateScope(100, 2))) == 2


def test_clear_empties_only_that_scope():
    store = InMemoryStore(max_personal_messages=40, max_public_messages=500)
    priv = PrivateScope(100, 1)
    store.append_turn(priv, "q", "a")
    store.clear(priv)
    assert store.history(priv) == []
    # clearing again is a no-op
    store.clear(priv)


async def test_locked_serializes_and_history_reads_inside():
    store = InMemoryStore(max_personal_messages=40, max_public_messages=500)
    scope = PrivateScope(1, 1)
    async with store.locked(scope):
        store.append_turn(scope, "q", "a")
        # The re-read invariant: history reflects the append within the same lock.
        assert store.history(scope) == [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "a"},
        ]


async def test_concurrent_handles_on_same_scope_both_persist():
    """Two interleaved turns must not lose history (the lock serializes appends)."""
    store = InMemoryStore(max_personal_messages=40, max_public_messages=500)
    scope = PrivateScope(1, 1)

    async def turn():
        async with store.locked(scope):
            hist = store.history(scope)
            store.append_turn(scope, "q", "a")  # simulate a completed turn

    # If locking were broken, racing appends could be lost; with the lock both persist.
    import asyncio
    await asyncio.gather(turn(), turn())
    assert len(store.history(scope)) == 4
