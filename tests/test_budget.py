from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.budget import BudgetExceeded, BudgetGuard


def _fixed_now(day: datetime):
    return lambda: day


def test_disabled_guard_never_raises():
    guard = BudgetGuard(0, now=_fixed_now(datetime(2026, 8, 12, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))))
    for _ in range(100):
        guard.check()
    assert guard.used_today == 100  # counts even when budget disabled (for 状态)
    assert guard.remaining == -1


def test_guard_raises_after_cap_then_resets_next_day():
    tz = ZoneInfo("Asia/Shanghai")
    day1 = datetime(2026, 8, 12, 23, 59, tzinfo=tz)
    day2 = datetime(2026, 8, 13, 0, 1, tzinfo=tz)

    guard = BudgetGuard(2, now=_fixed_now(day1))
    guard.check()
    guard.check()
    with pytest.raises(BudgetExceeded):
        guard.check()
    assert guard.remaining == 0

    # Roll the clock forward to a new calendar day.
    guard._now = _fixed_now(day2)
    guard.check()  # new day, counter reset
    assert guard.remaining == 1


def test_remaining_reports_full_on_new_day():
    tz = ZoneInfo("Asia/Shanghai")
    guard = BudgetGuard(5, now=_fixed_now(datetime(2026, 8, 12, 12, 0, tzinfo=tz)))
    guard.check()
    guard._now = _fixed_now(datetime(2026, 8, 12, 12, 0, tzinfo=tz) + timedelta(days=1))
    assert guard.remaining == 5
