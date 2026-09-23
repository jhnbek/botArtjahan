"""SCN-002 spec-driven false-breakout backtest.

This v2 uses `_knowledge_base/detector_specs/` as the controlling source instead
of the earlier proxy thresholds. It intentionally surfaces manual-only parts of
the lecture/spec logic in `manual_review_needed` instead of pretending D1 candles
can fully replace visual H1/M5 execution review.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from scn002_strict_kb_backtest import Bar, atr_at, load_history

SOURCE_CARDS = [
    "_knowledge_base/detector_specs/level_selection_strength_spec.md",
    "_knowledge_base/detector_specs/near_far_retest_spec.md",
    "_knowledge_base/detector_specs/false_breakout_reversal_spec.md",
    "_knowledge_base/detector_specs/breakout_preconditions_spec.md",
    "_knowledge_base/detector_specs/risk_stop_take_spec.md",
]

SOURCE_CHUNKS = [
    "lec_028_d0ba3c7b_0038",
    "lec_029_16a9683b_0023",
    "lec_030_92533c4f_0065",
    "lec_021_267c9e5f_0003",
    "lec_010_0348bfe6_0033",
]


@dataclass
class SpecParams:
    pivot_k: int = 3
    atr_period: int = 14
    contact_tol_atr: float = 0.02
    level_merge_tol_atr: float = 0.08
    departure_atr: float = 0.20
    min_departure_bars: int = 2
    near_retest_bars: int = 10
    far_retest_bars: int = 30
    very_far_retest_bars: int = 60
    close_near_level_atr: float = 0.50
    no_consolidation_lookback: int = 10
    no_consolidation_min_near_closes: int = 3
    compression_lookback: int = 5
    range_compression_ratio: float = 0.70
    paranormal_avg_range_mult: float = 1.50
    paranormal_atr_mult: float = 0.80
    atr_consumed_strong: float = 1.0
    atr_consumed_warning: float = 0.5
    luft_atr: float = 0.02
    stop_luft_atr: float = 0.02
    max_stop_atr: float = 0.13
    warn_stop_atr: float = 0.10
    min_room_r: float = 3.0
    target_r: float = 3.0
    fee_rate: float = 0.001
    min_score: float = 5.0
    reject_manual_review: bool = False
    require_far_retest_for_auto: bool = True


@dataclass
class PivotLevel:
    side: str
    price: float
    pivot_index: int
    basis_tags: list[str] = field(default_factory=list)
    touch_count: int = 0


@dataclass
class SpecSignal:
    symbol: str
    side: str
    status: str
    entry_i: int
    sweep_i: int
    entry_dt: datetime
    level: float
    entry: float
    stop: float
    target: float
    risk: float
    score: float
    outcome: str = ""
    exit_dt: datetime | None = None
    r_multiple: float = 0.0
    hard_rejects: list[str] = field(default_factory=list)
    strength_factors: list[str] = field(default_factory=list)
    weakness_factors: list[str] = field(default_factory=list)
    manual_review_needed: list[str] = field(default_factory=list)
    level_validation: dict[str, Any] = field(default_factory=dict)
    retest_features: dict[str, Any] = field(default_factory=dict)
    false_breakout_features: dict[str, Any] = field(default_factory=dict)
    breakout_reject_features: dict[str, Any] = field(default_factory=dict)
    risk_validation: dict[str, Any] = field(default_factory=dict)


def avg_range(bars: list[Bar], start: int, end: int) -> float | None:
    values = [bars[i].high - bars[i].low for i in range(max(0, start), max(0, end))]
    return sum(values) / len(values) if values else None


def confirmed_pivots(bars: list[Bar], upto: int, k: int) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    highs: list[tuple[int, float]] = []
    lows: list[tuple[int, float]] = []
    for i in range(k, upto - k):
        window = bars[i - k : i + k + 1]
        if bars[i].high == max(bar.high for bar in window):
            highs.append((i, bars[i].high))
        if bars[i].low == min(bar.low for bar in window):
            lows.append((i, bars[i].low))
    return highs, lows


def touches_level(bar: Bar, level: float, tol: float) -> bool:
    return bar.low - tol <= level <= bar.high + tol or abs(bar.close - level) <= tol


def closes_both_sides(bars: list[Bar], start: int, end: int, level: float) -> bool:
    closes = [bar.close for bar in bars[max(0, start) : max(0, end)]]
    return sum(close > level for close in closes) >= 2 and sum(close < level for close in closes) >= 2


def departure_confirmed(bars: list[Bar], contact_i: int, upto: int, level: float, atr: float, p: SpecParams) -> bool:
    tol = p.contact_tol_atr * atr
    after = bars[contact_i + 1 : min(upto, contact_i + 1 + p.min_departure_bars)]
    if len(after) >= p.min_departure_bars and all(not touches_level(bar, level, tol) for bar in after):
        return True
    return any(abs(bars[i].close - level) >= p.departure_atr * atr for i in range(contact_i + 1, upto))


def prior_valid_contact(bars: list[Bar], i: int, level: float, atr: float, p: SpecParams) -> int | None:
    tol = p.contact_tol_atr * atr
    for contact_i in range(i - 1, max(-1, i - 250), -1):
        if touches_level(bars[contact_i], level, tol) and departure_confirmed(bars, contact_i, i, level, atr, p):
            return contact_i
    return None


def classify_retest(bars: list[Bar], i: int, level: float, atr: float, p: SpecParams) -> dict[str, Any]:
    contact_i = prior_valid_contact(bars, i, level, atr, p)
    if contact_i is None:
        return {
            "classification": "no_prior_contact",
            "bias": "manual",
            "strength": 0.0,
            "bars_since_contact": None,
            "invalid_chop": False,
            "manual_review_needed": ["prior_contact_meaningfulness_unknown"],
        }
    bars_since = i - contact_i
    invalid_chop = closes_both_sides(bars, contact_i + 1, i, level)
    manual = ["level_identity_or_chop_requires_manual_review"] if invalid_chop else []
    if bars_since <= 3:
        classification, bias, strength = "ideal_near_retest", "breakout", 2.5
    elif bars_since <= 5:
        classification, bias, strength = "strong_near_retest", "breakout", 2.0
    elif bars_since <= p.near_retest_bars:
        classification, bias, strength = "near_retest", "breakout", 1.5
    elif bars_since >= p.very_far_retest_bars:
        classification, bias, strength = "very_far_retest", "false_breakout", 2.5
    elif bars_since >= p.far_retest_bars:
        classification, bias, strength = "far_retest", "false_breakout", 2.0
    else:
        classification, bias, strength = "gray_zone", "neutral/manual", 0.0
        manual.append("gray_zone_retest_11_29_bars")
    return {
        "classification": classification,
        "bias": bias,
        "strength": strength,
        "bars_since_contact": bars_since,
        "last_contact_time": bars[contact_i].dt.isoformat(),
        "invalid_chop": invalid_chop,
        "manual_review_needed": manual,
    }


def merge_pivots(side: str, pivots: list[tuple[int, float]], tol: float) -> list[PivotLevel]:
    levels: list[PivotLevel] = []
    for pivot_i, price in pivots:
        match = next((level for level in levels if abs(level.price - price) <= tol), None)
        if match is None:
            levels.append(PivotLevel(side, price, pivot_i, ["inflection"], 1))
            continue
        old_count = match.touch_count
        match.touch_count += 1
        match.price = (match.price * old_count + price) / match.touch_count
        if match.touch_count >= 2 and "two_exact_touches" not in match.basis_tags:
            match.basis_tags.append("two_exact_touches")
        if match.touch_count >= 3 and "three_plus_touches" not in match.basis_tags:
            match.basis_tags.append("three_plus_touches")
    return levels


def add_pivot_level(levels: list[PivotLevel], side: str, pivot_i: int, price: float, tol: float) -> None:
    match = next((level for level in levels if abs(level.price - price) <= tol), None)
    if match is None:
        levels.append(PivotLevel(side, price, pivot_i, ["inflection"], 1))
        return
    old_count = match.touch_count
    match.touch_count += 1
    match.price = (match.price * old_count + price) / match.touch_count
    if match.touch_count >= 2 and "two_exact_touches" not in match.basis_tags:
        match.basis_tags.append("two_exact_touches")
    if match.touch_count >= 3 and "three_plus_touches" not in match.basis_tags:
        match.basis_tags.append("three_plus_touches")


def update_confirmed_levels(bars: list[Bar], i: int, atr: float, resistances: list[PivotLevel], supports: list[PivotLevel], p: SpecParams) -> None:
    pivot_i = i - p.pivot_k - 1
    if pivot_i < p.pivot_k:
        return
    window = bars[pivot_i - p.pivot_k : pivot_i + p.pivot_k + 1]
    tol = p.level_merge_tol_atr * atr
    if bars[pivot_i].high == max(bar.high for bar in window):
        add_pivot_level(resistances, "resistance", pivot_i, bars[pivot_i].high, tol)
    if bars[pivot_i].low == min(bar.low for bar in window):
        add_pivot_level(supports, "support", pivot_i, bars[pivot_i].low, tol)


def build_levels(bars: list[Bar], upto: int, atr: float, p: SpecParams) -> tuple[list[PivotLevel], list[PivotLevel]]:
    highs, lows = confirmed_pivots(bars, upto, p.pivot_k)
    tol = p.level_merge_tol_atr * atr
    return merge_pivots("resistance", highs, tol), merge_pivots("support", lows, tol)


def validate_level(level: PivotLevel, bars: list[Bar], i: int) -> dict[str, Any]:
    hard_rejects: list[str] = []
    manual = [
        "visual_touch_equality_not_confirmed",
        "level_contamination_not_manually_reviewed",
        "nearest_working_level_mechanically_approximated",
    ]
    if not level.basis_tags:
        hard_rejects.append("no_structural_level_basis")
    if level.touch_count < 2:
        manual.append("level_has_only_single_mechanical_pivot_touch")
    if closes_both_sides(bars, max(0, level.pivot_index), i, level.price):
        hard_rejects.append("level_chopped_by_closes_on_both_sides")
    return {
        "timeframe": "1d",
        "level_price": level.price,
        "basis_tags": level.basis_tags,
        "structural_basis_count": len(level.basis_tags),
        "touch_count": level.touch_count,
        "nearest_level": True,
        "hard_rejects": hard_rejects,
        "manual_review_needed": manual,
    }


def approach_features(bars: list[Bar], i: int, level: float, atr: float, side: str, p: SpecParams) -> dict[str, Any]:
    prev_avg = avg_range(bars, i - 10, i)
    current_range = bars[i].high - bars[i].low
    paranormal = bool(prev_avg and current_range >= p.paranormal_avg_range_mult * prev_avg) or current_range >= p.paranormal_atr_mult * atr
    start_i = i
    for j in range(i - 1, max(-1, i - 12), -1):
        moving_toward = bars[j].close <= bars[j + 1].close if side == "short" else bars[j].close >= bars[j + 1].close
        if not moving_toward:
            break
        start_i = j
    approach_bars = i - start_i + 1
    atr_consumed = abs(bars[i].close - bars[start_i].close) / atr if atr else 0.0
    near_closes = sum(
        1 for bar in bars[max(0, i - p.no_consolidation_lookback) : i]
        if abs(bar.close - level) <= p.close_near_level_atr * atr
    )
    return {
        "approach_bars_count": approach_bars,
        "approach_range_atr": atr_consumed,
        "atr_consumed_before_level": atr_consumed,
        "paranormal_approach_candle": paranormal,
        "sharp_approach": approach_bars <= 3 or paranormal,
        "near_level_close_count_last_10": near_closes,
        "no_consolidation_at_level": near_closes < p.no_consolidation_min_near_closes,
    }


def breakout_reject_features(bars: list[Bar], i: int, level: float, atr: float, side: str, p: SpecParams) -> dict[str, Any]:
    if i < p.compression_lookback * 2:
        return {"consolidation_near_level": False, "compression": False, "volatility_fade": False, "directional_pressure_into_level": False}
    recent = bars[i - p.compression_lookback : i]
    prior = bars[i - p.compression_lookback * 2 : i - p.compression_lookback]
    recent_avg = sum(bar.high - bar.low for bar in recent) / len(recent)
    prior_avg = sum(bar.high - bar.low for bar in prior) / len(prior)
    near_count = sum(1 for bar in recent if abs(bar.close - level) <= p.close_near_level_atr * atr)
    pressure = sum(
        recent[j].close > recent[j - 1].close if side == "short" else recent[j].close < recent[j - 1].close
        for j in range(1, len(recent))
    ) >= 3
    return {
        "consolidation_near_level": near_count >= 3,
        "compression": bool(prior_avg and recent_avg <= p.range_compression_ratio * prior_avg and pressure),
        "volatility_fade": bool(prior_avg and (bars[i - 1].high - bars[i - 1].low) <= p.range_compression_ratio * prior_avg),
        "directional_pressure_into_level": pressure,
    }


def detect_sweep_return(bars: list[Bar], i: int, level: float, atr: float, side: str, p: SpecParams) -> tuple[bool, int, str]:
    luft = p.luft_atr * atr
    bar = bars[i]
    if side == "short":
        same = bar.high > level + luft and bar.close < level
        prev = i > 0 and bars[i - 1].high > level + luft and bars[i - 1].close >= level and bar.close < level
    else:
        same = bar.low < level - luft and bar.close > level
        prev = i > 0 and bars[i - 1].low < level - luft and bars[i - 1].close <= level and bar.close > level
    if same:
        return True, i, "same_bar_sweep_and_return"
    if prev:
        return True, i - 1, "two_bar_sweep_then_return"
    return False, i, "no_sweep_return"


def find_next_opposite_level(levels: list[PivotLevel], entry: float, side: str) -> float | None:
    if side == "short":
        below = [level.price for level in levels if level.price < entry]
        return max(below) if below else None
    above = [level.price for level in levels if level.price > entry]
    return min(above) if above else None


def risk_validate(entry: float, stop: float, next_level: float | None, risk_atr: float, side: str, p: SpecParams, risk_atr_interval: str) -> dict[str, Any]:
    rejects: list[str] = []
    warnings: list[str] = []
    risk_abs = stop - entry if side == "short" else entry - stop
    if risk_abs <= 0:
        rejects.append("stop_on_wrong_side_or_zero_risk")
    technical_stop_atr = risk_abs / risk_atr if risk_atr and risk_abs > 0 else 999.0
    if technical_stop_atr > p.max_stop_atr:
        rejects.append("technical_stop_gt_13pct_atr")
    elif technical_stop_atr > p.warn_stop_atr:
        warnings.append("technical_stop_between_10pct_and_13pct_atr")
    if next_level is None:
        room_r = 0.0
        rejects.append("next_technical_level_missing_for_room_check")
    else:
        room_abs = entry - next_level if side == "short" else next_level - entry
        room_r = room_abs / risk_abs if risk_abs > 0 else 0.0
        if room_r < p.min_room_r:
            rejects.append("room_to_next_level_lt_3r")
        elif room_r < 4.0:
            warnings.append("target_3r_but_less_than_4r_room")
    return {
        "status": "reject" if rejects else ("warn" if warnings else "pass"),
        "atr": risk_atr,
        "atr_interval": risk_atr_interval,
        "calculated_stop_abs": 0.10 * risk_atr,
        "technical_stop_abs": risk_abs,
        "technical_stop_atr": technical_stop_atr,
        "technical_vs_calculated": technical_stop_atr / 0.10,
        "room_to_next_level_r": room_r,
        "next_level_price": next_level,
        "target_3r": entry - p.target_r * risk_abs if side == "short" else entry + p.target_r * risk_abs,
        "warnings": warnings,
        "reject_reasons": rejects,
    }


def build_signal(symbol: str, bars: list[Bar], i: int, level: PivotLevel, opposite_levels: list[PivotLevel], side: str, setup_atr: float, risk_atr: float, risk_atr_interval: str, p: SpecParams) -> SpecSignal | None:
    sweep_ok, sweep_i, sweep_mode = detect_sweep_return(bars, i, level.price, setup_atr, side, p)
    if not sweep_ok:
        return None
    entry = bars[i].close
    stop = bars[sweep_i].high + p.stop_luft_atr * setup_atr if side == "short" else bars[sweep_i].low - p.stop_luft_atr * setup_atr
    level_validation = validate_level(level, bars, i)
    retest = classify_retest(bars, i, level.price, setup_atr, p)
    approach = approach_features(bars, i, level.price, setup_atr, side, p)
    reject_features = breakout_reject_features(bars, i, level.price, setup_atr, side, p)
    risk = risk_validate(entry, stop, find_next_opposite_level(opposite_levels, entry, side), risk_atr, side, p, risk_atr_interval)

    score = 0.0
    hard_rejects = list(level_validation["hard_rejects"])
    strength: list[str] = []
    weakness: list[str] = []
    manual = list(level_validation["manual_review_needed"]) + list(retest["manual_review_needed"])

    if retest["classification"] in ("far_retest", "very_far_retest"):
        strength.append(retest["classification"])
        score += retest["strength"]
    elif retest["classification"] in ("near_retest", "strong_near_retest", "ideal_near_retest"):
        hard_rejects.append("near_retest_biases_breakout_not_lp")
    elif p.require_far_retest_for_auto:
        hard_rejects.append("retest_not_far_for_automated_lp")
    if approach["sharp_approach"]:
        strength.append("sharp_approach")
        score += 1.5
    if approach["paranormal_approach_candle"]:
        strength.append("paranormal_approach_candle")
        score += 1.5
    if approach["atr_consumed_before_level"] >= p.atr_consumed_strong:
        strength.append("atr_consumed_before_level_ge_1atr")
        score += 2.0
    elif approach["atr_consumed_before_level"] >= p.atr_consumed_warning:
        strength.append("atr_consumed_before_level_ge_0_5atr")
        score += 1.0
    if approach["no_consolidation_at_level"]:
        strength.append("no_consolidation_at_level")
        score += 1.0
    else:
        weakness.append("standing_near_level_before_sweep")
    strength.append(sweep_mode)
    score += 2.0
    if (side == "short" and bars[i].close < bars[i].open) or (side == "long" and bars[i].close > bars[i].open):
        strength.append("strong_return_candle")
        score += 1.0

    if reject_features["consolidation_near_level"]:
        hard_rejects.append("breakout_precondition_consolidation_near_level")
    if reject_features["compression"]:
        hard_rejects.append("breakout_precondition_compression_podzhatie")
    if reject_features["volatility_fade"] and reject_features["directional_pressure_into_level"]:
        hard_rejects.append("breakout_precondition_volatility_fade_into_level")
    if retest["invalid_chop"]:
        hard_rejects.append("invalid_chop_from_retest_module")
    if risk["status"] == "reject":
        hard_rejects.extend(risk["reject_reasons"])
    else:
        weakness.extend(risk["warnings"])
    if score < p.min_score:
        hard_rejects.append("score_below_spec_trigger_threshold")
    if p.reject_manual_review and manual:
        hard_rejects.append("manual_review_required_by_spec")

    risk_abs = risk["technical_stop_abs"]
    false_breakout = {
        **approach,
        "sweep_detected": True,
        "returned_beyond_level": True,
        "sweep_mode": sweep_mode,
        "lp_tail_setup_atr": ((bars[sweep_i].high - max(bars[sweep_i].open, bars[sweep_i].close)) / setup_atr) if side == "short" else ((min(bars[sweep_i].open, bars[sweep_i].close) - bars[sweep_i].low) / setup_atr),
        "stop_size_setup_atr": risk_abs / setup_atr if setup_atr else None,
        "stop_size_risk_atr": risk_abs / risk_atr if risk_atr else None,
    }
    manual.extend([
        "visual_v_or_u_formation_not_reviewed",
        "participant_intent_not_reviewed",
        "hidden_accumulation_not_reviewed",
        "lower_timeframe_execution_trigger_not_available_on_d1",
    ])
    return SpecSignal(
        symbol=symbol,
        side=side,
        status="reject" if hard_rejects else "trigger",
        entry_i=i,
        sweep_i=sweep_i,
        entry_dt=bars[i].dt,
        level=level.price,
        entry=entry,
        stop=stop,
        target=risk["target_3r"],
        risk=risk_abs,
        score=score,
        hard_rejects=sorted(set(hard_rejects)),
        strength_factors=sorted(set(strength)),
        weakness_factors=sorted(set(weakness)),
        manual_review_needed=sorted(set(manual)),
        level_validation=level_validation,
        retest_features=retest,
        false_breakout_features=false_breakout,
        breakout_reject_features=reject_features,
        risk_validation=risk,
    )


def align_risk_atrs(exec_bars: list[Bar], risk_bars: list[Bar], p: SpecParams) -> list[float | None]:
    risk_times = [bar.dt for bar in risk_bars]
    aligned: list[float | None] = []
    for bar in exec_bars:
        # Use only the previous completed risk-ATR bar to avoid intraday lookahead.
        risk_i = bisect.bisect_left(risk_times, bar.dt) - 1
        aligned.append(atr_at(risk_bars, risk_i, p.atr_period) if risk_i >= p.atr_period else None)
    return aligned


def detect_symbol(symbol: str, bars: list[Bar], risk_atrs: list[float | None], risk_atr_interval: str, p: SpecParams, keep_rejected: bool) -> tuple[list[SpecSignal], list[SpecSignal]]:
    triggers: list[SpecSignal] = []
    candidates: list[SpecSignal] = []
    used: set[tuple[str, int]] = set()
    resistances: list[PivotLevel] = []
    supports: list[PivotLevel] = []
    for i in range(p.atr_period + p.pivot_k + 20, len(bars)):
        setup_atr = atr_at(bars, i, p.atr_period)
        risk_atr = risk_atrs[i] if i < len(risk_atrs) else None
        if setup_atr is None or setup_atr <= 0 or risk_atr is None or risk_atr <= 0:
            continue
        update_confirmed_levels(bars, i, setup_atr, resistances, supports, p)
        res_candidates = sorted([level for level in resistances if bars[i].high >= level.price], key=lambda level: abs(bars[i].close - level.price))[:3]
        sup_candidates = sorted([level for level in supports if bars[i].low <= level.price], key=lambda level: abs(bars[i].close - level.price))[:3]
        for side, candidates_for_side, opposite in (("short", res_candidates, supports), ("long", sup_candidates, resistances)):
            for level in candidates_for_side:
                signal = build_signal(symbol, bars, i, level, opposite, side, setup_atr, risk_atr, risk_atr_interval, p)
                if signal is None:
                    continue
                if keep_rejected:
                    candidates.append(signal)
                key = (side, round(level.price / max(setup_atr * p.level_merge_tol_atr, 1e-9)))
                if signal.status == "trigger" and key not in used:
                    simulate(bars, signal, p)
                    triggers.append(signal)
                    used.add(key)
                    break
    return triggers, candidates


def simulate(bars: list[Bar], signal: SpecSignal, p: SpecParams) -> None:
    fee_r = 2 * p.fee_rate * signal.entry / signal.risk if signal.risk > 0 else 0.0
    for bar in bars[signal.entry_i + 1 :]:
        if signal.side == "short":
            hit_stop = bar.high >= signal.stop
            hit_target = bar.low <= signal.target
        else:
            hit_stop = bar.low <= signal.stop
            hit_target = bar.high >= signal.target
        if hit_stop and hit_target:
            signal.outcome, signal.exit_dt, signal.r_multiple = "loss", bar.dt, -1.0 - fee_r
            return
        if hit_stop:
            signal.outcome, signal.exit_dt, signal.r_multiple = "loss", bar.dt, -1.0 - fee_r
            return
        if hit_target:
            signal.outcome, signal.exit_dt, signal.r_multiple = "win", bar.dt, p.target_r - fee_r
            return
    signal.outcome = "open"


def write_signals_csv(path: Path, signals: list[SpecSignal]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "symbol", "side", "status", "entry_dt", "exit_dt", "outcome", "score", "r_multiple",
            "level", "entry", "stop", "target", "risk", "strength_factors", "weakness_factors",
            "hard_rejects", "manual_review_needed", "level_validation", "retest_features",
            "false_breakout_features", "breakout_reject_features", "risk_validation", "source_cards", "source_chunks",
        ])
        for signal in signals:
            writer.writerow([
                signal.symbol,
                signal.side,
                signal.status,
                signal.entry_dt.isoformat(),
                signal.exit_dt.isoformat() if signal.exit_dt else "",
                signal.outcome,
                f"{signal.score:.2f}",
                f"{signal.r_multiple:.6f}",
                f"{signal.level:.12g}",
                f"{signal.entry:.12g}",
                f"{signal.stop:.12g}",
                f"{signal.target:.12g}",
                f"{signal.risk:.12g}",
                json.dumps(signal.strength_factors, ensure_ascii=False),
                json.dumps(signal.weakness_factors, ensure_ascii=False),
                json.dumps(signal.hard_rejects, ensure_ascii=False),
                json.dumps(signal.manual_review_needed, ensure_ascii=False),
                json.dumps(signal.level_validation, ensure_ascii=False),
                json.dumps(signal.retest_features, ensure_ascii=False),
                json.dumps(signal.false_breakout_features, ensure_ascii=False),
                json.dumps(signal.breakout_reject_features, ensure_ascii=False),
                json.dumps(signal.risk_validation, ensure_ascii=False),
                json.dumps(SOURCE_CARDS, ensure_ascii=False),
                json.dumps(SOURCE_CHUNKS, ensure_ascii=False),
            ])


def summarize(signals: list[SpecSignal]) -> None:
    closed = [signal for signal in signals if signal.outcome in ("win", "loss")]
    print("\n" + "=" * 72)
    print("SCN-002 SPEC-DRIVEN BACKTEST RESULT")
    print("=" * 72)
    print(f"Triggered signals:                 {len(signals)}")
    print(f"Closed trades:                     {len(closed)}")
    if not closed:
        print("No closed trades.")
        return
    wins = [signal for signal in closed if signal.outcome == "win"]
    total_r = sum(signal.r_multiple for signal in closed)
    print(f"Wins / Losses:                     {len(wins)} / {len(closed) - len(wins)}")
    print(f"Winrate:                           {len(wins) / len(closed) * 100:.1f}%")
    print(f"Total R net:                       {total_r:+.2f}R")
    print(f"Expectancy/trade:                  {total_r / len(closed):+.3f}R")
    group_print("By symbol", closed, lambda signal: signal.symbol)
    group_print("By year", closed, lambda signal: signal.entry_dt.strftime("%Y"))
    group_print("By month", closed, lambda signal: signal.entry_dt.strftime("%Y-%m"))
    print("=" * 72)


def group_print(title: str, signals: list[SpecSignal], key_fn) -> None:
    groups: dict[str, list[SpecSignal]] = {}
    for signal in signals:
        groups.setdefault(key_fn(signal), []).append(signal)
    print("-" * 72)
    print(title)
    for key, group in sorted(groups.items()):
        wins = sum(1 for signal in group if signal.outcome == "win")
        total_r = sum(signal.r_multiple for signal in group)
        print(f"  {key:<10} n={len(group):>4}  WR={wins / len(group) * 100:5.1f}%  totR={total_r:+7.2f}  expR={total_r / len(group):+.3f}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SCN-002 spec-driven backtest from detector specs.")
    parser.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT"])
    parser.add_argument("--interval", default="1d")
    parser.add_argument("--risk-atr-interval", default="1d")
    parser.add_argument("--start", default="2024-01")
    parser.add_argument("--end", default="2024-12")
    parser.add_argument("--trade-log", type=Path)
    parser.add_argument("--candidate-log", type=Path)
    parser.add_argument("--reject-manual-review", action="store_true")
    parser.add_argument("--min-score", type=float, default=5.0)
    parser.add_argument("--fee-rate", type=float, default=0.001)
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    params = SpecParams(reject_manual_review=args.reject_manual_review, min_score=args.min_score, fee_rate=args.fee_rate)
    all_triggers: list[SpecSignal] = []
    all_candidates: list[SpecSignal] = []
    keep_rejected = args.candidate_log is not None
    for symbol in args.symbols:
        print(f"Loading {symbol} {args.interval} {args.start}..{args.end} ...", file=sys.stderr)
        bars = load_history(symbol, args.interval, args.start, args.end)
        print(f"  total {len(bars)} bars", file=sys.stderr)
        if len(bars) < 120:
            print(f"  not enough data for {symbol}, skipping", file=sys.stderr)
            continue
        if args.risk_atr_interval == args.interval:
            risk_atrs = [atr_at(bars, i, params.atr_period) for i in range(len(bars))]
        else:
            print(f"Loading {symbol} risk ATR {args.risk_atr_interval} {args.start}..{args.end} ...", file=sys.stderr)
            risk_bars = load_history(symbol, args.risk_atr_interval, args.start, args.end)
            print(f"  risk ATR bars {len(risk_bars)}", file=sys.stderr)
            risk_atrs = align_risk_atrs(bars, risk_bars, params)
        triggers, candidates = detect_symbol(symbol, bars, risk_atrs, args.risk_atr_interval, params, keep_rejected)
        all_triggers.extend(triggers)
        all_candidates.extend(candidates)
    if args.trade_log:
        write_signals_csv(args.trade_log, all_triggers)
        print(f"Saved spec-driven trade log: {args.trade_log}", file=sys.stderr)
    if args.candidate_log:
        write_signals_csv(args.candidate_log, all_candidates)
        print(f"Saved spec-driven candidate log: {args.candidate_log}", file=sys.stderr)
    summarize(all_triggers)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
