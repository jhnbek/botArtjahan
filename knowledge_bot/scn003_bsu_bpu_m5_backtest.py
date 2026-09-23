"""SCN-003 BSU/BPU limit-entry backtest (faithful core model).

This implements the CORE Gerchik entry the earlier scripts skipped:

  D1 daily scenario  -> direction gate  (trend_context_spec.md)
  D1/H... level (BSU) -> level to defend (level_selection_strength_spec.md)
  M5 BPU1 + BPU2      -> two consecutive same-zone confirmations
                         (bsu_bpu_entry_spec.md)
  limit + luft        -> entry near level, short stop, room >= 3R
                         (risk_stop_take_spec.md)

Key fidelity points vs the proxy/v2 scripts:
  * Direction is NOT chosen from local price structure. It is set by the D1
    global trend (the "daily scenario"). Counter-trend and range are skipped,
    matching the spec hard reject "breakout/entry against global trend".
  * Execution is on M5 (5m), the spec-preferred timeframe, not H1.
  * Trigger requires BPU1 then BPU2 in the same zone, consecutive, with reset
    if a bar closes through the level against the model.
  * Risk uses the previous completed Daily ATR (risk_stop_take_spec.md), no
    look-ahead.

The D1 swing structure + nearest-level selection that the KB leaves as
`Manual Remains` is mechanized here with confirmed fractal pivots. That is an
approximation of a human-drawn level, surfaced honestly rather than hidden.

Source specs:
  _knowledge_base/detector_specs/bsu_bpu_entry_spec.md
  _knowledge_base/detector_specs/tbx_entry_models_spec.md
  _knowledge_base/detector_specs/trend_context_spec.md
  _knowledge_base/detector_specs/risk_stop_take_spec.md
"""

from __future__ import annotations

import argparse
import bisect
import csv
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone

from scn002_strict_kb_backtest import Bar, atr_at, load_history

SOURCE_SPECS = [
    "_knowledge_base/detector_specs/bsu_bpu_entry_spec.md",
    "_knowledge_base/detector_specs/tbx_entry_models_spec.md",
    "_knowledge_base/detector_specs/trend_context_spec.md",
    "_knowledge_base/detector_specs/risk_stop_take_spec.md",
]


# --------------------------------------------------------------------------- #
# Parameters
# --------------------------------------------------------------------------- #
@dataclass
class Params:
    # D1 swing structure
    pivot_wing: int = 3                # fractal wing size (bars each side)
    trend_pivots: int = 4              # pivots inspected for HH/HL or LH/LL
    daily_atr_period: int = 14

    # Level approach (M5 must be near the active D1 level to form BPU)
    approach_atr_frac: float = 0.35    # |price-level| <= frac * dailyATR to arm
    touch_atr_frac: float = 0.06       # wick within this band counts as a touch
    break_atr_frac: float = 0.04       # close beyond level by more => break/reset

    # BPU sequence
    bpu_max_gap: int = 0               # neutral bars allowed between BPU1/BPU2

    # Level / approach quality gate (level_selection + breakout_preconditions specs)
    min_touches: int = 2               # prior contacts of level before BPU (>=2 = structural)
    touch_lookback_bars: int = 576     # window to count prior touches (~2 days M5)
    near_retest_max_bars: int = 60     # last prior contact must be within this many bars
    far_retest_reject_bars: int = 360  # >= this gap with no near contact => reject (far retest)
    parabolic_run_bars: int = 12       # bars before BPU1 to measure approach speed
    parabolic_run_atr: float = 1.0     # reject if price ran >= this*ATR straight into level
    vol_fade_ratio: float = 0.70       # last bar range <= ratio * avg prev-5 range
    min_score: float = 5.0             # strength score gate (spec: strong setup >=5)

    # Entry / risk
    luft_frac_of_stop: float = 0.20    # limit offset from level = 20% of stop
    max_stop_atr_frac: float = 0.13    # reject stop > 13% of dailyATR
    warn_stop_atr_frac: float = 0.10
    min_room_r: float = 3.0            # room to next opposite level >= 3R
    rr_target: float = 3.0             # take profit at 3R
    fill_window_bars: int = 12         # M5 bars to wait for limit fill (1h)
    trade_timeout_bars: int = 288      # M5 bars to resolve a trade (24h)
    fee_rate: float = 0.0004           # per side


