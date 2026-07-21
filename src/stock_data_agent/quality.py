from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd

from .freshness import FreshnessResult


DEFAULT_REQUIRED_COLUMNS = ["code", "date", "open", "high", "low", "close", "volume"]


@dataclass
class QualityReport:
    ok: bool
    row_count: int
    symbol_count: int
    actual_as_of: str | None
    required_columns: list[str]
    missing_columns: list[str]
    field_coverage: dict[str, float]
    duplicate_key_rows: int
    invalid_ohlc_rows: int
    negative_value_rows: int
    non_monotonic_symbols: list[str]
    freshness: dict | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    samples: dict[str, list[dict]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def load_ohlcv(path: str | Path) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype={"code": "string"})
    if "code" in frame:
        frame["code"] = frame["code"].str.strip()
    if "date" in frame:
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    for column in ["open", "high", "low", "close", "volume", "amount", "turnover_rate"]:
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def _sample(frame: pd.DataFrame, mask: pd.Series, max_samples: int) -> list[dict]:
    if frame.empty or not mask.any():
        return []
    sample = frame.loc[mask].head(max_samples).copy()
    if "date" in sample:
        sample["date"] = sample["date"].dt.strftime("%Y-%m-%d")
    return sample.to_dict(orient="records")


def validate_ohlcv(
    frame: pd.DataFrame,
    *,
    required_columns: list[str] | None = None,
    freshness: FreshnessResult | None = None,
    max_error_samples: int = 20,
) -> QualityReport:
    required = required_columns or DEFAULT_REQUIRED_COLUMNS
    missing = sorted(set(required) - set(frame.columns))
    coverage = {
        column: round(float(frame[column].notna().mean() * 100), 2)
        for column in frame.columns
    }
    errors: list[str] = []
    warnings: list[str] = []
    samples: dict[str, list[dict]] = {}
    if missing:
        errors.append(f"missing required columns: {missing}")
        return QualityReport(
            ok=False,
            row_count=len(frame),
            symbol_count=int(frame["code"].nunique()) if "code" in frame else 0,
            actual_as_of=None,
            required_columns=required,
            missing_columns=missing,
            field_coverage=coverage,
            duplicate_key_rows=0,
            invalid_ohlc_rows=0,
            negative_value_rows=0,
            non_monotonic_symbols=[],
            freshness=freshness.to_dict() if freshness else None,
            errors=errors,
            warnings=warnings,
            samples=samples,
        )

    required_null = frame[required].isna().any(axis=1)
    if required_null.any():
        errors.append(f"{int(required_null.sum())} row(s) have null required values")
        samples["null_required"] = _sample(frame, required_null, max_error_samples)

    duplicate_mask = frame.duplicated(["code", "date"], keep=False)
    duplicate_rows = int(duplicate_mask.sum())
    if duplicate_rows:
        errors.append(f"{duplicate_rows} duplicate code/date row(s)")
        samples["duplicate_keys"] = _sample(frame, duplicate_mask, max_error_samples)

    invalid_ohlc = (
        (frame["high"] < frame[["open", "close", "low"]].max(axis=1))
        | (frame["low"] > frame[["open", "close", "high"]].min(axis=1))
    ).fillna(False)
    invalid_rows = int(invalid_ohlc.sum())
    if invalid_rows:
        errors.append(f"{invalid_rows} row(s) violate OHLC relationships")
        samples["invalid_ohlc"] = _sample(frame, invalid_ohlc, max_error_samples)

    non_negative_columns = ["open", "high", "low", "close", "volume"]
    negative_mask = (frame[non_negative_columns] < 0).any(axis=1).fillna(False)
    negative_rows = int(negative_mask.sum())
    if negative_rows:
        errors.append(f"{negative_rows} row(s) contain negative price/volume values")
        samples["negative_values"] = _sample(frame, negative_mask, max_error_samples)

    non_monotonic: list[str] = []
    for code, group in frame.groupby("code", dropna=False, sort=False):
        if not group["date"].is_monotonic_increasing:
            non_monotonic.append(str(code))
    if non_monotonic:
        warnings.append(f"{len(non_monotonic)} symbol(s) are not sorted by date")

    valid_dates = frame["date"].dropna()
    actual_as_of: date | None = valid_dates.max().date() if not valid_dates.empty else None
    if freshness is not None and freshness.status != "fresh":
        errors.append(f"freshness gate failed: {freshness.status}")

    return QualityReport(
        ok=not errors,
        row_count=len(frame),
        symbol_count=int(frame["code"].nunique(dropna=True)),
        actual_as_of=actual_as_of.isoformat() if actual_as_of else None,
        required_columns=required,
        missing_columns=missing,
        field_coverage=coverage,
        duplicate_key_rows=duplicate_rows,
        invalid_ohlc_rows=invalid_rows,
        negative_value_rows=negative_rows,
        non_monotonic_symbols=non_monotonic[:max_error_samples],
        freshness=freshness.to_dict() if freshness else None,
        errors=errors,
        warnings=warnings,
        samples=samples,
    )
