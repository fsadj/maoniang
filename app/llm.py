"""Responses-API client: long-lived httpx client, bounded retry, two timeout tiers.

No streaming / no tools in this phase — they are forward seams.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from .budget import BudgetGuard
from .config import Config

logger = logging.getLogger(__name__)

MessageItem = dict[str, str]


def api_url(base_url: str) -> str:
    """Support either a base URL or a URL that already ends in the endpoint."""
    return base_url if base_url.endswith("/responses") else f"{base_url}/responses"


def extract_output_text(data: Any) -> str:
    """Extract all output_text parts from a Responses API response."""
    if not isinstance(data, dict):
        raise RuntimeError("API response was not a JSON object")

    direct_text = data.get("output_text")
    if isinstance(direct_text, str) and direct_text.strip():
        return direct_text.strip()

    output = data.get("output")
    if not isinstance(output, list):
        raise RuntimeError("API response did not contain output items")

    text_parts: list[str] = []
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "output_text":
                continue
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                text_parts.append(text.strip())

    if not text_parts:
        raise RuntimeError("API response did not contain output text")
    return "\n".join(text_parts)


def build_input(
    prompt: str,
    history: Sequence[MessageItem] = (),
    prefill_user: str = "",
    prefill_assistant: str = "",
) -> list[MessageItem]:
    """Prefill pair, then bounded history, then the current user turn."""
    messages: list[MessageItem] = []
    if prefill_user and prefill_assistant:
        messages.append({"role": "user", "content": prefill_user})
        messages.append({"role": "assistant", "content": prefill_assistant})
    messages.extend(history)
    messages.append({"role": "user", "content": prompt})
    return messages


@dataclass(frozen=True)
class RetryDecision:
    action: str  # "retry" | "raise"
    delay: float


def classify(
    exc: BaseException,
    attempt: int,
    *,
    max_attempts: int,
    base_delay: float,
    max_delay: float,
) -> RetryDecision:
    """Decide whether to retry a failed call. Pure — unit-testable without a network.

    Non-httpx exceptions (RuntimeError from extraction, BudgetExceeded, etc.) are permanent.
    4xx (except 408/425/429) are permanent. Everything network-y is retryable up to max_attempts.
    """
    if not isinstance(exc, httpx.HTTPError):
        return RetryDecision("raise", 0.0)

    retryable = True
    retry_after: str | None = None
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if 400 <= status < 500 and status not in (408, 425, 429):
            retryable = False
        if status == 429:
            retry_after = exc.response.headers.get("Retry-After")

    if not retryable:
        return RetryDecision("raise", 0.0)
    if attempt >= max_attempts:
        return RetryDecision("raise", 0.0)

    delay = base_delay * (2 ** (attempt - 1))
    if retry_after:
        try:
            delay = max(delay, float(retry_after))
        except (TypeError, ValueError):
            pass
    delay = min(delay, max_delay)
    delay *= 0.75 + random.random() * 0.5  # ±25% jitter
    return RetryDecision("retry", delay)


class ResponsesClient:
    """Owns a single long-lived httpx.AsyncClient with bounded retry."""

    def __init__(
        self,
        cfg: Config,
        guard: BudgetGuard | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._cfg = cfg
        self._guard = guard
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._client_lock = asyncio.Lock()

    async def _ensure_client(self) -> httpx.AsyncClient:
        async with self._client_lock:
            if self._client is None:
                self._client = httpx.AsyncClient(transport=self._transport)
            return self._client

    async def start(self) -> None:
        await self._ensure_client()

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def complete(
        self,
        *,
        prompt: str,
        history: Sequence[MessageItem],
        instructions: str,
        prefill_user: str,
        prefill_assistant: str,
    ) -> str:
        if not self._cfg.api_key:
            raise RuntimeError("API_KEY is not configured")
        if self._guard is not None:
            self._guard.check()

        payload: dict[str, Any] = {
            "model": self._cfg.api_model,
            "instructions": instructions,
            "input": build_input(prompt, history, prefill_user, prefill_assistant),
            "store": False,
        }
        if self._cfg.api_reasoning_effort:
            payload["reasoning"] = {"effort": self._cfg.api_reasoning_effort}

        headers = {"Authorization": f"Bearer {self._cfg.api_key}", "Content-Type": "application/json"}
        client = await self._ensure_client()
        return await self._post_with_retry(
            client,
            "POST",
            api_url(self._cfg.api_base_url),
            headers=headers,
            json=payload,
            timeout=self._cfg.api_timeout,
        )

    async def _post_with_retry(self, client: httpx.AsyncClient, method: str, url: str, **kwargs: Any) -> str:
        max_attempts = self._cfg.max_retries + 1
        for attempt in range(1, max_attempts + 1):
            try:
                response = await client.request(method, url, **kwargs)
                response.raise_for_status()
                return extract_output_text(response.json())
            except Exception as exc:  # noqa: BLE001 — classify decides retry vs raise
                decision = classify(
                    exc,
                    attempt,
                    max_attempts=max_attempts,
                    base_delay=self._cfg.retry_base_delay,
                    max_delay=self._cfg.retry_max_delay,
                )
                if decision.action != "retry":
                    raise
                logger.warning(
                    "upstream call failed (attempt %s/%s): %s; retrying in %.2fs",
                    attempt,
                    max_attempts,
                    type(exc).__name__,
                    decision.delay,
                )
                await asyncio.sleep(decision.delay)
        # Unreachable: classify returns "raise" once attempt >= max_attempts.
        raise RuntimeError("retry loop exhausted")
