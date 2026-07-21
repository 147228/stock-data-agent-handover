from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

import pandas as pd


@dataclass(frozen=True)
class FreshnessResult:
    status: str
    actual_as_of: str | None
    expected_as_of: str | None
    checked_at: str
    calendar_quality: str
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


class TradingCalendar:
    """Small calendar abstraction.

    A production calendar should be loaded from a versioned CSV containing
    ``date,is_open``. Without one, weekday-only logic is intentionally marked
    as degraded evidence.
    """

    def __init__(self, open_dates: Iterable[date] | None = None) -> None:
        self._open_dates = set(open_dates) if open_dates is not None else None

    @property
    def quality(self) -> str:
        return "exchange_calendar" if self._open_dates is not None else "weekday_only"

    @classmethod
    def from_csv(cls, path: str | Path) -> "TradingCalendar":
        frame = pd.read_csv(path, dtype={"date": "string"})
        required = {"date", "is_open"}
        missing = required - set(frame.columns)
        if missing:
            raise ValueError(f"calendar missing columns: {sorted(missing)}")
        parsed = pd.to_datetime(frame["date"], errors="raise").dt.date
        is_open = frame["is_open"].astype(str).str.lower().isin({"1", "true", "yes", "y"})
        return cls(parsed[is_open].tolist())

    def is_open(self, value: date) -> bool:
        if self._open_dates is not None:
            return value in self._open_dates
        return value.weekday() < 5

    def previous_open_date(self, value: date, *, include_value: bool = False) -> date:
        cursor = value if include_value else value - timedelta(days=1)
        for _ in range(370):
            if self.is_open(cursor):
                return cursor
            cursor -= timedelta(days=1)
        raise RuntimeError("could not find an open date within 370 days")


def expected_daily_as_of(
    now: datetime,
    *,
    calendar: TradingCalendar,
    ready_time: time = time(17, 45),
) -> date:
    local_day = now.date()
    if calendar.is_open(local_day) and now.timetz().replace(tzinfo=None) >= ready_time:
        return local_day
    return calendar.previous_open_date(local_day, include_value=False)


def evaluate_freshness(
    actual_as_of: date | None,
    *,
    now: datetime | None = None,
    timezone: str = "Asia/Shanghai",
    ready_time: time = time(17, 45),
    calendar: TradingCalendar | None = None,
) -> FreshnessResult:
    zone = ZoneInfo(timezone)
    checked_at = now.astimezone(zone) if now is not None else datetime.now(zone)
    active_calendar = calendar or TradingCalendar()
    expected = expected_daily_as_of(
        checked_at,
        calendar=active_calendar,
        ready_time=ready_time,
    )
    if actual_as_of is None:
        return FreshnessResult(
            status="unknown",
            actual_as_of=None,
            expected_as_of=expected.isoformat(),
            checked_at=checked_at.isoformat(),
            calendar_quality=active_calendar.quality,
            reason="actual_as_of is unavailable",
        )
    if actual_as_of < expected:
        return FreshnessResult(
            status="stale",
            actual_as_of=actual_as_of.isoformat(),
            expected_as_of=expected.isoformat(),
            checked_at=checked_at.isoformat(),
            calendar_quality=active_calendar.quality,
            reason=f"data is behind by at least {(expected - actual_as_of).days} calendar day(s)",
        )
    return FreshnessResult(
        status="fresh",
        actual_as_of=actual_as_of.isoformat(),
        expected_as_of=expected.isoformat(),
        checked_at=checked_at.isoformat(),
        calendar_quality=active_calendar.quality,
        reason="actual_as_of meets the expected daily date",
    )
