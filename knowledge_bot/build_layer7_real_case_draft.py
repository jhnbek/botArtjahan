from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from chart_review_packet import ChartReviewParams, build_chart_review_packet, enrich_manual_context_with_vision
from level_discovery import DiscoveryParams, discover_levels
from permission_context import load_manual_context
from scn002_strict_kb_backtest import Bar, load_history


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES_DIR = ROOT / "_knowledge_base" / "scenario_review_casebook" / "layer7_real_chart_cases"
VERSION = "layer7_real_case_draft_builder_v1"

SAFETY_FLAGS: dict[str, bool] = {
    "execution_allowed": False,
    "runtime_signal_allowed": False,
    "order_generation_allowed": False,
    "pnl_computation_allowed": False,
    "paper_trading_allowed": False,
    "live_trading_allowed": False,
    "backtest_harness_allowed": False,
}


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def rel_path(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def slugify(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._")
    return cleaned or "layer7-real-case-draft"


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def bar_to_dict(bar: Bar) -> dict[str, Any]:
    return {
        "open_time": bar.dt.isoformat(),
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
    }


def packet_expectations(packet: dict[str, Any], *, max_asserted_items: int) -> dict[str, Any]:
    permission = packet.get("permission_summary") or {}
    entry = (packet.get("layer_reports") or {}).get("entry") or {}
    matrix = packet.get("checklist_matrix") or {}
    items = matrix.get("items") or []
    status_counts = matrix.get("summary", {}).get("status_counts", {})

    selected_ids: list[str] = []
    for row in items:
        if row.get("checklist_id") == "RSCD-000-hard-gates":
            selected_ids.append(str(row.get("item_id")))
    for row in items:
        if row.get("status") == "block":
            selected_ids.append(str(row.get("item_id")))
    for row in items:
        if row.get("status") == "manual_review":
            selected_ids.append(str(row.get("item_id")))

    deduped_ids: list[str] = []
    for item_id in selected_ids:
        if item_id and item_id not in deduped_ids:
            deduped_ids.append(item_id)
        if len(deduped_ids) >= max_asserted_items:
            break
    status_by_id = {str(row.get("item_id")): row.get("status") for row in items}

    return {
        "review_status": packet.get("review_status"),
        "entry_status": entry.get("status"),
        "hard_gate_status": permission.get("hard_gate_status"),
        "best_entry_model": permission.get("best_entry_model"),
        "best_entry_status": permission.get("best_entry_status"),
        "checklist_item_statuses": {item_id: status_by_id[item_id] for item_id in deduped_ids if item_id in status_by_id},
        "status_counts_at_least": {str(key): int(value) for key, value in status_counts.items()},
        "contains_blocker_items": [str(row.get("item_id")) for row in packet.get("blockers", [])[:max_asserted_items]],
        "contains_manual_review_items": [str(row.get("item_id")) for row in packet.get("manual_review_queue", [])[:max_asserted_items]],
        "safety_flags_false": True,
    }


def default_output_path(cases_dir: Path, case_id: str) -> Path:
    return cases_dir / f"{slugify(case_id)}.template.json"


def build_case_payload(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    symbol = args.symbol.strip().upper()
    manual_context = load_manual_context(args.manual_context_json)
    if args.screenshot:
        manual_context = enrich_manual_context_with_vision(
            manual_context,
            args.screenshot,
            model=args.vision_model,
            expected_symbol=symbol,
            expected_timeframes=[args.context_interval, args.execution_interval, args.higher_interval],
            run_vision=not args.skip_vision,
        )

    context_bars = load_history(symbol, args.context_interval, args.start, args.end)
    if not context_bars:
        raise ValueError(f"No context bars loaded for {symbol} {args.context_interval} {args.start}..{args.end}")
    execution_bars = load_history(symbol, args.execution_interval, args.start, args.end)
    if not execution_bars:
        raise ValueError(f"No execution bars loaded for {symbol} {args.execution_interval} {args.start}..{args.end}")

    level_params = DiscoveryParams()
    higher_levels = None
    if args.higher_interval and args.higher_interval != args.context_interval:
        higher_bars = load_history(symbol, args.higher_interval, args.start, args.end)
        higher_levels = discover_levels(higher_bars, level_params) if higher_bars else []
    levels = discover_levels(context_bars, level_params, higher_levels, args.higher_interval)

    params = ChartReviewParams(execution_lookback_bars=args.execution_lookback_bars)
    packet = build_chart_review_packet(
        symbol=symbol,
        context_timeframe=args.context_interval,
        execution_timeframe=args.execution_interval,
        context_bars=context_bars,
        execution_bars=execution_bars,
        levels=levels,
        higher_timeframe=args.higher_interval,
        breakout_direction_arg=args.breakout_direction,
        manual_context=manual_context,
        params=params,
    )

    payload: dict[str, Any] = {
        "case_id": args.case_id,
        "case_origin": "user_real_reviewed",
        "human_review": {
            "reviewed_by": "",
            "reviewed_at": "",
            "ohlc_reviewed": False,
            "levels_reviewed": False,
            "expectations_reviewed": False,
            "notes": "Set reviewed_by/reviewed_at and all reviewed flags after checking the draft, then rename from .template.json to .json.",
        },
        "title": args.title or f"Draft real chart case: {symbol} {args.context_interval}/{args.execution_interval} {args.start}..{args.end}",
        "symbol": symbol,
        "timeframes": {
            "context": args.context_interval,
            "execution": args.execution_interval,
            "higher": args.higher_interval,
        },
        "breakout_direction": args.breakout_direction,
        "execution_lookback_bars": args.execution_lookback_bars,
        "case_generation": {
            "version": VERSION,
            "generated_at": utc_now(),
            "source": "public Binance monthly klines loader used by Layer 6",
            "validation_loaded": False,
            "review_required_before_validation": True,
            "scope_note": "review-only draft; no outcome labels, PnL, order generation, paper/live trading, or execution",
            **SAFETY_FLAGS,
        },
        "bars": {
            "context": [bar_to_dict(bar) for bar in context_bars],
            "execution": [bar_to_dict(bar) for bar in execution_bars],
        },
        "levels": [asdict(level) for level in levels],
        "manual_context": manual_context,
        "expectations": packet_expectations(packet, max_asserted_items=args.max_asserted_items),
    }
    return payload, packet


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build an ignored Layer 7 user_real_reviewed case draft from real OHLC data."
    )
    parser.add_argument("--case-id", default="L7-USER-REAL-001")
    parser.add_argument("--title", default="")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--start", required=True, help="Start month in YYYY-MM format")
    parser.add_argument("--end", required=True, help="End month in YYYY-MM format")
    parser.add_argument("--context-interval", default="1d")
    parser.add_argument("--execution-interval", default="15m")
    parser.add_argument("--higher-interval", default="1w")
    parser.add_argument("--breakout-direction", choices=["auto", "long", "short"], default="auto")
    parser.add_argument("--execution-lookback-bars", type=int, default=ChartReviewParams.execution_lookback_bars)
    parser.add_argument("--manual-context-json", help="Optional JSON object or path accepted by permission_context.load_manual_context")
    parser.add_argument("--screenshot", help="Optional screenshot path to attach as review context")
    parser.add_argument("--skip-vision", action="store_true", help="Attach screenshot ref without calling the vision API")
    parser.add_argument("--vision-model", help="Optional OpenAI vision model override")
    parser.add_argument("--cases-dir", default=str(DEFAULT_CASES_DIR))
    parser.add_argument("--out-file", help="Explicit output path; should end with .template.json until human review is complete")
    parser.add_argument("--write", action="store_true", help="Write the draft template instead of only printing a summary")
    parser.add_argument("--print-case-json", action="store_true", help="Print the full draft JSON to stdout")
    parser.add_argument("--max-asserted-items", type=int, default=16)
    return parser


def main() -> int:
    configure_stdio()
    args = build_parser().parse_args()
    payload, packet = build_case_payload(args)
    out_path = Path(args.out_file) if args.out_file else default_output_path(Path(args.cases_dir), args.case_id)
    if out_path.suffix == ".json" and not out_path.name.endswith(".template.json"):
        print("error: output must end with .template.json until human_review is complete", file=sys.stderr)
        return 2
    if args.write:
        write_json(out_path, payload)

    summary = {
        "version": VERSION,
        "case_id": payload["case_id"],
        "case_origin": payload["case_origin"],
        "output_file": rel_path(out_path) if args.write else None,
        "validation_loaded": False,
        "next_step": "Review OHLC/levels/expectations, set human_review flags true, then rename from .template.json to .json and run validate_layer7_real_chart_cases.py.",
        "symbol": payload["symbol"],
        "context_bar_count": len(payload["bars"]["context"]),
        "execution_bar_count": len(payload["bars"]["execution"]),
        "level_count": len(payload["levels"]),
        "preview_review_status": packet.get("review_status"),
        "preview_hard_gate_status": (packet.get("permission_summary") or {}).get("hard_gate_status"),
        "preview_best_entry_model": (packet.get("permission_summary") or {}).get("best_entry_model"),
        "preview_best_entry_status": (packet.get("permission_summary") or {}).get("best_entry_status"),
        **SAFETY_FLAGS,
    }
    print(json.dumps(payload if args.print_case_json else summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())