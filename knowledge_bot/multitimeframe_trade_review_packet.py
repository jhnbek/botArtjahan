"""Layer 8: multi-timeframe trade review packet.

This layer is deliberately read-only. It separates the course workflow into a
higher-timeframe context, setup-timeframe context, and execution-timeframe entry
packet, then records alignment/manual-review gaps. It does not compute outcomes,
PnL, orders, paper trades, live trades, or backtests.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from approach_context import ApproachParams, build_approach_context
from chart_review_packet import ChartReviewParams, build_chart_review_packet, load_manual_context
from level_discovery import DiscoveryParams, Level, build_report as build_level_report, discover_levels
from scn002_strict_kb_backtest import Bar, load_history


ROOT = Path(__file__).resolve().parents[1]
VERSION = "layer8_multitimeframe_trade_review_packet_v1"

SOURCE_ARTIFACTS = [
    "_knowledge_base/structured/consolidation/signed_canonical_rulebook/signed_canonical_rulebook.md",
    "_knowledge_base/structured/consolidation/feature_contracts_validation/feature_contracts_validation.md",
    "_knowledge_base/structured/consolidation/updated_refined_checklist_retrieval/updated_refined_scenario_checklist_draft.md",
    "knowledge_bot/chart_review_packet.py",
]

SAFETY_FLAGS: dict[str, bool] = {
    "execution_allowed": False,
    "runtime_signal_allowed": False,
    "order_generation_allowed": False,
    "pnl_computation_allowed": False,
    "paper_trading_allowed": False,
    "live_trading_allowed": False,
    "backtest_harness_allowed": False,
}


@dataclass
class MultiTimeframeReviewParams:
    execution_lookback_bars: int = ChartReviewParams.execution_lookback_bars
    trend_swings: int = ApproachParams.trend_swings
    global_lookback_bars: int = ApproachParams.global_lookback_bars


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def bar_time(bar: Bar) -> str:
    return datetime.fromtimestamp(bar.open_time / 1000, tz=timezone.utc).isoformat()


def bar_span(bars: list[Bar]) -> dict[str, Any]:
    if not bars:
        return {"bar_count": 0, "first_time": None, "last_time": None}
    return {
        "bar_count": len(bars),
        "first_time": bar_time(bars[0]),
        "last_time": bar_time(bars[-1]),
        "last_close": bars[-1].close,
    }


def context_family_direction(approach: dict[str, Any]) -> tuple[str | None, str | None]:
    diagnosis = approach.get("diagnosis") or {}
    directions = approach.get("directions") or {}
    preferred = str(diagnosis.get("preferred_bias") or "").lower()
    if preferred == "breakout":
        return "breakout", directions.get("breakout")
    if "false" in preferred or "lp" in preferred or "reversal" in preferred:
        return "false_breakout", directions.get("false_breakout_reversal")
    if preferred == "no_trade":
        return "no_trade", None
    return None, None


def build_context_decision(symbol: str, timeframe: str, bars: list[Bar], levels: list[Level],
                           higher_timeframe: str, breakout_direction_arg: str,
                           params: MultiTimeframeReviewParams) -> dict[str, Any]:
    decision: dict[str, Any] = {
        "timeframe": timeframe,
        "bar_span": bar_span(bars),
        "level_summary": None,
        "status": "manual_review_required",
        "family": None,
        "direction": None,
        "manual_review": [],
        "blockers": [],
    }
    if not bars:
        decision["status"] = "blocked_review_only"
        decision["blockers"].append("no_bars_for_timeframe")
        return decision

    decision["level_summary"] = build_level_report(
        symbol,
        timeframe,
        higher_timeframe,
        bars[-1].close,
        levels,
    ).get("summary")
    try:
        approach = build_approach_context(
            symbol,
            timeframe,
            bars,
            levels,
            breakout_direction_arg,
            ApproachParams(trend_swings=params.trend_swings, global_lookback_bars=params.global_lookback_bars),
        )
    except Exception as exc:  # noqa: BLE001 - packet records review blockers instead of raising.
        decision["status"] = "blocked_review_only"
        decision["blockers"].append(f"approach_context_error:{type(exc).__name__}")
        decision["error"] = str(exc)
        return decision

    family, direction = context_family_direction(approach)
    decision.update({
        "approach_status": approach.get("status"),
        "diagnosis": approach.get("diagnosis"),
        "trend_summary": approach.get("trend_summary"),
        "nearest_level": approach.get("nearest_level"),
        "family": family,
        "direction": direction,
        "manual_review": approach.get("manual_review", []),
    })
    if approach.get("status") == "no_working_level":
        decision["status"] = "blocked_review_only"
        decision["blockers"].append("no_working_level")
    elif family in {"breakout", "false_breakout"} and direction in {"long", "short"}:
        decision["status"] = "context_ready"
    elif family == "no_trade":
        decision["status"] = "blocked_review_only"
        decision["blockers"].append("context_prefers_no_trade")
    else:
        decision["status"] = "manual_review_required"
        decision["manual_review"].append("timeframe_context_not_decisive")
    return decision


def execution_decision_from_packet(packet: dict[str, Any]) -> dict[str, Any]:
    entry_report = (packet.get("layer_reports") or {}).get("entry") or {}
    scenario = entry_report.get("scenario") or {}
    best = entry_report.get("best_entry") or {}
    audit = packet.get("review_structure_audit") or {}
    return {
        "timeframe": (packet.get("timeframes") or {}).get("execution"),
        "status": packet.get("review_status"),
        "family": scenario.get("family"),
        "direction": best.get("direction") or scenario.get("direction"),
        "best_entry_model": best.get("model"),
        "best_entry_status": best.get("status"),
        "structure_audit_status": audit.get("status"),
        "structure_kind": audit.get("structure_kind"),
        "checklist_status_counts": ((packet.get("checklist_matrix") or {}).get("summary") or {}).get("status_counts", {}),
        "blocker_count": len(packet.get("blockers") or []),
        "manual_review_count": len(packet.get("manual_review_queue") or []),
    }


def build_alignment_summary(higher_decision: dict[str, Any], setup_decision: dict[str, Any],
                            execution_decision: dict[str, Any]) -> dict[str, Any]:
    manual_review: list[str] = []
    blockers: list[str] = []
    higher_direction = higher_decision.get("direction")
    setup_direction = setup_decision.get("direction")
    execution_direction = execution_decision.get("direction")
    higher_family = higher_decision.get("family")
    setup_family = setup_decision.get("family")

    if higher_decision.get("status") != "context_ready":
        manual_review.append("higher_timeframe_context_not_decisive")
    if setup_decision.get("status") != "context_ready":
        manual_review.append("setup_timeframe_context_not_decisive")
    if higher_direction in {"long", "short"} and setup_direction in {"long", "short"} and higher_direction != setup_direction:
        manual_review.append("higher_setup_direction_conflict")
    if higher_family in {"breakout", "false_breakout"} and setup_family in {"breakout", "false_breakout"} and higher_family != setup_family:
        manual_review.append("higher_setup_family_conflict")
    if setup_direction in {"long", "short"} and execution_direction in {"long", "short"} and setup_direction != execution_direction:
        blockers.append("execution_direction_conflicts_with_setup_context")
    if execution_decision.get("status") == "blocked_review_only":
        blockers.append("execution_packet_blocked")

    if blockers:
        status = "structural_conflict"
    elif manual_review:
        status = "manual_review_required"
    else:
        status = "pass"
    return {
        "status": status,
        "higher_family": higher_family,
        "higher_direction": higher_direction,
        "setup_family": setup_family,
        "setup_direction": setup_direction,
        "execution_family": execution_decision.get("family"),
        "execution_direction": execution_direction,
        "manual_review": sorted(set(manual_review)),
        "blockers": sorted(set(blockers)),
    }


def layer8_review_status(execution_packet: dict[str, Any], alignment: dict[str, Any]) -> str:
    if execution_packet.get("review_status") == "blocked_review_only" or alignment.get("blockers"):
        return "blocked_review_only"
    if execution_packet.get("review_status") == "manual_review_required" or alignment.get("manual_review"):
        return "manual_review_required"
    return "checklist_complete_review_only"


def build_multitimeframe_trade_review_packet(
    symbol: str,
    higher_timeframe: str,
    setup_timeframe: str,
    execution_timeframe: str,
    higher_bars: list[Bar],
    setup_bars: list[Bar],
    execution_bars: list[Bar],
    breakout_direction_arg: str = "auto",
    manual_context: dict[str, Any] | None = None,
    params: MultiTimeframeReviewParams | None = None,
    higher_levels: list[Level] | None = None,
    setup_levels: list[Level] | None = None,
) -> dict[str, Any]:
    manual_context = manual_context or {}
    params = params or MultiTimeframeReviewParams()
    level_params = DiscoveryParams()
    if higher_levels is None:
        higher_levels = discover_levels(higher_bars, level_params) if higher_bars else []
    if setup_levels is None:
        setup_levels = discover_levels(setup_bars, level_params, higher_levels, higher_timeframe) if setup_bars else []

    higher_decision = build_context_decision(
        symbol,
        higher_timeframe,
        higher_bars,
        higher_levels,
        higher_timeframe,
        breakout_direction_arg,
        params,
    )
    setup_decision = build_context_decision(
        symbol,
        setup_timeframe,
        setup_bars,
        setup_levels,
        higher_timeframe,
        breakout_direction_arg,
        params,
    )
    execution_packet = build_chart_review_packet(
        symbol,
        setup_timeframe,
        execution_timeframe,
        setup_bars,
        execution_bars,
        setup_levels,
        higher_timeframe,
        breakout_direction_arg,
        manual_context,
        ChartReviewParams(execution_lookback_bars=params.execution_lookback_bars),
    )
    execution_decision = execution_decision_from_packet(execution_packet)
    alignment = build_alignment_summary(higher_decision, setup_decision, execution_decision)
    audit_blockers = [
        {
            "item_id": f"L8-ALIGN-{index:03d}",
            "text": "Multi-timeframe context conflicts with the execution review",
            "status": "block",
            "evidence": blocker,
            "source": "layer8_multitimeframe_alignment",
            "source_rule_ids": ["CRD-007B-timeframe-workflow", "CRD-007C-context-conflicts"],
        }
        for index, blocker in enumerate(alignment.get("blockers", []), start=1)
    ]
    manual_items = [
        {
            "item_id": f"L8-MANUAL-{index:03d}",
            "text": "Multi-timeframe review requires manual context confirmation",
            "status": "manual_review",
            "evidence": item,
            "source": "layer8_multitimeframe_alignment",
            "source_rule_ids": ["CRD-007B-timeframe-workflow", "CRD-007C-context-conflicts"],
        }
        for index, item in enumerate(alignment.get("manual_review", []), start=1)
    ]
    return {
        "version": VERSION,
        "generated_at": utc_now(),
        "symbol": symbol,
        "detector": "layer8_multitimeframe_trade_review_packet",
        "review_status": layer8_review_status(execution_packet, alignment),
        "source_artifacts": SOURCE_ARTIFACTS,
        "timeframes": {
            "higher": higher_timeframe,
            "setup": setup_timeframe,
            "execution": execution_timeframe,
        },
        "timeframe_decisions": {
            "higher": higher_decision,
            "setup": setup_decision,
            "execution": execution_decision,
        },
        "alignment_summary": alignment,
        "execution_packet": execution_packet,
        "manual_review_queue": manual_items + execution_packet.get("manual_review_queue", []),
        "blockers": audit_blockers + execution_packet.get("blockers", []),
        **SAFETY_FLAGS,
    }


def build_multitimeframe_trade_review_packet_from_data_source(
    symbol: str,
    higher_timeframe: str,
    setup_timeframe: str,
    execution_timeframe: str,
    start: str,
    end: str,
    breakout_direction_arg: str = "auto",
    manual_context: dict[str, Any] | None = None,
    params: MultiTimeframeReviewParams | None = None,
) -> dict[str, Any]:
    higher_bars = load_history(symbol, higher_timeframe, start, end)
    setup_bars = load_history(symbol, setup_timeframe, start, end)
    execution_bars = load_history(symbol, execution_timeframe, start, end)
    if not higher_bars:
        raise ValueError(f"No higher timeframe data for {symbol} {higher_timeframe} {start}..{end}")
    if not setup_bars:
        raise ValueError(f"No setup timeframe data for {symbol} {setup_timeframe} {start}..{end}")
    if not execution_bars:
        raise ValueError(f"No execution timeframe data for {symbol} {execution_timeframe} {start}..{end}")
    return build_multitimeframe_trade_review_packet(
        symbol,
        higher_timeframe,
        setup_timeframe,
        execution_timeframe,
        higher_bars,
        setup_bars,
        execution_bars,
        breakout_direction_arg,
        manual_context,
        params,
    )


def print_report(packet: dict[str, Any]) -> None:
    print("=" * 78)
    print(f"MULTI-TIMEFRAME TRADE REVIEW PACKET - {packet['symbol']}")
    print("rules: D1/H1/execution workflow + Layer 6 chart review packet")
    print("=" * 78)
    print(f"review_status: {packet['review_status']}")
    print("execution_allowed: false")
    print(f"timeframes: higher={packet['timeframes']['higher']} setup={packet['timeframes']['setup']} execution={packet['timeframes']['execution']}")
    for key in ["higher", "setup", "execution"]:
        decision = packet["timeframe_decisions"][key]
        print("-" * 78)
        print(f"{key}: status={decision.get('status')} family={decision.get('family')} direction={decision.get('direction')}")
        if decision.get("best_entry_model"):
            print(f"  best_entry={decision.get('best_entry_model')} / {decision.get('best_entry_status')} structure={decision.get('structure_audit_status')}")
        if decision.get("blockers"):
            print(f"  blockers={', '.join(decision['blockers'])}")
        if decision.get("manual_review"):
            print(f"  manual={', '.join(decision['manual_review'][:6])}")
    alignment = packet["alignment_summary"]
    print("-" * 78)
    print(f"alignment: status={alignment['status']} blockers={len(alignment['blockers'])} manual={len(alignment['manual_review'])}")
    if packet["blockers"]:
        print("blockers:")
        for item in packet["blockers"][:12]:
            print(f"  {item['item_id']}: {item['evidence']}")
    print("=" * 78)


def main() -> None:
    configure_stdio()
    parser = argparse.ArgumentParser(description="Layer 8: read-only multi-timeframe trade review packet")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--higher-interval", default="1d")
    parser.add_argument("--setup-interval", default="1h")
    parser.add_argument("--execution-interval", default="15m")
    parser.add_argument("--start", required=True, help="Start month in YYYY-MM format")
    parser.add_argument("--end", required=True, help="End month in YYYY-MM format")
    parser.add_argument("--breakout-direction", choices=["auto", "long", "short"], default="auto")
    parser.add_argument("--execution-lookback-bars", type=int, default=MultiTimeframeReviewParams.execution_lookback_bars)
    parser.add_argument("--manual-context-json", help="Optional JSON object with chart/manual context")
    parser.add_argument("--output-format", choices=["text", "json"], default="text")
    args = parser.parse_args()

    manual_context = load_manual_context(args.manual_context_json)
    print(
        f"Loading {args.symbol} {args.higher_interval}/{args.setup_interval}/{args.execution_interval} {args.start}..{args.end} ...",
        file=sys.stderr,
    )
    try:
        packet = build_multitimeframe_trade_review_packet_from_data_source(
            args.symbol,
            args.higher_interval,
            args.setup_interval,
            args.execution_interval,
            args.start,
            args.end,
            args.breakout_direction,
            manual_context,
            MultiTimeframeReviewParams(execution_lookback_bars=args.execution_lookback_bars),
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    if args.output_format == "json":
        json.dump(packet, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        print_report(packet)


if __name__ == "__main__":
    main()