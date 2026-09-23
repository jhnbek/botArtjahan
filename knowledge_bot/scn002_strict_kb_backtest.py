"""SCN-002 false-breakout reversal backtest — STRICT to the knowledge base.

Why this file exists
--------------------
The user asked to backtest SCN-002 ("ложный пробой / отбой / разворот") strictly
according to their own knowledge base, and to produce honest winrate / PnL / R.

The rulebook cards in `_knowledge_base/rulebook/` are still draft skeletons
(Entry/Stop/Take sections are TODO), so the *operational thresholds* are taken
directly from the lecture evidence quoted inside those cards and from the formal
SCN-002 decision gates / data requirements:

  * Far retest  : price returns to a level untouched for >= 30 bars (урок 26).      -> FAR_RETEST_DAYS
  * False break : bar sweeps the level, then price closes back behind it /          -> 2-bar false breakout
                  next bar returns (урок 19/20: "ложный пробой 2 бара").
  * Sharp approach / paranormal bar leads to reversal (урок 27/31).                 -> PARANORMAL_ATR_MULT
  * Close > 50% ATR beyond the level => continuation, NOT reversal (урок 27).       -> reject filter
  * Stop sits behind the false-breakout extreme (урок 33, gate SCN002-GATE-004).    -> stop anchor
  * Minimum 3R room to target, else no trade (gate SCN002-GATE-004).                -> TP_R

Anti-lookahead
--------------
Every decision at bar i uses only closed bars <= i. ATR and pivots are computed
from past bars only. Pivot levels are confirmed only after PIVOT_K bars. Outcome
is simulated strictly on bars after the entry bar.

Data
----
Real public Binance spot monthly klines (https://data.binance.vision). No API key.
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

BASE_URL = "https://data.binance.vision/data/spot/monthly/klines"


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #
@dataclass
class Bar:
    open_time: int  # ms epoch
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def dt(self) -> datetime:
        return datetime.fromtimestamp(self.open_time / 1000, tz=timezone.utc)


@dataclass
class Trade:
    symbol: str
    side: str  # "short" | "long"
    level: float
    entry_dt: datetime
    entry: float
    stop: float
    target: float
    risk: float
    outcome: str = ""  # "win" | "loss" | "open"
    exit_dt: datetime | None = None
    r_multiple: float = 0.0


# --------------------------------------------------------------------------- #
# Download / parse real Binance klines
# --------------------------------------------------------------------------- #
def month_range(start: str, end: str) -> list[str]:
    sy, sm = (int(x) for x in start.split("-"))
    ey, em = (int(x) for x in end.split("-"))
    out: list[str] = []
    y, m = sy, sm
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


def normalize_epoch_ms(value: str) -> int:
    timestamp = int(float(value))
    if timestamp > 10_000_000_000_000_000:
        return timestamp // 1_000_000
    if timestamp > 10_000_000_000_000:
        return timestamp // 1_000
    return timestamp


def fetch_month(symbol: str, interval: str, month: str) -> list[Bar]:
    url = f"{BASE_URL}/{symbol}/{interval}/{symbol}-{interval}-{month}.zip"
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            blob = resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return []
        raise
    bars: list[Bar] = []
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        name = zf.namelist()[0]
        with zf.open(name) as fh:
            text = io.TextIOWrapper(fh, encoding="utf-8")
            for row in csv.reader(text):
                if not row or not row[0].strip():
                    continue
                try:
                    bars.append(
                        Bar(
                            open_time=normalize_epoch_ms(row[0]),
                            open=float(row[1]),
                            high=float(row[2]),
                            low=float(row[3]),
                            close=float(row[4]),
                            volume=float(row[5]),
                        )
                    )
                except (ValueError, IndexError):
                    continue
    return bars


def load_history(symbol: str, interval: str, start: str, end: str) -> list[Bar]:
    bars: list[Bar] = []
    for month in month_range(start, end):
        chunk = fetch_month(symbol, interval, month)
        bars.extend(chunk)
        print(f"  {symbol} {interval} {month}: {len(chunk)} bars", file=sys.stderr)
    bars.sort(key=lambda b: b.open_time)
    # de-dup by open_time (monthly files can overlap nothing, but be safe)
    seen: set[int] = set()
    unique: list[Bar] = []
    for b in bars:
        if b.open_time in seen:
            continue
        seen.add(b.open_time)
        unique.append(b)
    return unique


# --------------------------------------------------------------------------- #
# Indicators (no lookahead)
# --------------------------------------------------------------------------- #
def atr_at(bars: list[Bar], i: int, period: int) -> float | None:
    """ATR using closed bars strictly before index i (Wilder-style simple mean)."""
    if i < period + 1:
        return None
    trs: list[float] = []
    for j in range(i - period, i):
        prev_close = bars[j - 1].close
        tr = max(
            bars[j].high - bars[j].low,
            abs(bars[j].high - prev_close),
            abs(bars[j].low - prev_close),
        )
        trs.append(tr)
    return sum(trs) / len(trs)


def confirmed_pivots(bars: list[Bar], upto: int, k: int) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """Return (resistance_pivots, support_pivots) confirmed strictly before bar `upto`.

    A pivot high at index p is confirmed only when upto > p + k (so we never peek
    into the future relative to the decision bar).
    """
    highs: list[tuple[int, float]] = []
    lows: list[tuple[int, float]] = []
    last = upto - k - 1
    for p in range(k, last + 1):
        seg = bars[p - k : p + k + 1]
        if bars[p].high == max(b.high for b in seg):
            highs.append((p, bars[p].high))
        if bars[p].low == min(b.low for b in seg):
            lows.append((p, bars[p].low))
    return highs, lows


# --------------------------------------------------------------------------- #
# SCN-002 detector + outcome simulation
# --------------------------------------------------------------------------- #
@dataclass
class Params:
    pivot_k: int = 3
    atr_period: int = 14
    far_retest_days: int = 30
    paranormal_atr_mult: float = 1.5
    require_paranormal: bool = True
    close_atr_reject: float = 0.5  # close beyond level by >50% ATR => continuation, reject
    level_tol_atr: float = 0.25
    tp_r: float = 3.0
    stop_buffer_atr: float = 0.10
    fee_rate: float = 0.001  # 0.1% per side (spot taker)


def backtest_symbol(symbol: str, bars: list[Bar], p: Params) -> list[Trade]:
    trades: list[Trade] = []
    used_levels: set[tuple[str, int]] = set()  # (side, rounded level key) one trade per level zone

    start_i = p.atr_period + p.pivot_k + 2
    i = start_i
    n = len(bars)
    while i < n:
        atr = atr_at(bars, i, p.atr_period)
        if atr is None or atr <= 0:
            i += 1
            continue

        res, sup = confirmed_pivots(bars, i, p.pivot_k)
        bar = bars[i]
        bar_range = bar.high - bar.low
        paranormal = bar_range >= p.paranormal_atr_mult * atr

        trade = _try_short(symbol, bars, i, res, atr, paranormal, p, used_levels)
        if trade is None:
            trade = _try_long(symbol, bars, i, sup, atr, paranormal, p, used_levels)

        if trade is not None:
            _simulate(bars, i, trade, p)
            trades.append(trade)
        i += 1
    return trades


def _try_short(symbol, bars, i, res, atr, paranormal, p: Params, used) -> Trade | None:
    bar = bars[i]
    if p.require_paranormal and not paranormal:
        return None
    tol = p.level_tol_atr * atr
    for (pidx, level) in res:
        if i - pidx < p.far_retest_days:  # FAR retest only (>=30 bars old)
            continue
        key = ("short", round(level / max(tol, 1e-9)))
        if key in used:
            continue
        # false breakout: sweep above level, close back below it
        if bar.high <= level + tol:
            continue  # no sweep
        if bar.close >= level:
            continue  # closed above => not rejected
        # close must NOT be far below the level (that would be continuation down, not a reversal setup)
        if (level - bar.close) > p.close_atr_reject * atr:
            continue
        entry = bar.close
        stop = bar.high + p.stop_buffer_atr * atr
        risk = stop - entry
        if risk <= 0:
            continue
        target = entry - p.tp_r * risk
        if target <= 0:
            continue
        used.add(key)
        return Trade(symbol, "short", level, bar.dt, entry, stop, target, risk)
    return None


def _try_long(symbol, bars, i, sup, atr, paranormal, p: Params, used) -> Trade | None:
    bar = bars[i]
    if p.require_paranormal and not paranormal:
        return None
    tol = p.level_tol_atr * atr
    for (pidx, level) in sup:
        if i - pidx < p.far_retest_days:
            continue
        key = ("long", round(level / max(tol, 1e-9)))
        if key in used:
            continue
        if bar.low >= level - tol:
            continue  # no sweep below
        if bar.close <= level:
            continue  # closed below => not rejected
        if (bar.close - level) > p.close_atr_reject * atr:
            continue
        entry = bar.close
        stop = bar.low - p.stop_buffer_atr * atr
        risk = entry - stop
        if risk <= 0:
            continue
        target = entry + p.tp_r * risk
        used.add(key)
        return Trade(symbol, "long", level, bar.dt, entry, stop, target, risk)
    return None


def _simulate(bars: list[Bar], entry_i: int, t: Trade, p: Params) -> None:
    fee = p.fee_rate
    for j in range(entry_i + 1, len(bars)):
        b = bars[j]
        if t.side == "short":
            hit_stop = b.high >= t.stop
            hit_tgt = b.low <= t.target
        else:
            hit_stop = b.low <= t.stop
            hit_tgt = b.high >= t.target
        if hit_stop and hit_tgt:
            # conservative: assume stop first
            t.outcome, t.exit_dt, t.r_multiple = "loss", b.dt, -1.0 - 2 * fee * t.entry / t.risk
            return
        if hit_stop:
            t.outcome, t.exit_dt, t.r_multiple = "loss", b.dt, -1.0 - 2 * fee * t.entry / t.risk
            return
        if hit_tgt:
            t.outcome, t.exit_dt, t.r_multiple = "win", b.dt, p.tp_r - 2 * fee * t.entry / t.risk
            return
    t.outcome = "open"


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def _print_group_summary(title: str, groups: dict[str, list[Trade]]) -> None:
    if not groups:
        return
    print(title)
    for key, ts in sorted(groups.items()):
        wins = sum(1 for t in ts if t.outcome == "win")
        total_r = sum(t.r_multiple for t in ts)
        print(f"  {key:<10} n={len(ts):>4}  WR={wins/len(ts)*100:5.1f}%  totR={total_r:+7.2f}  expR={total_r/len(ts):+.3f}")


def save_trade_log(path: Path, trades: list[Trade]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "symbol",
            "side",
            "entry_dt",
            "exit_dt",
            "outcome",
            "level",
            "entry",
            "stop",
            "target",
            "risk",
            "r_multiple",
        ])
        for t in trades:
            writer.writerow([
                t.symbol,
                t.side,
                t.entry_dt.isoformat(),
                t.exit_dt.isoformat() if t.exit_dt else "",
                t.outcome,
                f"{t.level:.12g}",
                f"{t.entry:.12g}",
                f"{t.stop:.12g}",
                f"{t.target:.12g}",
                f"{t.risk:.12g}",
                f"{t.r_multiple:.6f}",
            ])


def report(all_trades: list[Trade], p: Params) -> None:
    closed = [t for t in all_trades if t.outcome in ("win", "loss")]
    wins = [t for t in closed if t.outcome == "win"]
    losses = [t for t in closed if t.outcome == "loss"]
    n = len(closed)
    print("\n" + "=" * 64)
    print("SCN-002 STRICT-KB BACKTEST RESULT")
    print("=" * 64)
    print(f"Rules: far_retest>={p.far_retest_days}d, paranormal>={p.paranormal_atr_mult}xATR "
          f"(required={p.require_paranormal}), TP={p.tp_r}R, fee={p.fee_rate*100:.2f}%/side")
    print(f"Total signals (incl. still-open): {len(all_trades)}")
    print(f"Closed trades:                    {n}")
    if n == 0:
        print("No closed trades — nothing to score.")
        return
    wr = len(wins) / n
    total_r = sum(t.r_multiple for t in closed)
    exp_r = total_r / n
    breakeven_wr = 1.0 / (1.0 + p.tp_r)
    print(f"Wins / Losses:                    {len(wins)} / {len(losses)}")
    print(f"Winrate:                          {wr*100:.1f}%")
    print(f"Breakeven winrate (TP={p.tp_r}R):       {breakeven_wr*100:.1f}%")
    print(f"Total R (net of fees):            {total_r:+.2f}R")
    print(f"Expectancy / trade:               {exp_r:+.3f}R")
    print("-" * 64)
    by_symbol: dict[str, list[Trade]] = {}
    by_year: dict[str, list[Trade]] = {}
    by_month: dict[str, list[Trade]] = {}
    for t in closed:
        by_symbol.setdefault(t.symbol, []).append(t)
        by_year.setdefault(t.entry_dt.strftime("%Y"), []).append(t)
        by_month.setdefault(t.entry_dt.strftime("%Y-%m"), []).append(t)
    _print_group_summary("By symbol", by_symbol)
    print("-" * 64)
    _print_group_summary("By year", by_year)
    print("-" * 64)
    _print_group_summary("By month", by_month)
    print("=" * 64)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="SCN-002 strict-knowledge-base backtest on real Binance data.")
    ap.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT"])
    ap.add_argument("--interval", default="1d")
    ap.add_argument("--start", default="2024-01", help="first month YYYY-MM")
    ap.add_argument("--end", default="2024-12", help="last month YYYY-MM")
    ap.add_argument("--far-retest-days", type=int, default=30)
    ap.add_argument("--paranormal-atr-mult", type=float, default=1.5)
    ap.add_argument("--no-require-paranormal", action="store_true")
    ap.add_argument("--tp-r", type=float, default=3.0)
    ap.add_argument("--fee-rate", type=float, default=0.001)
    ap.add_argument("--trade-log", type=Path, help="optional CSV path for every simulated trade")
    return ap.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    p = Params(
        far_retest_days=args.far_retest_days,
        paranormal_atr_mult=args.paranormal_atr_mult,
        require_paranormal=not args.no_require_paranormal,
        tp_r=args.tp_r,
        fee_rate=args.fee_rate,
    )
    all_trades: list[Trade] = []
    for sym in args.symbols:
        print(f"Loading {sym} {args.interval} {args.start}..{args.end} ...", file=sys.stderr)
        bars = load_history(sym, args.interval, args.start, args.end)
        print(f"  total {len(bars)} bars", file=sys.stderr)
        if len(bars) < 60:
            print(f"  not enough data for {sym}, skipping", file=sys.stderr)
            continue
        trades = backtest_symbol(sym, bars, p)
        all_trades.extend(trades)
    if args.trade_log:
        save_trade_log(args.trade_log, all_trades)
        print(f"Saved trade log: {args.trade_log}", file=sys.stderr)
    report(all_trades, p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
