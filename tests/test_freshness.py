from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from stock_data_agent.freshness import TradingCalendar, evaluate_freshness, expected_daily_as_of

ZONE = ZoneInfo("Asia/Shanghai")


def test_before_ready_time_uses_previous_open_day():
    now = datetime(2026, 7, 20, 9, 0, tzinfo=ZONE)  # Monday
    expected = expected_daily_as_of(now, calendar=TradingCalendar(), ready_time=time(17, 45))
    assert expected == date(2026, 7, 17)


def test_after_ready_time_uses_current_open_day():
    now = datetime(2026, 7, 20, 18, 0, tzinfo=ZONE)
    expected = expected_daily_as_of(now, calendar=TradingCalendar(), ready_time=time(17, 45))
    assert expected == date(2026, 7, 20)


def test_stale_is_fail_closed():
    result = evaluate_freshness(
        date(2026, 7, 16),
        now=datetime(2026, 7, 20, 18, 0, tzinfo=ZONE),
        calendar=TradingCalendar(),
    )
    assert result.status == "stale"
    assert result.expected_as_of == "2026-07-20"


def test_exchange_calendar_can_skip_holiday():
    calendar = TradingCalendar({date(2026, 7, 16), date(2026, 7, 20)})
    now = datetime(2026, 7, 17, 18, 0, tzinfo=ZONE)
    expected = expected_daily_as_of(now, calendar=calendar)
    assert expected == date(2026, 7, 16)
