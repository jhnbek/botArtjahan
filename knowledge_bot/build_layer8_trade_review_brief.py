from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from multitimeframe_trade_review_packet import (
    MultiTimeframeReviewParams,
    build_multitimeframe_trade_review_packet_from_data_source,
)
from validate_layer7_real_chart_cases import (
    ROOT,
    SAFETY_FLAGS,
    load_json,
    rel_path,
    table_cell,
    validate_case_schema,
)


VERSION = "layer8_trade_review_brief_v1"
DEFAULT_OUT_DIR = ROOT / "_knowledge_base" / "structured" / "consolidation" / "layer8_trade_review_briefs"


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def output_stem(path: Path) -> str:
    name = path.name
    if name.endswith(".template.json"):
        return name[:-len(".template.json")]
    if name.endswith(".json"):
        return name[:-len(".json")]
    return path.stem


def parse_open_time(value: Any) -> datetime | None:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value) / 1000, tz=timezone.utc)
    if isinstance(value, str) and value.strip():
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    return None


def month_from_row(row: dict[str, Any]) -> str | None:
    dt = parse_open_time(row.get("open_time") or row.get("time"))
    return None if dt is None else f"{dt.year:04d}-{dt.month:02d}"


def case_month_range(case: dict[str, Any]) -> tuple[str, str]:
    bars = case.get("bars") or {}
    rows = (bars.get("context") or []) + (bars.get("execution") or [])
    months = [month for row in rows if isinstance(row, dict) for month in [month_from_row(row)] if month]
    if not months:
        raise ValueError("case bars do not contain open_time/time values for month range detection")
    return min(months), max(months)


def human_review_blockers(case: dict[str, Any], case_path: Path, schema_errors: list[str]) -> list[str]:
    blockers: list[str] = []
    if case_path.name.endswith(".template.json"):
        blockers.append("file_is_template_json_and_is_ignored_by_layer7_validator")
    human_review = case.get("human_review")
    if not isinstance(human_review, dict):
        blockers.append("human_review_missing")
    else:
        for key in ["ohlc_reviewed", "levels_reviewed", "expectations_reviewed"]:
            if human_review.get(key) is not True:
                blockers.append(f"human_review.{key}_not_true")
        if not str(human_review.get("reviewed_by") or "").strip():
            blockers.append("human_review.reviewed_by_missing")
        if not str(human_review.get("reviewed_at") or "").strip():
            blockers.append("human_review.reviewed_at_missing")
    blockers.extend(schema_errors)
    return blockers


def decision_preview(decision: dict[str, Any]) -> dict[str, Any]:
    return {
        "timeframe": decision.get("timeframe"),
        "status": decision.get("status"),
        "family": decision.get("family"),
        "direction": decision.get("direction"),
        "approach_status": decision.get("approach_status"),
        "diagnosis": decision.get("diagnosis"),
        "trend_summary": decision.get("trend_summary"),
        "best_entry_model": decision.get("best_entry_model"),
        "best_entry_status": decision.get("best_entry_status"),
        "structure_audit_status": decision.get("structure_audit_status"),
        "structure_kind": decision.get("structure_kind"),
        "blockers": decision.get("blockers", []),
        "manual_review": decision.get("manual_review", []),
    }


def build_layer8_packet_for_case(case: dict[str, Any], setup_timeframe: str,
                                 start_month: str | None = None, end_month: str | None = None) -> dict[str, Any]:
    timeframes = case.get("timeframes") or {}
    detected_start, detected_end = case_month_range(case)
    start = start_month or detected_start
    end = end_month or detected_end
    return build_multitimeframe_trade_review_packet_from_data_source(
        symbol=str(case["symbol"]),
        higher_timeframe=str(timeframes.get("context") or "1d"),
        setup_timeframe=setup_timeframe,
        execution_timeframe=str(timeframes.get("execution") or "15m"),
        start=str(start),
        end=str(end),
        breakout_direction_arg=str(case.get("breakout_direction", "auto")),
        manual_context=case.get("manual_context") or {},
        params=MultiTimeframeReviewParams(execution_lookback_bars=int(case.get("execution_lookback_bars", 96))),
    )