# --------------------------------------------------------------------------- #
# D1 swing structure (mechanized "daily scenario")
# --------------------------------------------------------------------------- #
@dataclass
class Pivot:
    index: int
    time_ms: int
    confirm_ms: int   # time at which this pivot becomes known (no look-ahead)
    price: float
    kind: str         # "H" | "L"


def confirmed_pivots(bars: list[Bar], wing: int) -> list[Pivot]:
    out: list[Pivot] = []
    for i in range(wing, len(bars) - wing):
        hi = bars[i].high
        lo = bars[i].low
        is_high = all(bars[i].high >= bars[j].high for j in range(i - wing, i + wing + 1)) and \
            any(bars[i].high > bars[j].high for j in range(i - wing, i + wing + 1))
        is_low = all(bars[i].low <= bars[j].low for j in range(i - wing, i + wing + 1)) and \
            any(bars[i].low < bars[j].low for j in range(i - wing, i + wing + 1))
        # пивот подтверждён только ЗАКРЫТИЕМ бара i+wing, а не его открытием:
        # до закрытия дня форма фрактала ещё может сломаться
        confirm_ms = bars[i + wing].open_time + 86_400_000
        if is_high:
            out.append(Pivot(i, bars[i].open_time, confirm_ms, hi, "H"))
        if is_low:
            out.append(Pivot(i, bars[i].open_time, confirm_ms, lo, "L"))
    out.sort(key=lambda p: p.confirm_ms)
    return out


def global_trend(known: list[Pivot], n: int) -> str:
    """Classify trend from the last n confirmed pivots."""
    if len(known) < n:
        return "range"
    highs = [p.price for p in known if p.kind == "H"][-n:]
    lows = [p.price for p in known if p.kind == "L"][-n:]
    if len(highs) < 2 or len(lows) < 2:
        return "range"
    hh = highs[-1] > highs[-2]
    hl = lows[-1] > lows[-2]
    lh = highs[-1] < highs[-2]
    ll = lows[-1] < lows[-2]
    if hh and hl:
        return "long"
    if lh and ll:
        return "short"
    return "range"


# --------------------------------------------------------------------------- #
# Daily context indexed for fast M5 lookup
# --------------------------------------------------------------------------- #
@dataclass
class DailyContext:
    pivots: list[Pivot]
    confirm_times: list[int]
    daily_atr_times: list[int]
    daily_atr_vals: list[float]

    def known_pivots(self, t_ms: int) -> list[Pivot]:
        # pivots confirmed strictly before t_ms
        idx = bisect.bisect_left(self.confirm_times, t_ms)
        return self.pivots[:idx]

    def daily_atr(self, t_ms: int) -> float | None:
        idx = bisect.bisect_right(self.daily_atr_times, t_ms) - 1
        if idx < 0:
            return None
        return self.daily_atr_vals[idx]


def build_daily_context(d1: list[Bar], p: Params) -> DailyContext:
    pivots = confirmed_pivots(d1, p.pivot_wing)
    confirm_times = [pv.confirm_ms for pv in pivots]
    atr_times: list[int] = []
    atr_vals: list[float] = []
    for i in range(len(d1)):
        a = atr_at(d1, i, p.daily_atr_period)
        if a is not None and a > 0:
            # known only after bar i closes -> available from next bar open
            atr_times.append(d1[i].open_time + 86_400_000)
            atr_vals.append(a)
    return DailyContext(pivots, confirm_times, atr_times, atr_vals)


