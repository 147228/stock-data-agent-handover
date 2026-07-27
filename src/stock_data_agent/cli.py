from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import yaml

from . import __version__
from .a_share_universe import fetch_a_share_universe, write_a_share_database
from .freshness import TradingCalendar, evaluate_freshness
from .indicators import compute_indicators, latest_snapshot
from .instock_client import InStockClient, InStockConfig
from .quality import DEFAULT_REQUIRED_COLUMNS, load_ohlcv, validate_ohlcv


def _write_json(path: str | Path, value: dict) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return output


def _sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_config(path: str | Path | None) -> dict:
    if path is None:
        return {}
    value = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise TypeError("config root must be a mapping")
    return value


def _client_from(config: dict, base_url_override: str | None = None) -> InStockClient:
    data = config.get("instock", {})
    client_config = InStockConfig(
        base_url=base_url_override or data.get("base_url", "http://127.0.0.1:9988"),
        timeout_seconds=float(data.get("timeout_seconds", 10)),
        max_response_bytes=int(data.get("max_response_bytes", 20_000_000)),
        allow_remote=bool(data.get("allow_remote", False)),
        allowed_modules=tuple(data.get("allowed_modules", []) or []),
    )
    return InStockClient(client_config)


def _analysis_settings(config: dict) -> dict:
    data = config.get("analysis", {})
    ready_parts = str(data.get("daily_ready_time", "17:45")).split(":")
    if len(ready_parts) != 2:
        raise ValueError("daily_ready_time must be HH:MM")
    return {
        "timezone": data.get("timezone", "Asia/Shanghai"),
        "ready_time": time(int(ready_parts[0]), int(ready_parts[1])),
        "calendar": data.get("trading_calendar"),
        "required_columns": data.get("required_columns", DEFAULT_REQUIRED_COLUMNS),
        "max_error_samples": int(data.get("max_error_samples", 20)),
    }


def _parse_now(value: str | None, timezone: str) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(timezone))
    return parsed


def _freshness_for(frame: pd.DataFrame, config: dict, now_value: str | None):
    settings = _analysis_settings(config)
    dates = frame["date"].dropna() if "date" in frame else pd.Series(dtype="datetime64[ns]")
    actual = dates.max().date() if not dates.empty else None
    calendar = (
        TradingCalendar.from_csv(settings["calendar"])
        if settings["calendar"]
        else TradingCalendar()
    )
    return evaluate_freshness(
        actual,
        now=_parse_now(now_value, settings["timezone"]),
        timezone=settings["timezone"],
        ready_time=settings["ready_time"],
        calendar=calendar,
    )


