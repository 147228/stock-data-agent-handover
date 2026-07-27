from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

SINA_COUNT_URL = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "Market_Center.getHQNodeStockCount"
)
SINA_DATA_URL = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "Market_Center.getHQNodeData"
)
SINA_SOURCE = "sina_finance_hs_a"
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")

STOCK_INFO_COLUMNS = (
    "code",
    "name",
    "exchange",
    "exchange_code",
    "last_price",
    "change_pct",
    "change_amount",
    "volume",
    "amount",
    "amplitude_pct",
    "turnover_rate",
    "pe_dynamic",
    "buy",
    "sell",
    "high",
    "low",
    "open",
    "prev_close",
    "total_market_cap_cny_10k",
    "float_market_cap_cny_10k",
    "pb",
    "tick_time",
    "retrieved_at",
    "source",
)


def _session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=0.6,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 Chrome/126.0 Safari/537.36"
            ),
            "Referer": "https://finance.sina.com.cn/",
            "Accept": "application/json,text/plain,*/*",
        }
    )
    return session


def _as_float(value: Any) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_row(raw: dict[str, Any], retrieved_at: str) -> dict[str, Any] | None:
    code = str(raw.get("code") or "").strip()
    name = str(raw.get("name") or "").strip()
    symbol = str(raw.get("symbol") or "").strip().lower()
    if not code or not name or len(symbol) < 3:
        return None
    exchange = {"sh": "SSE", "sz": "SZSE", "bj": "BSE"}.get(symbol[:2])
    if exchange is None:
        return None
    previous_close = _as_float(raw.get("settlement"))
    high = _as_float(raw.get("high"))
    low = _as_float(raw.get("low"))
    amplitude_pct = None
    if previous_close and high is not None and low is not None:
        amplitude_pct = round((high - low) / previous_close * 100, 6)
    return {
        "code": code,
        "name": name,
        "exchange": exchange,
        "exchange_code": symbol.upper(),
        "last_price": _as_float(raw.get("trade")),
        "change_pct": _as_float(raw.get("changepercent")),
        "change_amount": _as_float(raw.get("pricechange")),
        "volume": _as_float(raw.get("volume")),
        "amount": _as_float(raw.get("amount")),
        "amplitude_pct": amplitude_pct,
        "turnover_rate": _as_float(raw.get("turnoverratio")),
        "pe_dynamic": _as_float(raw.get("per")),
        "buy": _as_float(raw.get("buy")),
        "sell": _as_float(raw.get("sell")),
        "high": high,
        "low": low,
        "open": _as_float(raw.get("open")),
        "prev_close": previous_close,
        "total_market_cap_cny_10k": _as_float(raw.get("mktcap")),
        "float_market_cap_cny_10k": _as_float(raw.get("nmc")),
        "pb": _as_float(raw.get("pb")),
        "tick_time": str(raw.get("ticktime") or "").strip() or None,
        "retrieved_at": retrieved_at,
        "source": SINA_SOURCE,
    }


