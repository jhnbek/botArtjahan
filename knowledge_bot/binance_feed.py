"""Read-only Binance market data feed for the trader-brain layers.

This module is the data foundation for the desktop client: it picks a symbol,
pulls candlestick (kline) history from Binance public endpoints, and writes an
OHLC file that the existing read-only modules already understand
(``ohlc_situation_adapter`` / ``chart_review_packet`` light mode / casebook).

SAFETY INVARIANTS
-----------------
* Only **public** REST endpoints are called (klines, exchangeInfo, ticker).
* No API key, no signing, no account access, **no order placement** of any kind.
* Symbol and interval are validated against allowlists before any URL is built,
  so untrusted input can never be injected into the request URL.
* Output is plain market data; this module never computes winrate, PnL, signals,
  or trades. It is purely a feed.

It uses only the Python standard library (``urllib``) so it adds no dependency
and works inside a PyInstaller bundle.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
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

# Public REST hosts. spot data is read from the non-geo-restricted market-data
# mirror first (data-api.binance.vision), then the main host as a fallback.
# usd-m futures = fapi.binance.com. Hosts are tried in order until one answers.
MARKET_BASES = {
    "spot": ("https://data-api.binance.vision", "https://api.binance.com"),
    "futures": ("https://fapi.binance.com",),
}
MARKET_KLINE_PATH = {
    "spot": "/api/v3/klines",
    "futures": "/fapi/v1/klines",
}
MARKET_EXCHANGE_INFO_PATH = {
    "spot": "/api/v3/exchangeInfo",
    "futures": "/fapi/v1/exchangeInfo",
}

# Binance kline intervals. Validated before use so they can never be injected.
VALID_INTERVALS = {
    "1m", "3m", "5m", "15m", "30m",
    "1h", "2h", "4h", "6h", "8h", "12h",
    "1d", "3d", "1w", "1M",
}
# Binance caps a single klines response at 1000 rows.
MAX_LIMIT = 1000
SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,20}$")

USER_AGENT = "knowledge-bot-readonly-feed/1.0"
DEFAULT_TIMEOUT = 15
DEFAULT_RETRIES = 3


class FeedError(RuntimeError):
    """Raised when the public feed cannot be read."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_symbol(symbol: str) -> str:
    cleaned = str(symbol).strip().upper().replace("/", "").replace("-", "")
    if not SYMBOL_RE.fullmatch(cleaned):
        raise FeedError(f"invalid symbol: {symbol!r} (expected letters/digits like BTCUSDT)")
    return cleaned


def validate_market(market: str) -> str:
    if market not in MARKET_BASES:
        raise FeedError(f"market must be one of {sorted(MARKET_BASES)}, got {market!r}")
    return market


def validate_interval(interval: str) -> str:
    if interval not in VALID_INTERVALS:
        raise FeedError(f"interval must be one of {sorted(VALID_INTERVALS)}, got {interval!r}")
    return interval


def _http_get_json(url: str, timeout: int = DEFAULT_TIMEOUT, retries: int = DEFAULT_RETRIES) -> Any:
    """GET a public JSON endpoint with simple exponential backoff. Read-only."""
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT}, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (https only, validated host)
                charset = response.headers.get_content_charset() or "utf-8"
                return json.loads(response.read().decode(charset))
        except urllib.error.HTTPError as exc:
            # 4xx (bad symbol etc.) will not improve on retry; surface immediately.
            if 400 <= exc.code < 500:
                raise FeedError(f"HTTP {exc.code} from {url}: {exc.read().decode('utf-8', 'replace')[:200]}") from exc
            last_error = exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last_error = exc
        if attempt < retries:
            time.sleep(min(2 ** attempt, 8))
    raise FeedError(f"failed to read {url} after {retries} attempts: {last_error}")


def _build_url(host: str, path: str, params: dict[str, Any] | None = None) -> str:
    query = f"?{urllib.parse.urlencode(params)}" if params else ""
    return f"{host}{path}{query}"


