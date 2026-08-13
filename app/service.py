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
    ) -> str:
        async with self._store.locked(scope):
            # Re-read AFTER acquiring the lock: another turn may have completed while we waited.
            history = self._store.history(scope)
            summary = self._store.get_summary(scope)
            full_instructions = summary_mod.inject_summary(instructions, summary)
            try:
                answer = await self._client.complete(
                    prompt=prompt,
                    history=history,
                    instructions=full_instructions,
                    prefill_user=prefill_user,
                    prefill_assistant=prefill_assistant,
                )
            except httpx.HTTPStatusError as exc:
                logger.warning("Upstream API returned HTTP %s", exc.response.status_code)
                answer = self._cfg.fallback_upstream
            except BudgetExceeded:
                answer = self._cfg.fallback_budget
            except (httpx.HTTPError, RuntimeError, ValueError) as exc:
                logger.warning("Could not obtain upstream reply (%s): %s", type(exc).__name__, exc)
                answer = self._cfg.fallback_generic
            else:
                self._store.append_turn(scope, prompt, answer)
                if (
                    self._cfg.summarize_enabled
                    and len(self._store.history(scope)) >= self._cfg.summarize_at_messages
                ):
                    summary_mod.schedule_compact(scope, self._store, self._client, self._cfg)
        return answer

    async def undo(self, scope: Scope) -> int:
        """Remove the last user+assistant turn (the 撤销 command). Returns msgs dropped."""
        async with self._store.locked(scope):
            return self._store.drop_last_turn(scope)
