import pandas as pd
import pytest

from stock_data_agent.indicators import compute_indicators, latest_snapshot


def make_frame(rows: int = 40) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "code": ["000001"] * rows,
            "date": pd.bdate_range("2026-01-01", periods=rows),
            "open": [10 + i * 0.1 for i in range(rows)],
            "high": [10.3 + i * 0.1 for i in range(rows)],
            "low": [9.8 + i * 0.1 for i in range(rows)],
            "close": [10.1 + i * 0.1 for i in range(rows)],
            "volume": [1000 + i * 10 for i in range(rows)],
        }
    )


def test_indicators_do_not_backfill_short_history():
    output = compute_indicators(make_frame())
    assert output.loc[3, "sma_5"] != output.loc[3, "sma_5"]  # NaN
    assert output.loc[4, "sma_5"] == pytest.approx(output.loc[:4, "close"].mean())


def test_volume_ratio_uses_previous_five_rows():
    frame = make_frame()
    output = compute_indicators(frame)
    expected = frame.loc[5, "volume"] / frame.loc[:4, "volume"].mean()
    assert output.loc[5, "volume_ratio_5"] == pytest.approx(expected)


def test_latest_snapshot_returns_one_row_per_symbol():
    frame = pd.concat([make_frame(), make_frame().assign(code="000002")], ignore_index=True)
    latest = latest_snapshot(compute_indicators(frame))
    assert set(latest["code"]) == {"000001", "000002"}
    assert len(latest) == 2
