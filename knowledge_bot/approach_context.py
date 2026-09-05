"""Layer 3: approach-to-level context.

This layer keeps the KB validators as the source of truth. It discovers the
current approach state around the nearest working level, then feeds the same
OHLC evidence into retest, breakout, and false-breakout/reversal detectors.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from detector_prototype import (
    BREAKOUT_CLOSE_NEAR_LEVEL_ATR,
    PARANORMAL_RANGE_ATR,
    PARANORMAL_RANGE_MULTIPLIER,
    contact_tolerance,
    detect_breakout_preconditions,
    detect_false_breakout_reversal,
    detect_retest,
)
from level_discovery import DiscoveryParams, Level, build_report, discover_levels
from scn002_strict_kb_backtest import Bar, atr_at, load_history
from trend_direction import TrendParams, build_trend_context, nearest_working_level


SOURCE_SPECS = [
    "_knowledge_base/detector_specs/breakout_preconditions_spec.md",
    "_knowledge_base/detector_specs/near_far_retest_spec.md",
    "_knowledge_base/detector_specs/false_breakout_reversal_spec.md",
    "_knowledge_base/detector_specs/fixation_return_entry_spec.md",
    "_knowledge_base/detector_specs/tbx_entry_models_spec.md",
]

FACTOR_TEXT = {
    "near_retest": "ближний ретест поддерживает пробой/продолжение",
    "strong_near_retest": "сильный ближний ретест поддерживает пробой",
    "ideal_near_retest": "идеальный ближний ретест: очень свежий возврат к уровню",
    "far_retest": "дальний ретест первого контакта склоняет к LP/развороту",
    "very_far_retest": "очень дальний ретест усиливает LP/разворот",
    "far_to_near_retest_exception": "дальний ретест стал ближним после стояния у уровня",
    "close_near_level": "закрытие рядом с уровнем",
    "close_very_near_level": "закрытие очень близко к уровню",
    "close_at_extreme": "закрытие у хая/лоя в сторону сценария",
    "accumulation": "есть база/накопление возле уровня",
    "podzhatie": "есть сжатие/поджатие к уровню",
    "volatility_fade": "волатильность затухает перед возможной ТВХ",
    "trend_aligned": "глобальный контекст совпадает с пробойной стороной",
    "sharp_approach": "резкий подход к уровню поддерживает LP/разворот",
    "paranormal_bar_to_level": "паранормальный бар пришел к уровню",
    "atr_consumed_before_level": "до уровня уже пройдено 1+ ATR",
    "atr_consumed_before_level_warning": "до уровня уже пройдено 0.5+ ATR",
    "no_consolidation_at_level": "у уровня нет базы/проторговки",
    "actual_lp_sweep_and_return": "уровень прокололи и вернулись обратно",
    "strong_return": "возвратная свеча сильная в сторону LP",
    "lp_bias": "суммарно подход склоняет к LP/развороту",
}

REJECT_TEXT = {
    "candidate_level_invalid": "уровень не прошел KB-проверку силы",
    "level_chopped_by_closes": "уровень распилен закрытиями с обеих сторон",
    "invalid_chop": "ретест нельзя использовать: уровень распилен между контактами",
    "single_large_bar_without_consolidation": "один большой бар к уровню без базы",
    "atr_consumed_before_level_without_consolidation": "до уровня уже съеден ATR без базы",
    "far_retest_first_contact_lp_bias": "дальний ретест первого контакта склоняет к LP, не к пробою",
    "breakout_preparation_against_lp": "у уровня есть пробойная подготовка против LP-сценария",
    "lp_converted_to_breakout": "после прокола цена закрепилась за уровнем",
    "far_retest_converted_to_near_breakout_context": "дальний ретест стал пробойным ближним ретестом",
    "room_to_target_less_than_3r": "до следующего уровня меньше 3R",
}


@dataclass
class ApproachParams:
    atr_period: int = 14
    no_consolidation_lookback: int = 10
    no_consolidation_min_near_closes: int = 3
    current_contact_max_age: int = 1
    trend_swings: int = TrendParams.trend_swings
    global_lookback_bars: int = TrendParams.global_lookback_bars


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


def bar_range(bar: Bar) -> float:
    return max(bar.high - bar.low, 1e-12)


def opposite(direction: str) -> str:
    return "short" if direction == "long" else "long"


def infer_breakout_direction(level: Level, current_price: float, requested: str) -> str:
    if requested in {"long", "short"}:
        return requested
    if level.side == "resistance":
        return "long"
    if level.side == "support":
        return "short"
    return "long" if current_price <= level.price else "short"


def level_snapshot(level: Level | None) -> dict[str, Any] | None:
    if level is None:
        return None
    return {
        "price": level.price,
        "side": level.side,
        "scope": level.scope,
        "basis_tags": level.basis_tags,
        "touch_count": level.touch_count,
        "false_breakout_count": level.false_breakout_count,
        "kb_status": level.kb_status,
        "kb_score": level.kb_score,
        "kb_hard_rejects": level.kb_hard_rejects,
    }


def next_level_price(levels: list[Level], current_level: Level, direction: str) -> float | None:
    working = [lv for lv in levels if lv.kb_status == "pass" and id(lv) != id(current_level)]
    if direction == "long":
        candidates = [lv.price for lv in working if lv.price > current_level.price]
        return min(candidates) if candidates else None
    candidates = [lv.price for lv in working if lv.price < current_level.price]
    return max(candidates) if candidates else None


def contact_summary(bars: list[Bar], level_price: float, atr: float) -> dict[str, Any]:
    tolerance = contact_tolerance(atr, None)
    contacts = [
        bar.low <= level_price <= bar.high
        or abs(bar.close - level_price) <= tolerance
        or abs(bar.open - level_price) <= tolerance
        for bar in bars
    ]
    last_contact_index = max((i for i, contact in enumerate(contacts) if contact), default=None)
    age = None if last_contact_index is None else len(bars) - 1 - last_contact_index
    distance_atr = abs(bars[-1].close - level_price) / atr if atr else None
    return {
        "contact_tolerance": tolerance,
        "contact_count": sum(contacts),
        "last_contact_time": None if last_contact_index is None else bar_time(bars[last_contact_index]),
        "current_contact_age_bars": age,
        "currently_touching": bool(contacts[-1]) if contacts else False,
        "current_distance_atr": round(distance_atr, 4) if distance_atr is not None else None,
        "current_close_near_level": bool(distance_atr is not None and distance_atr <= BREAKOUT_CLOSE_NEAR_LEVEL_ATR),
    }


def approach_motion(bars: list[Bar], level_price: float, atr: float,
                    direction: str, reversal_trade_direction: bool,
                    params: ApproachParams) -> dict[str, Any]:
    if len(bars) < 2 or atr <= 0:
        return {
            "approach_bars_count": 0,
            "distance_travelled_toward_level": 0.0,
            "atr_consumed_before_level": 0.0,
            "paranormal_bar_to_level": False,
            "sharp_approach": False,
            "near_level_close_count_last_10": 0,
            "no_consolidation_at_level": True,
        }

    approach_direction = opposite(direction) if reversal_trade_direction else direction

    def moved_toward(previous_close: float, next_close: float) -> bool:
        return next_close >= previous_close if approach_direction == "long" else next_close <= previous_close

    start_index = len(bars) - 1
    for index in range(len(bars) - 2, max(-1, len(bars) - 13), -1):
        if not moved_toward(bars[index].close, bars[index + 1].close):
            break
        start_index = index

    latest = bars[-1]
    previous = bars[max(0, len(bars) - 11):len(bars) - 1]
    previous_avg_range = sum(bar_range(bar) for bar in previous) / len(previous) if previous else 0.0
    latest_range = bar_range(latest)
    paranormal = (
        latest_range >= PARANORMAL_RANGE_ATR * atr
        or (previous_avg_range > 0 and latest_range >= PARANORMAL_RANGE_MULTIPLIER * previous_avg_range)
    )
    travelled = abs(latest.close - bars[start_index].close)
    near_window = bars[max(0, len(bars) - params.no_consolidation_lookback - 1):len(bars) - 1]
    near_closes = sum(
        1 for bar in near_window
        if abs(bar.close - level_price) <= BREAKOUT_CLOSE_NEAR_LEVEL_ATR * atr
    )
    approach_bars = len(bars) - start_index
    return {
        "approach_bars_count": approach_bars,
        "distance_travelled_toward_level": travelled,
        "atr_consumed_before_level": travelled / atr,
        "paranormal_bar_to_level": paranormal,
        "sharp_approach": approach_bars <= 3 or paranormal,
        "near_level_close_count_last_10": near_closes,
        "no_consolidation_at_level": near_closes < params.no_consolidation_min_near_closes,
    }


def current_retest_bars(retest: dict[str, Any], contacts: dict[str, Any], params: ApproachParams) -> int | None:
    age = contacts.get("current_contact_age_bars")
    if age is None or age > params.current_contact_max_age:
        return None
    bars_since = retest.get("retest_features", {}).get("bars_since_contact")
    return bars_since if isinstance(bars_since, int) else None


def factor_evidence(output: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"tag": tag, "summary": FACTOR_TEXT[tag]}
        for tag in output.get("strength_factors", []) + output.get("weakness_factors", [])
        if tag in FACTOR_TEXT
    ]


def reject_evidence(output: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"tag": tag, "summary": REJECT_TEXT.get(tag, tag)}
        for tag in output.get("hard_rejects", [])
    ]


def classify_bias(breakout: dict[str, Any], false_breakout: dict[str, Any]) -> dict[str, Any]:
    breakout_ok = breakout["status"] == "setup"
    lp_ok = false_breakout["status"] in {"setup", "trigger"}
    breakout_reject = breakout["status"] == "reject"
    lp_reject = false_breakout["status"] == "reject"
    if breakout_ok and lp_reject:
        status, preferred = "breakout_context", "breakout"
    elif lp_ok and breakout_reject:
        status, preferred = "lp_context", "false_breakout"
    elif breakout_ok and lp_ok:
        status, preferred = "conflict", "manual_review"
    elif breakout_reject and lp_reject:
        status, preferred = "both_rejected", "no_trade"
    else:
        status, preferred = "candidate_or_manual", "wait"
    return {
        "status": status,
        "preferred_bias": preferred,
        "breakout_status": breakout["status"],
        "breakout_score": breakout.get("score"),
        "false_breakout_status": false_breakout["status"],
        "false_breakout_score": false_breakout.get("score"),
    }


def build_approach_context(symbol: str, timeframe: str, bars: list[Bar], levels: list[Level],
                           breakout_direction_arg: str, params: ApproachParams) -> dict[str, Any]:
    current_price = bars[-1].close
    atr = atr_at(bars, len(bars) - 1, params.atr_period) or 0.0
    if atr <= 0:
        raise ValueError("ATR must be positive")
    nearest = nearest_working_level(levels, current_price)
    if nearest is None:
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "source_specs": SOURCE_SPECS,
            "status": "no_working_level",
            "current_price": current_price,
            "nearest_level": None,
            "manual_review": ["nearest_working_level"],
        }

    breakout_direction = infer_breakout_direction(nearest, current_price, breakout_direction_arg)
    lp_direction = opposite(breakout_direction)
    candles = [candle_dict(bar) for bar in bars]
    contacts = contact_summary(bars, nearest.price, atr)
    retest = detect_retest({
        "timeframe": timeframe,
        "level_price": nearest.price,
        "atr": atr,
        "candles": candles,
    }, symbol)
    bars_since_contact = current_retest_bars(retest, contacts, params)
    far_to_near_exception = bool(retest.get("retest_features", {}).get("far_to_near_retest_exception"))
    trend = build_trend_context(
        symbol,
        bars,
        levels,
        "breakout",
        breakout_direction,
        TrendParams(trend_swings=params.trend_swings, global_lookback_bars=params.global_lookback_bars),
    )
    trend_features = trend["verdict"]["trend_features"]
    breakout_motion = approach_motion(bars, nearest.price, atr, breakout_direction, False, params)
    lp_motion = approach_motion(bars, nearest.price, atr, lp_direction, True, params)

    breakout_raw = {
        "timeframe": timeframe,
        "direction": breakout_direction,
        "level_price": nearest.price,
        "atr": atr,
        "candles": candles,
        "bars_since_last_contact": bars_since_contact,
        "level_valid": nearest.kb_status == "pass",
        "level_contaminated": bool(nearest.repeated_chop or nearest.kb_hard_rejects),
        "sharp_approach": breakout_motion["sharp_approach"],
        "far_to_near_exception": far_to_near_exception,
        "trend_aligned": bool(trend_features.get("global_aligned")),
        "next_level_price": next_level_price(levels, nearest, breakout_direction),
    }
    breakout = detect_breakout_preconditions(breakout_raw, symbol)
    breakout_features = breakout.get("breakout_features", {})
    breakout_preparation = bool(
        breakout["status"] == "setup"
        and (
            breakout_features.get("compression")
            or breakout_features.get("consolidation_near_level")
            or breakout_features.get("volatility_fade")
        )
    )

    false_breakout_raw = {
        "timeframe": timeframe,
        "direction": lp_direction,
        "level_price": nearest.price,
        "atr": atr,
        "candles": candles,
        "bars_since_last_contact": bars_since_contact,
        "approach_bars_count": lp_motion["approach_bars_count"],
        "distance_travelled_toward_level": lp_motion["distance_travelled_toward_level"],
        "atr_consumed_before_level": lp_motion["atr_consumed_before_level"],
        "paranormal_bar_to_level": lp_motion["paranormal_bar_to_level"],
        "sharp_approach": lp_motion["sharp_approach"],
        "no_consolidation_at_level": lp_motion["no_consolidation_at_level"],
        "level_valid": nearest.kb_status == "pass",
        "level_contaminated": bool(nearest.repeated_chop or nearest.kb_hard_rejects),
        "breakout_preparation": breakout_preparation,
        "compression_against_lp": bool(breakout_features.get("compression")),
        "far_to_near_exception": far_to_near_exception,
        "next_level_price": next_level_price(levels, nearest, lp_direction),
    }
    false_breakout = detect_false_breakout_reversal(false_breakout_raw, symbol)

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "source_specs": SOURCE_SPECS,
        "current_price": current_price,
        "atr": atr,
        "nearest_level": level_snapshot(nearest),
        "directions": {"breakout": breakout_direction, "false_breakout_reversal": lp_direction},
        "contact_summary": contacts,
        "trend_summary": {
            "status": trend["verdict"]["status"],
            "global_trend": trend_features["global_trend"],
            "global_aligned_with_breakout": trend_features["global_aligned"],
            "local_zone": trend_features["local_zone"],
        },
        "approach_motion": {"breakout": breakout_motion, "false_breakout_reversal": lp_motion},
        "retest": retest,
        "breakout": breakout,
        "false_breakout_reversal": false_breakout,
        "diagnosis": classify_bias(breakout, false_breakout),
        "evidence": {
            "retest": factor_evidence(retest),
            "breakout": factor_evidence(breakout),
            "false_breakout_reversal": factor_evidence(false_breakout),
        },
        "reject_reasons": {
            "retest": reject_evidence(retest),
            "breakout": reject_evidence(breakout),
            "false_breakout_reversal": reject_evidence(false_breakout),
        },
        "manual_review": sorted(set(
            retest.get("manual_review_needed", [])
            + breakout.get("manual_review_needed", [])
            + false_breakout.get("manual_review_needed", [])
        )),
    }


def print_section(title: str, verdict: dict[str, Any], evidence: list[dict[str, str]],
                  rejects: list[dict[str, str]]) -> None:
    score = verdict.get("score")
    score_text = "" if score is None else f" score={score}"
    print(f"{title}: status={verdict['status']} bias={verdict.get('bias')}{score_text}")
    for item in evidence:
        print(f"  + {item['tag']}: {item['summary']}")
    for item in rejects:
        print(f"  - {item['tag']}: {item['summary']}")


def print_report(report: dict[str, Any]) -> None:
    print("=" * 78)
    print(f"APPROACH CONTEXT - {report['symbol']} {report['timeframe']}")
    print("rules: breakout_preconditions + near_far_retest + false_breakout_reversal")
    print("=" * 78)
    if report.get("status") == "no_working_level":
        print("nearest working level: none")
        return
    level = report["nearest_level"]
    contacts = report["contact_summary"]
    trend = report["trend_summary"]
    diagnosis = report["diagnosis"]
    print(f"current={report['current_price']:.4f} atr={report['atr']:.4f}")
    print(f"nearest level: {level['price']:.4f} side={level['side']} score={level['kb_score']} scope={level['scope']}")
    print(f"directions: breakout={report['directions']['breakout']} lp/reversal={report['directions']['false_breakout_reversal']}")
    print(f"trend: global={trend['global_trend']} aligned_with_breakout={trend['global_aligned_with_breakout']} local_zone={trend['local_zone']}")
    print(f"contact: age={contacts['current_contact_age_bars']} close_near={contacts['current_close_near_level']} distance_atr={contacts['current_distance_atr']}")
    print(f"retest: classification={report['retest'].get('classification')} bias={report['retest'].get('bias')} status={report['retest'].get('status')}")
    print(f"diagnosis: {diagnosis['status']} preferred={diagnosis['preferred_bias']}")
    print("-" * 78)
    print_section("breakout", report["breakout"], report["evidence"]["breakout"], report["reject_reasons"]["breakout"])
    print_section("false breakout / reversal", report["false_breakout_reversal"], report["evidence"]["false_breakout_reversal"], report["reject_reasons"]["false_breakout_reversal"])
    if report["manual_review"]:
        print("manual review:")
        for item in report["manual_review"]:
            print(f"  ? {item}")
    print("=" * 78)


def main() -> None:
    ap = argparse.ArgumentParser(description="Layer 3: approach context near a working level")
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--interval", default="1d")
    ap.add_argument("--start", required=True, help="Start month in YYYY-MM format")
    ap.add_argument("--end", required=True, help="End month in YYYY-MM format")
    ap.add_argument("--higher-interval", default="1w")
    ap.add_argument("--breakout-direction", choices=["auto", "long", "short"], default="auto")
    ap.add_argument("--trend-swings", type=int, default=ApproachParams.trend_swings)
    ap.add_argument("--global-lookback-bars", type=int, default=ApproachParams.global_lookback_bars)
    ap.add_argument("--output-format", choices=["text", "json"], default="text")
    args = ap.parse_args()

    print(f"Loading {args.symbol} {args.interval} {args.start}..{args.end} ...", file=sys.stderr)
    bars = load_history(args.symbol, args.interval, args.start, args.end)
    if not bars:
        print("No data.", file=sys.stderr)
        sys.exit(1)
    level_params = DiscoveryParams()
    higher_tf = args.higher_interval.strip()
    higher_levels: list[Level] | None = None
    if higher_tf and higher_tf != args.interval:
        print(f"Loading higher timeframe {args.symbol} {higher_tf} ...", file=sys.stderr)
        higher_bars = load_history(args.symbol, higher_tf, args.start, args.end)
        higher_levels = discover_levels(higher_bars, level_params) if higher_bars else []
    levels = discover_levels(bars, level_params, higher_levels, higher_tf)
    report = build_approach_context(
        args.symbol,
        args.interval,
        bars,
        levels,
        args.breakout_direction,
        ApproachParams(trend_swings=args.trend_swings, global_lookback_bars=args.global_lookback_bars),
    )
    if report.get("nearest_level") is not None:
        report["level_summary"] = build_report(args.symbol, args.interval, higher_tf, bars[-1].close, levels)["summary"]
    if args.output_format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_report(report)


if __name__ == "__main__":
    main()