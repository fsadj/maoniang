import httpx
import pytest

from app import config as config_mod
from app.llm import (
    ResponsesClient,
    build_chat_messages,
    build_input,
    chat_url,
    classify,
    extract_chat_content,
    extract_output_text,
)


# ---------- classify (pure) ----------

def _status_error(status: int, retry_after: str | None = None) -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "https://x/responses")
    headers = {"Retry-After": retry_after} if retry_after else None
    resp = httpx.Response(status, request=req, headers=headers)
    return httpx.HTTPStatusError(f"{status}", request=req, response=resp)


def test_classify_permanent_4xx():
    assert classify(_status_error(400), 1, max_attempts=3, base_delay=0.5, max_delay=20).action == "raise"
    assert classify(_status_error(404), 1, max_attempts=3, base_delay=0.5, max_delay=20).action == "raise"


def test_classify_retries_5xx_and_transient_4xx():
    assert classify(_status_error(500), 1, max_attempts=3, base_delay=0.5, max_delay=20).action == "retry"
    assert classify(_status_error(429), 1, max_attempts=3, base_delay=0.5, max_delay=20).action == "retry"
    assert classify(_status_error(408), 1, max_attempts=3, base_delay=0.5, max_delay=20).action == "retry"
    assert classify(httpx.TimeoutException("t"), 1, max_attempts=3, base_delay=0.5, max_delay=20).action == "retry"


def test_classify_honors_retry_after_header():
    d = classify(_status_error(429, retry_after="2"), 1, max_attempts=3, base_delay=0.5, max_delay=20)
    assert d.action == "retry"
    assert 1.5 <= d.delay <= 2.5  # 2s ±25% jitter


def test_classify_raises_when_attempts_exhausted():
    assert classify(_status_error(500), 3, max_attempts=3, base_delay=0.5, max_delay=20).action == "raise"


def test_classify_non_httpx_is_permanent():
    assert classify(RuntimeError("no output"), 1, max_attempts=3, base_delay=0.5, max_delay=20).action == "raise"


# ---------- extract_output_text / build_input (pure) ----------

def test_extract_prefers_output_text_field():
    assert extract_output_text({"output_text": " hi "}) == "hi"


def test_extract_walks_output_items():
    data = {"output": [{"type": "message", "content": [{"type": "output_text", "text": "a"}, {"type": "output_text", "text": "b"}]}]}
    assert extract_output_text(data) == "a\nb"


def test_extract_raises_when_no_text():
    with pytest.raises(RuntimeError):
        extract_output_text({"output": []})


def test_build_input_order_prefill_history_current():
    msgs = build_input("now", history=[{"role": "user", "content": "old"}],
                       prefill_user="pu", prefill_assistant="pa")
    assert msgs == [
        {"role": "user", "content": "pu"},
        {"role": "assistant", "content": "pa"},
        {"role": "user", "content": "old"},
        {"role": "user", "content": "now"},
    ]


# ---------- complete() with a mock transport ----------

def _cfg(**overrides):
    env = {
        "API_KEY": "sk-test",
        "API_BASE_URL": "https://example.com/v1",
        "API_MODEL": "m",
        "LLM_MAX_RETRIES": "0",
        "LLM_RETRY_BASE_DELAY": "0",
        "LLM_RETRY_MAX_DELAY": "0",
    }
    env.update(overrides)
    return config_mod.load_config(env)


def _client(cfg, handler):
    return ResponsesClient(cfg, transport=httpx.MockTransport(handler))


async def test_complete_returns_output_text():
    def handler(req):
        return httpx.Response(200, json={"output_text": "hello"})
    answer = await _client(_cfg(), handler).complete(
        prompt="hi", history=[], instructions="sys", prefill_user="", prefill_assistant="")
    assert answer == "hello"


