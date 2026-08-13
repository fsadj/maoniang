"""Daily upstream-API-call guard. Caps cost across ALL scopes (foreground + background)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from zoneinfo import ZoneInfo

_DEFAULT_TZ = "Asia/Shanghai"


class BudgetExceeded(Exception):
    """Raised before an API call when the daily cap has been reached."""


class BudgetGuard:
    """Count intended API calls per Asia/Shanghai day; refuse once the cap is hit.

    `daily_calls <= 0` disables the guard (no limit).
    """

    def __init__(
        self,
        daily_calls: int,
        *,
        tz: str = _DEFAULT_TZ,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._limit = daily_calls
        self._tz = ZoneInfo(tz)
        self._now = now or (lambda: datetime.now(self._tz))
        self._day: date = self._today()
        self._count = 0

    def _today(self) -> date:
        return self._now().date()

    def check(self) -> None:
        """Increment the day's counter; raise BudgetExceeded if already at the cap."""
        if self._limit <= 0:
            return
        today = self._today()
        if today != self._day:
            self._day = today
            self._count = 0
        if self._count >= self._limit:
            raise BudgetExceeded(f"daily API call limit ({self._limit}) reached")
        self._count += 1

    @property
    def remaining(self) -> int:
        """Calls left today (int when limited, -1 when disabled)."""
        if self._limit <= 0:
            return -1
        if self._today() != self._day:
            return self._limit
        return max(0, self._limit - self._count)

    @property
    def used_today(self) -> int:
        """Calls made today (0 when disabled or on a fresh day)."""
        if self._limit <= 0 or self._today() != self._day:
            return 0
        return self._count
