"""Typed configuration, loaded once from .env.

Deliberately pydantic-free (avoids the pydantic v1/v2 + NoneBot boundary risk).
Malformed integers are warned-and-skipped to preserve the reference bot's lenient behavior.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_PATH = _PROJECT_ROOT / ".env"


def csv_ints(name: str, env: Mapping[str, str]) -> frozenset[int]:
    """Parse a comma-separated list of ints, ignoring malformed values (with a warning)."""
    values: set[int] = set()
    for item in env.get(name, "").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            values.add(int(item))
        except ValueError:
            logger.warning("Ignoring invalid integer in %s: %r", name, item)
    return frozenset(values)


def _get(env: Mapping[str, str], name: str, default: str = "") -> str:
    return env.get(name, default)


def _get_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("Ignoring invalid int for %s=%r, using default %s", name, raw, default)
        return default


def _get_float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("Ignoring invalid float for %s=%r, using default %s", name, raw, default)
        return default


@dataclass(frozen=True)
class Config:
    # Permissions
    target_user_ids: frozenset[int]
    target_group_ids: frozenset[int]
    all_users_group_ids: frozenset[int]
    # Upstream API
    api_base_url: str
    api_key: str
    api_model: str
    api_reasoning_effort: str
    # "responses" (OpenAI Responses API) or "chat" (OpenAI-compatible Chat Completions,
    # used by domestic providers like DeepSeek / 智谱 / 通义).
    api_style: str
    # Prompts
    system_prompt: str
    public_system_prompt: str
    rating_system_prompt: str
    prefill_user: str
    prefill_assistant: str
    public_prefill_user: str
    public_prefill_assistant: str
    # Limits
    api_timeout: float
    short_timeout: float
    max_message_length: int
    max_conversation_turns: int
    max_public_messages: int
    # Persistence
    memory_backend: str
    sqlite_path: str
    # Summarization (context compaction) — opt-in, off by default
    summarize_enabled: bool
    summarize_at_messages: int
    summarize_batch: int
    summarize_model: str
    # Reliability / cost
    max_retries: int
    retry_base_delay: float
    retry_max_delay: float
    budget_daily_calls: int
    # Status notifications
    status_group_ids: frozenset[int]
    online_message: str
    offline_message: str
    # In-character fallback messages shown on upstream/error/budget failures (overridable;
    # defaults are a non-explicit nya voice). Set FALLBACK_* in .env for your own flavor.
    fallback_upstream: str
    fallback_generic: str
    fallback_budget: str
    # Derived convenience (not from env)
    personal_history_max: int = field(init=False)

    def __post_init__(self) -> None:
        # Each turn is user+assistant = 2 deque entries.
        object.__setattr__(self, "personal_history_max", max(2, self.max_conversation_turns * 2))


def load_config(env: Mapping[str, str] | None = None) -> Config:
    """Build a Config from `env`, loading .env first when env is None."""
    if env is None:
        load_dotenv(_ENV_PATH, override=False)
        env = os.environ

    system_prompt = _get(env, "SYSTEM_PROMPT", "你是一个友善、简洁的 QQ 群助手。请直接回答用户的问题。")
    public_system_prompt = _get(env, "PUBLIC_SYSTEM_PROMPT").strip() or system_prompt

    prefill_user = _get(env, "PREFILL_USER").strip()
    prefill_assistant = _get(env, "PREFILL_ASSISTANT").strip()
    public_prefill_user = _get(env, "PUBLIC_PREFILL_USER").strip()
    public_prefill_assistant = _get(env, "PUBLIC_PREFILL_ASSISTANT").strip()

    return Config(
        target_user_ids=csv_ints("TARGET_USER_IDS", env),
        target_group_ids=csv_ints("TARGET_GROUP_IDS", env),
        all_users_group_ids=csv_ints("ALL_USERS_GROUP_IDS", env),
        api_base_url=_get(env, "API_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
        api_key=_get(env, "API_KEY"),
        api_model=_get(env, "API_MODEL", "gpt-4o-mini"),
        api_reasoning_effort=_get(env, "API_REASONING_EFFORT").strip(),
        api_style=(_get(env, "API_STYLE", "responses").strip().lower() or "responses"),
        system_prompt=system_prompt,
        public_system_prompt=public_system_prompt,
        rating_system_prompt=_get(env, "RATING_SYSTEM_PROMPT").strip(),
        prefill_user=prefill_user,
        prefill_assistant=prefill_assistant,
        public_prefill_user=public_prefill_user,
        public_prefill_assistant=public_prefill_assistant,
        api_timeout=_get_float(env, "API_TIMEOUT_SECONDS", 90.0),
        short_timeout=_get_float(env, "LLM_SHORT_TIMEOUT_SECONDS", 15.0),
        max_message_length=_get_int(env, "MAX_MESSAGE_LENGTH", 2000),
        max_conversation_turns=max(1, _get_int(env, "MAX_CONVERSATION_TURNS", 20)),
        max_public_messages=max(1, _get_int(env, "MAX_PUBLIC_CONVERSATION_MESSAGES", 500)),
        memory_backend=(_get(env, "MEMORY_BACKEND", "memory").strip().lower() or "memory"),
        sqlite_path=_get(env, "SQLITE_PATH", "data/memory.sqlite"),
        summarize_enabled=(_get(env, "SUMMARIZE_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")),
        summarize_at_messages=max(2, _get_int(env, "SUMMARIZE_AT_MESSAGES", 28)),
        summarize_batch=max(2, _get_int(env, "SUMMARIZE_BATCH", 16)),
        summarize_model=_get(env, "SUMMARIZE_MODEL", "").strip(),
        max_retries=max(0, _get_int(env, "LLM_MAX_RETRIES", 3)),
        retry_base_delay=max(0.0, _get_float(env, "LLM_RETRY_BASE_DELAY", 0.5)),
        retry_max_delay=max(0.0, _get_float(env, "LLM_RETRY_MAX_DELAY", 20.0)),
        budget_daily_calls=max(0, _get_int(env, "BUDGET_DAILY_API_CALLS", 0)),
        status_group_ids=csv_ints("STATUS_GROUP_IDS", env),
        online_message=_get(env, "ONLINE_MESSAGE").strip(),
        offline_message=_get(env, "OFFLINE_MESSAGE").strip(),
        fallback_upstream=_get(env, "FALLBACK_UPSTREAM", "nya…脑子卡住了，等会儿再来戳我喵"),
        fallback_generic=_get(env, "FALLBACK_GENERIC", "呜…nya晕过去了，叫管理员来把我弄醒喵"),
        fallback_budget=_get(env, "FALLBACK_BUDGET", "nya今天被聊累啦，明天再来吧喵"),
    )


# Parsed once at import.
config = load_config()
