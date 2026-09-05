"""Layer 4: entry / TVH context engine.

Layer 3 decides whether the current chart context prefers breakout or
false-breakout/reversal. This module does not re-decide that scenario. It turns
recent execution-timeframe candles into candidate entry models and sends them to
the KB validators:

  * fixation / return entry
  * BSU / BPU limit entry
  * TBX model validation
  * risk / stop / take validation

Source specs:
  _knowledge_base/detector_specs/fixation_return_entry_spec.md
  _knowledge_base/detector_specs/bsu_bpu_entry_spec.md
  _knowledge_base/detector_specs/tbx_entry_models_spec.md
  _knowledge_base/detector_specs/risk_stop_take_spec.md
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from approach_context import ApproachParams, build_approach_context, next_level_price
from detector_prototype import (
    CALCULATED_STOP_ATR,
    detect_bsu_bpu,
    detect_fixation,
    validate_risk,
    validate_tbx_entry_model,
)
from level_discovery import DiscoveryParams, Level, discover_levels
from scn002_strict_kb_backtest import Bar, load_history
from trend_direction import nearest_working_level


SOURCE_SPECS = [
    "_knowledge_base/detector_specs/fixation_return_entry_spec.md",
    "_knowledge_base/detector_specs/bsu_bpu_entry_spec.md",
    "_knowledge_base/detector_specs/tbx_entry_models_spec.md",
    "_knowledge_base/detector_specs/risk_stop_take_spec.md",
]

MODEL_TEXT = {
    "fixation_return": "закрепление: бар целиком за уровнем -> попытка возврата -> второй бар целиком",
    "bsu_bpu_limit": "лимитная ТВХ: БСУ уровня + БПУ1/БПУ2 в одной плоскости",
    "primary_impulse": "первичный импульс: база/поджатие + маленькая волатильность перед пробоем",
    "false_breakout_return": "LP-entry: прокол/снос уровня и возврат обратно по дневному сценарию",
}


@dataclass
class EntryParams:
    execution_lookback_bars: int = 96
    setup_recent_bars: int = 6
    stop_buffer_atr: float = 0.01
    entry_luft_atr: float = 0.01
    trend_swings: int = ApproachParams.trend_swings
    global_lookback_bars: int = ApproachParams.global_lookback_bars


def bar_time(bar: Bar) -> str:
    return datetime.fromtimestamp(bar.open_time / 1000, tz=timezone.utc).isoformat()


def candle_dict(bar: Bar) -> dict[str, float | str]:
    return {
        "time": bar_time(bar),
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
    }


def trim_execution_bars(bars: list[Bar], params: EntryParams) -> list[Bar]:
    if params.execution_lookback_bars <= 0:
        return bars
    return bars[-params.execution_lookback_bars:]


def index_by_time(candles: list[dict[str, Any]], value: str | None) -> int | None:
    if value is None:
        return None
    for index, candle in enumerate(candles):
        if candle["time"] == value:
            return index
    return None


def window_stop(candles: list[dict[str, Any]], direction: str, start: int, end: int, atr: float,
                params: EntryParams) -> float | None:
    if start < 0 or end < start or end >= len(candles):
        return None
    segment = candles[start:end + 1]
    buffer = params.stop_buffer_atr * atr
    if direction == "long":
        return min(float(candle["low"]) for candle in segment) - buffer
    return max(float(candle["high"]) for candle in segment) + buffer


def recent_structure_stop(candles: list[dict[str, Any]], direction: str, atr: float,
                          params: EntryParams) -> float | None:
    if not candles:
        return None
    start = max(0, len(candles) - params.setup_recent_bars)
    return window_stop(candles, direction, start, len(candles) - 1, atr, params)


def structure_window_metadata(candles: list[dict[str, Any]], start: int, end: int,
                              kind: str, trigger_index: int | None = None) -> dict[str, Any]:
    if start < 0 or end < start or end >= len(candles):
        return {}
    trigger = end if trigger_index is None else trigger_index
    if trigger < start or trigger > end:
        return {}
    return {
        "kind": kind,
        "start_time": candles[start]["time"],
        "end_time": candles[end]["time"],
        "trigger_time": candles[trigger]["time"],
        "bar_count": end - start + 1,
    }


def entry_near_level(level_price: float, direction: str, atr: float, params: EntryParams) -> float:
    luft = params.entry_luft_atr * atr
    return level_price + luft if direction == "long" else level_price - luft


def bsu_bpu_limit_entry_from_stop(level_price: float, stop_price: float, multiplier: float = 0.20) -> float:
    return (level_price - multiplier * stop_price) / (1.0 - multiplier)


def stop_size_atr(entry_price: float | None, stop_price: float | None, atr: float) -> float | None:
    if entry_price is None or stop_price is None or atr <= 0:
        return None
    return abs(entry_price - stop_price) / atr


def room_to_target_r(entry_price: float | None, stop_price: float | None,
                     target_price: float | None) -> float | None:
    if entry_price is None or stop_price is None or target_price is None:
        return None
    risk_abs = abs(entry_price - stop_price)
    if risk_abs <= 0:
        return None
    return abs(target_price - entry_price) / risk_abs


def status_rank(status: str | None) -> int:
    return {"trigger": 4, "pass": 4, "setup": 3, "warn": 2, "candidate": 1, "reject": 0}.get(status or "", 0)


def combined_status(entry: dict[str, Any] | None, risk: dict[str, Any] | None,
                    tbx: dict[str, Any] | None) -> str:
    statuses = [obj.get("status") for obj in (entry, risk, tbx) if obj]
    if "reject" in statuses:
        return "reject"
    if entry and entry.get("status") == "trigger" and risk and tbx:
        return "trigger"
    if any(status in {"setup", "pass", "warn"} for status in statuses):
        return "setup"
    return "candidate"


def collect_manual_review(*outputs: dict[str, Any] | None) -> list[str]:
    items: set[str] = set()
    for output in outputs:
        if not output:
            continue
        for item in output.get("manual_review_needed", []):
            items.add(str(item))
    return sorted(items)


def build_risk(symbol: str, direction: str, atr: float, level_price: float,
               entry_price: float | None, stop_price: float | None,
               target_price: float | None, stop_reason: str) -> dict[str, Any] | None:
    if entry_price is None or stop_price is None:
        return None
    return validate_risk({
        "direction": direction,
        "atr": atr,
        "entry_price": entry_price,
        "stop_price": stop_price,
        "level_price": level_price,
        "next_level_price": target_price,
        "stop_reason": stop_reason,
    }, symbol)


def build_tbx(symbol: str, model: str, direction: str, daily_valid: bool,
              entry_price: float | None, stop_price: float | None,
              target_price: float | None, atr: float, extra: dict[str, Any]) -> dict[str, Any]:
    return validate_tbx_entry_model({
        "entry_model": model,
        "direction": direction,
        "timeframe": extra.get("timeframe", "unknown"),
        "daily_scenario_valid": daily_valid,
        "execution_aligned_with_daily": extra.get("execution_aligned_with_daily", True),
        "stop_defined": entry_price is not None and stop_price is not None,
        "stop_size_atr": stop_size_atr(entry_price, stop_price, atr),
        "room_to_target_r": room_to_target_r(entry_price, stop_price, target_price),
        **extra,
    }, symbol)


def candidate_record(model: str, direction: str, entry: dict[str, Any] | None,
                     risk: dict[str, Any] | None, tbx: dict[str, Any] | None,
                     entry_price: float | None, stop_price: float | None,
                     target_price: float | None) -> dict[str, Any]:
    return {
        "model": model,
        "summary": MODEL_TEXT.get(model, model),
        "direction": direction,
        "status": combined_status(entry, risk, tbx),
        "entry_price": entry_price,
        "stop_price": stop_price,
        "target_price": target_price,
        "entry_detector": entry,
        "risk": risk,
        "tbx_validation": tbx,
        "manual_review": collect_manual_review(entry, risk, tbx),
    }


def apply_false_breakout_regime_gate(scenario: dict[str, Any], approach: dict[str, Any]) -> dict[str, Any]:
    """KB regime gate for counter-trend false_breakout (LP) reclaims.

    A false_breakout reclaim that opposes the global D1 trend is a counter-trend
    LP. Per the false_breakout_reversal spec it is only tradeable with exhaustion
    context: a sharp approach into the level together with at least one realized
    exhaustion signal (>=1 ATR already consumed by the approach impulse, or a
    paranormal bar arriving at the level). Without any exhaustion context the
    engine would fade a confirmed trend on a slow drift (catch a falling knife),
    so downgrade to manual_review. With exhaustion context the counter-trend LP
    stays valid and is annotated for transparency.

    Note: on the current case library every counter-trend false_breakout carries
    an exhaustion bar, so this gate is a forward guardrail (it fires only on a
    contextless counter-trend fade) rather than a change to existing outputs.
    """
    global_trend = str(approach.get("trend_summary", {}).get("global_trend", "unknown"))
    direction = str(scenario.get("direction", ""))
    counter_trend = (
        (direction == "long" and global_trend == "short")
        or (direction == "short" and global_trend == "long")
    )
    if not counter_trend:
        return scenario

    lp_motion = approach.get("approach_motion", {}).get("false_breakout_reversal", {})
    sharp_approach = bool(lp_motion.get("sharp_approach"))
    atr_consumed = float(lp_motion.get("atr_consumed_before_level", 0.0) or 0.0)
    paranormal = bool(lp_motion.get("paranormal_bar_to_level"))
    exhaustion_context = sharp_approach and (atr_consumed >= 1.0 or paranormal)

    gate = {
        "applied": True,
        "global_trend": global_trend,
        "scenario_direction": direction,
        "counter_trend": True,
        "exhaustion_context": exhaustion_context,
        "sharp_approach": sharp_approach,
        "atr_consumed_before_level": atr_consumed,
        "paranormal_bar_to_level": paranormal,
    }
    if exhaustion_context:
        gate["action"] = "kept_countertrend_lp_with_exhaustion"
        return {**scenario, "regime_gate": gate}
    gate["action"] = "downgraded_to_manual_review"
    return {
        "family": "manual_review",
        "direction": direction,
        "valid": False,
        "verdict": scenario.get("verdict", {}),
        "regime_gate": gate,
    }


def scenario_from_approach(approach: dict[str, Any]) -> dict[str, Any]:
    diagnosis = approach.get("diagnosis", {})
    preferred = str(diagnosis.get("preferred_bias", "manual_review")).lower()
    directions = approach.get("directions", {})
    if preferred == "breakout":
        verdict = approach.get("breakout", {})
        return {
            "family": "breakout",
            "direction": directions.get("breakout", "long"),
            "valid": verdict.get("status") in {"setup", "trigger"},
            "verdict": verdict,
        }
    if "false" in preferred or "lp" in preferred or "reversal" in preferred:
        verdict = approach.get("false_breakout_reversal", {})
        scenario = {
            "family": "false_breakout",
            "direction": directions.get("false_breakout_reversal", "short"),
            "valid": verdict.get("status") in {"setup", "trigger"},
            "verdict": verdict,
        }
        return apply_false_breakout_regime_gate(scenario, approach)
    return {
        "family": "manual_review",
        "direction": directions.get("breakout", "long"),
        "valid": False,
        "verdict": {},
    }


def build_fixation_candidate(symbol: str, timeframe: str, direction: str, daily_valid: bool,
                             level: Level, levels: list[Level], candles: list[dict[str, Any]],
                             atr: float, params: EntryParams) -> dict[str, Any]:
    base_raw = {
        "timeframe": timeframe,
        "direction": direction,
        "level_price": level.price,
        "atr": atr,
        "candles": candles,
        "parent_setup_valid": daily_valid,
    }
    first = detect_fixation(base_raw, symbol)
    features = first.get("fixation_features", {})
    full_bar_2_index = index_by_time(candles, features.get("full_bar_2_time"))
    return_index = index_by_time(candles, features.get("return_attempt_time"))
    target_price = next_level_price(levels, level, direction)
    entry_price: float | None = None
    stop_price: float | None = None
    entry_output = first
    if full_bar_2_index is not None and return_index is not None:
        entry_price = float(candles[full_bar_2_index]["close"])
        stop_price = window_stop(candles, direction, return_index, full_bar_2_index, atr, params)
        entry_output = detect_fixation({
            **base_raw,
            "attempted_entry": True,
            "entry_price": entry_price,
            "stop_price": stop_price,
            "stop_reason": "return_attempt_tail",
        }, symbol)
        structure_window = structure_window_metadata(candles, return_index, full_bar_2_index, "return_attempt_tail", full_bar_2_index)
        if structure_window:
            entry_output = {**entry_output, "structure_window": structure_window}
    risk = build_risk(symbol, direction, atr, level.price, entry_price, stop_price, target_price, "return_attempt_tail")
    tbx = build_tbx(symbol, "fixation_return", direction, daily_valid, entry_price, stop_price, target_price, atr, {
        "timeframe": timeframe,
        "return_attempt": return_index is not None,
        "second_full_bar": full_bar_2_index is not None,
    })
    return candidate_record("fixation_return", direction, entry_output, risk, tbx, entry_price, stop_price, target_price)


def build_bsu_bpu_candidate(symbol: str, timeframe: str, direction: str, daily_valid: bool,
                            level: Level, levels: list[Level], candles: list[dict[str, Any]],
                            atr: float, params: EntryParams) -> dict[str, Any]:
    base_raw = {
        "timeframe": timeframe,
        "direction": direction,
        "level_price": level.price,
        "atr": atr,
        "candles": candles,
        "bsu_time": level.bsu_time,
        "bsu_timeframe": timeframe,
    }
    first = detect_bsu_bpu(base_raw, symbol)
    features = first.get("bsu_bpu_features", {})
    bpu1_index = index_by_time(candles, features.get("bpu1_time"))
    bpu2_index = index_by_time(candles, features.get("bpu2_time"))
    zone = features.get("zone") or direction
    target_price = next_level_price(levels, level, str(zone)) if zone in {"long", "short"} else None
    entry_price: float | None = None
    stop_price: float | None = None
    entry_output = first
    if bpu1_index is not None and bpu2_index is not None and zone in {"long", "short"}:
        for _ in range(3):
            stop_price = window_stop(candles, str(zone), bpu1_index, bpu2_index, atr, params)
            if stop_price is None:
                break
            entry_price = bsu_bpu_limit_entry_from_stop(level.price, stop_price)
            entry_output = detect_bsu_bpu({
                **base_raw,
                "entry_price": entry_price,
                "stop_price": stop_price,
                "next_level_price": target_price,
                "stop_reason": "bpu_structure",
            }, symbol)

            refined = entry_output.get("bsu_bpu_features", {})
            refined_bpu1_index = index_by_time(candles, refined.get("bpu1_time"))
            refined_bpu2_index = index_by_time(candles, refined.get("bpu2_time"))
            refined_zone = refined.get("zone") or zone
            if refined_bpu1_index == bpu1_index and refined_bpu2_index == bpu2_index and refined_zone == zone:
                break
            if refined_bpu1_index is None or refined_bpu2_index is None or refined_zone not in {"long", "short"}:
                break
            bpu1_index = refined_bpu1_index
            bpu2_index = refined_bpu2_index
            zone = refined_zone
            target_price = next_level_price(levels, level, str(zone))
    if bpu1_index is not None and bpu2_index is not None:
        structure_window = structure_window_metadata(candles, bpu1_index, bpu2_index, "bpu_structure", bpu2_index)
        if structure_window:
            entry_output = {**entry_output, "structure_window": structure_window}
    risk = build_risk(symbol, str(zone), atr, level.price, entry_price, stop_price, target_price, "bpu_structure") if zone in {"long", "short"} else None
    tbx = build_tbx(symbol, "bsu_bpu_limit", str(zone), daily_valid and zone == direction, entry_price, stop_price, target_price, atr, {
        "timeframe": timeframe,
        "bpu1_bpu2": bpu1_index is not None and bpu2_index is not None,
        "execution_aligned_with_daily": zone == direction,
    }) if zone in {"long", "short"} else None
    return candidate_record("bsu_bpu_limit", str(zone), entry_output, risk, tbx, entry_price, stop_price, target_price)


def build_primary_impulse_candidate(symbol: str, timeframe: str, direction: str, daily_valid: bool,
                                    approach: dict[str, Any], level: Level, levels: list[Level],
                                    candles: list[dict[str, Any]], atr: float,
                                    params: EntryParams) -> dict[str, Any]:
    breakout_features = approach.get("breakout", {}).get("breakout_features", {})
    pre_breakout_base = bool(
        breakout_features.get("compression")
        or breakout_features.get("podzhatie")
        or breakout_features.get("consolidation_near_level")
    )
    volatility_fade = bool(breakout_features.get("volatility_fade"))
    entry_price = entry_near_level(level.price, direction, atr, params)
    stop_price = recent_structure_stop(candles, direction, atr, params)
    target_price = next_level_price(levels, level, direction)
    structure_start = max(0, len(candles) - params.setup_recent_bars)
    entry_stub = {
        "detector": "primary_impulse_probe",
        "status": "setup" if pre_breakout_base else "candidate",
        "direction": direction,
        "hard_rejects": [] if pre_breakout_base else ["primary_impulse_without_base"],
        "strength_factors": [tag for tag, ok in {
            "pre_breakout_base": pre_breakout_base,
            "volatility_fade": volatility_fade,
        }.items() if ok],
        "weakness_factors": [] if volatility_fade else ["volatility_not_faded_yet"],
        "manual_review_needed": ["stop_market_trigger_quality", "pre_breakout_base_visual_quality"],
        "structure_window": structure_window_metadata(candles, structure_start, len(candles) - 1, "pre_breakout_base", len(candles) - 1),
    }
    risk = build_risk(symbol, direction, atr, level.price, entry_price, stop_price, target_price, "pre_breakout_base")
    tbx = build_tbx(symbol, "primary_impulse", direction, daily_valid, entry_price, stop_price, target_price, atr, {
        "timeframe": timeframe,
        "pre_breakout_base": pre_breakout_base,
        "volatility_fade": volatility_fade,
    })
    return candidate_record("primary_impulse", direction, entry_stub, risk, tbx, entry_price, stop_price, target_price)


def build_false_breakout_entry_candidate(symbol: str, timeframe: str, direction: str, daily_valid: bool,
                                         approach: dict[str, Any], level: Level, levels: list[Level],
                                         candles: list[dict[str, Any]], atr: float,
                                         params: EntryParams) -> dict[str, Any]:
    fb_features = approach.get("false_breakout_reversal", {}).get("false_breakout_features", {})
    entry_price = float(candles[-1]["close"]) if candles else None
    stop_price = recent_structure_stop(candles, direction, atr, params)
    target_price = next_level_price(levels, level, direction)
    structure_start = max(0, len(candles) - params.setup_recent_bars)
    entry_stub = {
        "detector": "false_breakout_entry_probe",
        "status": "setup" if fb_features.get("sweep_detected") else "candidate",
        "direction": direction,
        "hard_rejects": [],
        "strength_factors": [tag for tag in ["lp_sweep", "returned_beyond_level"] if fb_features.get("sweep_detected" if tag == "lp_sweep" else tag)],
        "weakness_factors": [] if fb_features.get("returned_beyond_level") else ["waiting_for_return_beyond_level"],
        "manual_review_needed": ["actual_sweep_return", "lp_stop_structure"],
        "structure_window": structure_window_metadata(candles, structure_start, len(candles) - 1, "lp_rejection_structure", len(candles) - 1),
    }
    risk = build_risk(symbol, direction, atr, level.price, entry_price, stop_price, target_price, "lp_rejection_structure")
    tbx = build_tbx(symbol, "false_breakout_return", direction, daily_valid, entry_price, stop_price, target_price, atr, {
        "timeframe": timeframe,
        "lp_sweep": bool(fb_features.get("sweep_detected")),
        "returned_beyond_level": bool(fb_features.get("returned_beyond_level")),
    })
    return candidate_record("false_breakout_return", direction, entry_stub, risk, tbx, entry_price, stop_price, target_price)


def best_candidate(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not candidates:
        return None
    return max(candidates, key=lambda item: status_rank(item.get("status")))


def build_entry_context(symbol: str, context_timeframe: str, execution_timeframe: str,
                        context_bars: list[Bar], execution_bars: list[Bar], levels: list[Level],
                        breakout_direction_arg: str, params: EntryParams) -> dict[str, Any]:
    approach = build_approach_context(
        symbol,
        context_timeframe,
        context_bars,
        levels,
        breakout_direction_arg,
        ApproachParams(trend_swings=params.trend_swings, global_lookback_bars=params.global_lookback_bars),
    )
    if approach.get("status") == "no_working_level":
        return {
            "symbol": symbol,
            "source_specs": SOURCE_SPECS,
            "status": "no_working_level",
            "approach": approach,
            "entry_candidates": [],
            "best_entry": None,
        }

    current_price = context_bars[-1].close
    level = nearest_working_level(levels, current_price)
    if level is None:
        return {
            "symbol": symbol,
            "source_specs": SOURCE_SPECS,
            "status": "no_working_level",
            "approach": approach,
            "entry_candidates": [],
            "best_entry": None,
        }

    scenario = scenario_from_approach(approach)
    direction = str(scenario["direction"])
    atr = float(approach["atr"])
    trimmed_execution = trim_execution_bars(execution_bars, params)
    candles = [candle_dict(bar) for bar in trimmed_execution]
    candidates: list[dict[str, Any]] = []
    if candles:
        candidates.append(build_fixation_candidate(
            symbol, execution_timeframe, direction, bool(scenario["valid"]), level, levels, candles, atr, params
        ))
        candidates.append(build_bsu_bpu_candidate(
            symbol, execution_timeframe, direction, bool(scenario["valid"]), level, levels, candles, atr, params
        ))
        if scenario["family"] == "breakout":
            candidates.append(build_primary_impulse_candidate(
                symbol, execution_timeframe, direction, bool(scenario["valid"]), approach, level, levels, candles, atr, params
            ))
        elif scenario["family"] == "false_breakout":
            candidates.append(build_false_breakout_entry_candidate(
                symbol, execution_timeframe, direction, bool(scenario["valid"]), approach, level, levels, candles, atr, params
            ))

    best = best_candidate(candidates)
    return {
        "symbol": symbol,
        "context_timeframe": context_timeframe,
        "execution_timeframe": execution_timeframe,
        "source_specs": SOURCE_SPECS,
        "status": "trigger" if best and best["status"] == "trigger" else "setup" if best and best["status"] == "setup" else "candidate",
        "scenario": scenario,
        "nearest_level": approach["nearest_level"],
        "atr": atr,
        "execution_window": {
            "bars": len(candles),
            "first_time": candles[0]["time"] if candles else None,
            "last_time": candles[-1]["time"] if candles else None,
        },
        "approach_summary": {
            "diagnosis": approach.get("diagnosis"),
            "breakout_status": approach.get("breakout", {}).get("status"),
            "false_breakout_status": approach.get("false_breakout_reversal", {}).get("status"),
        },
        "entry_candidates": candidates,
        "best_entry": best,
        "manual_review": sorted(set().union(*(set(item.get("manual_review", [])) for item in candidates))) if candidates else [],
    }


def print_report(report: dict[str, Any]) -> None:
    print("=" * 78)
    print(f"ENTRY CONTEXT - {report['symbol']} {report.get('context_timeframe')} -> {report.get('execution_timeframe')}")
    print("rules: fixation_return + bsu_bpu + tbx_model + risk_stop_take")
    print("=" * 78)
    if report.get("status") == "no_working_level":
        print("nearest working level: none")
        return
    level = report["nearest_level"]
    scenario = report["scenario"]
    print(f"scenario: family={scenario['family']} direction={scenario['direction']} valid={scenario['valid']}")
    print(f"nearest level: {level['price']:.4f} side={level['side']} score={level['kb_score']}")
    print(f"execution window: {report['execution_window']['bars']} bars, last={report['execution_window']['last_time']}")
    print(f"overall status: {report['status']}")
    print("-" * 78)
    for candidate in report["entry_candidates"]:
        print(f"{candidate['model']}: status={candidate['status']} direction={candidate['direction']}")
        print(f"  entry={candidate['entry_price']} stop={candidate['stop_price']} target={candidate['target_price']}")
        entry = candidate.get("entry_detector") or {}
        risk = candidate.get("risk") or {}
        tbx = candidate.get("tbx_validation") or {}
        if entry.get("hard_rejects"):
            print(f"  entry rejects: {', '.join(entry['hard_rejects'])}")
        if risk.get("hard_rejects"):
            print(f"  risk rejects: {', '.join(risk['hard_rejects'])}")
        if tbx.get("hard_rejects"):
            print(f"  tbx rejects: {', '.join(tbx['hard_rejects'])}")
        if candidate.get("manual_review"):
            print(f"  manual: {', '.join(candidate['manual_review'])}")
    if report.get("best_entry"):
        best = report["best_entry"]
        print("-" * 78)
        print(f"best: {best['model']} status={best['status']} direction={best['direction']}")
    print("=" * 78)


def main() -> None:
    ap = argparse.ArgumentParser(description="Layer 4: entry / TVH context near a working level")
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--context-interval", default="1d")
    ap.add_argument("--execution-interval", default="15m")
    ap.add_argument("--start", required=True, help="Start month in YYYY-MM format")
    ap.add_argument("--end", required=True, help="End month in YYYY-MM format")
    ap.add_argument("--higher-interval", default="1w")
    ap.add_argument("--breakout-direction", choices=["auto", "long", "short"], default="auto")
    ap.add_argument("--execution-lookback-bars", type=int, default=EntryParams.execution_lookback_bars)
    ap.add_argument("--output-format", choices=["text", "json"], default="text")
    args = ap.parse_args()

    print(f"Loading {args.symbol} {args.context_interval} {args.start}..{args.end} ...", file=sys.stderr)
    context_bars = load_history(args.symbol, args.context_interval, args.start, args.end)
    if not context_bars:
        print("No context data.", file=sys.stderr)
        sys.exit(1)

    print(f"Loading {args.symbol} {args.execution_interval} {args.start}..{args.end} ...", file=sys.stderr)
    execution_bars = load_history(args.symbol, args.execution_interval, args.start, args.end)
    if not execution_bars:
        print("No execution data.", file=sys.stderr)
        sys.exit(1)

    level_params = DiscoveryParams()
    higher_tf = args.higher_interval.strip()
    higher_levels: list[Level] | None = None
    if higher_tf and higher_tf != args.context_interval:
        print(f"Loading higher timeframe {args.symbol} {higher_tf} ...", file=sys.stderr)
        higher_bars = load_history(args.symbol, higher_tf, args.start, args.end)
        higher_levels = discover_levels(higher_bars, level_params) if higher_bars else []
    levels = discover_levels(context_bars, level_params, higher_levels, higher_tf)

    report = build_entry_context(
        args.symbol,
        args.context_interval,
        args.execution_interval,
        context_bars,
        execution_bars,
        levels,
        args.breakout_direction,
        EntryParams(execution_lookback_bars=args.execution_lookback_bars),
    )
    if args.output_format == "json":
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        print_report(report)


if __name__ == "__main__":
    main()