def _get_json_with_fallback(market: str, path: str, params: dict[str, Any] | None = None,
                            timeout: int = DEFAULT_TIMEOUT) -> Any:
    """Try each candidate host for the market until one returns JSON."""
    validate_market(market)
    last_error: Exception | None = None
    for host in MARKET_BASES[market]:
        url = _build_url(host, path, params)
        try:
            return _http_get_json(url, timeout=timeout)
        except FeedError as exc:
            last_error = exc
            continue
    raise FeedError(f"all hosts failed for {market} {path}: {last_error}")


def list_symbols(market: str = "spot", quote: str | None = "USDT",
                 trading_only: bool = True, timeout: int = DEFAULT_TIMEOUT) -> list[str]:
    """Return available trading symbols for the market (optionally filtered by quote asset)."""
    validate_market(market)
    payload = _get_json_with_fallback(market, MARKET_EXCHANGE_INFO_PATH[market], timeout=timeout)
    symbols_info = payload.get("symbols", []) if isinstance(payload, dict) else []
    out: list[str] = []
    quote_upper = quote.upper() if quote else None
    for info in symbols_info:
        if trading_only and info.get("status") != "TRADING":
            continue
        if quote_upper and info.get("quoteAsset") != quote_upper:
            continue
        sym = info.get("symbol")
        if sym:
            out.append(sym)
    return sorted(out)


def klines_to_bars(raw: list[list[Any]]) -> list[dict[str, Any]]:
    """Convert Binance kline arrays to OHLC bar dicts compatible with the adapter.

    Binance kline array layout:
    [0]=open_time_ms [1]=open [2]=high [3]=low [4]=close [5]=volume
    [6]=close_time_ms [7]=quote_volume [8]=trades ...
    """
    bars: list[dict[str, Any]] = []
    for row in raw:
        open_ms = int(row[0])
        bars.append({
            "open_time": datetime.fromtimestamp(open_ms / 1000, tz=timezone.utc)
            .isoformat().replace("+00:00", "Z"),
            "open_time_ms": open_ms,
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
            "close_time_ms": int(row[6]),
        })
    return bars


def fetch_klines(symbol: str, interval: str = "1d", limit: int = 200,
                 market: str = "spot", start_ms: int | None = None,
                 end_ms: int | None = None, timeout: int = DEFAULT_TIMEOUT) -> list[dict[str, Any]]:
    """Fetch candlestick history from a public endpoint. Read-only.

    When ``limit`` exceeds the per-request cap the window is paginated forward.
    """
    symbol = normalize_symbol(symbol)
    validate_market(market)
    validate_interval(interval)
    if limit < 1:
        raise FeedError("limit must be >= 1")

    collected: list[dict[str, Any]] = []
    remaining = limit
    cursor = start_ms
    seen_open_ms: set[int] = set()
    path = MARKET_KLINE_PATH[market]

    while remaining > 0:
        page = min(remaining, MAX_LIMIT)
        params: dict[str, Any] = {"symbol": symbol, "interval": interval, "limit": page}
        if cursor is not None:
            params["startTime"] = int(cursor)
        if end_ms is not None:
            params["endTime"] = int(end_ms)
        raw = _get_json_with_fallback(market, path, params, timeout=timeout)
        if not isinstance(raw, list) or not raw:
            break
        bars = klines_to_bars(raw)
        new_bars = [bar for bar in bars if bar["open_time_ms"] not in seen_open_ms]
        if not new_bars:
            break
        for bar in new_bars:
            seen_open_ms.add(bar["open_time_ms"])
        collected.extend(new_bars)
        remaining -= len(new_bars)
        if len(raw) < page:
            break  # exchange returned fewer rows than asked -> no more data
        cursor = new_bars[-1]["close_time_ms"] + 1

    collected.sort(key=lambda bar: bar["open_time_ms"])
    if limit and len(collected) > limit and start_ms is None:
        # When paging a plain limit (no start), keep the most recent `limit`.
        collected = collected[-limit:]
    return collected


