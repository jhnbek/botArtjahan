"""Layer 2: Direction / Trend Context Engine.

Layer 1 (`level_discovery.py`) finds and validates candidate levels. This
module builds the next context layer:

  * global trend from daily swing structure (6-12 month context in the course)
  * local zone from current price relative to the nearest working level
  * scenario direction validation through `validate_trend_context`

The knowledge base owns the rule verdicts. This module supplies the missing
automation inputs (swing-derived GT, nearest level, local zone, simple base /
approach flags) and then calls the existing validator.

Source specs:
  _knowledge_base/rulebook/trend_context.md
  _knowledge_base/detector_specs/trend_context_spec.md
  _knowledge_base/detector_specs/level_selection_strength_spec.md
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from detector_prototype import validate_trend_context
from level_discovery import DiscoveryParams, Level, build_report, discover_levels
from scn002_strict_kb_backtest import Bar, atr_at, load_history

SOURCE_SPECS = [
    "_knowledge_base/rulebook/trend_context.md",
    "_knowledge_base/detector_specs/trend_context_spec.md",
    "_knowledge_base/detector_specs/level_selection_strength_spec.md",
]

TREND_EXPLANATIONS = {
    "global_trend_aligned": {
        "summary": "сценарий совпадает с глобальным трендом",
        "source": "trend_context: lec_026_f125c472_0028/0029",
    },
    "global_trend_not_aligned": {
        "summary": "сценарий против глобального тренда; нужен контекст истощения или база",
        "source": "trend_context: lec_026_f125c472_0030, lec_030_911591e5_0046",
    },
    "local_trend_aligned": {
        "summary": "цена находится в локальной плоскости направления относительно рабочего уровня",
        "source": "trend_context: lec_009_ea04576a_0004/0005/0007",
    },
    "local_trend_not_aligned": {
        "summary": "направление конфликтует с локальной зоной относительно ближайшего уровня",
        "source": "trend_context: lec_009_ea04576a_0007",
    },
    "countertrend_accumulation_or_base": {
        "summary": "противотрендовый пробой допустимее при базе/накоплении возле уровня",
        "source": "breakout_preconditions_spec.md; trend_context_spec.md",
    },
    "character_change": {
        "summary": "есть смена характера структуры после движения, противотрендовый сценарий не слепой",
        "source": "trend_context: lec_026_f125c472_0030",
    },
    "countertrend_lp_context": {
        "summary": "ложный пробой против глобального тренда поддержан резким подходом к сильному уровню",
        "source": "false_breakout_reversal_spec.md; trend_context_spec.md",
    },
}

REJECT_EXPLANATIONS = {
    "nearest_level_missing_for_local_trend": "нет ближайшего рабочего уровня, поэтому локальный тренд нельзя определить",
    "countertrend_breakout_without_accumulation": "пробой против глобального тренда без базы/накопления/смены характера",
    "breakout_against_local_zone": "пробой направлен против текущей локальной зоны",
}


@dataclass(frozen=True)
class Pivot:
    index: int
    time_ms: int
    confirm_ms: int
    price: float
    kind: str  # H | L


@dataclass
class TrendParams:
    pivot_wing: int = 3
    trend_swings: int = 6
    global_lookback_bars: int = 180
    atr_period: int = 14
    base_lookback: int = 8
    base_near_level_atr: float = 0.50
    compression_ratio: float = 0.70
    sharp_approach_bars: int = 3
    sharp_approach_atr: float = 0.50


def bar_time_ms(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def confirmed_pivots(bars: list[Bar], wing: int) -> list[Pivot]:
    out: list[Pivot] = []
    for i in range(wing, len(bars) - wing):
        hi = bars[i].high
        lo = bars[i].low
        window = bars[i - wing:i + wing + 1]
        is_high = hi >= max(b.high for b in window) and any(hi > b.high for b in window)
        is_low = lo <= min(b.low for b in window) and any(lo < b.low for b in window)
        confirm_ms = bars[i + wing].open_time
        if is_high:
            out.append(Pivot(i, bars[i].open_time, confirm_ms, hi, "H"))
        if is_low:
            out.append(Pivot(i, bars[i].open_time, confirm_ms, lo, "L"))
    out.sort(key=lambda p: p.confirm_ms)
    return out


def representative_pivots(pivots: list[Pivot], max_count: int) -> list[Pivot]:
    if max_count <= 0 or len(pivots) <= max_count:
        return pivots
    if max_count == 1:
        return [pivots[-1]]
    last_index = len(pivots) - 1
    indexes = {
        round(i * last_index / (max_count - 1))
        for i in range(max_count)
    }
    return [pivots[i] for i in sorted(indexes)]


def classify_global_trend(pivots: list[Pivot], swing_count: int,
                          min_time_ms: int | None = None) -> tuple[str, dict[str, Any]]:
    if min_time_ms is not None:
        pivots = [p for p in pivots if p.time_ms >= min_time_ms]
    high_pivots = [p for p in pivots if p.kind == "H"]
    low_pivots = [p for p in pivots if p.kind == "L"]
    highs = representative_pivots(high_pivots, swing_count)
    lows = representative_pivots(low_pivots, swing_count)
    evidence: dict[str, Any] = {
        "lookback_start": bar_time_ms(min_time_ms) if min_time_ms else None,
        "available_high_count": len(high_pivots),
        "available_low_count": len(low_pivots),
        "representative_swing_count": swing_count,
        "highs": [{"price": p.price, "time": bar_time_ms(p.time_ms)} for p in highs],
        "lows": [{"price": p.price, "time": bar_time_ms(p.time_ms)} for p in lows],
    }
    if len(highs) < 2 or len(lows) < 2:
        evidence["reason"] = "not_enough_confirmed_swings"
        return "unknown", evidence

    # Compare representative swings across the full lookback window. This keeps
    # global trend tied to the course's 6-12 month context instead of shrinking
    # to only the latest dense cluster of pivots.
    hh = highs[-1].price > highs[0].price
    hl = lows[-1].price > lows[0].price
    lh = highs[-1].price < highs[0].price
    ll = lows[-1].price < lows[0].price
    evidence.update({"higher_high": hh, "higher_low": hl, "lower_high": lh, "lower_low": ll})
    if hh and hl:
        return "long", evidence
    if lh and ll:
        return "short", evidence
    return "range", evidence


def nearest_working_level(levels: list[Level], current_price: float) -> Level | None:
    working = [lv for lv in levels if lv.kb_status == "pass"]
    if not working:
        return None
    return min(working, key=lambda lv: abs(lv.price - current_price))


def local_zone(current_price: float, level_price: float | None) -> str:
    if level_price is None:
        return "unknown"
    if current_price > level_price:
        return "long"
    if current_price < level_price:
        return "short"
    return "at_level"


def bar_range(b: Bar) -> float:
    return max(b.high - b.low, 1e-12)


def has_base_near_level(bars: list[Bar], level_price: float | None, atr: float,
                        p: TrendParams) -> bool:
    if level_price is None or atr <= 0 or len(bars) < p.base_lookback * 2:
        return False
    recent = bars[-p.base_lookback:]
    previous = bars[-p.base_lookback * 2:-p.base_lookback]
    distances = [abs(b.close - level_price) / atr for b in recent]
    near = statistics.median(distances) <= p.base_near_level_atr
    recent_range = sum(bar_range(b) for b in recent) / len(recent)
    previous_range = sum(bar_range(b) for b in previous) / len(previous)
    compressed = recent_range <= p.compression_ratio * previous_range
    return near and compressed


def sharp_countertrend_approach(bars: list[Bar], level_price: float | None,
                                atr: float, p: TrendParams) -> bool:
    if level_price is None or atr <= 0 or len(bars) <= p.sharp_approach_bars:
        return False
    start = bars[-p.sharp_approach_bars - 1].close
    end = bars[-1].close
    moved_toward_level = abs(end - level_price) < abs(start - level_price)
    travelled = abs(end - start) / atr
    large_last_bar = bar_range(bars[-1]) / atr >= p.sharp_approach_atr
    return moved_toward_level and (travelled >= p.sharp_approach_atr or large_last_bar)


def choose_direction(requested: str, zone: str, global_trend: str) -> str:
    if requested in {"long", "short"}:
        return requested
    if zone in {"long", "short"}:
        return zone
    if global_trend in {"long", "short"}:
        return global_trend
    return "long"


def trend_evidence(output: dict[str, Any]) -> list[dict[str, str]]:
    evidence: list[dict[str, str]] = []
    for tag in output.get("strength_factors", []):
        info = TREND_EXPLANATIONS.get(tag)
        if info:
            evidence.append({"tag": tag, **info})
    for tag in output.get("weakness_factors", []):
        info = TREND_EXPLANATIONS.get(tag)
        if info:
            evidence.append({"tag": tag, **info})
    return evidence


def trend_rejections(output: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"tag": tag, "summary": REJECT_EXPLANATIONS.get(tag, tag)}
        for tag in output.get("hard_rejects", [])
    ]


def build_trend_context(symbol: str, bars: list[Bar], levels: list[Level],
                        scenario: str, direction_arg: str,
                        p: TrendParams) -> dict[str, Any]:
    current_price = bars[-1].close
    pivots = confirmed_pivots(bars, p.pivot_wing)
    lookback_index = max(0, len(bars) - p.global_lookback_bars)
    global_trend, swing_evidence = classify_global_trend(
        pivots, p.trend_swings, bars[lookback_index].open_time)
    nearest = nearest_working_level(levels, current_price)
    nearest_price = nearest.price if nearest else None
    zone = local_zone(current_price, nearest_price)
    direction = choose_direction(direction_arg, zone, global_trend)
    atr = atr_at(bars, len(bars) - 1, p.atr_period) or 0.0
    base_near = has_base_near_level(bars, nearest_price, atr, p)
    sharp_approach = sharp_countertrend_approach(bars, nearest_price, atr, p)
    strong_level = bool(nearest and nearest.kb_status == "pass" and nearest.kb_score >= 3.0)

    raw = {
        "scenario": scenario,
        "direction": direction,
        "global_trend": global_trend,
        "nearest_level_price": nearest_price,
        "current_price": current_price,
        "local_zone": zone,
        "has_accumulation": base_near,
        "base_near_level": base_near,
        "sharp_countertrend_approach": sharp_approach,
        "strong_level_ahead": strong_level,
    }
    verdict = validate_trend_context(raw, symbol)
    return {
        "symbol": symbol,
        "source_specs": SOURCE_SPECS,
        "scenario": scenario,
        "direction": direction,
        "current_price": current_price,
        "nearest_level": None if nearest is None else {
            "price": nearest.price,
            "side": nearest.side,
            "kb_status": nearest.kb_status,
            "kb_score": nearest.kb_score,
            "scope": nearest.scope,
            "basis_tags": nearest.basis_tags,
        },
        "global_swing_structure": swing_evidence,
        "derived_flags": {
            "base_near_level": base_near,
            "sharp_countertrend_approach": sharp_approach,
            "strong_level_ahead": strong_level,
            "atr": atr,
        },
        "verdict": verdict,
        "evidence": trend_evidence(verdict),
        "reject_reasons": trend_rejections(verdict),
        "manual_review": verdict.get("manual_review_needed", []),
    }


def print_report(report: dict[str, Any]) -> None:
    verdict = report["verdict"]
    features = verdict["trend_features"]
    nearest = report["nearest_level"]
    print("=" * 78)
    print(f"TREND / DIRECTION  —  {report['symbol']}  scenario={report['scenario']} direction={report['direction']}")
    print("rules: rulebook/trend_context.md + detector_specs/trend_context_spec.md")
    print("=" * 78)
    print(f"status={verdict['status']}  global={features['global_trend']}  local_zone={features['local_zone']}")
    if nearest:
        print(f"nearest working level: {nearest['price']:.4f}  score={nearest['kb_score']}  scope={nearest['scope']}")
    else:
        print("nearest working level: none")
    print(f"aligned: global={features['global_aligned']} local={features['local_aligned']}")
    flags = report["derived_flags"]
    print(f"derived: base_near_level={flags['base_near_level']} sharp_approach={flags['sharp_countertrend_approach']} strong_level={flags['strong_level_ahead']}")

    if report["evidence"]:
        print("why:")
        for item in report["evidence"]:
            print(f"  + {item['tag']}: {item['summary']}")
    if report["reject_reasons"]:
        print("why rejected:")
        for item in report["reject_reasons"]:
            print(f"  - {item['tag']}: {item['summary']}")
    if report["manual_review"]:
        print("manual review:")
        for item in report["manual_review"]:
            print(f"  ? {item}")
    swings = report["global_swing_structure"]
    print("swing evidence:")
    print(f"  highs: {swings.get('highs', [])}")
    print(f"  lows:  {swings.get('lows', [])}")
    print("=" * 78)


def main() -> None:
    ap = argparse.ArgumentParser(description="Layer 2: direction / trend context")
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--interval", default="1d")
    ap.add_argument("--start", required=True, help="Start month in YYYY-MM format")
    ap.add_argument("--end", required=True, help="End month in YYYY-MM format")
    ap.add_argument("--scenario", choices=["breakout", "false_breakout", "reversal", "lp", "unknown"], default="breakout")
    ap.add_argument("--direction", choices=["auto", "long", "short"], default="auto")
    ap.add_argument("--higher-interval", default="1w")
    ap.add_argument("--trend-swings", type=int, default=TrendParams.trend_swings,
                    help="Representative swing highs/lows sampled across the global lookback")
    ap.add_argument("--global-lookback-bars", type=int, default=TrendParams.global_lookback_bars,
                    help="Bars used for global trend context; 180 D1 bars is about 6 months")
    ap.add_argument("--output-format", choices=["text", "json"], default="text")
    args = ap.parse_args()

    print(f"Loading {args.symbol} {args.interval} {args.start}..{args.end} ...", file=sys.stderr)
    bars = load_history(args.symbol, args.interval, args.start, args.end)
    if not bars:
        print("No data.", file=sys.stderr)
        sys.exit(1)

    level_params = DiscoveryParams()
    higher_levels: list[Level] | None = None
    higher_tf = args.higher_interval.strip()
    if higher_tf and higher_tf != args.interval:
        print(f"Loading higher timeframe {args.symbol} {higher_tf} ...", file=sys.stderr)
        higher_bars = load_history(args.symbol, higher_tf, args.start, args.end)
        higher_levels = discover_levels(higher_bars, level_params) if higher_bars else []
    levels = discover_levels(bars, level_params, higher_levels, higher_tf)

    report = build_trend_context(
        args.symbol,
        bars,
        levels,
        args.scenario,
        args.direction,
        TrendParams(trend_swings=args.trend_swings, global_lookback_bars=args.global_lookback_bars),
    )
    report["level_summary"] = build_report(args.symbol, args.interval, higher_tf, bars[-1].close, levels)["summary"]

    if args.output_format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_report(report)


if __name__ == "__main__":
    main()