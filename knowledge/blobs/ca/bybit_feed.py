"""Read-only Bybit public market data feed for the trader-brain."""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime_root import data_root

ROOT = data_root(__file__)
CACHE_DIR = ROOT / "_knowledge_base" / "live_market_cache"
DEFAULT_BASE_URLS = ("https://api.bybit.com", "https://api.bytick.com")
CONFIGURED_BASE_URL = os.environ.get("BYBIT_API_BASE", "").strip().rstrip("/")
BASE_URLS = (CONFIGURED_BASE_URL,) if CONFIGURED_BASE_URL else DEFAULT_BASE_URLS
KLINE_PATH = "/v5/market/kline"
INSTRUMENTS_PATH = "/v5/market/instruments-info"
MAX_LIMIT = 1000
DEFAULT_TIMEOUT = 15
DEFAULT_RETRIES = 3
SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,20}$")

# Bybit uses numeric minute intervals and letters for larger periods.
INTERVALS = {
    "1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30",
    "1h": "60", "2h": "120", "4h": "240", "6h": "360", "12h": "720",
    "1d": "D", "3d": "3D", "1w": "W", "1M": "M",
}


class FeedError(RuntimeError):
    """Raised when the public Bybit feed cannot be read."""


def _normalize_symbol(symbol: str) -> str:
    cleaned = str(symbol).strip().upper().replace("/", "").replace("-", "")
    if not SYMBOL_RE.fullmatch(cleaned):
        raise FeedError(f"invalid symbol: {symbol!r}")
    return cleaned


def normalize_symbol(symbol: str) -> str:
    return _normalize_symbol(symbol)


def _http_get_json(path: str, params: dict[str, Any], timeout: int = DEFAULT_TIMEOUT) -> Any:
    query = urllib.parse.urlencode(params)
    last_error: Exception | None = None
    for base_url in BASE_URLS:
        url = f"{base_url}{path}?{query}"
        for attempt in range(1, DEFAULT_RETRIES + 1):
            request = urllib.request.Request(url, headers={"User-Agent": "knowledge-bot-readonly-feed/1.0"})
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    payload = json.loads(response.read().decode(response.headers.get_content_charset() or "utf-8"))
                if payload.get("retCode") != 0:
                    raise FeedError(f"Bybit retCode {payload.get('retCode')}: {payload.get('retMsg')}")
                return payload
            except urllib.error.HTTPError as exc:
                if 400 <= exc.code < 500 and exc.code != 403:
                    raise FeedError(f"Bybit HTTP {exc.code}") from exc
                last_error = exc
            except (urllib.error.URLError, TimeoutError, ConnectionError, FeedError) as exc:
                last_error = exc
            if attempt < DEFAULT_RETRIES:
                time.sleep(min(2 ** attempt, 8))
    if isinstance(last_error, urllib.error.HTTPError) and last_error.code == 403:
        raise FeedError(
            "Bybit отклонил публичный API-запрос (HTTP 403). "
            "Проверьте региональный доступ или задайте BYBIT_API_BASE для разрешённого домена."
        ) from last_error
    raise FeedError(f"failed to read Bybit after {DEFAULT_RETRIES} attempts: {last_error}")


def list_symbols(category: str = "linear", quote: str = "USDT", timeout: int = DEFAULT_TIMEOUT) -> list[str]:
    payload = _http_get_json(INSTRUMENTS_PATH, {"category": category, "limit": 1000}, timeout)
    rows = ((payload.get("result") or {}).get("list") or [])
    return sorted(
        str(row["symbol"]).upper()
        for row in rows
        if row.get("status") == "Trading" and row.get("quoteCoin") == quote.upper()
    )


def fetch_klines(symbol: str, interval: str = "1d", limit: int = 200,
                 category: str = "linear", timeout: int = DEFAULT_TIMEOUT) -> list[dict[str, Any]]:
    symbol = _normalize_symbol(symbol)
    if interval not in INTERVALS:
        raise FeedError(f"unsupported interval: {interval}")
    if not 1 <= limit <= MAX_LIMIT:
        raise FeedError(f"limit must be between 1 and {MAX_LIMIT}")
    payload = _http_get_json(KLINE_PATH, {
        "category": category,
        "symbol": symbol,
        "interval": INTERVALS[interval],
        "limit": limit,
    }, timeout)
    rows = ((payload.get("result") or {}).get("list") or [])
    bars: list[dict[str, Any]] = []
    for row in reversed(rows):
        open_ms = int(row[0])
        bars.append({
            "open_time": datetime.fromtimestamp(open_ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
            "open_time_ms": open_ms,
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
            "close_time_ms": open_ms,
        })
    return bars


def cache_path(symbol: str, interval: str, category: str = "linear") -> Path:
    return CACHE_DIR / f"bybit_{category}_{_normalize_symbol(symbol)}_{interval}.json"


def get_ohlc(symbol: str, interval: str = "1d", limit: int = 200,
             category: str = "linear", write_cache: bool = True,
             timeout: int = DEFAULT_TIMEOUT) -> dict[str, Any]:
    bars = fetch_klines(symbol, interval, limit, category, timeout)
    if not bars:
        raise FeedError(f"no klines returned for {symbol} {interval} on Bybit")
    payload = {
        "source": "bybit_public_feed",
        "read_only": True,
        "fetched_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "market": category,
        "symbol": _normalize_symbol(symbol),
        "interval": interval,
        "bar_count": len(bars),
        "first_open_time": bars[0]["open_time"],
        "last_open_time": bars[-1]["open_time"],
        "disclaimer": "Public market data only. No orders, no PnL, no signals.",
        "bars": bars,
    }
    if write_cache:
        path = cache_path(symbol, interval, category)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload
