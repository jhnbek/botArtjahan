"""Read-only historical Bybit candles with explicit UTC decision boundaries.

The requested range is half-open in candle OPEN timestamps: [start_ms, end_ms).
Only candles closed by both ``as_of_ms`` and the exchange response snapshot are
returned. ``close_time_ms`` is the exclusive next-open boundary, not open time.
No missing candle is interpolated, and symbol/category are never guessed.

API contract: https://bybit-exchange.github.io/docs/v5/market/kline
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

API_DOCUMENTATION = "https://bybit-exchange.github.io/docs/v5/market/kline"
KLINE_PATH = "/v5/market/kline"
INTERVALS = {"1m": ("1", 60_000), "3m": ("3", 180_000),
             "5m": ("5", 300_000), "15m": ("15", 900_000),
             "30m": ("30", 1_800_000), "1h": ("60", 3_600_000),
             "2h": ("120", 7_200_000), "4h": ("240", 14_400_000),
             "6h": ("360", 21_600_000), "12h": ("720", 43_200_000),
             "1d": ("D", 86_400_000)}
DEFAULT_CACHE = (Path(__file__).resolve().parents[1] / "_knowledge_base" /
                 "manual_reviews" / "scenarios_dzhahan_20260925" /
                 "training" / "market_cache")


class HistoryError(RuntimeError):
    """Invalid, unavailable, inconsistent, or incomplete API protocol response."""


def _utc(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc).isoformat().replace("+00:00", "Z")


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False).encode("utf-8")).hexdigest()


def _integer(value: Any, label: str, *, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < (1 if positive else 0):
        raise ValueError(f"{label} must be a {'positive' if positive else 'non-negative'} integer")
    return value


def _decode(row: Any, duration: int) -> dict[str, Any]:
    if not isinstance(row, list) or len(row) < 7:
        raise HistoryError("malformed candle row")
    try:
        opened = int(row[0])
        values = [float(value) for value in row[1:7]]
    except (ValueError, TypeError, OverflowError) as exc:
        raise HistoryError("non-numeric candle row") from exc
    if opened < 0 or opened % duration:
        raise HistoryError("candle timestamp is not on the UTC interval grid")
    o, h, low, c, volume, turnover = values
    if not all(math.isfinite(value) for value in values):
        raise HistoryError("non-finite candle values")
    if min(o, h, low, c) <= 0 or volume < 0 or turnover < 0 or low > min(o, c) or h < max(o, c):
        raise HistoryError("invalid OHLC or volume geometry")
    closed = opened + duration
    return dict(open_time_ms=opened, close_time_ms=closed,
                open_time=_utc(opened), close_time=_utc(closed),
                open=o, high=h, low=low, close=c, volume=volume, turnover=turnover)


def _missing_ranges(bars: list[dict[str, Any]], start: int, end: int,
                    as_of: int, duration: int) -> tuple[int, list[dict[str, int]]]:
    first = ((start + duration - 1) // duration) * duration
    last = min(((end - 1) // duration) * duration, ((as_of - duration) // duration) * duration)
    expected = max(0, (last - first) // duration + 1)
    cursor = first
    gaps = []
    for bar in bars:
        opened = bar["open_time_ms"]
        if opened > cursor:
            gaps.append(dict(start_ms=cursor, end_ms_exclusive=opened,
                             missing_bars=(opened - cursor) // duration))
        cursor = opened + duration
    if cursor <= last:
        gaps.append(dict(start_ms=cursor, end_ms_exclusive=last + duration,
                         missing_bars=(last + duration - cursor) // duration))
    return expected, gaps


class BybitHistoryClient:
    """Sequential public GET client; caches exact ranges and proves page progress.

    Cached historical ranges are reused only after their requested end was
    observed and every returned candle was closed. New/current ranges refresh.
    ``transport``/``sleep``/``clock`` are injectable for deterministic checks.
    A transport receives query parameters and returns the complete Bybit JSON.
    """

    def __init__(self, cache_dir: Path | str | None = DEFAULT_CACHE, *,
                 base_url: str = "https://api.bybit.com", timeout: float = 20,
                 retries: int = 3, min_request_interval: float = .25,
                 transport: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        parsed = urllib.parse.urlparse(base_url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.path not in ("", "/"):
            raise ValueError("base_url must be an HTTPS origin")
        if timeout <= 0 or retries < 1 or min_request_interval < 0:
            raise ValueError("invalid timeout, retries, or request interval")
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self.min_request_interval = min_request_interval
        self.transport = transport or self._http
        self.sleep, self.clock = sleep, clock
        self._last_request: float | None = None

    def _http(self, params: dict[str, Any]) -> dict[str, Any]:
        url = self.base_url + KLINE_PATH + "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, headers={"User-Agent": "scenario-history-readonly/1.0"})
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def _request(self, params: dict[str, Any]) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(self.retries):
            if self._last_request is not None:
                pause = self.min_request_interval - (self.clock() - self._last_request)
                if pause > 0:
                    self.sleep(pause)
            self._last_request = self.clock()
            retry_after = None
            try:
                payload = self.transport(params)
                if not isinstance(payload, dict):
                    raise HistoryError("Bybit payload must be an object")
                code = payload.get("retCode")
                if code == 0:
                    return payload
                if code not in {10000, 10006, 10016}:
                    raise HistoryError(f"Bybit retCode {code}: {payload.get('retMsg')}")
                last_error = HistoryError(f"Bybit transient retCode {code}: {payload.get('retMsg')}")
            except urllib.error.HTTPError as exc:
                if exc.code not in {408, 429, 500, 502, 503, 504}:
                    raise HistoryError(f"Bybit HTTP {exc.code}; no alternate-market fallback") from exc
                last_error = exc
                if exc.headers:
                    try:
                        retry_after = max(0., min(30., float(exc.headers.get("Retry-After", "0"))))
                    except ValueError:
                        pass
            except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as exc:
                last_error = exc
            if attempt + 1 < self.retries:
                self.sleep(max(min(2. ** attempt, 8.), retry_after or 0.))
        raise HistoryError(f"Bybit request failed after {self.retries} attempts: {last_error}") from last_error

    def fetch_range(self, symbol: str, interval: str, start_ms: int, end_ms: int, *,
                    category: str = "linear", as_of_ms: int | None = None,
                    force_refresh: bool = False, page_limit: int = 1000) -> dict[str, Any]:
        """Fetch closed OHLC in [start,end); ``as_of`` bounds information time.

        ``closed`` is always true in returned bars. For a historical ``as_of``,
        the enclosing incomplete candle is omitted instead of leaking its later
        final OHLC. Gaps are explicit and are never counted as obtained history.
        """
        symbol = str(symbol).strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{2,32}", symbol):
            raise ValueError("symbol must be an exact Bybit symbol, such as BTCUSDT")
        if interval not in INTERVALS or category not in {"linear", "spot", "inverse"}:
            raise ValueError("unsupported interval or category")
        _integer(start_ms, "start_ms")
        _integer(end_ms, "end_ms", positive=True)
        _integer(page_limit, "page_limit", positive=True)
        if end_ms <= start_ms or page_limit > 1000:
            raise ValueError("end must exceed start; page_limit must be <= 1000")
        if as_of_ms is not None:
            _integer(as_of_ms, "as_of_ms")
        api_interval, duration = INTERVALS[interval]
        identity = dict(symbol=symbol, category=category, interval=interval,
                        start_ms=start_ms, end_ms_exclusive=end_ms, base_url=self.base_url)
        key = f"bybit_{category}_{symbol}_{interval}_{start_ms}_{end_ms}.json"
        path = self.cache_dir / key if self.cache_dir is not None else None
        cached = None
        if path is not None and path.exists() and not force_refresh:
            try:
                candidate = json.loads(path.read_text(encoding="utf-8"))
                checksum = candidate.pop("cache_sha256")
                if candidate.get("schema_version") != 1 or candidate.get("identity") != identity or _hash(candidate) != checksum:
                    raise HistoryError(f"invalid cached history identity or hash: {path}")
                snapshot = candidate["exchange_snapshot_ms"]
                if snapshot >= end_ms and all(b["close_time_ms"] <= snapshot for b in candidate["bars"]):
                    cached = candidate
            except (ValueError, KeyError, TypeError) as exc:
                raise HistoryError(f"cannot validate cached history: {path}") from exc
        cache_hit = cached is not None
        if cached is None:
            cursor = end_ms - 1
            collected: dict[int, dict[str, Any]] = {}
            pages = []
            snapshots = []
            while cursor >= start_ms:
                params = dict(category=category, symbol=symbol, interval=api_interval,
                              start=start_ms, end=cursor, limit=page_limit)
                payload = self._request(params)
                result = payload.get("result")
                if not isinstance(result, dict) or result.get("symbol") != symbol or result.get("category") != category:
                    raise HistoryError("Bybit returned a different symbol/category")
                rows = result.get("list")
                if not isinstance(rows, list) or len(rows) > page_limit:
                    raise HistoryError("invalid Bybit candle list or page size")
                snapshot = payload.get("time")
                if isinstance(snapshot, bool) or not isinstance(snapshot, int) or snapshot <= 0:
                    raise HistoryError("missing exchange snapshot timestamp")
                snapshots.append(snapshot)
                decoded = [_decode(row, duration) for row in rows]
                pages.append(dict(request=params, exchange_time_ms=snapshot, rows=len(rows),
                                  response_sha256=_hash(payload)))
                if not decoded:
                    break
                oldest = min(bar["open_time_ms"] for bar in decoded)
                if oldest > cursor:
                    raise HistoryError("Bybit pagination made no progress")
                for bar in decoded:
                    opened = bar["open_time_ms"]
                    if opened in collected and collected[opened] != bar:
                        raise HistoryError("conflicting duplicate candles across pages")
                    if start_ms <= opened < end_ms:
                        collected[opened] = bar
                cursor = oldest - 1
                # Do not assume a short page proves completeness. A final empty
                # request or reaching start is required to finish the range.
            cached = dict(schema_version=1, identity=identity,
                          fetched_at=_utc(int(time.time() * 1000)),
                          exchange_snapshot_ms=min(snapshots), pages=pages,
                          bars=[collected[key] for key in sorted(collected)])
            if path is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
                stored = dict(cached, cache_sha256=_hash(cached))
                temporary = path.with_suffix(".json.tmp")
                temporary.write_text(json.dumps(stored, ensure_ascii=False, indent=2), encoding="utf-8")
                temporary.replace(path)
        snapshot = cached["exchange_snapshot_ms"]
        cutoff = min(snapshot, as_of_ms) if as_of_ms is not None else snapshot
        bars = [dict(bar, closed=True) for bar in cached["bars"] if bar["close_time_ms"] <= cutoff]
        expected, gaps = _missing_ranges(bars, start_ms, end_ms, cutoff, duration)
        return dict(schema_version=1, source="bybit_public_kline", read_only=True,
                    api_documentation=API_DOCUMENTATION, **identity,
                    as_of_ms=cutoff, exchange_snapshot_ms=snapshot,
                    fetched_at=cached["fetched_at"], cache_hit=cache_hit,
                    cache_path=str(path) if path is not None else None,
                    source_cache_sha256=_hash(cached), pages=cached["pages"],
                    close_time_convention="exclusive UTC boundary; open_time + interval duration",
                    excluded_unclosed_count=len(cached["bars"]) - len(bars),
                    bar_count=len(bars), expected_closed_bar_count=expected,
                    coverage_complete=not gaps, missing_ranges=gaps, bars=bars)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbol")
    parser.add_argument("interval", choices=INTERVALS)
    parser.add_argument("start_ms", type=int)
    parser.add_argument("end_ms", type=int)
    parser.add_argument("--category", default="linear", choices=("linear", "spot", "inverse"))
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--as-of-ms", type=int)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = BybitHistoryClient(cache_dir=args.cache_dir).fetch_range(
        args.symbol, args.interval, args.start_ms, args.end_ms,
        category=args.category, as_of_ms=args.as_of_ms)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key not in {"bars", "pages"}}, indent=2))


if __name__ == "__main__":
    main()
