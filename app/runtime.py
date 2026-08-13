"""Process-wide singletons, wired from config. bot.py drives client lifecycle."""

from __future__ import annotations

from . import config
from .budget import BudgetGuard
from .history import InMemoryStore
from .llm import ResponsesClient
from .service import ConversationService

_store: InMemoryStore | None = None
_guard: BudgetGuard | None = None
_client: ResponsesClient | None = None
_service: ConversationService | None = None


def get_store() -> InMemoryStore:
    global _store
    if _store is None:
        cfg = config.config
        if cfg.memory_backend == "sqlite":
            from .history_sqlite import SqliteConversationStore

            _store = SqliteConversationStore(
                cfg.personal_history_max, cfg.max_public_messages, cfg.sqlite_path
            )
        else:
            _store = InMemoryStore(cfg.personal_history_max, cfg.max_public_messages)
    return _store


def get_guard() -> BudgetGuard:
    global _guard
    if _guard is None:
        _guard = BudgetGuard(config.config.budget_daily_calls)
    return _guard


def get_client() -> ResponsesClient:
    global _client
    if _client is None:
        _client = ResponsesClient(config.config, get_guard())
    return _client


def get_service() -> ConversationService:
    global _service
    if _service is None:
        _service = ConversationService(get_store(), get_client(), config.config)
    return _service