def build_summary(case_path: Path, setup_timeframe: str = "1h",
                  start_month: str | None = None, end_month: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    case = load_json(case_path)
    if not isinstance(case, dict):
        raise ValueError("case file must contain a JSON object")
    schema_errors = validate_case_schema(case, case_path)
    detected_start, detected_end = case_month_range(case)
    packet = build_layer8_packet_for_case(case, setup_timeframe, start_month, end_month)
    decisions = packet.get("timeframe_decisions") or {}
    alignment = packet.get("alignment_summary") or {}
    execution_packet = packet.get("execution_packet") or {}
    execution_decision = decisions.get("execution") or {}
    blockers = human_review_blockers(case, case_path, schema_errors)
    summary = {
        "version": VERSION,
        "generated_at": utc_now(),
        "case_file": rel_path(case_path),
        "case_id": case.get("case_id"),
        "case_origin": case.get("case_origin"),
        "template_ignored_by_validator": case_path.name.endswith(".template.json"),
        "human_review_complete": not blockers,
        "promotion_blockers": blockers,
        "symbol": case.get("symbol"),
        "source_timeframes": case.get("timeframes"),
        "layer8_timeframes": packet.get("timeframes"),
        "data_window": {
            "detected_start_month": detected_start,
            "detected_end_month": detected_end,
            "used_start_month": start_month or detected_start,
            "used_end_month": end_month or detected_end,
        },
        "packet_preview": {
            "review_status": packet.get("review_status"),
            "alignment_status": alignment.get("status"),
            "alignment_blockers": alignment.get("blockers", []),
            "alignment_manual_review": alignment.get("manual_review", []),
            "execution_review_status": execution_decision.get("status"),
            "execution_best_entry_model": execution_decision.get("best_entry_model"),
            "execution_best_entry_status": execution_decision.get("best_entry_status"),
            "execution_structure_audit_status": execution_decision.get("structure_audit_status"),
            "blocker_count": len(packet.get("blockers", [])),
            "manual_review_count": len(packet.get("manual_review_queue", [])),
        },
        "timeframe_decisions": {
            "higher": decision_preview(decisions.get("higher") or {}),
            "setup": decision_preview(decisions.get("setup") or {}),
            "execution": decision_preview(execution_decision),
        },
        "alignment_summary": alignment,
        "execution_packet_preview": {
            "detector": execution_packet.get("detector"),
            "review_status": execution_packet.get("review_status"),
            "review_structure_audit": execution_packet.get("review_structure_audit") or {},
            "checklist_status_counts": (((execution_packet.get("checklist_matrix") or {}).get("summary") or {}).get("status_counts") or {}),
        },
        **SAFETY_FLAGS,
    }
    return summary, packet


def render_markdown(summary: dict[str, Any], packet: dict[str, Any]) -> str:
    preview = summary["packet_preview"]
    lines = [
        "# Layer 8 Multi-Timeframe Trade Review Brief",
        "",
        "## Verdict",
        "",
        f"- Generated: `{summary['generated_at']}`",
        f"- Case: `{summary['case_id']}`",
        f"- File: `{summary['case_file']}`",
        f"- Review status: `{preview['review_status']}`",
        f"- Alignment status: `{preview['alignment_status']}`",
        f"- Template ignored by Layer 7 validator: `{str(summary['template_ignored_by_validator']).lower()}`",
        f"- Human review complete: `{str(summary['human_review_complete']).lower()}`",
        f"- Execution allowed: `{str(summary['execution_allowed']).lower()}`",
        f"- PnL computation allowed: `{str(summary['pnl_computation_allowed']).lower()}`",
        f"- Backtest harness allowed: `{str(summary['backtest_harness_allowed']).lower()}`",
        "",
    ]
    if summary["promotion_blockers"]:
        lines.extend(["## Promotion Blockers", ""])
        for blocker in summary["promotion_blockers"]:
            lines.append(f"- `{blocker}`")
        lines.append("")
    lines.extend([
        "## Data Window",
        "",
        f"- Symbol: `{summary['symbol']}`",
        f"- Source timeframes: `{json.dumps(summary['source_timeframes'], ensure_ascii=False)}`",
        f"- Layer 8 timeframes: `{json.dumps(summary['layer8_timeframes'], ensure_ascii=False)}`",
        f"- Used months: `{summary['data_window']['used_start_month']}` .. `{summary['data_window']['used_end_month']}`",
        "",
        "## Timeframe Decisions",
        "",
        "| Layer | Timeframe | Status | Family | Direction | Key Evidence |",
        "| --- | --- | --- | --- | --- | --- |",
    ])
    for key in ["higher", "setup", "execution"]:
        decision = summary["timeframe_decisions"][key]
        evidence = {
            "diagnosis": decision.get("diagnosis"),
            "best_entry": [decision.get("best_entry_model"), decision.get("best_entry_status")],
            "structure": [decision.get("structure_audit_status"), decision.get("structure_kind")],
            "blockers": decision.get("blockers", []),
        }
        lines.append("| " + " | ".join([
            table_cell(key),
            table_cell(decision.get("timeframe")),
            table_cell(decision.get("status")),
            table_cell(decision.get("family")),
            table_cell(decision.get("direction")),
            table_cell(evidence),
        ]) + " |")
    lines.extend([
        "",
        "## Alignment",
        "",
        f"- Status: `{summary['alignment_summary'].get('status')}`",
        f"- Higher: `{summary['alignment_summary'].get('higher_family')}` / `{summary['alignment_summary'].get('higher_direction')}`",
        f"- Setup: `{summary['alignment_summary'].get('setup_family')}` / `{summary['alignment_summary'].get('setup_direction')}`",
        f"- Execution: `{summary['alignment_summary'].get('execution_family')}` / `{summary['alignment_summary'].get('execution_direction')}`",
        f"- Blockers: `{json.dumps(summary['alignment_summary'].get('blockers', []), ensure_ascii=False)}`",
        f"- Manual review: `{json.dumps(summary['alignment_summary'].get('manual_review', []), ensure_ascii=False)}`",
        "",
        "## Execution Packet",
        "",
        f"- Review status: `{summary['execution_packet_preview'].get('review_status')}`",
        f"- Checklist counts: `{json.dumps(summary['execution_packet_preview'].get('checklist_status_counts', {}), ensure_ascii=False, sort_keys=True)}`",
        f"- Structure audit status: `{summary['execution_packet_preview'].get('review_structure_audit', {}).get('status')}`",
        f"- Structure kind: `{summary['execution_packet_preview'].get('review_structure_audit', {}).get('structure_kind')}`",
        f"- Planned stop crossed in review window: `{summary['execution_packet_preview'].get('review_structure_audit', {}).get('planned_stop_crossed_in_review_window')}`",
        "",
        "## What To Review Visually",
        "",
    ])
    visual_items = []
    for decision in summary["timeframe_decisions"].values():
        visual_items.extend(decision.get("manual_review") or [])
    visual_items.extend(summary["alignment_summary"].get("manual_review", []))
    if visual_items:
        for item in sorted(set(str(item) for item in visual_items))[:30]:
            lines.append(f"- `{item}`")
    else:
        lines.append("- none")
    lines.extend(["", "## Blockers", ""])
    blockers = packet.get("blockers", [])
    if blockers:
        for item in blockers[:30]:
            lines.append(f"- `{item.get('item_id')}` `{item.get('status')}`: {item.get('text')} -- {item.get('evidence')}")
    else:
        lines.append("- none")
    lines.append("")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a Layer 8 multi-timeframe review brief for a Layer 7 case/draft")
    parser.add_argument("case_file", help="Path to a Layer 7 .template.json or .json case")
    parser.add_argument("--setup-interval", default="1h", help="Setup timeframe between case context and execution")
    parser.add_argument("--start", help="Override start month YYYY-MM")
    parser.add_argument("--end", help="Override end month YYYY-MM")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--write", action="store_true", help="Write markdown and JSON brief outputs")
    parser.add_argument("--print-json", action="store_true", help="Print JSON summary instead of compact status")
    return parser


def main() -> int:
    configure_stdio()
    args = build_parser().parse_args()
    case_path = Path(args.case_file)
    summary, packet = build_summary(case_path, args.setup_interval, args.start, args.end)
    out_dir = Path(args.out_dir)
    stem = output_stem(case_path)
    md_path = out_dir / f"{stem}.layer8.review.md"
    json_path = out_dir / f"{stem}.layer8.review.json"
    if args.write:
        write_text(md_path, render_markdown(summary, packet))
        write_json(json_path, summary)
    status = {
        "version": summary["version"],
        "case_id": summary["case_id"],
        "human_review_complete": summary["human_review_complete"],
        "template_ignored_by_validator": summary["template_ignored_by_validator"],
        "review_status": summary["packet_preview"]["review_status"],
        "alignment_status": summary["packet_preview"]["alignment_status"],
        "execution_review_status": summary["packet_preview"]["execution_review_status"],
        "blocker_count": summary["packet_preview"]["blocker_count"],
        "manual_review_count": summary["packet_preview"]["manual_review_count"],
        "markdown_report": rel_path(md_path) if args.write else None,
        "json_report": rel_path(json_path) if args.write else None,
        **SAFETY_FLAGS,
    }
    print(json.dumps(summary if args.print_json else status, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())