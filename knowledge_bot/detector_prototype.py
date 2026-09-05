from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


NEAR_RETEST_BARS = 10
STRONG_NEAR_RETEST_BARS = 5
IDEAL_NEAR_RETEST_BARS = 3
FAR_RETEST_BARS = 30
VERY_FAR_RETEST_BARS = 60
MIN_DEPARTURE_BARS = 2
CONTACT_TOLERANCE_ATR = 0.02
DEPARTURE_DISTANCE_ATR = 0.20
CALCULATED_STOP_ATR = 0.10
WARNING_STOP_ATR = 0.10
MAX_STOP_ATR = 0.13
MIN_TARGET_R = 3.0
PREFERRED_TARGET_R = 4.0
FIXATION_LUFT_ATR = 0.01
FIXATION_RETURN_TOLERANCE_ATR = 0.03
VOLATILITY_FADE_RANGE_MULTIPLIER = 0.70
VOLATILITY_FADE_TWO_BAR_MULTIPLIER = 0.80
BSU_BPU_LUFT_STOP_MULTIPLIER = 0.20
BSU_BPU_LUFT_ATR_FALLBACK = 0.02
BSU_BPU_MAX_DRIFT_ATR = 0.20
BREAKOUT_CLOSE_NEAR_LEVEL_ATR = 0.50
BREAKOUT_CLOSE_VERY_NEAR_LEVEL_ATR = 0.10
CLOSE_AT_EXTREME_FRACTION = 0.20
CONSOLIDATION_LOOKBACK = 5
FALSE_BREAKOUT_LUFT_ATR = 0.02
PARANORMAL_RANGE_MULTIPLIER = 1.50
PARANORMAL_RANGE_ATR = 0.80
STRUCTURAL_LEVEL_BASIS = {
    "inflection",
    "mirror_level",
    "paranormal_bar",
    "long_false_breakout_tail",
    "two_bar_limit",
    "post_chop_acceptance",
    "strong_movement_stop",
}
TBX_ENTRY_MODELS = {
    "primary_impulse",
    "fixation_return",
    "bsu_bpu_limit",
    "false_breakout_return",
    "false_breakout_stop_market",
}


@dataclass(frozen=True)
class Candle:
    time: str
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class ContactCluster:
    start: int
    end: int


class DetectorInputError(ValueError):
    pass


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def default_root() -> Path:
    return Path(__file__).resolve().parents[1]