# --------------------------------------------------------------------------- #
# Signal / trade
# --------------------------------------------------------------------------- #
@dataclass
class Trade:
    symbol: str
    direction: str
    level: float
    bpu1_ms: int
    bpu2_ms: int
    entry_ms: int
    entry: float
    stop: float
    target: float
    risk: float
    daily_atr: float
    stop_atr_frac: float
    room_r: float
    outcome: str = ""
    exit_ms: int | None = None
    r_net: float = 0.0
    rejects: list[str] = field(default_factory=list)


def nearest_support(known: list[Pivot], price: float) -> Pivot | None:
    best: Pivot | None = None
    for pv in known:
        if pv.kind == "L" and pv.price <= price:
            if best is None or pv.price > best.price:
                best = pv
    return best


def nearest_resistance(known: list[Pivot], price: float) -> Pivot | None:
    best: Pivot | None = None
    for pv in known:
        if pv.kind == "H" and pv.price >= price:
            if best is None or pv.price < best.price:
                best = pv
    return best


def quality_gate(m5: list[Bar], bpu1: int, level: float, trend: str, atr: float,
                 touch_tol: float, p: Params) -> tuple[list[str], float, dict]:
    """Level/approach quality per level_selection + breakout_preconditions specs.

    Returns (hard_rejects, strength_score, features).
    """
    rejects: list[str] = []
    feats: dict = {}

    # --- prior touches of this level (structural multi-touch) ---
    lo_win = max(0, bpu1 - p.touch_lookback_bars)
    touches = 0
    last_contact = -1
    in_contact = False
    for k in range(lo_win, bpu1):
        b = m5[k]
        contact = (b.low <= level + touch_tol) and (b.high >= level - touch_tol)
        if contact and not in_contact:
            touches += 1
            last_contact = k
            in_contact = True
        elif not contact:
            in_contact = False
    feats["prior_touches"] = touches
    bars_since_contact = (bpu1 - last_contact) if last_contact >= 0 else 10**9
    feats["bars_since_contact"] = bars_since_contact

    if touches < p.min_touches:
        rejects.append("level_lacks_multi_touch_basis")
    # far retest: long gap and no recent contact
    if bars_since_contact >= p.far_retest_reject_bars:
        rejects.append("far_retest_first_contact")

    # --- parabolic / single-run approach reject ---
    run_lo = max(0, bpu1 - p.parabolic_run_bars)
    seg = m5[run_lo:bpu1 + 1]
    if seg:
        run = max(b.high for b in seg) - min(b.low for b in seg)
        feats["approach_run_atr"] = run / atr if atr else 0.0
        if run >= p.parabolic_run_atr * atr:
            rejects.append("approach_ge_1atr_parabolic")

    # --- volatility fade before BPU (compression) ---
    vf_lo = max(0, bpu1 - 6)
    prev = m5[vf_lo:bpu1]
    vol_fade = False
    if len(prev) >= 5:
        avg_prev = sum(b.high - b.low for b in prev[-5:]) / 5.0
        last_range = m5[bpu1].high - m5[bpu1].low
        vol_fade = avg_prev > 0 and last_range <= p.vol_fade_ratio * avg_prev
    feats["vol_fade"] = vol_fade

    # --- compression: closes stacking toward level ---
    comp = False
    if bpu1 >= 4:
        last4 = m5[bpu1 - 3:bpu1 + 1]
        if trend == "long":
            comp = sum(1 for a, b in zip(last4, last4[1:]) if b.close >= a.close) >= 2
        else:
            comp = sum(1 for a, b in zip(last4, last4[1:]) if b.close <= a.close) >= 2
    feats["compression"] = comp

    # --- near retest strength ---
    near = bars_since_contact <= p.near_retest_max_bars
    strong_near = bars_since_contact <= 5
    feats["near_retest"] = near

    # --- strength score (spec additive, ranking gate) ---
    score = 0.0
    score += 1.5  # structural basis (multi-touch level present)
    if touches >= 3:
        score += 1.0
    elif touches >= 2:
        score += 0.5
    if strong_near:
        score += 2.0
    elif near:
        score += 1.5
    if vol_fade:
        score += 1.0
    if comp:
        score += 1.5
    feats["score"] = score
    return rejects, score, feats


