from __future__ import annotations

import pandas as pd


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    relative_strength = avg_gain / avg_loss.replace(0, float("nan"))
    rsi = 100 - (100 / (1 + relative_strength))
    rsi = rsi.mask((avg_loss == 0) & (avg_gain > 0), 100.0)
    return rsi


def _atr(group: pd.DataFrame, period: int = 14) -> pd.Series:
    previous_close = group["close"].shift(1)
    true_range = pd.concat(
        [
            group["high"] - group["low"],
            (group["high"] - previous_close).abs(),
            (group["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def compute_indicators(frame: pd.DataFrame) -> pd.DataFrame:
    """Compute generic, strategy-free indicators.

    The function sorts each symbol by date and only uses current/past rows.
    It never assigns a score, rank, recommendation or trading signal.
    """

    required = {"code", "date", "open", "high", "low", "close", "volume"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"missing required columns: {missing}")

    output = frame.copy()
    output["date"] = pd.to_datetime(output["date"], errors="raise")
    output = output.sort_values(["code", "date"], kind="stable").reset_index(drop=True)

    calculated: list[pd.DataFrame] = []
    for _, group in output.groupby("code", sort=False, group_keys=False):
        g = group.copy()
        close = g["close"].astype(float)
        volume = g["volume"].astype(float)
        g["sma_5"] = close.rolling(5, min_periods=5).mean()
        g["sma_10"] = close.rolling(10, min_periods=10).mean()
        g["sma_20"] = close.rolling(20, min_periods=20).mean()
        g["ema_12"] = close.ewm(span=12, adjust=False, min_periods=12).mean()
        g["ema_26"] = close.ewm(span=26, adjust=False, min_periods=26).mean()
        g["macd"] = g["ema_12"] - g["ema_26"]
        g["macd_signal"] = g["macd"].ewm(span=9, adjust=False, min_periods=9).mean()
        g["macd_hist"] = g["macd"] - g["macd_signal"]
        g["rsi_14"] = _rsi(close, 14)
        g["atr_14"] = _atr(g, 14)
        g["volume_ma_5"] = volume.shift(1).rolling(5, min_periods=5).mean()
        g["volume_ratio_5"] = volume / g["volume_ma_5"].replace(0, float("nan"))
        calculated.append(g)

    return pd.concat(calculated, ignore_index=True) if calculated else output


def latest_snapshot(indicators: pd.DataFrame) -> pd.DataFrame:
    if indicators.empty:
        return indicators.copy()
    ordered = indicators.sort_values(["code", "date"], kind="stable")
    return ordered.groupby("code", sort=False, as_index=False).tail(1).reset_index(drop=True)
