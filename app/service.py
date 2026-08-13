"""Conversation service: the lock→reread→call→remember/clear orchestration.

Isolating this here makes it unit-testable without NoneBot events. The rating command's
"clear in finally even on API error" semantics are preserved.
"""

from __future__ import annotations

import logging

import httpx

from . import summary as summary_mod
from .budget import BudgetExceeded
from .config import Config
from .history import ConversationStore, Scope
from .llm import ResponsesClient

logger = logging.getLogger(__name__)

_UPSTREAM_DOWN = "上游服务暂时不可用，请稍后再试。"
_GENERIC_FAILURE = "我暂时无法处理这条消息，请检查机器人配置或稍后再试。"
_BUDGET_OUT = "今天的额度已用完，请明天再试。"


class ConversationService:
    def __init__(self, store: ConversationStore, client: ResponsesClient, cfg: Config) -> None:
        self._store = store
        self._client = client
        self._cfg = cfg

    async def clear(self, scope: Scope) -> None:
        async with self._store.locked(scope):
            self._store.clear(scope)

    async def handle(
        self,
        scope: Scope,
        prompt: str,
        *,
        instructions: str,
        prefill_user: str,
        prefill_assistant: str,
        is_rating: bool = False,
    ) -> str:
        async with self._store.locked(scope):
            # Re-read AFTER acquiring the lock: another turn may have completed while we waited.
            # Rating is a one-shot evaluation: it must NOT see conversation history, otherwise a
            # poisoned prior turn could exfiltrate RATING_SYSTEM_PROMPT.
            history = [] if is_rating else self._store.history(scope)
            summary = "" if is_rating else self._store.get_summary(scope)
            full_instructions = (
                instructions if is_rating else summary_mod.inject_summary(instructions, summary)
            )
            try:
                if is_rating and not instructions:
                    raise RuntimeError("RATING_SYSTEM_PROMPT is not configured")
                answer = await self._client.complete(
                    prompt=prompt,
                    history=history,
                    instructions=full_instructions,
                    prefill_user=prefill_user,
                    prefill_assistant=prefill_assistant,
                )
            except httpx.HTTPStatusError as exc:
                logger.warning("Upstream API returned HTTP %s", exc.response.status_code)
                answer = _UPSTREAM_DOWN
            except BudgetExceeded:
                answer = _BUDGET_OUT
            except (httpx.HTTPError, RuntimeError, ValueError) as exc:
                logger.warning("Could not obtain upstream reply (%s): %s", type(exc).__name__, exc)
                answer = _GENERIC_FAILURE
            else:
                if not is_rating:
                    self._store.append_turn(scope, prompt, answer)
                    if (
                        self._cfg.summarize_enabled
                        and len(self._store.history(scope)) >= self._cfg.summarize_at_messages
                    ):
                        summary_mod.schedule_compact(scope, self._store, self._client, self._cfg)
            finally:
                if is_rating:
                    self._store.clear(scope)
        return answer