def build_ohlc_payload(symbol: str, interval: str, market: str,
                       bars: list[dict[str, Any]]) -> dict[str, Any]:
    """Wrap bars in an adapter-compatible, self-describing payload."""
    symbol = normalize_symbol(symbol)
    return {
        "source": "binance_public_feed",
        "read_only": True,
        "fetched_at": now_iso(),
        "market": market,
        "symbol": symbol,
        "interval": interval,
        "bar_count": len(bars),
        "first_open_time": bars[0]["open_time"] if bars else None,
        "last_open_time": bars[-1]["open_time"] if bars else None,
        "disclaimer": "Public market data only. No orders, no PnL, no signals.",
        "bars": bars,
    }


def cache_path(symbol: str, interval: str, market: str) -> Path:
    symbol = normalize_symbol(symbol)
    validate_market(market)
    validate_interval(interval)
    return CACHE_DIR / f"{market}_{symbol}_{interval}.json"


def save_ohlc_file(payload: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def get_ohlc(symbol: str, interval: str = "1d", limit: int = 200, market: str = "spot",
             start_ms: int | None = None, end_ms: int | None = None,
             write_cache: bool = True, timeout: int = DEFAULT_TIMEOUT) -> dict[str, Any]:
    """High-level helper: fetch + wrap + optionally cache. Returns the payload."""
    bars = fetch_klines(symbol, interval=interval, limit=limit, market=market,
                        start_ms=start_ms, end_ms=end_ms, timeout=timeout)
    if not bars:
        raise FeedError(f"no klines returned for {symbol} {interval} on {market}")
    payload = build_ohlc_payload(symbol, interval, market, bars)
    if write_cache:
        save_ohlc_file(payload, cache_path(symbol, interval, market))
    return payload


def _cmd_list_symbols(args: argparse.Namespace) -> int:
    symbols = list_symbols(market=args.market, quote=args.quote,
                           trading_only=not args.include_non_trading, timeout=args.timeout)
    if args.limit:
        symbols = symbols[:args.limit]
    print(json.dumps({
        "market": args.market,
        "quote": args.quote,
        "count": len(symbols),
        "symbols": symbols,
    }, ensure_ascii=False, indent=2))
    return 0


def _cmd_fetch(args: argparse.Namespace) -> int:
    payload = get_ohlc(
        args.symbol, interval=args.interval, limit=args.limit, market=args.market,
        write_cache=not args.no_cache, timeout=args.timeout,
    )
    if args.output:
        out_path = save_ohlc_file(payload, Path(args.output))
    else:
        out_path = cache_path(args.symbol, args.interval, args.market)
    summary = {
        "symbol": payload["symbol"],
        "market": payload["market"],
        "interval": payload["interval"],
        "bar_count": payload["bar_count"],
        "first_open_time": payload["first_open_time"],
        "last_open_time": payload["last_open_time"],
        "ohlc_file": out_path.as_posix(),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only Binance public market data feed (no orders, no account).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    ls = sub.add_parser("list-symbols", help="list available trading symbols")
    ls.add_argument("--market", choices=sorted(MARKET_BASES), default="spot")
    ls.add_argument("--quote", default="USDT", help="filter by quote asset (e.g. USDT); empty for all")
    ls.add_argument("--include-non-trading", action="store_true")
    ls.add_argument("--limit", type=int, default=0, help="cap number of symbols printed")
    ls.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    ls.set_defaults(func=_cmd_list_symbols)

    fetch = sub.add_parser("fetch", help="fetch klines and write an OHLC file")
    fetch.add_argument("--symbol", required=True, help="e.g. BTCUSDT")
    fetch.add_argument("--interval", default="1d", choices=sorted(VALID_INTERVALS))
    fetch.add_argument("--limit", type=int, default=200)
    fetch.add_argument("--market", choices=sorted(MARKET_BASES), default="spot")
    fetch.add_argument("--output", help="explicit OHLC output path (default: cache dir)")
    fetch.add_argument("--no-cache", action="store_true", help="do not write the cache copy")
    fetch.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    fetch.set_defaults(func=_cmd_fetch)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except FeedError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