def as_float(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DetectorInputError(f"{field_name} must be a number")
    return float(value)


def optional_float(value: Any, field_name: str) -> float | None:
    if value is None:
        return None
    return as_float(value, field_name)


def parse_candles(raw_candles: Any, field_name: str) -> list[Candle]:
    if not isinstance(raw_candles, list):
        raise DetectorInputError(f"{field_name} must be a list")

    candles: list[Candle] = []
    for index, raw in enumerate(raw_candles):
        if not isinstance(raw, dict):
            raise DetectorInputError(f"{field_name}[{index}] must be an object")
        try:
            candles.append(
                Candle(
                    time=str(raw["time"]),
                    open=as_float(raw["open"], f"{field_name}[{index}].open"),
                    high=as_float(raw["high"], f"{field_name}[{index}].high"),
                    low=as_float(raw["low"], f"{field_name}[{index}].low"),
                    close=as_float(raw["close"], f"{field_name}[{index}].close"),
                )
            )
        except KeyError as exc:
            raise DetectorInputError(f"{field_name}[{index}] missing field {exc.args[0]!r}") from exc
        if candles[-1].high < candles[-1].low:
            raise DetectorInputError(f"{field_name}[{index}].high must be >= low")

    return candles


def contact_tolerance(atr: float, tick_size: float | None) -> float:
    atr_tolerance = CONTACT_TOLERANCE_ATR * atr
    if tick_size is None:
        return atr_tolerance
    return max(tick_size * 2.0, atr_tolerance)


def candle_contacts_level(candle: Candle, level_price: float, tolerance: float) -> bool:
    return (
        candle.low <= level_price <= candle.high
        or abs(candle.close - level_price) <= tolerance
        or abs(candle.open - level_price) <= tolerance
    )


def build_contact_clusters(contacts: list[bool]) -> list[ContactCluster]:
    clusters: list[ContactCluster] = []
    index = 0
    while index < len(contacts):
        if not contacts[index]:
            index += 1
            continue
        start = index
        while index + 1 < len(contacts) and contacts[index + 1]:
            index += 1
        clusters.append(ContactCluster(start=start, end=index))
        index += 1
    return clusters


def has_valid_departure(candles: list[Candle], contacts: list[bool], prior: ContactCluster, current: ContactCluster, level_price: float, atr: float) -> bool:
    gap_start = prior.end + 1
    gap_end = current.start
    gap = candles[gap_start:gap_end]
    if not gap:
        return False

    non_contact_count = sum(1 for index in range(gap_start, gap_end) if not contacts[index])
    if non_contact_count >= MIN_DEPARTURE_BARS:
        return True

    departure_distance = max(abs(candle.close - level_price) for candle in gap)
    return departure_distance >= DEPARTURE_DISTANCE_ATR * atr


def find_retest_pair(candles: list[Candle], contacts: list[bool], clusters: list[ContactCluster], level_price: float, atr: float) -> tuple[ContactCluster | None, ContactCluster | None]:
    if not clusters:
        return None, None

    current = clusters[-1]
    for prior in reversed(clusters[:-1]):
        if has_valid_departure(candles, contacts, prior, current, level_price, atr):
            return prior, current
    return None, current


def classify_retest(bars_since_contact: int) -> tuple[str, str, float]:
    if bars_since_contact <= IDEAL_NEAR_RETEST_BARS:
        return "ideal_near_retest", "breakout", 2.5
    if bars_since_contact <= STRONG_NEAR_RETEST_BARS:
        return "strong_near_retest", "breakout", 2.0
    if bars_since_contact <= NEAR_RETEST_BARS:
        return "near_retest", "breakout", 1.5
    if bars_since_contact >= VERY_FAR_RETEST_BARS:
        return "very_far_retest", "false_breakout", 2.5
    if bars_since_contact >= FAR_RETEST_BARS:
        return "far_retest", "false_breakout", 2.0
    return "gray_zone", "neutral/manual", 0.0


def is_invalid_chop(candles: list[Candle], contacts: list[bool], prior: ContactCluster, current: ContactCluster, level_price: float) -> bool:
    window_start = prior.end + 1
    window_end = current.start
    between = candles[window_start:window_end]
    if not between:
        return False

    closes_above = sum(1 for candle in between if candle.close > level_price)
    closes_below = sum(1 for candle in between if candle.close < level_price)
    if len(between) <= 10 and closes_above >= 2 and closes_below >= 2:
        return True

    full_window_start = prior.start
    full_window_end = current.end + 1
    full_window_length = max(1, full_window_end - full_window_start)
    contact_count = sum(1 for index in range(full_window_start, full_window_end) if contacts[index])
    return contact_count / full_window_length > 0.50


def detect_far_to_near_exception(clusters: list[ContactCluster], current: ContactCluster) -> bool:
    if len(clusters) < 3 or clusters[-1] != current:
        return False
    old_contact = clusters[-3]
    far_contact = clusters[-2]
    old_gap = far_contact.start - old_contact.end
    new_gap = current.start - far_contact.end
    return old_gap >= FAR_RETEST_BARS and new_gap <= NEAR_RETEST_BARS


def detect_retest(raw_config: dict[str, Any], symbol: str | None) -> dict[str, Any]:
    timeframe = str(raw_config.get("timeframe", "unknown"))
    level_price = as_float(raw_config.get("level_price"), "retests[].level_price")
    atr = as_float(raw_config.get("atr"), "retests[].atr")
    if atr <= 0:
        raise DetectorInputError("retests[].atr must be > 0")
    tick_size = optional_float(raw_config.get("tick_size"), "retests[].tick_size")
    candles = parse_candles(raw_config.get("candles"), "retests[].candles")
    tolerance = contact_tolerance(atr, tick_size)
    contacts = [candle_contacts_level(candle, level_price, tolerance) for candle in candles]
    clusters = build_contact_clusters(contacts)
    prior, current = find_retest_pair(candles, contacts, clusters, level_price, atr)

    manual_review_needed = ["level_quality", "prior_contact_meaningfulness"]
    weaknesses: list[str] = []
    strength_factors: list[str] = []

    if current is None:
        return {
            "symbol": symbol,
            "detector": "near_far_retest",
            "status": "candidate",
            "timeframe": timeframe,
            "level_price": level_price,
            "contact_tolerance": tolerance,
            "classification": "no_current_contact",
            "bias": "none",
            "strength": 0.0,
            "required_passed": False,
            "hard_rejects": [],
            "strength_factors": [],
            "weakness_factors": ["price_has_not_returned_to_level"],
            "manual_review_needed": manual_review_needed,
            "retest_features": {
                "contact_count": 0,
                "contact_clusters": [],
            },
        }

    if prior is None:
        return {
            "symbol": symbol,
            "detector": "near_far_retest",
            "status": "candidate",
            "timeframe": timeframe,
            "level_price": level_price,
            "contact_tolerance": tolerance,
            "classification": "no_valid_prior_contact",
            "bias": "none",
            "strength": 0.0,
            "required_passed": False,
            "hard_rejects": [],
            "strength_factors": [],
            "weakness_factors": ["no_valid_departure_before_current_contact"],
            "manual_review_needed": manual_review_needed,
            "retest_features": {
                "current_contact_time": candles[current.start].time,
                "contact_count": sum(contacts),
                "contact_clusters": [cluster.__dict__ for cluster in clusters],
            },
        }

    bars_since_contact = current.start - prior.end
    classification, bias, strength = classify_retest(bars_since_contact)
    invalid_chop = is_invalid_chop(candles, contacts, prior, current, level_price)
    far_to_near_exception = detect_far_to_near_exception(clusters, current)

    if far_to_near_exception:
        bias = "breakout"
        strength_factors.append("far_to_near_retest_exception")

    if raw_config.get("u_formation") and classification in {"far_retest", "very_far_retest"}:
        manual_review_needed.append("u_formation_may_override_far_retest")
        weaknesses.append("far_retest_with_u_formation_override_risk")

    if raw_config.get("sharp_approach") and classification in {"near_retest", "strong_near_retest", "ideal_near_retest"}:
        manual_review_needed.append("near_retest_after_sharp_approach")
        weaknesses.append("near_retest_after_exhaustion")

    if invalid_chop:
        status = "reject"
        hard_rejects = ["invalid_chop"]
        required_passed = False
        bias = "manual/reject"
    else:
        status = "candidate"
        hard_rejects = []
        required_passed = True

    strength_factors.append(classification)

    return {
        "symbol": symbol,
        "detector": "near_far_retest",
        "status": status,
        "timeframe": timeframe,
        "level_price": level_price,
        "contact_tolerance": tolerance,
        "classification": classification,
        "bias": bias,
        "strength": strength,
        "required_passed": required_passed,
        "hard_rejects": hard_rejects,
        "strength_factors": strength_factors,
        "weakness_factors": weaknesses,
        "manual_review_needed": manual_review_needed,
        "retest_features": {
            "last_contact_time": candles[prior.end].time,
            "current_contact_time": candles[current.start].time,
            "bars_since_contact": bars_since_contact,
            "invalid_chop": invalid_chop,
            "far_to_near_retest_exception": far_to_near_exception,
            "contact_count": sum(contacts),
            "contact_clusters": [cluster.__dict__ for cluster in clusters],
        },
    }


def fixation_luft(atr: float, tick_size: float | None) -> float:
    atr_luft = FIXATION_LUFT_ATR * atr
    if tick_size is None:
        return atr_luft
    return max(tick_size * 2.0, atr_luft)


def full_bar_beyond_level(candle: Candle, direction: str, level_price: float, luft: float) -> bool:
    if direction == "long":
        threshold = level_price + luft
        return candle.open > threshold and candle.low > threshold and candle.close > threshold
    threshold = level_price - luft
    return candle.open < threshold and candle.high < threshold and candle.close < threshold


def detect_return_attempt(
    candle: Candle,
    direction: str,
    level_price: float,
    return_tolerance: float,
    reference_extreme: float,
    calculated_stop_abs: float,
) -> str | None:
    adverse_pullback = 0.30 * calculated_stop_abs
    if direction == "long":
        if candle.low < level_price and candle.close > level_price:
            return "local_lp"
        if candle.close < level_price:
            return "overshoot"
        if candle.low <= level_price + return_tolerance:
            return "touch"
        if reference_extreme - candle.low >= adverse_pullback:
            return "undershoot"
        return None

    if candle.high > level_price and candle.close < level_price:
        return "local_lp"
    if candle.close > level_price:
        return "overshoot"
    if candle.high >= level_price - return_tolerance:
        return "touch"
    if candle.high - reference_extreme >= adverse_pullback:
        return "undershoot"
    return None


def candle_range(candle: Candle) -> float:
    return candle.high - candle.low


def average(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def median(values: list[float]) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    middle = len(sorted_values) // 2
    if len(sorted_values) % 2:
        return sorted_values[middle]
    return (sorted_values[middle - 1] + sorted_values[middle]) / 2.0


def optional_int(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise DetectorInputError(f"{field_name} must be an integer")
    return value


def string_list(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise DetectorInputError(f"{field_name} must be a list")
    parsed: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise DetectorInputError(f"{field_name}[{index}] must be a non-empty string")
        parsed.append(item.strip())
    return parsed


def close_at_direction_extreme(candle: Candle, direction: str) -> bool:
    range_abs = candle_range(candle)
    if range_abs <= 0:
        return False
    if direction == "long":
        return (candle.high - candle.close) / range_abs <= CLOSE_AT_EXTREME_FRACTION
    return (candle.close - candle.low) / range_abs <= CLOSE_AT_EXTREME_FRACTION


def range_compression(candles: list[Candle]) -> bool:
    if len(candles) < CONSOLIDATION_LOOKBACK * 2:
        return False
    previous = candles[-CONSOLIDATION_LOOKBACK * 2:-CONSOLIDATION_LOOKBACK]
    recent = candles[-CONSOLIDATION_LOOKBACK:]
    previous_average = average([candle_range(candle) for candle in previous])
    recent_average = average([candle_range(candle) for candle in recent])
    return previous_average > 0 and recent_average <= VOLATILITY_FADE_RANGE_MULTIPLIER * previous_average


def closes_consolidate_near_level(candles: list[Candle], level_price: float, atr: float) -> bool:
    if not candles:
        return False
    window = candles[-CONSOLIDATION_LOOKBACK:]
    distances = [abs(candle.close - level_price) / atr for candle in window]
    return median(distances) <= BREAKOUT_CLOSE_NEAR_LEVEL_ATR


def directional_pressure(candles: list[Candle], direction: str) -> bool:
    window = candles[-CONSOLIDATION_LOOKBACK:]
    if len(window) < 4:
        return False
    if direction == "long":
        close_steps = sum(1 for index in range(1, len(window)) if window[index].close >= window[index - 1].close)
        protected_extremes = sum(1 for index in range(1, len(window)) if window[index].low >= window[index - 1].low)
    else:
        close_steps = sum(1 for index in range(1, len(window)) if window[index].close <= window[index - 1].close)
        protected_extremes = sum(1 for index in range(1, len(window)) if window[index].high <= window[index - 1].high)
    return close_steps >= 3 and protected_extremes >= 3


def room_to_target(entry_price: float | None, stop_price: float | None, next_level_price: float | None, direction: str) -> tuple[float | None, list[str]]:
    hard_rejects: list[str] = []
    if entry_price is None or stop_price is None or next_level_price is None:
        return None, hard_rejects
    if direction == "long":
        risk_abs = entry_price - stop_price
        target_in_direction = next_level_price > entry_price
    else:
        risk_abs = stop_price - entry_price
        target_in_direction = next_level_price < entry_price
    if risk_abs <= 0:
        hard_rejects.append("stop_on_wrong_side_of_entry")
        return None, hard_rejects
    if not target_in_direction:
        hard_rejects.append("next_level_not_in_trade_direction")
        return None, hard_rejects
    return abs(next_level_price - entry_price) / risk_abs, hard_rejects


def validate_level_strength(raw_config: dict[str, Any], symbol: str | None) -> dict[str, Any]:
    timeframe = str(raw_config.get("timeframe", "unknown"))
    level_price = as_float(raw_config.get("level_price"), "levels[].level_price")
    current_price = optional_float(raw_config.get("current_price"), "levels[].current_price")
    basis_tags = string_list(raw_config.get("basis_tags"), "levels[].basis_tags")
    touch_count = optional_int(raw_config.get("touch_count"), "levels[].touch_count") or 0
    false_breakout_count = optional_int(raw_config.get("false_breakout_count"), "levels[].false_breakout_count") or 0
    stop_anchor = raw_config.get("stop_anchor")

    hard_rejects: list[str] = []
    weakness_factors: list[str] = []
    strength_factors: list[str] = []
    manual_review_needed = ["same_price_quality", "visual_level_contamination"]

    structural_basis = sorted(set(basis_tags).intersection(STRUCTURAL_LEVEL_BASIS))
    if not structural_basis:
        hard_rejects.append("no_structural_level_basis")
    else:
        strength_factors.extend(f"basis_{tag}" for tag in structural_basis)

    if "round_number" in basis_tags and not structural_basis:
        weakness_factors.append("round_number_without_structure")

    if bool(raw_config.get("nearest_level")):
        strength_factors.append("nearest_working_level")
    else:
        hard_rejects.append("not_nearest_working_level")

    if bool(raw_config.get("inside_channel") or raw_config.get("local_noise")):
        hard_rejects.append("level_inside_channel")
    if bool(raw_config.get("short_tail_without_confirmation")):
        hard_rejects.append("short_tail_without_confirmation")
    if bool(raw_config.get("level_contaminated") or raw_config.get("repeated_chop_without_winner")):
        hard_rejects.append("level_chopped_without_winner")

    score = 0.0
    score += 1.5 * len(structural_basis)
    if touch_count >= 3:
        score += 1.0
        strength_factors.append("three_or_more_exact_touches")
    elif touch_count >= 2:
        score += 0.5
        strength_factors.append("two_exact_touches")
    if false_breakout_count > 0:
        score += 0.75
        strength_factors.append("false_breakout_confirmation")
    if bool(raw_config.get("impulse_confirmed_after_break")):
        score += 0.5
        strength_factors.append("impulse_confirmed_after_break")
    if isinstance(stop_anchor, str) and stop_anchor.strip():
        score += 0.5
        strength_factors.append("logical_stop_anchor")
    else:
        weakness_factors.append("stop_anchor_missing")
        manual_review_needed.append("stop_anchor")

    distance_to_current: float | None = None
    if current_price is not None:
        distance_to_current = abs(current_price - level_price)

    if hard_rejects:
        status = "reject"
    elif score >= 2.0:
        status = "pass"
    else:
        status = "warn"
        weakness_factors.append("weak_level_requires_additional_confirmation")

    return {
        "symbol": symbol,
        "detector": "level_selection_strength",
        "status": status,
        "direction": "any",
        "bias": "any",
        "score": round(score, 2),
        "required_passed": not hard_rejects,
        "hard_rejects": hard_rejects,
        "strength_factors": strength_factors,
        "weakness_factors": weakness_factors,
        "manual_review_needed": manual_review_needed,
        "level_validation": {
            "timeframe": timeframe,
            "level_price": level_price,
            "current_price": current_price,
            "distance_to_current": distance_to_current,
            "basis_tags": basis_tags,
            "structural_basis_count": len(structural_basis),
            "touch_count": touch_count,
            "false_breakout_count": false_breakout_count,
            "nearest_level": bool(raw_config.get("nearest_level")),
            "inside_channel": bool(raw_config.get("inside_channel") or raw_config.get("local_noise")),
            "level_contaminated": bool(raw_config.get("level_contaminated") or raw_config.get("repeated_chop_without_winner")),
            "stop_anchor": stop_anchor if isinstance(stop_anchor, str) else None,
        },
    }


def local_zone_from_price(current_price: float | None, nearest_level_price: float | None) -> str:
    if current_price is None or nearest_level_price is None:
        return "unknown"
    if current_price > nearest_level_price:
        return "long"
    if current_price < nearest_level_price:
        return "short"
    return "at_level"


def validate_trend_context(raw_config: dict[str, Any], symbol: str | None) -> dict[str, Any]:
    scenario = str(raw_config.get("scenario", "unknown")).lower()
    direction = str(raw_config.get("direction", "")).lower()
    if direction not in {"long", "short"}:
        raise DetectorInputError("trends[].direction must be 'long' or 'short'")

    global_trend = str(raw_config.get("global_trend", "unknown")).lower()
    if global_trend not in {"long", "short", "range", "unknown"}:
        raise DetectorInputError("trends[].global_trend must be 'long', 'short', 'range', or 'unknown'")

    nearest_level_price = optional_float(raw_config.get("nearest_level_price"), "trends[].nearest_level_price")
    current_price = optional_float(raw_config.get("current_price"), "trends[].current_price")
    local_zone = str(raw_config.get("local_zone") or local_zone_from_price(current_price, nearest_level_price)).lower()
    if local_zone not in {"long", "short", "at_level", "unknown"}:
        raise DetectorInputError("trends[].local_zone must be 'long', 'short', 'at_level', or 'unknown'")

    has_accumulation = bool(raw_config.get("has_accumulation") or raw_config.get("base_near_level"))
    character_change = bool(raw_config.get("character_change"))
    sharp_countertrend_approach = bool(raw_config.get("sharp_countertrend_approach"))
    strong_level_ahead = bool(raw_config.get("strong_level_ahead"))

    hard_rejects: list[str] = []
    weakness_factors: list[str] = []
    strength_factors: list[str] = []
    manual_review_needed = ["global_swing_structure", "nearest_level_quality"]

    if nearest_level_price is None or local_zone == "unknown":
        hard_rejects.append("nearest_level_missing_for_local_trend")
    if global_trend == "unknown":
        weakness_factors.append("global_trend_unknown")
        manual_review_needed.append("global_trend_structure")

    global_aligned = global_trend == direction
    local_aligned = local_zone == direction or local_zone == "at_level"
    if global_aligned:
        strength_factors.append("global_trend_aligned")
    else:
        weakness_factors.append("global_trend_not_aligned")
    if local_aligned:
        strength_factors.append("local_trend_aligned")
    else:
        weakness_factors.append("local_trend_not_aligned")

    if scenario == "breakout":
        if not global_aligned and not (has_accumulation or character_change):
            hard_rejects.append("countertrend_breakout_without_accumulation")
        if local_zone in {"long", "short"} and not local_aligned:
            hard_rejects.append("breakout_against_local_zone")
        if has_accumulation:
            strength_factors.append("countertrend_accumulation_or_base")
        if character_change:
            strength_factors.append("character_change")
    elif scenario in {"false_breakout", "reversal", "lp"}:
        if sharp_countertrend_approach and strong_level_ahead:
            strength_factors.append("countertrend_lp_context")
        elif not global_aligned:
            weakness_factors.append("countertrend_lp_needs_exhaustion_context")
    else:
        manual_review_needed.append("scenario_type")

    if hard_rejects:
        status = "reject"
    elif weakness_factors:
        status = "warn"
    else:
        status = "pass"

    return {
        "symbol": symbol,
        "detector": "trend_context",
        "status": status,
        "direction": direction,
        "bias": direction,
        "score": None,
        "required_passed": not hard_rejects,
        "hard_rejects": hard_rejects,
        "strength_factors": strength_factors,
        "weakness_factors": weakness_factors,
        "manual_review_needed": manual_review_needed,
        "trend_features": {
            "scenario": scenario,
            "global_trend": global_trend,
            "local_zone": local_zone,
            "global_aligned": global_aligned,
            "local_aligned": local_aligned,
            "nearest_level_price": nearest_level_price,
            "current_price": current_price,
            "has_accumulation": has_accumulation,
            "character_change": character_change,
            "sharp_countertrend_approach": sharp_countertrend_approach,
            "strong_level_ahead": strong_level_ahead,
        },
    }


def validate_tbx_entry_model(raw_config: dict[str, Any], symbol: str | None) -> dict[str, Any]:
    direction = str(raw_config.get("direction", "")).lower()
    if direction not in {"long", "short"}:
        raise DetectorInputError("tbx_entries[].direction must be 'long' or 'short'")

    entry_model = str(raw_config.get("entry_model", "")).lower()
    if entry_model not in TBX_ENTRY_MODELS:
        raise DetectorInputError("tbx_entries[].entry_model is unknown")

    timeframe = str(raw_config.get("timeframe", "unknown"))
    stop_size_atr = optional_float(raw_config.get("stop_size_atr"), "tbx_entries[].stop_size_atr")
    room_to_target_r = optional_float(raw_config.get("room_to_target_r"), "tbx_entries[].room_to_target_r")
    moved_from_level_atr = optional_float(raw_config.get("moved_from_level_atr"), "tbx_entries[].moved_from_level_atr")

    hard_rejects: list[str] = []
    weakness_factors: list[str] = []
    strength_factors: list[str] = []
    manual_review_needed = ["daily_scenario_quality", "stop_structure_quality"]

    if not bool(raw_config.get("daily_scenario_valid")):
        hard_rejects.append("daily_scenario_missing_or_invalid")
    else:
        strength_factors.append("daily_scenario_valid")

    if not bool(raw_config.get("execution_aligned_with_daily", True)):
        hard_rejects.append("entry_against_daily_scenario")

    stop_defined = bool(raw_config.get("stop_defined"))
    if not stop_defined:
        hard_rejects.append("logical_stop_missing")
    else:
        strength_factors.append("logical_stop_defined")

    if stop_size_atr is None:
        manual_review_needed.append("stop_size_atr")
    elif stop_size_atr > MAX_STOP_ATR:
        hard_rejects.append("stop_too_large_for_tbx")
    elif stop_size_atr <= CALCULATED_STOP_ATR:
        strength_factors.append("stop_within_calculated_atr")
    else:
        weakness_factors.append("stop_between_10_and_13pct_atr")

    if room_to_target_r is None:
        manual_review_needed.append("room_to_target_r")
    elif room_to_target_r < MIN_TARGET_R:
        hard_rejects.append("room_to_target_less_than_3r")
    elif room_to_target_r >= PREFERRED_TARGET_R:
        strength_factors.append("room_to_target_at_least_4r")

    volatility_fade = bool(raw_config.get("volatility_fade"))
    pre_breakout_base = bool(raw_config.get("pre_breakout_base") or raw_config.get("podzhatie"))
    if entry_model == "primary_impulse":
        if pre_breakout_base:
            strength_factors.append("pre_breakout_base")
        else:
            weakness_factors.append("primary_impulse_without_base")
        if volatility_fade:
            strength_factors.append("volatility_fade")
        else:
            hard_rejects.append("primary_impulse_without_volatility_fade")
    elif entry_model == "fixation_return":
        if not bool(raw_config.get("return_attempt")):
            hard_rejects.append("fixation_without_return_attempt")
        if not bool(raw_config.get("second_full_bar")):
            hard_rejects.append("fixation_without_second_full_bar")
    elif entry_model == "bsu_bpu_limit":
        if not bool(raw_config.get("bpu1_bpu2")):
            hard_rejects.append("limit_without_bpu1_bpu2")
        if bool(raw_config.get("lp_after_bpu2")):
            strength_factors.append("lp_after_bpu2")
    elif entry_model in {"false_breakout_return", "false_breakout_stop_market"}:
        if not bool(raw_config.get("lp_sweep")):
            hard_rejects.append("lp_entry_without_sweep")
        if not bool(raw_config.get("returned_beyond_level")):
            hard_rejects.append("lp_entry_without_return")

    if moved_from_level_atr is not None and moved_from_level_atr > 0.30:
        hard_rejects.append("price_moved_too_far_after_break")

    if hard_rejects:
        status = "reject"
    elif weakness_factors:
        status = "warn"
    else:
        status = "pass"

    return {
        "symbol": symbol,
        "detector": "tbx_entry_models",
        "status": status,
        "direction": direction,
        "bias": direction,
        "score": None,
        "required_passed": not hard_rejects,
        "hard_rejects": hard_rejects,
        "strength_factors": strength_factors,
        "weakness_factors": weakness_factors,
        "manual_review_needed": manual_review_needed,
        "tbx_validation": {
            "timeframe": timeframe,
            "entry_model": entry_model,
            "daily_scenario_valid": bool(raw_config.get("daily_scenario_valid")),
            "execution_aligned_with_daily": bool(raw_config.get("execution_aligned_with_daily", True)),
            "stop_defined": stop_defined,
            "stop_size_atr": stop_size_atr,
            "room_to_target_r": room_to_target_r,
            "volatility_fade": volatility_fade,
            "pre_breakout_base": pre_breakout_base,
            "moved_from_level_atr": moved_from_level_atr,
        },
    }


def validate_v_u_formation(raw_config: dict[str, Any], symbol: str | None) -> dict[str, Any]:
    formation_type = str(raw_config.get("formation_type", "")).lower()
    if formation_type not in {"v", "u"}:
        raise DetectorInputError("formations[].formation_type must be 'v' or 'u'")

    timeframe = str(raw_config.get("timeframe", "unknown"))
    level_price = optional_float(raw_config.get("level_price"), "formations[].level_price")
    accumulation_near_level = bool(raw_config.get("accumulation_near_level"))
    hard_rejects: list[str] = []
    weakness_factors: list[str] = []
    strength_factors: list[str] = []
    manual_review_needed = ["formation_visual_quality", "level_quality"]

    if level_price is None:
        hard_rejects.append("formation_level_missing")
    if bool(raw_config.get("contaminated_zone")):
        hard_rejects.append("formation_in_contaminated_zone")

    if formation_type == "v":
        sharp_move_out = bool(raw_config.get("sharp_move_out"))
        sharp_return = bool(raw_config.get("sharp_return"))
        returned_to_origin = bool(raw_config.get("returned_to_origin"))
        if not sharp_move_out or not sharp_return:
            hard_rejects.append("v_without_sharpness")
        if not returned_to_origin:
            hard_rejects.append("v_not_returned_to_origin")
        if accumulation_near_level:
            bias = "breakout"
            strength_factors.append("v_with_accumulation_breakout_bias")
            weakness_factors.append("classic_v_lp_bias_overridden_by_accumulation")
        else:
            bias = "false_breakout"
            strength_factors.append("classic_v_lp_bias")
    else:
        rounded_return = bool(raw_config.get("rounded_return"))
        flat_base_touches = optional_int(raw_config.get("flat_base_touches"), "formations[].flat_base_touches") or 0
        returned_to_origin = bool(raw_config.get("returned_to_origin"))
        if not rounded_return:
            hard_rejects.append("u_without_rounding")
        if flat_base_touches < 2:
            hard_rejects.append("u_without_flat_base")
        if not returned_to_origin:
            hard_rejects.append("u_not_returned_to_origin")
        bias = "breakout"
        strength_factors.append("u_formation_breakout_bias")
        if bool(raw_config.get("first_touch")):
            weakness_factors.append("u_first_touch_needs_confirmation")

    if bool(raw_config.get("atr_consumed")):
        strength_factors.append("atr_consumed_before_return")
    if bool(raw_config.get("near_retest_after_first_failure")):
        strength_factors.append("near_retest_after_first_failure")

    if hard_rejects:
        status = "reject"
    elif weakness_factors:
        status = "warn"
    else:
        status = "pass"

    return {
        "symbol": symbol,
        "detector": "v_u_formations",
        "status": status,
        "direction": "any",
        "bias": bias,
        "score": None,
        "required_passed": not hard_rejects,
        "hard_rejects": hard_rejects,
        "strength_factors": strength_factors,
        "weakness_factors": weakness_factors,
        "manual_review_needed": manual_review_needed,
        "formation_features": {
            "timeframe": timeframe,
            "formation_type": formation_type,
            "level_price": level_price,
            "accumulation_near_level": accumulation_near_level,
            "sharp_move_out": bool(raw_config.get("sharp_move_out")),
            "sharp_return": bool(raw_config.get("sharp_return")),
            "rounded_return": bool(raw_config.get("rounded_return")),
            "flat_base_touches": optional_int(raw_config.get("flat_base_touches"), "formations[].flat_base_touches") or 0,
            "returned_to_origin": bool(raw_config.get("returned_to_origin")),
            "first_touch": bool(raw_config.get("first_touch")),
            "near_retest_after_first_failure": bool(raw_config.get("near_retest_after_first_failure")),
        },
    }


def validate_tail_bars(raw_config: dict[str, Any], symbol: str | None) -> dict[str, Any]:
    timeframe = str(raw_config.get("timeframe", "unknown"))
    prior_move = str(raw_config.get("prior_move", "unknown")).lower()
    if prior_move not in {"up", "down", "unknown"}:
        raise DetectorInputError("tail_bars[].prior_move must be 'up', 'down', or 'unknown'")

    tail_bar_count = optional_int(raw_config.get("tail_bar_count"), "tail_bars[].tail_bar_count") or 0
    level_price = optional_float(raw_config.get("level_price"), "tail_bars[].level_price")
    small_bodies = bool(raw_config.get("small_bodies"))
    both_sided_tails = bool(raw_config.get("both_sided_tails"))
    near_level = bool(raw_config.get("near_level")) or level_price is not None

    hard_rejects: list[str] = []
    weakness_factors: list[str] = []
    strength_factors: list[str] = []
    manual_review_needed = ["tail_size_quality", "limit_context_quality"]

    if prior_move == "unknown":
        hard_rejects.append("prior_move_missing")
    if not near_level:
        hard_rejects.append("tail_context_without_level")
    if tail_bar_count < 2:
        hard_rejects.append("single_tail_bar_without_context")
    if not small_bodies:
        weakness_factors.append("bodies_not_small")
    if not both_sided_tails:
        weakness_factors.append("not_two_sided_limit")
    if bool(raw_config.get("contaminated_zone")):
        hard_rejects.append("tail_bars_inside_contaminated_zone")

    if prior_move == "up":
        bias = "short"
        strength_factors.append("tail_accumulation_after_up_move")
    elif prior_move == "down":
        bias = "long"
        strength_factors.append("tail_accumulation_after_down_move")
    else:
        bias = "unknown"

    if small_bodies:
        strength_factors.append("small_bodies")
    if both_sided_tails:
        strength_factors.append("two_sided_limit")
    if bool(raw_config.get("long_tail_level_basis")):
        strength_factors.append("long_tail_level_basis")
    if bool(raw_config.get("no_tail_toward_level_close")):
        strength_factors.append("close_without_tail_toward_level")

    if hard_rejects:
        status = "reject"
    elif weakness_factors:
        status = "warn"
    else:
        status = "pass"

    return {
        "symbol": symbol,
        "detector": "tail_bars_two_sided_limit",
        "status": status,
        "direction": bias,
        "bias": bias,
        "score": None,
        "required_passed": not hard_rejects,
        "hard_rejects": hard_rejects,
        "strength_factors": strength_factors,
        "weakness_factors": weakness_factors,
        "manual_review_needed": manual_review_needed,
        "tail_bar_features": {
            "timeframe": timeframe,
            "prior_move": prior_move,
            "tail_bar_count": tail_bar_count,
            "small_bodies": small_bodies,
            "both_sided_tails": both_sided_tails,
            "near_level": near_level,
            "level_price": level_price,
            "contaminated_zone": bool(raw_config.get("contaminated_zone")),
            "long_tail_level_basis": bool(raw_config.get("long_tail_level_basis")),
            "no_tail_toward_level_close": bool(raw_config.get("no_tail_toward_level_close")),
        },
    }


def detect_breakout_preconditions(raw_config: dict[str, Any], symbol: str | None) -> dict[str, Any]:
    direction = str(raw_config.get("direction", "")).lower()
    if direction not in {"long", "short"}:
        raise DetectorInputError("breakouts[].direction must be 'long' or 'short'")

    timeframe = str(raw_config.get("timeframe", "unknown"))
    level_price = as_float(raw_config.get("level_price"), "breakouts[].level_price")
    atr = as_float(raw_config.get("atr"), "breakouts[].atr")
    if atr <= 0:
        raise DetectorInputError("breakouts[].atr must be > 0")
    candles = parse_candles(raw_config.get("candles"), "breakouts[].candles")
    if not candles:
        raise DetectorInputError("breakouts[].candles must not be empty")

    entry_price = optional_float(raw_config.get("entry_price"), "breakouts[].entry_price")
    stop_price = optional_float(raw_config.get("stop_price"), "breakouts[].stop_price")
    next_level_price = optional_float(raw_config.get("next_level_price"), "breakouts[].next_level_price")
    bars_since_last_contact = optional_int(raw_config.get("bars_since_last_contact"), "breakouts[].bars_since_last_contact")

    latest = candles[-1]
    distance_atr = abs(latest.close - level_price) / atr
    close_near_level = distance_atr <= BREAKOUT_CLOSE_NEAR_LEVEL_ATR
    close_very_near_level = distance_atr <= BREAKOUT_CLOSE_VERY_NEAR_LEVEL_ATR
    close_at_extreme = close_at_direction_extreme(latest, direction)
    near_retest = bars_since_last_contact is not None and bars_since_last_contact <= NEAR_RETEST_BARS
    strong_near_retest = bars_since_last_contact is not None and bars_since_last_contact <= STRONG_NEAR_RETEST_BARS
    consolidation = bool(raw_config.get("consolidation_near_level")) or closes_consolidate_near_level(candles, level_price, atr)
    compression = bool(raw_config.get("compression") or raw_config.get("podzhatie")) or (consolidation and range_compression(candles) and directional_pressure(candles, direction))
    volatility_fade = bool(raw_config.get("volatility_fade")) or has_volatility_fade(candles, len(candles) - 1)
    no_reaction_to_lp = bool(raw_config.get("no_reaction_to_false_breakout") or raw_config.get("no_reaction_to_lp"))
    local_lp_in_breakout_direction = bool(raw_config.get("local_lp_in_breakout_direction"))
    trend_aligned = bool(raw_config.get("trend_aligned") or raw_config.get("global_trend_aligned"))
    approach_range_atr = candle_range(latest) / atr
    far_to_near_exception = bool(raw_config.get("far_to_near_exception"))

    hard_rejects: list[str] = []
    weakness_factors: list[str] = []
    strength_factors: list[str] = []
    manual_review_needed = ["level_quality", "visual_rounding", "market_leader_context"]

    if not bool(raw_config.get("level_valid", True)):
        hard_rejects.append("candidate_level_invalid")
    if bool(raw_config.get("level_contaminated")):
        hard_rejects.append("level_chopped_by_closes")
    if bool(raw_config.get("sharp_approach")) and not consolidation:
        hard_rejects.append("single_large_bar_without_consolidation")
    if approach_range_atr >= 1.0 and not consolidation:
        hard_rejects.append("atr_consumed_before_level_without_consolidation")
    if bars_since_last_contact is not None and bars_since_last_contact >= FAR_RETEST_BARS and not far_to_near_exception and not consolidation:
        hard_rejects.append("far_retest_first_contact_lp_bias")

    room_to_target_r, room_rejects = room_to_target(entry_price, stop_price, next_level_price, direction)
    hard_rejects.extend(room_rejects)
    if room_to_target_r is not None and room_to_target_r < MIN_TARGET_R:
        hard_rejects.append("room_to_target_less_than_3r")

    stop_reason = raw_config.get("stop_reason")
    if not isinstance(stop_reason, str) or not stop_reason.strip():
        manual_review_needed.append("stop_reason")
        if bool(raw_config.get("attempted_entry")):
            hard_rejects.append("stop_cannot_be_placed_behind_structure")

    score = 0.0
    if strong_near_retest:
        score += 2.0
        strength_factors.append("strong_near_retest")
    elif near_retest:
        score += 1.5
        strength_factors.append("near_retest")
    if close_near_level:
        score += 1.0
        strength_factors.append("close_near_level")
    if close_very_near_level:
        score += 0.5
        strength_factors.append("close_very_near_level")
    if close_at_extreme:
        score += 1.0
        strength_factors.append("close_at_extreme")
    if consolidation:
        score += 1.0
        strength_factors.append("accumulation")
    if compression:
        score += 1.5
        strength_factors.append("podzhatie")
    if volatility_fade:
        score += 1.0
        strength_factors.append("volatility_fade")
    else:
        weakness_factors.append("volatility_not_faded")
    if no_reaction_to_lp:
        score += 1.5
        strength_factors.append("no_reaction_to_false_breakout")
    if local_lp_in_breakout_direction:
        score += 1.5
        strength_factors.append("local_lp_in_breakout_direction")
    if trend_aligned:
        score += 1.0
        strength_factors.append("trend_aligned")
    if room_to_target_r is not None and room_to_target_r >= PREFERRED_TARGET_R:
        score += 0.5
        strength_factors.append("room_to_target_at_least_4r")

    if score < 3.0:
        weakness_factors.append("factor_count_below_setup_threshold")

    status = "reject" if hard_rejects else "setup" if score >= 3.0 else "candidate"

    return {
        "symbol": symbol,
        "detector": "breakout_preconditions",
        "status": status,
        "direction": direction,
        "bias": "breakout",
        "score": round(score, 2),
        "required_passed": not hard_rejects,
        "hard_rejects": hard_rejects,
        "strength_factors": strength_factors,
        "weakness_factors": weakness_factors,
        "manual_review_needed": manual_review_needed,
        "breakout_features": {
            "timeframe": timeframe,
            "level_price": level_price,
            "distance_atr": round(distance_atr, 4),
            "near_retest_bars": bars_since_last_contact,
            "close_near_level": close_near_level,
            "close_at_extreme": close_at_extreme,
            "consolidation_near_level": consolidation,
            "compression": compression,
            "volatility_fade": volatility_fade,
            "no_reaction_to_lp": no_reaction_to_lp,
            "trend_aligned": trend_aligned,
            "room_to_target_r": round(room_to_target_r, 4) if room_to_target_r is not None else None,
        },
    }


def false_breakout_luft(atr: float, tick_size: float | None) -> float:
    atr_luft = FALSE_BREAKOUT_LUFT_ATR * atr
    if tick_size is None:
        return atr_luft
    return max(tick_size * 2.0, atr_luft)


def detect_sweep(candle: Candle, direction: str, level_price: float, luft: float) -> tuple[bool, bool, float]:
    if direction == "short":
        sweep_detected = candle.high > level_price + luft
        returned_beyond_level = sweep_detected and candle.close < level_price
        tail_abs = candle.high - max(candle.open, candle.close)
    else:
        sweep_detected = candle.low < level_price - luft
        returned_beyond_level = sweep_detected and candle.close > level_price
        tail_abs = min(candle.open, candle.close) - candle.low
    return sweep_detected, returned_beyond_level, max(0.0, tail_abs)


def detect_false_breakout_reversal(raw_config: dict[str, Any], symbol: str | None) -> dict[str, Any]:
    direction = str(raw_config.get("direction", "")).lower()
    if direction not in {"long", "short"}:
        raise DetectorInputError("false_breakouts[].direction must be 'long' or 'short'")

    timeframe = str(raw_config.get("timeframe", "unknown"))
    level_price = as_float(raw_config.get("level_price"), "false_breakouts[].level_price")
    atr = as_float(raw_config.get("atr"), "false_breakouts[].atr")
    if atr <= 0:
        raise DetectorInputError("false_breakouts[].atr must be > 0")
    tick_size = optional_float(raw_config.get("tick_size"), "false_breakouts[].tick_size")
    candles = parse_candles(raw_config.get("candles"), "false_breakouts[].candles")
    if not candles:
        raise DetectorInputError("false_breakouts[].candles must not be empty")

    entry_price = optional_float(raw_config.get("entry_price"), "false_breakouts[].entry_price")
    stop_price = optional_float(raw_config.get("stop_price"), "false_breakouts[].stop_price")
    next_level_price = optional_float(raw_config.get("next_level_price"), "false_breakouts[].next_level_price")
    bars_since_last_contact = optional_int(raw_config.get("bars_since_last_contact"), "false_breakouts[].bars_since_last_contact")
    approach_bars_count = optional_int(raw_config.get("approach_bars_count"), "false_breakouts[].approach_bars_count")

    latest = candles[-1]
    previous_ranges = [candle_range(candle) for candle in candles[-11:-1]]
    average_previous_range = average(previous_ranges)
    approach_range = candle_range(latest)
    approach_range_atr = approach_range / atr
    paranormal_bar_to_level = bool(raw_config.get("paranormal_bar_to_level")) or approach_range_atr >= PARANORMAL_RANGE_ATR or (average_previous_range > 0 and approach_range >= PARANORMAL_RANGE_MULTIPLIER * average_previous_range)
    sharp_approach = bool(raw_config.get("sharp_approach")) or (approach_bars_count is not None and approach_bars_count <= 3) or paranormal_bar_to_level
    distance_travelled = optional_float(raw_config.get("distance_travelled_toward_level"), "false_breakouts[].distance_travelled_toward_level")
    atr_consumed_before_level = optional_float(raw_config.get("atr_consumed_before_level"), "false_breakouts[].atr_consumed_before_level")
    if atr_consumed_before_level is None and distance_travelled is not None:
        atr_consumed_before_level = distance_travelled / atr

    far_retest = bars_since_last_contact is not None and bars_since_last_contact >= FAR_RETEST_BARS
    very_far_retest = bars_since_last_contact is not None and bars_since_last_contact >= VERY_FAR_RETEST_BARS
    no_consolidation_at_level = bool(raw_config.get("no_consolidation_at_level")) or not closes_consolidate_near_level(candles, level_price, atr)
    luft = false_breakout_luft(atr, tick_size)
    sweep_detected, returned_beyond_level, lp_tail_abs = detect_sweep(latest, direction, level_price, luft)
    strong_return = bool(raw_config.get("strong_return")) or returned_beyond_level and close_at_direction_extreme(latest, direction)

    hard_rejects: list[str] = []
    weakness_factors: list[str] = []
    strength_factors: list[str] = []
    manual_review_needed = ["level_strength", "sweep_meaningfulness", "hidden_accumulation"]

    if not bool(raw_config.get("level_valid", True)):
        hard_rejects.append("candidate_level_invalid")
    if bool(raw_config.get("level_contaminated")):
        hard_rejects.append("level_chopped_by_closes")
    if bool(raw_config.get("breakout_preparation")) or bool(raw_config.get("compression_against_lp")):
        hard_rejects.append("breakout_preparation_against_lp")
    if bool(raw_config.get("full_bar_beyond_level_after_sweep")):
        hard_rejects.append("lp_converted_to_breakout")
    if bool(raw_config.get("far_to_near_exception")):
        hard_rejects.append("far_retest_converted_to_near_breakout_context")

    stop_size_atr: float | None = None
    room_to_target_r, room_rejects = room_to_target(entry_price, stop_price, next_level_price, direction)
    hard_rejects.extend(room_rejects)
    if entry_price is not None and stop_price is not None:
        risk_abs = abs(entry_price - stop_price)
        stop_size_atr = risk_abs / atr
        if stop_size_atr > MAX_STOP_ATR:
            hard_rejects.append("stop_behind_lp_tail_too_large")
    if room_to_target_r is not None and room_to_target_r < MIN_TARGET_R:
        hard_rejects.append("room_to_target_less_than_3r")

    score = 0.0
    if sharp_approach:
        score += 1.5
        strength_factors.append("sharp_approach")
    if paranormal_bar_to_level:
        score += 1.5
        strength_factors.append("paranormal_bar_to_level")
    if atr_consumed_before_level is not None and atr_consumed_before_level >= 1.0:
        score += 2.0
        strength_factors.append("atr_consumed_before_level")
    elif atr_consumed_before_level is not None and atr_consumed_before_level >= 0.5:
        score += 1.0
        strength_factors.append("atr_consumed_before_level_warning")
    if far_retest:
        score += 2.0
        strength_factors.append("far_retest")
    if very_far_retest:
        score += 0.5
        strength_factors.append("very_far_retest")
    if no_consolidation_at_level:
        score += 1.0
        strength_factors.append("no_consolidation_at_level")
    if sweep_detected and returned_beyond_level:
        score += 2.0
        strength_factors.append("actual_lp_sweep_and_return")
    if strong_return:
        score += 1.0
        strength_factors.append("strong_return")
    if bool(raw_config.get("v_formation_without_accumulation")):
        score += 1.0
        strength_factors.append("v_formation_without_accumulation")

    lp_bias = sharp_approach or far_retest or (atr_consumed_before_level is not None and atr_consumed_before_level >= 0.5) or no_consolidation_at_level
    if lp_bias:
        strength_factors.append("lp_bias")
    if not sweep_detected:
        manual_review_needed.append("actual_sweep_return")
    if score < 3.0:
        weakness_factors.append("factor_count_below_setup_threshold")

    status = "reject" if hard_rejects else "trigger" if sweep_detected and returned_beyond_level and score >= 3.0 else "setup" if score >= 3.0 else "candidate"

    return {
        "symbol": symbol,
        "detector": "false_breakout_reversal",
        "status": status,
        "direction": direction,
        "bias": "false_breakout",
        "score": round(score, 2),
        "required_passed": not hard_rejects,
        "hard_rejects": hard_rejects,
        "strength_factors": strength_factors,
        "weakness_factors": weakness_factors,
        "manual_review_needed": manual_review_needed,
        "false_breakout_features": {
            "timeframe": timeframe,
            "level_price": level_price,
            "sharp_approach": sharp_approach,
            "approach_range_atr": round(approach_range_atr, 4),
            "paranormal_bar_to_level": paranormal_bar_to_level,
            "atr_consumed_before_level": round(atr_consumed_before_level, 4) if atr_consumed_before_level is not None else None,
            "bars_since_last_contact": bars_since_last_contact,
            "far_retest": far_retest,
            "no_consolidation_at_level": no_consolidation_at_level,
            "sweep_detected": sweep_detected,
            "returned_beyond_level": returned_beyond_level,
            "lp_tail_atr": round(lp_tail_abs / atr, 4),
            "stop_size_atr": round(stop_size_atr, 4) if stop_size_atr is not None else None,
            "room_to_target_r": round(room_to_target_r, 4) if room_to_target_r is not None else None,
            "lp_bias": lp_bias,
        },
    }


def has_volatility_fade(candles: list[Candle], trigger_index: int) -> bool:
    previous = candles[max(0, trigger_index - 5):trigger_index]
    if not previous:
        return False
    average_previous_range = sum(candle_range(candle) for candle in previous) / len(previous)
    if average_previous_range <= 0:
        return False

    trigger_range = candle_range(candles[trigger_index])
    if trigger_range <= VOLATILITY_FADE_RANGE_MULTIPLIER * average_previous_range:
        return True

    if trigger_index == 0:
        return False
    previous_trigger_range = candle_range(candles[trigger_index - 1])
    return (
        trigger_range <= VOLATILITY_FADE_TWO_BAR_MULTIPLIER * average_previous_range
        and previous_trigger_range <= VOLATILITY_FADE_TWO_BAR_MULTIPLIER * average_previous_range
    )


def detect_fixation(raw_config: dict[str, Any], symbol: str | None) -> dict[str, Any]:
    direction = str(raw_config.get("direction", "")).lower()
    if direction not in {"long", "short"}:
        raise DetectorInputError("fixations[].direction must be 'long' or 'short'")

    timeframe = str(raw_config.get("timeframe", "unknown"))
    level_price = as_float(raw_config.get("level_price"), "fixations[].level_price")
    atr = as_float(raw_config.get("atr"), "fixations[].atr")
    if atr <= 0:
        raise DetectorInputError("fixations[].atr must be > 0")

    tick_size = optional_float(raw_config.get("tick_size"), "fixations[].tick_size")
    candles = parse_candles(raw_config.get("candles"), "fixations[].candles")
    if not candles:
        raise DetectorInputError("fixations[].candles must not be empty")

    parent_setup_valid = bool(raw_config.get("parent_setup_valid", True))
    attempted_entry = bool(raw_config.get("attempted_entry", False))
    entry_price = optional_float(raw_config.get("entry_price"), "fixations[].entry_price")
    stop_price = optional_float(raw_config.get("stop_price"), "fixations[].stop_price")
    stop_reason = raw_config.get("stop_reason")
    calculated_stop_abs = CALCULATED_STOP_ATR * atr
    luft = fixation_luft(atr, tick_size)
    return_tolerance = max(tick_size * 2.0 if tick_size is not None else 0.0, FIXATION_RETURN_TOLERANCE_ATR * atr)

    hard_rejects: list[str] = []
    weakness_factors: list[str] = []
    strength_factors: list[str] = []
    manual_review_needed = ["return_attempt_meaningfulness", "stop_structure_quality"]

    if not parent_setup_valid:
        hard_rejects.append("parent_setup_invalid")

    full_bar_1_index = next(
        (index for index, candle in enumerate(candles) if full_bar_beyond_level(candle, direction, level_price, luft)),
        None,
    )

    return_attempt_index: int | None = None
    return_attempt_type: str | None = None
    full_bar_2_index: int | None = None

    if full_bar_1_index is None:
        hard_rejects.append("no_full_bar_beyond_level")
    else:
        strength_factors.append("full_bar_beyond_level")
        reference_extreme = candles[full_bar_1_index].high if direction == "long" else candles[full_bar_1_index].low
        for index in range(full_bar_1_index + 1, len(candles)):
            attempt_type = detect_return_attempt(
                candles[index],
                direction,
                level_price,
                return_tolerance,
                reference_extreme,
                calculated_stop_abs,
            )
            if attempt_type is not None:
                return_attempt_index = index
                return_attempt_type = attempt_type
                strength_factors.append("return_attempt")
                break

        if return_attempt_index is not None:
            for index in range(return_attempt_index + 1, len(candles)):
                if full_bar_beyond_level(candles[index], direction, level_price, luft):
                    full_bar_2_index = index
                    strength_factors.append("second_full_bar")
                    break

    if raw_config.get("podzhatie_on_return"):
        strength_factors.append("podzhatie_on_return")

    if full_bar_1_index is not None and return_attempt_index is None:
        if attempted_entry:
            hard_rejects.append("no_return_attempt_before_entry")
        else:
            weakness_factors.append("waiting_for_return_attempt")

    if return_attempt_index is not None and full_bar_2_index is None:
        weakness_factors.append("waiting_for_second_full_bar")

    volatility_fade = False
    if full_bar_2_index is not None:
        volatility_fade = has_volatility_fade(candles, full_bar_2_index)
        if volatility_fade:
            strength_factors.append("volatility_fade")
        else:
            weakness_factors.append("volatility_not_faded_yet")

    structural_stop_exists = stop_price is not None and isinstance(stop_reason, str) and bool(stop_reason.strip())
    if not structural_stop_exists:
        if attempted_entry or full_bar_2_index is not None:
            hard_rejects.append("no_clear_stop_after_fixation")
        manual_review_needed.append("stop_reason")

    stop_size_atr: float | None = None
    if stop_price is not None and entry_price is not None:
        if direction == "long":
            risk_abs = entry_price - stop_price
            stop_on_correct_side = stop_price < level_price
        else:
            risk_abs = stop_price - entry_price
            stop_on_correct_side = stop_price > level_price

        if risk_abs <= 0:
            hard_rejects.append("stop_on_wrong_side_of_entry")
        elif not stop_on_correct_side:
            hard_rejects.append("stop_on_wrong_side_of_level")
        else:
            stop_size_atr = risk_abs / atr
            if stop_size_atr > MAX_STOP_ATR:
                hard_rejects.append("stop_behind_return_attempt_too_large")
            else:
                strength_factors.append("structural_stop")

    if entry_price is not None and abs(entry_price - level_price) > calculated_stop_abs:
        hard_rejects.append("entry_too_far_from_level")

    if hard_rejects:
        status = "reject"
    elif full_bar_1_index is not None and return_attempt_index is not None and full_bar_2_index is not None and volatility_fade and structural_stop_exists:
        status = "trigger"
    elif full_bar_1_index is not None:
        status = "setup"
    else:
        status = "candidate"

    fixation_features = {
        "timeframe": timeframe,
        "level_price": level_price,
        "luft": luft,
        "return_tolerance": return_tolerance,
        "full_bar_1_time": candles[full_bar_1_index].time if full_bar_1_index is not None else None,
        "return_attempt_time": candles[return_attempt_index].time if return_attempt_index is not None else None,
        "return_attempt_type": return_attempt_type,
        "full_bar_2_time": candles[full_bar_2_index].time if full_bar_2_index is not None else None,
        "volatility_fade": volatility_fade,
        "entry_price": entry_price,
        "structural_stop_price": stop_price,
        "stop_reason": stop_reason if isinstance(stop_reason, str) else None,
        "stop_size_atr": stop_size_atr,
        "podzhatie_on_return": bool(raw_config.get("podzhatie_on_return")),
    }

    return {
        "symbol": symbol,
        "detector": "fixation_return_entry",
        "status": status,
        "direction": direction,
        "bias": direction,
        "score": None,
        "required_passed": not hard_rejects,
        "hard_rejects": hard_rejects,
        "strength_factors": strength_factors,
        "weakness_factors": weakness_factors,
        "manual_review_needed": manual_review_needed,
        "fixation_features": fixation_features,
    }


def bsu_bpu_luft(atr: float, tick_size: float | None, entry_price: float | None, stop_price: float | None) -> float:
    if entry_price is not None and stop_price is not None:
        stop_size = abs(entry_price - stop_price)
        luft = BSU_BPU_LUFT_STOP_MULTIPLIER * stop_size
    else:
        luft = BSU_BPU_LUFT_ATR_FALLBACK * atr
    if tick_size is None:
        return luft
    return max(tick_size * 2.0, luft)


def bpu_candidate_zones(candle: Candle, level_price: float, luft: float) -> list[str]:
    zones: list[str] = []
    if candle.low <= level_price + luft and candle.close >= level_price:
        zones.append("long")
    if candle.high >= level_price - luft and candle.close <= level_price:
        zones.append("short")
    return zones


def breaks_bpu_zone(candle: Candle, zone: str, level_price: float) -> bool:
    if zone == "long":
        return candle.close < level_price
    return candle.close > level_price


def bpu_touch_distance(candle: Candle, zone: str, level_price: float) -> float:
    if zone == "long":
        return abs(candle.low - level_price)
    return abs(candle.high - level_price)


def choose_bpu_zone(common_zones: set[str], direction: str) -> str:
    if direction in common_zones:
        return direction
    return sorted(common_zones)[0]


def find_bpu_sequence(candles: list[Candle], level_price: float, luft: float, direction: str) -> dict[str, Any]:
    bpu1_index: int | None = None
    bpu2_index: int | None = None
    zone: str | None = None
    reset_reason: str | None = None
    opposite_planes = False

    for index in range(len(candles) - 1):
        current_zones = bpu_candidate_zones(candles[index], level_price, luft)
        if not current_zones:
            continue

        next_zones = bpu_candidate_zones(candles[index + 1], level_price, luft)
        common_zones = set(current_zones).intersection(next_zones)
        if common_zones:
            bpu1_index = index
            bpu2_index = index + 1
            zone = choose_bpu_zone(common_zones, direction)
            break

        if next_zones:
            opposite_planes = True
            bpu1_index = index
            bpu2_index = index + 1
            zone = current_zones[0]
            break

        for current_zone in current_zones:
            if breaks_bpu_zone(candles[index + 1], current_zone, level_price):
                bpu1_index = index
                zone = current_zone
                reset_reason = "old_bpu1_invalid"
                break
        if reset_reason:
            break

    if bpu1_index is None and candles:
        for index, candle in enumerate(candles):
            candidate_zones = bpu_candidate_zones(candle, level_price, luft)
            if candidate_zones:
                bpu1_index = index
                zone = choose_bpu_zone(set(candidate_zones), direction)
                break

    return {
        "bpu1_index": bpu1_index,
        "bpu2_index": bpu2_index,
        "zone": zone,
        "same_zone": bpu1_index is not None and bpu2_index is not None and not opposite_planes,
        "consecutive": bpu1_index is not None and bpu2_index is not None and bpu2_index == bpu1_index + 1,
        "opposite_planes": opposite_planes,
        "bpu_reset_reason": reset_reason,
    }


def detect_bsu_bpu(raw_config: dict[str, Any], symbol: str | None) -> dict[str, Any]:
    direction = str(raw_config.get("direction", "unknown")).lower()
    if direction not in {"long", "short", "unknown"}:
        raise DetectorInputError("bsu_bpu[].direction must be 'long', 'short', or 'unknown'")

    timeframe = str(raw_config.get("timeframe", "unknown"))
    level_price = as_float(raw_config.get("level_price"), "bsu_bpu[].level_price")
    atr = as_float(raw_config.get("atr"), "bsu_bpu[].atr")
    if atr <= 0:
        raise DetectorInputError("bsu_bpu[].atr must be > 0")

    tick_size = optional_float(raw_config.get("tick_size"), "bsu_bpu[].tick_size")
    candles = parse_candles(raw_config.get("candles"), "bsu_bpu[].candles")
    if not candles:
        raise DetectorInputError("bsu_bpu[].candles must not be empty")

    entry_price = optional_float(raw_config.get("entry_price"), "bsu_bpu[].entry_price")
    stop_price = optional_float(raw_config.get("stop_price"), "bsu_bpu[].stop_price")
    next_level_price = optional_float(raw_config.get("next_level_price"), "bsu_bpu[].next_level_price")
    stop_reason = raw_config.get("stop_reason")
    bsu_time = raw_config.get("bsu_time")
    bsu_timeframe = raw_config.get("bsu_timeframe")

    sequence_luft = bsu_bpu_luft(atr, tick_size, None, None)
    entry_luft = bsu_bpu_luft(atr, tick_size, entry_price, stop_price)
    sequence = find_bpu_sequence(candles, level_price, sequence_luft, direction)
    bpu1_index = sequence["bpu1_index"]
    bpu2_index = sequence["bpu2_index"]
    zone = sequence["zone"]

    hard_rejects: list[str] = []
    weakness_factors: list[str] = []
    strength_factors: list[str] = []
    manual_review_needed = ["bsu_level_quality", "same_price_zone_quality"]

    if not isinstance(bsu_time, str) or not bsu_time.strip():
        hard_rejects.append("bsu_level_missing")
    else:
        strength_factors.append("bsu_level_exists")

    if direction in {"long", "short"} and zone is not None and zone != direction:
        hard_rejects.append("direction_conflicts_with_daily_scenario")

    if sequence["opposite_planes"]:
        hard_rejects.extend(["bpu1_bpu2_opposite_sides", "not_same_plane"])
    elif bpu1_index is not None and bpu2_index is not None:
        strength_factors.extend(["bpu1", "bpu2", "same_zone", "consecutive"])
    elif bpu1_index is not None:
        strength_factors.append("bpu1_candidate")
        if not sequence["bpu_reset_reason"]:
            weakness_factors.append("waiting_for_bpu2")
    else:
        hard_rejects.append("no_bpu1_candidate")

    if sequence["bpu_reset_reason"]:
        weakness_factors.extend(["level_break_after_bpu1", sequence["bpu_reset_reason"]])

    opposing_podzhatie = bool(raw_config.get("opposing_podzhatie"))
    confirmation_after_podzhatie = bool(raw_config.get("equalizing_bar") or raw_config.get("lp_confirmation"))
    if opposing_podzhatie:
        weakness_factors.append("opposing_podzhatie")
        if not confirmation_after_podzhatie:
            hard_rejects.append("need_equalizing_bar_or_lp_confirmation")

    risk_abs: float | None = None
    room_to_target_r: float | None = None
    drift_atr: float | None = None
    if entry_price is not None:
        drift_atr = abs(entry_price - level_price) / atr
        if drift_atr > BSU_BPU_MAX_DRIFT_ATR:
            hard_rejects.append("price_moved_too_far_before_entry")

    if entry_price is not None and stop_price is not None:
        if direction == "long":
            risk_abs = entry_price - stop_price
            stop_on_correct_side = stop_price < level_price
        elif direction == "short":
            risk_abs = stop_price - entry_price
            stop_on_correct_side = stop_price > level_price
        else:
            risk_abs = abs(entry_price - stop_price)
            stop_on_correct_side = True

        if risk_abs <= 0:
            hard_rejects.append("stop_on_wrong_side_of_entry")
        elif not stop_on_correct_side:
            hard_rejects.append("stop_on_wrong_side_of_level")
        elif risk_abs / atr > MAX_STOP_ATR:
            hard_rejects.append("stop_not_short_or_logical")
        else:
            strength_factors.append("short_logical_stop")

    if not isinstance(stop_reason, str) or not stop_reason.strip():
        manual_review_needed.append("stop_reason")
        if bpu2_index is not None and not sequence["opposite_planes"]:
            hard_rejects.append("stop_not_short_or_logical")

    if next_level_price is not None and entry_price is not None and risk_abs is not None and risk_abs > 0:
        if direction == "long" and next_level_price <= entry_price:
            hard_rejects.append("next_level_not_in_trade_direction")
        elif direction == "short" and next_level_price >= entry_price:
            hard_rejects.append("next_level_not_in_trade_direction")
        else:
            room_to_target_r = abs(next_level_price - entry_price) / risk_abs
            if room_to_target_r < MIN_TARGET_R:
                hard_rejects.append("room_to_target_less_than_3r")
            else:
                strength_factors.append("room_to_target_at_least_3r")

    if bpu2_index is not None and zone is not None:
        bpu2_distance = bpu_touch_distance(candles[bpu2_index], zone, level_price)
        if bpu2_distance > 0 and bpu2_distance <= sequence_luft:
            strength_factors.append("bpu2_missed_by_luft")
        strength_factors.append("limit_with_luft")

    if hard_rejects:
        status = "reject"
    elif bpu1_index is not None and bpu2_index is not None:
        status = "trigger"
    elif bpu1_index is not None:
        status = "setup"
    else:
        status = "candidate"

    limit_entry_price: float | None = None
    if zone == "long":
        limit_entry_price = level_price + entry_luft
    elif zone == "short":
        limit_entry_price = level_price - entry_luft

    bsu_bpu_features = {
        "timeframe": timeframe,
        "level_price": level_price,
        "bsu_time": bsu_time if isinstance(bsu_time, str) else None,
        "bsu_timeframe": bsu_timeframe if isinstance(bsu_timeframe, str) else None,
        "bpu1_time": candles[bpu1_index].time if bpu1_index is not None else None,
        "bpu2_time": candles[bpu2_index].time if bpu2_index is not None else None,
        "zone": zone,
        "same_zone": bool(sequence["same_zone"]),
        "consecutive": bool(sequence["consecutive"]),
        "touch_distance_bpu1": bpu_touch_distance(candles[bpu1_index], zone, level_price) if bpu1_index is not None and zone is not None else None,
        "touch_distance_bpu2": bpu_touch_distance(candles[bpu2_index], zone, level_price) if bpu2_index is not None and zone is not None else None,
        "luft": entry_luft,
        "sequence_luft": sequence_luft,
        "limit_entry_price": limit_entry_price,
        "entry_price": entry_price,
        "stop_price": stop_price,
        "stop_reason": stop_reason if isinstance(stop_reason, str) else None,
        "bpu_reset_reason": sequence["bpu_reset_reason"],
        "opposing_podzhatie": opposing_podzhatie,
        "drift_atr": drift_atr,
        "room_to_target_r": room_to_target_r,
    }

    return {
        "symbol": symbol,
        "detector": "bsu_bpu_entry",
        "status": status,
        "direction": direction,
        "bias": zone or direction,
        "score": None,
        "required_passed": not hard_rejects,
        "hard_rejects": hard_rejects,
        "strength_factors": strength_factors,
        "weakness_factors": weakness_factors,
        "manual_review_needed": manual_review_needed,
        "bsu_bpu_features": bsu_bpu_features,
    }


def target_price(entry_price: float, risk_abs: float, direction: str, multiple: float) -> float:
    if direction == "long":
        return entry_price + multiple * risk_abs
    return entry_price - multiple * risk_abs


def validate_risk(raw_config: dict[str, Any], symbol: str | None) -> dict[str, Any]:
    direction = str(raw_config.get("direction", "")).lower()
    if direction not in {"long", "short"}:
        raise DetectorInputError("risk.direction must be 'long' or 'short'")

    atr = as_float(raw_config.get("atr"), "risk.atr")
    if atr <= 0:
        raise DetectorInputError("risk.atr must be > 0")

    adjusted_atr = optional_float(raw_config.get("adjusted_atr"), "risk.adjusted_atr")
    used_atr = adjusted_atr if adjusted_atr is not None else atr
    if used_atr <= 0:
        raise DetectorInputError("risk.adjusted_atr must be > 0")

    entry_price = as_float(raw_config.get("entry_price"), "risk.entry_price")
    stop_price = as_float(raw_config.get("stop_price"), "risk.stop_price")
    next_level_price = optional_float(raw_config.get("next_level_price"), "risk.next_level_price")
    level_price = optional_float(raw_config.get("level_price"), "risk.level_price")
    account_risk_money = optional_float(raw_config.get("account_risk_money"), "risk.account_risk_money")

    if direction == "long":
        risk_abs = entry_price - stop_price
        next_level_in_direction = next_level_price is None or next_level_price > entry_price
        stop_on_level_side = level_price is None or stop_price < level_price
    else:
        risk_abs = stop_price - entry_price
        next_level_in_direction = next_level_price is None or next_level_price < entry_price
        stop_on_level_side = level_price is None or stop_price > level_price

    calculated_stop_abs = CALCULATED_STOP_ATR * used_atr
    technical_stop_atr = risk_abs / used_atr if used_atr else 0.0
    technical_vs_calculated = risk_abs / calculated_stop_abs if calculated_stop_abs else 0.0

    reject_reasons: list[str] = []
    warnings: list[str] = []
    strength_factors: list[str] = []
    manual_review_needed = ["technical_stop_structure", "next_level_quality"]

    if risk_abs <= 0:
        reject_reasons.append("stop_on_wrong_side_of_entry")

    if level_price is not None and not stop_on_level_side:
        reject_reasons.append("stop_on_wrong_side_of_level")

    if raw_config.get("atr_unstable") and adjusted_atr is None:
        reject_reasons.append("atr_unstable_without_adjusted_atr")
    elif raw_config.get("atr_unstable"):
        warnings.append("using_adjusted_atr")

    stop_reason = raw_config.get("stop_reason")
    if not isinstance(stop_reason, str) or not stop_reason.strip():
        warnings.append("stop_reason_missing")
        manual_review_needed.append("stop_reason")

    if technical_stop_atr > MAX_STOP_ATR:
        reject_reasons.append("technical_stop_too_large")
    elif technical_stop_atr > WARNING_STOP_ATR:
        warnings.append("technical_stop_between_10_and_13pct_atr")
    elif technical_stop_atr <= 0.07 and risk_abs > 0:
        strength_factors.append("compact_stop_ideal_if_structure_is_real")

    room_to_next_level_r = None
    if next_level_price is None:
        warnings.append("next_level_price_missing")
        manual_review_needed.append("room_to_target")
    elif not next_level_in_direction:
        reject_reasons.append("next_level_not_in_trade_direction")
    elif risk_abs > 0:
        room_to_next_level_r = abs(next_level_price - entry_price) / risk_abs
        if room_to_next_level_r < MIN_TARGET_R:
            reject_reasons.append("room_to_target_less_than_3r")
        elif room_to_next_level_r < PREFERRED_TARGET_R:
            warnings.append("room_to_target_between_3r_and_4r")
        else:
            strength_factors.append("room_to_target_at_least_4r")

    if account_risk_money is not None and account_risk_money <= 0:
        reject_reasons.append("account_risk_money_must_be_positive")

    position_size_units = None
    notional = None
    if account_risk_money is not None and risk_abs > 0:
        position_size_units = account_risk_money / risk_abs
        notional = position_size_units * entry_price

    if reject_reasons:
        status = "reject"
    elif warnings:
        status = "warn"
    else:
        status = "pass"

    risk_validation = {
        "status": status,
        "atr": atr,
        "used_atr": used_atr,
        "calculated_stop_abs": calculated_stop_abs,
        "entry_price": entry_price,
        "stop_price": stop_price,
        "technical_stop_abs": risk_abs,
        "technical_stop_atr": technical_stop_atr,
        "technical_vs_calculated": technical_vs_calculated,
        "room_to_next_level_r": room_to_next_level_r,
        "target_3r": target_price(entry_price, risk_abs, direction, 3.0) if risk_abs > 0 else None,
        "target_4r": target_price(entry_price, risk_abs, direction, 4.0) if risk_abs > 0 else None,
        "target_next_level": next_level_price,
        "position_size_units": position_size_units,
        "notional": notional,
        "warnings": warnings,
        "reject_reasons": reject_reasons,
    }

    return {
        "symbol": symbol,
        "detector": "risk_stop_take",
        "status": status,
        "direction": direction,
        "score": None,
        "required_passed": not reject_reasons,
        "hard_rejects": reject_reasons,
        "strength_factors": strength_factors,
        "weakness_factors": warnings,
        "manual_review_needed": manual_review_needed,
        "risk_validation": risk_validation,
    }


def validate_hard_gate(raw_config: dict[str, Any], symbol: str | None) -> dict[str, Any]:
    direction = str(raw_config.get("direction", "")).lower()
    if direction and direction not in {"long", "short", "none", "unknown"}:
        raise DetectorInputError("hard_gates[].direction must be 'long', 'short', 'none', or 'unknown'")

    scenario_family = str(raw_config.get("scenario_family", "")).lower()
    level_price = optional_float(raw_config.get("level_price"), "hard_gates[].level_price")
    entry_price = optional_float(raw_config.get("entry_price"), "hard_gates[].entry_price")
    stop_price = optional_float(raw_config.get("stop_price"), "hard_gates[].stop_price")
    next_level_price = optional_float(raw_config.get("next_level_price"), "hard_gates[].next_level_price")
    provided_room_to_target_r = optional_float(raw_config.get("room_to_target_r"), "hard_gates[].room_to_target_r")

    has_level = bool(raw_config.get("has_level", level_price is not None)) and not bool(raw_config.get("level_unclear"))
    has_direction = direction in {"long", "short"}
    has_stop_before_entry = bool(raw_config.get("has_stop_before_entry", stop_price is not None))
    scenario_complete = scenario_family in {"breakout", "false_breakout", "rebound", "continuation"}
    no_trade_gates = string_list(raw_config.get("no_trade_gates"), "hard_gates[].no_trade_gates")
    discipline_violations = string_list(raw_config.get("discipline_violations"), "hard_gates[].discipline_violations")

    room_to_target_r = provided_room_to_target_r
    room_rejects: list[str] = []
    if room_to_target_r is None and has_direction:
        room_to_target_r, room_rejects = room_to_target(entry_price, stop_price, next_level_price, direction)

    missing_inputs: list[str] = []
    hard_rejects: list[str] = []
    weakness_factors: list[str] = []
    strength_factors: list[str] = []
    manual_review_needed = ["visual_level_clarity", "plan_change_emotion", "homework_or_in_play"]

    if has_level:
        strength_factors.append("has_level")
    else:
        missing_inputs.append("level")
        hard_rejects.append("no_clear_level")

    if has_direction:
        strength_factors.append("has_direction")
    else:
        missing_inputs.append("direction")

    if has_stop_before_entry:
        strength_factors.append("has_stop_before_entry")
    else:
        missing_inputs.append("stop_before_entry")
        hard_rejects.append("no_logical_stop")

    if scenario_complete:
        strength_factors.append("scenario_complete")
    else:
        missing_inputs.append("scenario_family")

    hard_rejects.extend(room_rejects)
    if room_to_target_r is None:
        missing_inputs.append("room_to_target_r")
    elif room_to_target_r < MIN_TARGET_R:
        hard_rejects.append("room_to_target_less_than_3r")
    else:
        strength_factors.append("room_to_target_at_least_3r")

    if bool(raw_config.get("conflicting_daily_execution")):
        hard_rejects.append("conflicting_daily_and_execution_logic")
    if bool(raw_config.get("reverse_without_new_scenario") or raw_config.get("plan_reversal_without_new_scenario")):
        hard_rejects.append("reverse_without_new_scenario")
    if bool(raw_config.get("chart_unclear") or raw_config.get("sawed_chart")):
        hard_rejects.append("chart_unclear_or_sawed")

    hard_rejects.extend(no_trade_gates)
    if no_trade_gates:
        weakness_factors.append("active_no_trade_gate")
    if discipline_violations:
        weakness_factors.append("discipline_violations_present")
        manual_review_needed.append("discipline_state")

    status = "reject" if hard_rejects else "warn" if missing_inputs or discipline_violations else "pass"
    permission_features = {
        "has_level": has_level,
        "has_direction": has_direction,
        "has_stop_before_entry": has_stop_before_entry,
        "room_to_target_r": round(room_to_target_r, 4) if room_to_target_r is not None else None,
        "scenario_complete": scenario_complete,
        "no_trade_gate_count": len(no_trade_gates),
        "discipline_violation_count": len(discipline_violations),
        "missing_inputs": missing_inputs,
    }

    return {
        "symbol": symbol,
        "detector": "hard_gates_and_permission",
        "status": status,
        "permission_status": status,
        "direction": direction if has_direction else "unknown",
        "bias": "any",
        "score": None,
        "required_passed": not hard_rejects,
        "hard_rejects": hard_rejects,
        "missing_inputs": missing_inputs,
        "strength_factors": strength_factors,
        "weakness_factors": weakness_factors,
        "manual_review_needed": manual_review_needed,
        "execution_allowed": False,
        "permission_features": permission_features,
    }


def validate_market_mechanics(raw_config: dict[str, Any], symbol: str | None) -> dict[str, Any]:
    pressure_side = str(raw_config.get("pressure_side", "unknown")).lower()
    if pressure_side not in {"buyers", "sellers", "longs", "shorts", "none", "unknown"}:
        raise DetectorInputError("market_mechanics[].pressure_side is unknown")

    trapped_side = str(raw_config.get("trapped_side", "none")).lower()
    if trapped_side not in {"buyers", "sellers", "longs", "shorts", "none", "unknown"}:
        raise DetectorInputError("market_mechanics[].trapped_side is unknown")

    forced_exit_probability = optional_float(raw_config.get("forced_exit_probability"), "market_mechanics[].forced_exit_probability")
    range_accumulation_duration = optional_int(raw_config.get("range_accumulation_duration"), "market_mechanics[].range_accumulation_duration") or 0
    volume_expansion = bool(raw_config.get("volume_expansion"))
    failed_reaction = bool(raw_config.get("failed_reaction"))
    expected_pressure_source = str(raw_config.get("expected_pressure_source", "")).strip()

    hard_rejects: list[str] = []
    weakness_factors: list[str] = []
    strength_factors: list[str] = []
    manual_review_needed = ["large_player_intent", "accumulation_or_distribution", "forced_pressure_quality"]

    identifiable_pressure = bool(expected_pressure_source) or pressure_side not in {"none", "unknown"} or trapped_side not in {"none", "unknown"} or failed_reaction
    if not identifiable_pressure:
        hard_rejects.append("no_identifiable_pressure")
    else:
        strength_factors.append("identifiable_pressure_source")

    if range_accumulation_duration > 0:
        strength_factors.append("range_accumulation_context")
    if volume_expansion:
        strength_factors.append("volume_expansion")
    if failed_reaction:
        strength_factors.append("failed_reaction")
    if trapped_side not in {"none", "unknown"}:
        strength_factors.append("trapped_side")
    if forced_exit_probability is not None and forced_exit_probability >= 0.5:
        strength_factors.append("forced_exit_probability")

    if bool(raw_config.get("imbalance_exhausted")):
        hard_rejects.append("participant_imbalance_exhausted")
    if bool(raw_config.get("order_book_claim")) and not bool(raw_config.get("order_book_data_available")):
        hard_rejects.append("order_book_claim_without_data")
    if forced_exit_probability is None:
        weakness_factors.append("forced_exit_probability_missing")

    mechanics_confidence = len(strength_factors) - len(weakness_factors)
    status = "reject" if hard_rejects else "setup" if mechanics_confidence >= 2 else "candidate"

    return {
        "symbol": symbol,
        "detector": "market_mechanics_context",
        "status": status,
        "direction": "unknown",
        "bias": pressure_side,
        "score": float(mechanics_confidence),
        "required_passed": not hard_rejects,
        "hard_rejects": hard_rejects,
        "strength_factors": strength_factors,
        "weakness_factors": weakness_factors,
        "manual_review_needed": manual_review_needed,
        "mechanics_features": {
            "pressure_side": pressure_side,
            "trapped_side": trapped_side,
            "forced_exit_probability": forced_exit_probability,
            "range_accumulation_duration": range_accumulation_duration,
            "volume_expansion": volume_expansion,
            "failed_reaction": failed_reaction,
            "participant_pressure": expected_pressure_source or None,
            "mechanics_confidence": mechanics_confidence,
        },
    }


def detect_breakout_failure(raw_config: dict[str, Any], symbol: str | None) -> dict[str, Any]:
    direction = str(raw_config.get("direction", "")).lower()
    if direction not in {"long", "short"}:
        raise DetectorInputError("breakout_failures[].direction must be 'long' or 'short'")

    timeframe = str(raw_config.get("timeframe", "unknown"))
    level_price = as_float(raw_config.get("level_price"), "breakout_failures[].level_price")
    candles = parse_candles(raw_config.get("candles"), "breakout_failures[].candles")
    if not candles:
        raise DetectorInputError("breakout_failures[].candles must not be empty")

    breakout_candle_index = optional_int(raw_config.get("breakout_candle_index"), "breakout_failures[].breakout_candle_index")
    if breakout_candle_index is None:
        breakout_candle_index = max(0, len(candles) - 2)
    if breakout_candle_index < 0 or breakout_candle_index >= len(candles):
        raise DetectorInputError("breakout_failures[].breakout_candle_index is out of range")

    breakout_candle = candles[breakout_candle_index]
    latest = candles[-1]
    close_back_inside = bool(raw_config.get("close_back_inside"))
    if direction == "long":
        close_back_inside = close_back_inside or latest.close < level_price
        adverse_extreme_update = bool(raw_config.get("adverse_extreme_update")) or latest.low < breakout_candle.low
    else:
        close_back_inside = close_back_inside or latest.close > level_price
        adverse_extreme_update = bool(raw_config.get("adverse_extreme_update")) or latest.high > breakout_candle.high

    no_impulse_after_break = bool(raw_config.get("no_impulse_after_break"))
    failed_fixation = bool(raw_config.get("failed_fixation")) or (close_back_inside and not bool(raw_config.get("second_fixation_bar")))
    reclassification_candidate = close_back_inside and (adverse_extreme_update or no_impulse_after_break)

    hard_rejects: list[str] = []
    strength_factors: list[str] = []
    weakness_factors: list[str] = []
    manual_review_needed = ["reversal_fuel", "normal_retest_or_true_invalidation"]

    if failed_fixation:
        strength_factors.append("failed_fixation")
    if close_back_inside:
        strength_factors.append("close_back_inside")
    if adverse_extreme_update:
        strength_factors.append("adverse_extreme_update")
    if no_impulse_after_break:
        strength_factors.append("no_impulse_after_break")
    if reclassification_candidate:
        strength_factors.append("reclassification_candidate")

    if close_back_inside and bool(raw_config.get("chasing_after_failure")):
        hard_rejects.append("chasing_after_close_back_through_level")
    if bool(raw_config.get("stop_widened") or raw_config.get("ignored_invalidation")):
        hard_rejects.append("ignoring_invalidation_and_widening_stop")
    if bool(raw_config.get("continuation_without_new_setup") or raw_config.get("treat_as_continuation_without_new_setup")):
        hard_rejects.append("failed_breakout_continuation_without_new_setup")

    if not close_back_inside and not failed_fixation:
        weakness_factors.append("failure_not_confirmed")

    status = "reject" if hard_rejects else "warn" if failed_fixation or close_back_inside else "candidate"
    reclassification_hint = "false_breakout_or_reversal_review" if reclassification_candidate else None

    return {
        "symbol": symbol,
        "detector": "breakout_failure",
        "status": status,
        "failure_status": status,
        "direction": direction,
        "bias": "false_breakout" if reclassification_candidate else "breakout",
        "score": None,
        "required_passed": not hard_rejects,
        "hard_rejects": hard_rejects,
        "reject_reason": hard_rejects,
        "reclassification_hint": reclassification_hint,
        "strength_factors": strength_factors,
        "weakness_factors": weakness_factors,
        "manual_review_needed": manual_review_needed,
        "breakout_failure_features": {
            "timeframe": timeframe,
            "level_price": level_price,
            "breakout_candle_time": breakout_candle.time,
            "latest_candle_time": latest.time,
            "failed_fixation": failed_fixation,
            "close_back_inside": close_back_inside,
            "adverse_extreme_update": adverse_extreme_update,
            "no_impulse_after_break": no_impulse_after_break,
            "reclassification_candidate": reclassification_candidate,
        },
    }


def detect_rebound_model(raw_config: dict[str, Any], symbol: str | None) -> dict[str, Any]:
    direction = str(raw_config.get("direction", "")).lower()
    if direction not in {"long", "short"}:
        raise DetectorInputError("rebounds[].direction must be 'long' or 'short'")

    timeframe = str(raw_config.get("timeframe", "unknown"))
    level_price = as_float(raw_config.get("level_price"), "rebounds[].level_price")
    atr = as_float(raw_config.get("atr"), "rebounds[].atr")
    if atr <= 0:
        raise DetectorInputError("rebounds[].atr must be > 0")

    entry_price = optional_float(raw_config.get("entry_price"), "rebounds[].entry_price")
    stop_price = optional_float(raw_config.get("stop_price"), "rebounds[].stop_price")
    next_level_price = optional_float(raw_config.get("next_level_price"), "rebounds[].next_level_price")
    provided_target_room_r = optional_float(raw_config.get("target_room_r"), "rebounds[].target_room_r")
    reaction_bar_count = optional_int(raw_config.get("reaction_bar_count"), "rebounds[].reaction_bar_count") or 0
    entry_model = str(raw_config.get("entry_model", "first_impulse")).lower()

    level_valid = bool(raw_config.get("level_valid", True))
    level_strong = bool(raw_config.get("level_strong", True))
    level_reaction = bool(raw_config.get("reaction_confirmed")) or reaction_bar_count > 0
    confirmation_after_rebound = bool(raw_config.get("confirmation_after_rebound"))
    secondary_movement_available = bool(raw_config.get("secondary_movement_available"))

    stop_behind_reaction = False
    stop_size_atr: float | None = None
    if entry_price is not None and stop_price is not None:
        if direction == "long":
            risk_abs = entry_price - stop_price
            stop_behind_reaction = stop_price < level_price
        else:
            risk_abs = stop_price - entry_price
            stop_behind_reaction = stop_price > level_price
        if risk_abs > 0:
            stop_size_atr = risk_abs / atr

    target_room_r = provided_target_room_r
    room_rejects: list[str] = []
    if target_room_r is None:
        target_room_r, room_rejects = room_to_target(entry_price, stop_price, next_level_price, direction)

    first_impulse_acceptable_stop = bool(raw_config.get("first_impulse_acceptable_stop", stop_behind_reaction and stop_size_atr is not None and stop_size_atr <= MAX_STOP_ATR))

    hard_rejects: list[str] = []
    strength_factors: list[str] = []
    weakness_factors: list[str] = []
    manual_review_needed = ["first_impulse_quality", "secondary_movement_statistics", "reaction_quality"]

    if not level_valid or not level_strong:
        hard_rejects.append("weak_local_level_without_context")
    else:
        strength_factors.append("strong_usable_level")
    if level_reaction:
        strength_factors.append("level_reaction")
    else:
        hard_rejects.append("touch_without_reaction")
    if confirmation_after_rebound:
        strength_factors.append("confirmation_after_rebound")
    else:
        weakness_factors.append("confirmation_missing")
    if secondary_movement_available:
        strength_factors.append("secondary_movement_available")
    if stop_behind_reaction:
        strength_factors.append("stop_behind_reaction")
    if not first_impulse_acceptable_stop:
        hard_rejects.append("first_impulse_has_no_acceptable_stop")

    hard_rejects.extend(room_rejects)
    if target_room_r is None:
        weakness_factors.append("target_room_unknown")
    elif target_room_r < MIN_TARGET_R:
        if secondary_movement_available:
            hard_rejects.append("secondary_movement_arrives_after_room_is_gone")
        else:
            hard_rejects.append("room_to_target_less_than_3r")
    else:
        strength_factors.append("target_room_at_least_3r")

    status = "reject" if hard_rejects else "trigger" if confirmation_after_rebound and stop_behind_reaction and target_room_r is not None and target_room_r >= MIN_TARGET_R else "setup" if level_reaction else "candidate"

    return {
        "symbol": symbol,
        "detector": "rebound_models",
        "status": status,
        "rebound_status": status,
        "direction": direction,
        "bias": direction,
        "score": None,
        "required_passed": not hard_rejects,
        "hard_rejects": hard_rejects,
        "strength_factors": strength_factors,
        "weakness_factors": weakness_factors,
        "manual_review_needed": manual_review_needed,
        "rebound_features": {
            "timeframe": timeframe,
            "level_price": level_price,
            "entry_model": entry_model,
            "level_reaction": level_reaction,
            "reaction_bar_count": reaction_bar_count,
            "first_impulse_acceptable_stop": first_impulse_acceptable_stop,
            "secondary_movement_available": secondary_movement_available,
            "confirmation_after_rebound": confirmation_after_rebound,
            "stop_behind_reaction": stop_behind_reaction,
            "stop_size_atr": round(stop_size_atr, 4) if stop_size_atr is not None else None,
            "target_room_r": round(target_room_r, 4) if target_room_r is not None else None,
        },
    }


def validate_workflow_review(raw_config: dict[str, Any], symbol: str | None) -> dict[str, Any]:
    detector_output_has_source_refs = bool(raw_config.get("detector_output_has_source_refs"))
    casebook_coverage = optional_int(raw_config.get("casebook_coverage"), "workflow_reviews[].casebook_coverage") or 0
    manual_review_queue_count = optional_int(raw_config.get("manual_review_queue_count"), "workflow_reviews[].manual_review_queue_count") or 0
    unresolved_warning_count = optional_int(raw_config.get("unresolved_warning_count"), "workflow_reviews[].unresolved_warning_count") or 0
    sampled_large_card_count = optional_int(raw_config.get("sampled_large_card_count"), "workflow_reviews[].sampled_large_card_count") or 0
    spec_version = str(raw_config.get("spec_version", "")).strip()
    favorable_example_count = optional_int(raw_config.get("favorable_example_count"), "workflow_reviews[].favorable_example_count") or 0

    hard_rejects: list[str] = []
    strength_factors: list[str] = []
    weakness_factors: list[str] = []
    manual_review_needed = ["example_quality", "ambiguous_rule_labels", "data_mining_risk"]

    if detector_output_has_source_refs:
        strength_factors.append("source_refs_preserved")
    else:
        hard_rejects.append("detector_output_without_source_refs")

    if casebook_coverage > 0:
        strength_factors.append("casebook_coverage_present")
    else:
        weakness_factors.append("casebook_coverage_missing")
    if sampled_large_card_count > 0:
        strength_factors.append("large_cards_sampled")
    else:
        weakness_factors.append("large_card_sampling_missing")
    if spec_version:
        strength_factors.append("versioned_spec_status")
    else:
        weakness_factors.append("spec_version_missing")

    if bool(raw_config.get("promotion_requested")) and favorable_example_count <= 1:
        hard_rejects.append("spec_promoted_after_only_one_favorable_example")
    if bool(raw_config.get("runtime_trading_enabled")):
        hard_rejects.append("runtime_trading_enabled_from_draft_docs")
    if not bool(raw_config.get("backtest_harness_separate", True)):
        hard_rejects.append("backtest_harness_not_separate")
    if manual_review_queue_count > 0:
        weakness_factors.append("manual_review_queue_open")
    if unresolved_warning_count > 0:
        weakness_factors.append("unresolved_warnings_open")

    status = "reject" if hard_rejects else "warn" if weakness_factors else "pass"

    return {
        "symbol": symbol,
        "detector": "workflow_review_data_quality",
        "status": status,
        "review_status": status,
        "direction": "unknown",
        "bias": "any",
        "score": None,
        "required_passed": not hard_rejects,
        "hard_rejects": hard_rejects,
        "strength_factors": strength_factors,
        "weakness_factors": weakness_factors,
        "manual_review_needed": manual_review_needed,
        "workflow_features": {
            "casebook_coverage": casebook_coverage,
            "manual_review_queue_count": manual_review_queue_count,
            "unresolved_warning_count": unresolved_warning_count,
            "sampled_large_card_count": sampled_large_card_count,
            "spec_version": spec_version,
            "detector_output_has_source_refs": detector_output_has_source_refs,
            "backtest_harness_separate": bool(raw_config.get("backtest_harness_separate", True)),
            "runtime_trading_enabled": bool(raw_config.get("runtime_trading_enabled")),
        },
    }


def load_input(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        parsed = json.load(handle)
    if not isinstance(parsed, dict):
        raise DetectorInputError("input JSON must be an object")
    return parsed


def build_output(config: dict[str, Any]) -> dict[str, Any]:
    symbol = config.get("symbol")
    symbol_value = str(symbol) if symbol is not None else None
    results: dict[str, Any] = {
        "symbol": symbol_value,
        "generated_by": "knowledge_bot/detector_prototype.py",
        "detectors": {},
    }

    raw_hard_gates = config.get("hard_gates", [])
    if raw_hard_gates:
        if not isinstance(raw_hard_gates, list):
            raise DetectorInputError("hard_gates must be a list")
        results["detectors"]["hard_gates_and_permission"] = [
            validate_hard_gate(raw_gate, symbol_value) for raw_gate in raw_hard_gates
        ]

    raw_retests = config.get("retests", [])
    if raw_retests:
        if not isinstance(raw_retests, list):
            raise DetectorInputError("retests must be a list")
        results["detectors"]["near_far_retest"] = [
            detect_retest(raw_retest, symbol_value) for raw_retest in raw_retests
        ]

    raw_levels = config.get("levels", [])
    if raw_levels:
        if not isinstance(raw_levels, list):
            raise DetectorInputError("levels must be a list")
        results["detectors"]["level_selection_strength"] = [
            validate_level_strength(raw_level, symbol_value) for raw_level in raw_levels
        ]

    raw_trends = config.get("trends", [])
    if raw_trends:
        if not isinstance(raw_trends, list):
            raise DetectorInputError("trends must be a list")
        results["detectors"]["trend_context"] = [
            validate_trend_context(raw_trend, symbol_value) for raw_trend in raw_trends
        ]

    raw_market_mechanics = config.get("market_mechanics", [])
    if raw_market_mechanics:
        if not isinstance(raw_market_mechanics, list):
            raise DetectorInputError("market_mechanics must be a list")
        results["detectors"]["market_mechanics_context"] = [
            validate_market_mechanics(raw_mechanics, symbol_value) for raw_mechanics in raw_market_mechanics
        ]

    raw_tbx_entries = config.get("tbx_entries", [])
    if raw_tbx_entries:
        if not isinstance(raw_tbx_entries, list):
            raise DetectorInputError("tbx_entries must be a list")
        results["detectors"]["tbx_entry_models"] = [
            validate_tbx_entry_model(raw_entry, symbol_value) for raw_entry in raw_tbx_entries
        ]

    raw_formations = config.get("formations", [])
    if raw_formations:
        if not isinstance(raw_formations, list):
            raise DetectorInputError("formations must be a list")
        results["detectors"]["v_u_formations"] = [
            validate_v_u_formation(raw_formation, symbol_value) for raw_formation in raw_formations
        ]

    raw_tail_bars = config.get("tail_bars", [])
    if raw_tail_bars:
        if not isinstance(raw_tail_bars, list):
            raise DetectorInputError("tail_bars must be a list")
        results["detectors"]["tail_bars_two_sided_limit"] = [
            validate_tail_bars(raw_tail_bar, symbol_value) for raw_tail_bar in raw_tail_bars
        ]

    raw_fixations = config.get("fixations", [])
    if raw_fixations:
        if not isinstance(raw_fixations, list):
            raise DetectorInputError("fixations must be a list")
        results["detectors"]["fixation_return_entry"] = [
            detect_fixation(raw_fixation, symbol_value) for raw_fixation in raw_fixations
        ]

    raw_bsu_bpu = config.get("bsu_bpu", [])
    if raw_bsu_bpu:
        if not isinstance(raw_bsu_bpu, list):
            raise DetectorInputError("bsu_bpu must be a list")
        results["detectors"]["bsu_bpu_entry"] = [
            detect_bsu_bpu(raw_entry, symbol_value) for raw_entry in raw_bsu_bpu
        ]

    raw_breakouts = config.get("breakouts", [])
    if raw_breakouts:
        if not isinstance(raw_breakouts, list):
            raise DetectorInputError("breakouts must be a list")
        results["detectors"]["breakout_preconditions"] = [
            detect_breakout_preconditions(raw_breakout, symbol_value) for raw_breakout in raw_breakouts
        ]

    raw_breakout_failures = config.get("breakout_failures", [])
    if raw_breakout_failures:
        if not isinstance(raw_breakout_failures, list):
            raise DetectorInputError("breakout_failures must be a list")
        results["detectors"]["breakout_failure"] = [
            detect_breakout_failure(raw_failure, symbol_value) for raw_failure in raw_breakout_failures
        ]

    raw_false_breakouts = config.get("false_breakouts", [])
    if raw_false_breakouts:
        if not isinstance(raw_false_breakouts, list):
            raise DetectorInputError("false_breakouts must be a list")
        results["detectors"]["false_breakout_reversal"] = [
            detect_false_breakout_reversal(raw_false_breakout, symbol_value) for raw_false_breakout in raw_false_breakouts
        ]

    raw_rebounds = config.get("rebounds", [])
    if raw_rebounds:
        if not isinstance(raw_rebounds, list):
            raise DetectorInputError("rebounds must be a list")
        results["detectors"]["rebound_models"] = [
            detect_rebound_model(raw_rebound, symbol_value) for raw_rebound in raw_rebounds
        ]

    raw_workflow_reviews = config.get("workflow_reviews", [])
    if raw_workflow_reviews:
        if not isinstance(raw_workflow_reviews, list):
            raise DetectorInputError("workflow_reviews must be a list")
        results["detectors"]["workflow_review_data_quality"] = [
            validate_workflow_review(raw_review, symbol_value) for raw_review in raw_workflow_reviews
        ]

    raw_risk = config.get("risk")
    if raw_risk is not None:
        if not isinstance(raw_risk, dict):
            raise DetectorInputError("risk must be an object")
        results["detectors"]["risk_stop_take"] = validate_risk(raw_risk, symbol_value)

    if not results["detectors"]:
        raise DetectorInputError("input must contain at least one of: hard_gates, retests, levels, trends, market_mechanics, tbx_entries, formations, tail_bars, fixations, bsu_bpu, breakouts, breakout_failures, false_breakouts, rebounds, workflow_reviews, risk")

    return results


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run read-only detector prototypes for hard gates, level/trend/mechanics context, TBX, V/U, tail bars, retest, fixation, BSU/BPU, breakout/failure/false-breakout, rebound, workflow, and risk/stop/take.",
    )
    parser.add_argument("--input", required=True, type=Path, help="Path to scenario JSON input.")
    parser.add_argument("--out", type=Path, help="Optional path to write detector output JSON.")
    parser.add_argument("--indent", type=int, default=2, help="JSON output indentation.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    args = parse_args(sys.argv[1:] if argv is None else argv)

    try:
        config = load_input(args.input)
        output = build_output(config)
    except (OSError, json.JSONDecodeError, DetectorInputError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    rendered = json.dumps(output, ensure_ascii=False, indent=args.indent)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
