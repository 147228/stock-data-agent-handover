from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from stock_data_agent.freshness import TradingCalendar, evaluate_freshness
from stock_data_agent.quality import validate_ohlcv


def valid_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "code": ["000001", "000001"],
            "date": pd.to_datetime(["2026-07-20", "2026-07-21"]),
            "open": [10.0, 10.2],
            "high": [10.4, 10.5],
            "low": [9.9, 10.1],
            "close": [10.2, 10.4],
            "volume": [100.0, 110.0],
        }
    )


def test_valid_ohlcv_passes():
    report = validate_ohlcv(valid_frame())
    assert report.ok
    assert report.actual_as_of == "2026-07-21"


def test_invalid_ohlc_fails():
    frame = valid_frame()
    frame.loc[1, "high"] = 9.0
    report = validate_ohlcv(frame)
    assert not report.ok
    assert report.invalid_ohlc_rows == 1


def test_duplicates_fail():
    frame = pd.concat([valid_frame(), valid_frame().iloc[[1]]], ignore_index=True)
    report = validate_ohlcv(frame)
    assert not report.ok
    assert report.duplicate_key_rows == 2


def test_stale_freshness_fails_quality_gate():
    frame = valid_frame().iloc[[0]].copy()
    freshness = evaluate_freshness(
        frame["date"].max().date(),
        now=datetime(2026, 7, 21, 18, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        calendar=TradingCalendar(),
    )
    report = validate_ohlcv(frame, freshness=freshness)
    assert not report.ok
    assert freshness.status == "stale"