def detect_symbol(symbol: str, m5: list[Bar], ctx: DailyContext, p: Params,
                  rejects_counter: dict[str, int]) -> list[Trade]:
    trades: list[Trade] = []
    n = len(m5)
    i = 0
    # cooldown index after a trade resolves to avoid stacking on same level
    while i < n - 2:
        bar = m5[i]
        atr = ctx.daily_atr(bar.open_time)
        if atr is None or atr <= 0:
            i += 1
            continue
        known = ctx.known_pivots(bar.open_time)
        trend = global_trend(known, p.trend_pivots)
        if trend == "range":
            i += 1
            continue

        if trend == "long":
            lvl_pivot = nearest_support(known, bar.close)
            opp = nearest_resistance(known, bar.close)
        else:
            lvl_pivot = nearest_resistance(known, bar.close)
            opp = nearest_support(known, bar.close)

        if lvl_pivot is None:
            i += 1
            continue
        level = lvl_pivot.price
        if abs(bar.close - level) > p.approach_atr_frac * atr:
            i += 1
            continue

        touch_tol = p.touch_atr_frac * atr
        break_tol = p.break_atr_frac * atr

        # --- find BPU1 then BPU2 in same zone, consecutive (+ allowed gap) ---
        def is_bpu(b: Bar) -> bool:
            if trend == "long":
                return (b.low <= level + touch_tol) and (b.close > level) and \
                    (b.low >= level - break_tol)
            return (b.high >= level - touch_tol) and (b.close < level) and \
                (b.high <= level + break_tol)

        def is_break(b: Bar) -> bool:
            if trend == "long":
                return b.close < level - break_tol
            return b.close > level + break_tol

        # BPU1
        if not is_bpu(bar):
            i += 1
            continue
        bpu1 = i
        # find BPU2 within allowed gap, no break in between
        bpu2 = -1
        j = bpu1 + 1
        gap = 0
        while j < n and gap <= p.bpu_max_gap:
            if is_break(m5[j]):
                break
            if is_bpu(m5[j]):
                bpu2 = j
                break
            gap += 1
            j += 1
        if bpu2 < 0:
            i += 1
            continue

        # --- level/approach quality gate (full KB selection layer) ---
        q_rejects, score, _qfeats = quality_gate(m5, bpu1, level, trend, atr,
                                                 touch_tol, p)
        if q_rejects:
            for r in q_rejects:
                rejects_counter[r] = rejects_counter.get(r, 0) + 1
            i = bpu1 + 1
            continue
        if score < p.min_score:
            rejects_counter["strength_score_lt_min"] = \
                rejects_counter.get("strength_score_lt_min", 0) + 1
            i = bpu1 + 1
            continue

        # --- build entry / stop / target ---
        if trend == "long":
            struct_low = min(m5[bpu1].low, m5[bpu2].low)
            stop = struct_low - 0.5 * touch_tol
            luft = p.luft_frac_of_stop * max(level - stop, 1e-9)
            entry = level + luft
            risk = entry - stop
            target = entry + p.rr_target * risk
            room = (opp.price - entry) if opp else float("inf")
        else:
            struct_high = max(m5[bpu1].high, m5[bpu2].high)
            stop = struct_high + 0.5 * touch_tol
            luft = p.luft_frac_of_stop * max(stop - level, 1e-9)
            entry = level - luft
            risk = stop - entry
            target = entry - p.rr_target * risk
            room = (entry - opp.price) if opp else float("inf")

        rj: list[str] = []
        if risk <= 0:
            rj.append("nonpositive_risk")
        stop_frac = risk / atr if atr else 99.0
        if stop_frac > p.max_stop_atr_frac:
            rj.append("technical_stop_gt_13pct_atr")
        room_r = (room / risk) if risk > 0 else 0.0
        if room_r < p.min_room_r:
            rj.append("room_to_next_level_lt_3r")

        if rj:
            for r in rj:
                rejects_counter[r] = rejects_counter.get(r, 0) + 1
            i = bpu2 + 1
            continue

        # --- limit fill within window, then simulate ---
        outcome, exit_ms, r_net = _simulate(m5, bpu2, entry, stop, target, risk,
                                            trend, p)
        if outcome == "no_fill":
            rejects_counter["limit_not_filled"] = rejects_counter.get("limit_not_filled", 0) + 1
            i = bpu2 + 1
            continue

        trades.append(Trade(
            symbol=symbol, direction=trend, level=level,
            bpu1_ms=m5[bpu1].open_time, bpu2_ms=m5[bpu2].open_time,
            entry_ms=m5[bpu2].open_time, entry=entry, stop=stop, target=target,
            risk=risk, daily_atr=atr, stop_atr_frac=stop_frac, room_r=room_r,
            outcome=outcome, exit_ms=exit_ms, r_net=r_net,
        ))
        # jump past the resolved trade
        if exit_ms is not None:
            k = bisect.bisect_right([b.open_time for b in m5], exit_ms)
            i = max(bpu2 + 1, k)
        else:
            i = bpu2 + 1
    return trades