def command_health(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    result = _client_from(config, args.base_url).health()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 2


def command_fetch(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    output, metadata = _client_from(config).save_module_snapshot(
        args.module,
        output=args.output,
        date_value=args.date,
    )
    print(json.dumps({"ok": True, "output": str(output), "metadata": str(metadata)}, indent=2))
    return 0


def command_validate(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    settings = _analysis_settings(config)
    frame = load_ohlcv(args.input)
    freshness = _freshness_for(frame, config, args.now) if args.check_freshness else None
    report = validate_ohlcv(
        frame,
        required_columns=settings["required_columns"],
        freshness=freshness,
        max_error_samples=settings["max_error_samples"],
    )
    target = _write_json(args.output, report.to_dict()) if args.output else None
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, default=str))
    if target:
        print(f"quality report: {target}", file=sys.stderr)
    return 0 if report.ok else 2


def command_indicators(args: argparse.Namespace) -> int:
    frame = load_ohlcv(args.input)
    result = compute_indicators(frame)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(output, index=False, date_format="%Y-%m-%d")
    print(json.dumps({"ok": True, "rows": len(result), "output": str(output)}, indent=2))
    return 0


def command_report(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    settings = _analysis_settings(config)
    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    frame = load_ohlcv(input_path)
    freshness = _freshness_for(frame, config, args.now)
    quality = validate_ohlcv(
        frame,
        required_columns=settings["required_columns"],
        freshness=freshness,
        max_error_samples=settings["max_error_samples"],
    )
    quality_path = _write_json(output_dir / "quality.json", quality.to_dict())

    output_files: dict[str, str] = {"quality": str(quality_path)}
    if not quality.errors or args.write_indicators_on_failure:
        indicators = compute_indicators(frame)
        indicators_path = output_dir / "indicators.csv"
        latest_path = output_dir / "latest_snapshot.csv"
        indicators.to_csv(indicators_path, index=False, date_format="%Y-%m-%d")
        latest_snapshot(indicators).to_csv(latest_path, index=False, date_format="%Y-%m-%d")
        output_files.update({"indicators": str(indicators_path), "latest_snapshot": str(latest_path)})

    manifest = {
        "tool": "stock-data-agent",
        "version": __version__,
        "run_time": datetime.now().astimezone().isoformat(),
        "input": str(input_path),
        "input_sha256": _sha256(input_path),
        "row_count": len(frame),
        "symbol_count": int(frame["code"].nunique()) if "code" in frame else 0,
        "freshness": freshness.to_dict(),
        "quality_ok": quality.ok,
        "outputs": output_files,
    }
    manifest_path = _write_json(output_dir / "manifest.json", manifest)
    print(json.dumps({"ok": quality.ok, "manifest": str(manifest_path)}, indent=2))
    return 0 if quality.ok else 2


def command_sync_a_share_universe(args: argparse.Namespace) -> int:
    records, metadata = fetch_a_share_universe(
        timeout_seconds=args.timeout,
        page_size=args.page_size,
        max_pages=args.max_pages,
    )
    manifest = write_a_share_database(
        records,
        metadata,
        database=args.database,
        min_rows=args.min_rows,
    )
    if args.manifest:
        _write_json(args.manifest, manifest)
    print(json.dumps({"ok": True, **manifest}, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stock-data-agent",
        description="Strategy-free stock data health, freshness, quality and indicator toolkit",
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    health = sub.add_parser("health", help="check the InStock web service")
    health.add_argument("--config")
    health.add_argument("--base-url")
    health.set_defaults(func=command_health)

    fetch = sub.add_parser("fetch-module", help="fetch an owner-approved read-only InStock module")
    fetch.add_argument("--config", required=True)
    fetch.add_argument("--module", required=True)
    fetch.add_argument("--date")
    fetch.add_argument("--output", required=True)
    fetch.set_defaults(func=command_fetch)

    validate = sub.add_parser("validate", help="validate an OHLCV CSV")
    validate.add_argument("--config")
    validate.add_argument("--input", required=True)
    validate.add_argument("--output")
    validate.add_argument("--check-freshness", action="store_true")
    validate.add_argument("--now", help="ISO-8601 time used for deterministic freshness checks")
    validate.set_defaults(func=command_validate)

    indicators = sub.add_parser("indicators", help="compute generic technical indicators")
    indicators.add_argument("--input", required=True)
    indicators.add_argument("--output", required=True)
    indicators.set_defaults(func=command_indicators)

    report = sub.add_parser("report", help="run quality, freshness and indicator analysis")
    report.add_argument("--config")
    report.add_argument("--input", required=True)
    report.add_argument("--output-dir", required=True)
    report.add_argument("--now", help="ISO-8601 time used for deterministic freshness checks")
    report.add_argument("--write-indicators-on-failure", action="store_true")
    report.set_defaults(func=command_report)

    universe = sub.add_parser(
        "sync-a-share-universe",
        help="fetch the real Shanghai/Shenzhen/Beijing A-share universe into SQLite",
    )
    universe.add_argument("--database", default="data/a_share.db")
    universe.add_argument("--manifest")
    universe.add_argument("--timeout", type=float, default=15.0)
    universe.add_argument("--page-size", type=int, default=100)
    universe.add_argument("--max-pages", type=int, default=100)
    universe.add_argument("--min-rows", type=int, default=4_000)
    universe.set_defaults(func=command_sync_a_share_universe)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (
        OSError,
        TypeError,
        ValueError,
        PermissionError,
        requests.RequestException,
        sqlite3.Error,
    ) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
