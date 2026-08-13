import httpx
import pytest

from app import config as config_mod
from app.budget import BudgetExceeded
from app.history import InMemoryStore, PrivateScope
from app.service import ConversationService


class FakeClient:
    def __init__(self, answer: str = "ok", exc: BaseException | None = None) -> None:
        self.answer = answer
        self.exc = exc
        self.calls: list[dict] = []

    async def complete(self, *, prompt, history, instructions, prefill_user, prefill_assistant):
        self.calls.append({
            "prompt": prompt,
            "history": list(history),
            "instructions": instructions,
            "prefill_user": prefill_user,
            "prefill_assistant": prefill_assistant,
        })
        if self.exc is not None:
            raise self.exc
        return self.answer


def _service(client: FakeClient):
    store = InMemoryStore(max_personal_messages=40, max_public_messages=500)
    cfg = config_mod.load_config({"API_KEY": "sk-test"})
    return ConversationService(store, client, cfg), store


def _status_error(status: int) -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "https://x/responses")
    return httpx.HTTPStatusError(str(status), request=req, response=httpx.Response(status, request=req))


async def test_normal_turn_is_remembered_and_visible_next_turn():
    client = FakeClient(answer="answer1")
    svc, store = _service(client)
    scope = PrivateScope(100, 1)

    await svc.handle(scope, "hello", instructions="sys", prefill_user="", prefill_assistant="")
    # The turn was appended; the next call must see it in history.
    await svc.handle(scope, "again", instructions="sys", prefill_user="", prefill_assistant="")
    assert client.calls[1]["history"] == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "answer1"},
    ]


async def test_rating_does_not_append_and_clears_history():
    client = FakeClient(answer="rating-result")
    svc, store = _service(client)
    scope = PrivateScope(100, 1)

    # Seed history, then run a rating turn.
    await svc.handle(scope, "q", instructions="sys", prefill_user="", prefill_assistant="")
    await svc.handle(scope, "评分", instructions="RATING", prefill_user="", prefill_assistant="", is_rating=True)
    assert client.calls[-1]["instructions"] == "RATING"
    # Rating is a one-shot eval: it must NOT receive (possibly poisoned) conversation history.
    assert client.calls[-1]["history"] == []
    # Rating must not have appended itself, and must have cleared prior history.
    assert store.history(scope) == []


async def test_rating_clears_history_even_when_api_fails():
    client = FakeClient(exc=RuntimeError("upstream down"))
    svc, store = _service(client)
    scope = PrivateScope(100, 1)

    await svc.handle(scope, "q", instructions="sys", prefill_user="", prefill_assistant="")
    answer = await svc.handle(scope, "评分", instructions="RATING", prefill_user="", prefill_assistant="", is_rating=True)
    assert answer == "我暂时无法处理这条消息，请检查机器人配置或稍后再试。"
    assert store.history(scope) == []  # cleared in finally despite the failure


async def test_http_status_error_returns_upstream_down_message_without_appending():
    client = FakeClient(exc=_status_error(503))
    svc, store = _service(client)
    scope = PrivateScope(100, 1)

    answer = await svc.handle(scope, "q", instructions="sys", prefill_user="", prefill_assistant="")
    assert answer == "上游服务暂时不可用，请稍后再试。"
    assert store.history(scope) == []  # not appended on failure


async def test_budget_exceeded_returns_budget_message_without_appending():
    client = FakeClient(exc=BudgetExceeded("limit"))
    svc, store = _service(client)
    scope = PrivateScope(100, 1)

    answer = await svc.handle(scope, "q", instructions="sys", prefill_user="", prefill_assistant="")
    assert answer == "今天的额度已用完，请明天再试。"
    assert store.history(scope) == []


async def test_clear_empties_scope():
    client = FakeClient(answer="a")
    svc, store = _service(client)
    scope = PrivateScope(100, 1)
    await svc.handle(scope, "q", instructions="sys", prefill_user="", prefill_assistant="")
    assert store.history(scope)
    await svc.clear(scope)
    assert store.history(scope) == []