def _simulate(m5: list[Bar], start_idx: int, entry: float, stop: float,
              target: float, risk: float, trend: str, p: Params):
    n = len(m5)
    # wait for limit fill
    fill_idx = -1
    for k in range(start_idx + 1, min(n, start_idx + 1 + p.fill_window_bars)):
        b = m5[k]
        if trend == "long":
            if b.low <= entry:
                fill_idx = k
                break
        else:
            if b.high >= entry:
                fill_idx = k
                break
    if fill_idx < 0:
        return "no_fill", None, 0.0

    fee_r = (p.fee_rate * entry + p.fee_rate * entry) / risk  # ~2 sides over risk
    for k in range(fill_idx, min(n, fill_idx + p.trade_timeout_bars)):
        b = m5[k]
        if trend == "long":
            hit_stop = b.low <= stop
            hit_tp = b.high >= target
        else:
            hit_stop = b.high >= stop
            hit_tp = b.low <= target
        # conservative: if both in same bar, assume stop first
        if hit_stop:
            return "loss", b.open_time, -1.0 - fee_r
        if hit_tp:
            return "win", b.open_time, p.rr_target - fee_r
    # timeout -> mark flat-ish at last close
    b = m5[min(n - 1, fill_idx + p.trade_timeout_bars - 1)]
    if trend == "long":
        r = (b.close - entry) / risk
    else:
        r = (entry - b.close) / risk
    return "timeout", b.open_time, r - fee_r


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def summarize(trades: list[Trade], rejects: dict[str, int]) -> None:
    closed = [t for t in trades if t.outcome in ("win", "loss", "timeout")]
    wins = [t for t in closed if t.outcome == "win"]
    losses = [t for t in closed if t.outcome == "loss"]
    tot = sum(t.r_net for t in closed)
    print("=" * 72)
    print("SCN-003 BSU/BPU M5 BACKTEST RESULT")
    print("=" * 72)
    print(f"Closed trades:        {len(closed)}")
    print(f"Wins / Losses / TO:   {len(wins)} / {len(losses)} / "
          f"{len(closed) - len(wins) - len(losses)}")
    wr = (len(wins) / len(closed) * 100) if closed else 0.0
    print(f"Winrate:              {wr:.1f}%")
    print(f"Total R net:          {tot:+.2f}R")
    print(f"Expectancy/trade:     {(tot/len(closed)) if closed else 0:+.3f}R")
    print("-" * 72)
    by: dict[str, list[Trade]] = {}
    for t in closed:
        by.setdefault(t.symbol, []).append(t)
    for sym in sorted(by):
        ts = by[sym]
        w = sum(1 for t in ts if t.outcome == "win")
        r = sum(t.r_net for t in ts)
        print(f"  {sym:<10} n={len(ts):>4}  WR={w/len(ts)*100:>5.1f}%  "
              f"totR={r:+7.2f}  expR={r/len(ts):+.3f}")
    print("-" * 72)
    bym: dict[str, list[Trade]] = {}
    for t in closed:
        mo = datetime.fromtimestamp(t.entry_ms / 1000, tz=timezone.utc).strftime("%Y-%m")
        bym.setdefault(mo, []).append(t)
    for mo in sorted(bym):
        ts = bym[mo]
        w = sum(1 for t in ts if t.outcome == "win")
        r = sum(t.r_net for t in ts)
        print(f"  {mo}   n={len(ts):>4}  WR={w/len(ts)*100:>5.1f}%  "
              f"totR={r:+7.2f}  expR={r/len(ts):+.3f}")
    print("-" * 72)
    print("Top rejects (candidates filtered before trade):")
    for k, v in sorted(rejects.items(), key=lambda kv: -kv[1]):
        print(f"  {k:<34} {v}")
    print("=" * 72)