def fetch_a_share_universe(
    *,
    session: requests.Session | None = None,
    timeout_seconds: float = 15.0,
    page_size: int = 100,
    max_pages: int = 100,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fetch a real-time Shanghai/Shenzhen/Beijing A-share universe snapshot."""

    if page_size < 1 or page_size > 100:
        raise ValueError("page_size must be between 1 and 100")
    if max_pages < 1:
        raise ValueError("max_pages must be positive")

    client = session or _session()
    retrieved_at = datetime.now(tz=SHANGHAI_TZ).isoformat()
    records: dict[str, dict[str, Any]] = {}
    count_response = client.get(
        SINA_COUNT_URL,
        params={"node": "hs_a"},
        timeout=timeout_seconds,
    )
    count_response.raise_for_status()
    reported_total = int(count_response.json())
    pages_fetched = 0

    for page in range(1, max_pages + 1):
        response = client.get(
            SINA_DATA_URL,
            params={
                "page": page,
                "num": page_size,
                "sort": "symbol",
                "asc": 1,
                "node": "hs_a",
                "symbol": "",
                "_s_r_a": "page",
            },
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        rows = response.json() or []
        if not isinstance(rows, list):
            raise TypeError("Sina Finance response is not a list")
        pages_fetched += 1
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            record = _normalize_row(raw, retrieved_at)
            if record is not None:
                records[record["code"]] = record
        if not rows or (reported_total and len(records) >= reported_total):
            break

    result = sorted(records.values(), key=lambda item: item["code"])
    if reported_total and len(result) < reported_total:
        raise ValueError(
            f"incomplete Sina Finance snapshot: expected {reported_total}, got {len(result)} "
            f"after {pages_fetched} page(s)"
        )
    metadata = {
        "source": SINA_SOURCE,
        "source_url": SINA_DATA_URL,
        "scope": "Shanghai, Shenzhen and Beijing A-shares",
        "retrieved_at": retrieved_at,
        "reported_total": reported_total,
        "row_count": len(result),
        "pages_fetched": pages_fetched,
    }
    return result, metadata


def _records_sha256(records: list[dict[str, Any]]) -> str:
    canonical = json.dumps(
        records,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def write_a_share_database(
    records: list[dict[str, Any]],
    metadata: dict[str, Any],
    *,
    database: str | Path,
    min_rows: int = 4_000,
) -> dict[str, Any]:
    """Atomically replace stock_info with a validated full-market snapshot."""

    if len(records) < min_rows:
        raise ValueError(f"refusing partial snapshot: expected at least {min_rows}, got {len(records)}")
    database_path = Path(database)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    records_sha256 = _records_sha256(records)
    placeholders = ",".join("?" for _ in STOCK_INFO_COLUMNS)
    columns_sql = ",".join(STOCK_INFO_COLUMNS)
    values = [tuple(record.get(column) for column in STOCK_INFO_COLUMNS) for record in records]

    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS stock_info (
                code TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                exchange TEXT NOT NULL,
                exchange_code TEXT NOT NULL,
                last_price REAL,
                change_pct REAL,
                change_amount REAL,
                volume REAL,
                amount REAL,
                amplitude_pct REAL,
                turnover_rate REAL,
                pe_dynamic REAL,
                buy REAL,
                sell REAL,
                high REAL,
                low REAL,
                open REAL,
                prev_close REAL,
                total_market_cap_cny_10k REAL,
                float_market_cap_cny_10k REAL,
                pb REAL,
                tick_time TEXT,
                retrieved_at TEXT NOT NULL,
                source TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sync_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                retrieved_at TEXT NOT NULL,
                source TEXT NOT NULL,
                reported_total INTEGER,
                row_count INTEGER NOT NULL,
                pages_fetched INTEGER NOT NULL,
                records_sha256 TEXT NOT NULL
            )
            """
        )
        connection.execute("DROP TABLE IF EXISTS stock_info_staging")
        connection.execute(
            "CREATE TEMP TABLE stock_info_staging AS SELECT * FROM stock_info WHERE 0"
        )
        connection.executemany(
            f"INSERT INTO stock_info_staging ({columns_sql}) VALUES ({placeholders})",
            values,
        )
        staged_count = connection.execute(
            "SELECT COUNT(*) FROM stock_info_staging"
        ).fetchone()[0]
        if staged_count != len(records):
            raise ValueError(f"staging row count mismatch: expected {len(records)}, got {staged_count}")
        connection.execute("DELETE FROM stock_info")
        connection.execute(
            f"INSERT INTO stock_info ({columns_sql}) SELECT {columns_sql} FROM stock_info_staging"
        )
        connection.execute(
            """
            INSERT INTO sync_runs (
                retrieved_at, source, reported_total, row_count, pages_fetched, records_sha256
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                metadata["retrieved_at"],
                metadata["source"],
                metadata.get("reported_total"),
                len(records),
                metadata.get("pages_fetched", 0),
                records_sha256,
            ),
        )

    return {
        **metadata,
        "database": str(database_path),
        "table": "stock_info",
        "row_count": len(records),
        "records_sha256": records_sha256,
    }