async def test_complete_retries_5xx_then_succeeds():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"output_text": "ok"})

    answer = await _client(_cfg(LLM_MAX_RETRIES="2"), handler).complete(
        prompt="hi", history=[], instructions="sys", prefill_user="", prefill_assistant="")
    assert answer == "ok"
    assert calls["n"] == 2


async def test_complete_does_not_retry_permanent_4xx():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(400, json={"error": "bad"})

    with pytest.raises(httpx.HTTPStatusError):
        await _client(_cfg(LLM_MAX_RETRIES="2"), handler).complete(
            prompt="hi", history=[], instructions="sys", prefill_user="", prefill_assistant="")
    assert calls["n"] == 1  # no retries


async def test_complete_enforces_budget_before_posting():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app.budget import BudgetGuard

    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(200, json={"output_text": "ok"})

    guard = BudgetGuard(1, now=lambda: datetime(2026, 8, 12, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai")))
    client = _client(_cfg(), handler)
    client._guard = guard

    first = await client.complete(prompt="hi", history=[], instructions="sys", prefill_user="", prefill_assistant="")
    assert first == "ok"
    assert calls["n"] == 1

    from app.budget import BudgetExceeded
    with pytest.raises(BudgetExceeded):
        await client.complete(prompt="hi", history=[], instructions="sys", prefill_user="", prefill_assistant="")
    assert calls["n"] == 1  # second call never reached the transport


# ---------- Chat Completions (domestic providers, API_STYLE=chat) ----------

def _chat_cfg(**overrides):
    env = {
        "API_KEY": "sk-test",
        "API_BASE_URL": "https://api.deepseek.com/v1",
        "API_MODEL": "deepseek-chat",
        "API_STYLE": "chat",
        "LLM_MAX_RETRIES": "0",
        "LLM_RETRY_BASE_DELAY": "0",
        "LLM_RETRY_MAX_DELAY": "0",
    }
    env.update(overrides)
    return config_mod.load_config(env)


def test_chat_url_appends_endpoint():
    assert chat_url("https://api.deepseek.com/v1").endswith("/chat/completions")
    assert chat_url("https://x/v1/chat/completions") == "https://x/v1/chat/completions"


def test_build_chat_messages_order():
    msgs = build_chat_messages("你是助手", "现在", history=[{"role": "user", "content": "old"}],
                               prefill_user="pu", prefill_assistant="pa")
    assert msgs == [
        {"role": "system", "content": "你是助手"},
        {"role": "user", "content": "pu"},
        {"role": "assistant", "content": "pa"},
        {"role": "user", "content": "old"},
        {"role": "user", "content": "现在"},
    ]


def test_extract_chat_content():
    assert extract_chat_content({"choices": [{"message": {"content": " hi "}}]}) == "hi"
    with pytest.raises(RuntimeError):
        extract_chat_content({"choices": []})


async def test_chat_complete_posts_to_chat_completions_and_extracts():
    import json as _json
    seen = {}

    def handler(req):
        seen["url"] = str(req.url)
        seen["body"] = req.read().decode()
        return httpx.Response(200, json={"choices": [{"message": {"content": "喵～"}}]})

    answer = await _client(_chat_cfg(), handler).complete(
        prompt="你好", history=[], instructions="你是猫娘", prefill_user="", prefill_assistant="")
    assert answer == "喵～"
    assert seen["url"].endswith("/chat/completions")
    body = _json.loads(seen["body"])
    assert body["messages"][0] == {"role": "system", "content": "你是猫娘"}
    assert body["messages"][-1] == {"role": "user", "content": "你好"}
    assert "instructions" not in body and "store" not in body  # chat style drops Responses-only fields


async def test_responses_style_unchanged_when_api_style_unset():
    def handler(req):
        return httpx.Response(200, json={"output_text": "ok"})
    answer = await _client(_cfg(), handler).complete(
        prompt="hi", history=[], instructions="sys", prefill_user="", prefill_assistant="")
    assert answer == "ok"