def write_trades(path: str, trades: list[Trade]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["symbol", "direction", "level", "bpu1", "bpu2", "entry_time",
                    "entry", "stop", "target", "risk", "daily_atr",
                    "stop_atr_frac", "room_r", "outcome", "exit_time", "r_net"])
        for t in trades:
            def fmt(ms: int | None) -> str:
                if ms is None:
                    return ""
                return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()
            w.writerow([t.symbol, t.direction, f"{t.level:.6f}", fmt(t.bpu1_ms),
                        fmt(t.bpu2_ms), fmt(t.entry_ms), f"{t.entry:.6f}",
                        f"{t.stop:.6f}", f"{t.target:.6f}", f"{t.risk:.6f}",
                        f"{t.daily_atr:.6f}", f"{t.stop_atr_frac:.4f}",
                        f"{t.room_r:.2f}", t.outcome, fmt(t.exit_ms),
                        f"{t.r_net:.4f}"])


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--exec-interval", default="5m")
    ap.add_argument("--fee-rate", type=float, default=0.0004)
    ap.add_argument("--trend-pivots", type=int, default=4)
    ap.add_argument("--bpu-max-gap", type=int, default=0)
    ap.add_argument("--trade-log", default="")
    args = ap.parse_args()

    p = Params(fee_rate=args.fee_rate, trend_pivots=args.trend_pivots,
               bpu_max_gap=args.bpu_max_gap)

    all_trades: list[Trade] = []
    rejects: dict[str, int] = {}
    for sym in args.symbols:
        print(f"Loading {sym} D1 {args.start}..{args.end} ...", file=sys.stderr)
        d1 = load_history(sym, "1d", args.start, args.end)
        if len(d1) < p.pivot_wing * 2 + p.daily_atr_period + 2:
            print(f"  {sym}: not enough D1 bars, skip", file=sys.stderr)
            continue
        ctx = build_daily_context(d1, p)
        print(f"Loading {sym} {args.exec_interval} {args.start}..{args.end} ...",
              file=sys.stderr)
        m5 = load_history(sym, args.exec_interval, args.start, args.end)
        print(f"  {sym}: {len(m5)} exec bars, {len(ctx.pivots)} D1 pivots",
              file=sys.stderr)
        trades = detect_symbol(sym, m5, ctx, p, rejects)
        print(f"  {sym}: {len(trades)} trades", file=sys.stderr)
        all_trades.extend(trades)

    summarize(all_trades, rejects)
    if args.trade_log:
        write_trades(args.trade_log, all_trades)
        print(f"Saved trade log: {args.trade_log}", file=sys.stderr)


if __name__ == "__main__":
    main()
