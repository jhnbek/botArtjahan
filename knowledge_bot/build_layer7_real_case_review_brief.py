from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from validate_layer7_real_chart_cases import (
    ROOT,
    SAFETY_FLAGS,
    build_packet,
    load_json,
    rel_path,
    table_cell,
    validate_case_schema,
    validate_packet_expectations,
)


VERSION = "layer7_real_case_review_brief_v1"
DEFAULT_OUT_DIR = ROOT / "_knowledge_base" / "structured" / "consolidation" / "layer7_real_case_review_briefs"


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


def bar_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"count": 0}
    highs = [float(row["high"]) for row in rows]
    lows = [float(row["low"]) for row in rows]
    closes = [float(row["close"]) for row in rows]
    return {
        "count": len(rows),
        "first_open_time": rows[0].get("open_time") or rows[0].get("time"),
        "last_open_time": rows[-1].get("open_time") or rows[-1].get("time"),
        "low_min": min(lows),
        "high_max": max(highs),
        "first_close": closes[0],
        "last_close": closes[-1],
    }


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


def build_summary(case_path: Path) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    case = load_json(case_path)
    if not isinstance(case, dict):
        raise ValueError("case file must contain a JSON object")
    schema_errors = validate_case_schema(case, case_path)
    packet = build_packet(case)
    assertions = validate_packet_expectations(case, packet)
    failed_assertions = [row for row in assertions if not row.get("passed")]
    bars = case.get("bars") or {}
    checklist_counts = (packet.get("checklist_matrix") or {}).get("summary", {}).get("status_counts", {})
    permission = packet.get("permission_summary") or {}
    levels = case.get("levels") if isinstance(case.get("levels"), list) else []
    summary = {
        "version": VERSION,
        "generated_at": utc_now(),
        "case_file": rel_path(case_path),
        "case_id": case.get("case_id"),
        "case_origin": case.get("case_origin"),
        "template_ignored_by_validator": case_path.name.endswith(".template.json"),
        "human_review_complete": not human_review_blockers(case, case_path, schema_errors),
        "promotion_blockers": human_review_blockers(case, case_path, schema_errors),
        "symbol": case.get("symbol"),
        "timeframes": case.get("timeframes"),
        "context_bars": bar_summary(bars.get("context") or []),
        "execution_bars": bar_summary(bars.get("execution") or []),
        "level_count": len(levels),
        "levels": [
            {
                "price": level.get("price"),
                "side": level.get("side"),
                "basis_tags": level.get("basis_tags"),
                "touch_count": level.get("touch_count"),
                "exact_touch_count": level.get("exact_touch_count"),
                "kb_status": level.get("kb_status"),
                "kb_score": level.get("kb_score"),
                "distance_atr": level.get("distance_atr"),
                "active_after_last_touch": level.get("active_after_last_touch"),
            }
            for level in levels
        ],
        "packet_preview": {
            "review_status": packet.get("review_status"),
            "hard_gate_status": permission.get("hard_gate_status"),
            "hard_rejects": permission.get("hard_rejects", []),
            "missing_inputs": permission.get("missing_inputs", []),
            "best_entry_model": permission.get("best_entry_model"),
            "best_entry_status": permission.get("best_entry_status"),
            "checklist_status_counts": checklist_counts,
            "blocker_count": len(packet.get("blockers", [])),
            "manual_review_count": len(packet.get("manual_review_queue", [])),
            "review_structure_audit_status": (packet.get("review_structure_audit") or {}).get("status"),
        },
        "review_structure_audit": packet.get("review_structure_audit") or {},
        "expectation_assertions": {
            "assertion_count": len(assertions),
            "failed_assertion_count": len(failed_assertions),
            "all_generated_expectations_match_current_packet": not failed_assertions,
        },
        **SAFETY_FLAGS,
    }
    return summary, packet, assertions


def render_markdown(summary: dict[str, Any], packet: dict[str, Any], assertions: list[dict[str, Any]]) -> str:
    preview = summary["packet_preview"]
    lines = [
        "# Layer 7 Real Case Review Brief",
        "",
        "## Verdict",
        "",
        f"- Generated: `{summary['generated_at']}`",
        f"- Case: `{summary['case_id']}`",
        f"- File: `{summary['case_file']}`",
        f"- Template ignored by validator: `{str(summary['template_ignored_by_validator']).lower()}`",
        f"- Human review complete: `{str(summary['human_review_complete']).lower()}`",
        f"- Generated expectations match current packet: `{str(summary['expectation_assertions']['all_generated_expectations_match_current_packet']).lower()}`",
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
        "## Data Summary",
        "",
        f"- Symbol: `{summary['symbol']}`",
        f"- Timeframes: `{json.dumps(summary['timeframes'], ensure_ascii=False)}`",
        f"- Context bars: `{summary['context_bars']['count']}` from `{summary['context_bars'].get('first_open_time')}` to `{summary['context_bars'].get('last_open_time')}`",
        f"- Execution bars: `{summary['execution_bars']['count']}` from `{summary['execution_bars'].get('first_open_time')}` to `{summary['execution_bars'].get('last_open_time')}`",
        f"- Context range: low `{summary['context_bars'].get('low_min')}`, high `{summary['context_bars'].get('high_max')}`, close `{summary['context_bars'].get('first_close')}` -> `{summary['context_bars'].get('last_close')}`",
        "",
        "## Levels",
        "",
        "| Price | Side | KB | Score | Touches | Exact | Distance ATR | Basis | Active |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ])
    for level in summary["levels"]:
        lines.append("| " + " | ".join([
            table_cell(level.get("price")),
            table_cell(level.get("side")),
            table_cell(level.get("kb_status")),
            table_cell(level.get("kb_score")),
            table_cell(level.get("touch_count")),
            table_cell(level.get("exact_touch_count")),
            table_cell(level.get("distance_atr")),
            table_cell(level.get("basis_tags")),
            table_cell(level.get("active_after_last_touch")),
        ]) + " |")
    lines.extend([
        "",
        "## Packet Preview",
        "",
        f"- Review status: `{preview['review_status']}`",
        f"- Hard gate: `{preview['hard_gate_status']}`",
        f"- Best entry: `{preview['best_entry_model']}` / `{preview['best_entry_status']}`",
        f"- Checklist counts: `{json.dumps(preview['checklist_status_counts'], ensure_ascii=False, sort_keys=True)}`",
        f"- Review structure audit: `{preview.get('review_structure_audit_status')}`",
        f"- Hard rejects: `{json.dumps(preview['hard_rejects'], ensure_ascii=False)}`",
        f"- Missing inputs: `{json.dumps(preview['missing_inputs'], ensure_ascii=False)}`",
        "",
        "## Review Structure Audit",
        "",
        f"- Status: `{(summary.get('review_structure_audit') or {}).get('status')}`",
        f"- Trigger time: `{(summary.get('review_structure_audit') or {}).get('trigger_time')}`",
        f"- Entry / stop: `{(summary.get('review_structure_audit') or {}).get('entry_price')}` / `{(summary.get('review_structure_audit') or {}).get('stop_price')}`",
        f"- Trigger structure stop protected: `{(summary.get('review_structure_audit') or {}).get('trigger_structure_stop_protected')}`",
        f"- Planned stop crossed in review window: `{(summary.get('review_structure_audit') or {}).get('planned_stop_crossed_in_review_window')}`",
        f"- Post-trigger adverse extreme: `{(summary.get('review_structure_audit') or {}).get('post_trigger_adverse_extreme')}` at `{(summary.get('review_structure_audit') or {}).get('post_trigger_adverse_extreme_time')}`",
        f"- Audit blockers: `{json.dumps((summary.get('review_structure_audit') or {}).get('blockers', []), ensure_ascii=False)}`",
        "",
        "## Blockers",
        "",
    ])
    blockers = packet.get("blockers", [])
    if blockers:
        for item in blockers[:20]:
            lines.append(f"- `{item.get('item_id')}` `{item.get('status')}`: {item.get('text')} -- {item.get('evidence')}")
    else:
        lines.append("- none")
    lines.extend(["", "## Manual Review Queue", ""])
    manual_queue = packet.get("manual_review_queue", [])
    if manual_queue:
        for item in manual_queue[:30]:
            lines.append(f"- `{item.get('item_id')}`: {item.get('text')} -- {item.get('evidence')}")
    else:
        lines.append("- none")
    failed = [row for row in assertions if not row.get("passed")]
    lines.extend([
        "",
        "## Expectation Check",
        "",
        f"- Assertions: `{len(assertions)}`",
        f"- Failed: `{len(failed)}`",
    ])
    for row in failed:
        lines.append(f"- `{row.get('check_id')}` expected `{row.get('expected')}`, actual `{row.get('actual')}` -- {row.get('evidence')}")
    lines.append("")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a review brief for a Layer 7 real-case draft or case file")
    parser.add_argument("case_file", help="Path to a Layer 7 .template.json or .json case")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--write", action="store_true", help="Write markdown and JSON brief outputs")
    parser.add_argument("--print-json", action="store_true", help="Print JSON summary instead of a compact status")
    return parser


def main() -> int:
    configure_stdio()
    args = build_parser().parse_args()
    case_path = Path(args.case_file)
    summary, packet, assertions = build_summary(case_path)
    out_dir = Path(args.out_dir)
    stem = output_stem(case_path)
    md_path = out_dir / f"{stem}.review.md"
    json_path = out_dir / f"{stem}.review.json"
    if args.write:
        write_text(md_path, render_markdown(summary, packet, assertions))
        write_json(json_path, summary)
    status = {
        "version": summary["version"],
        "case_id": summary["case_id"],
        "human_review_complete": summary["human_review_complete"],
        "template_ignored_by_validator": summary["template_ignored_by_validator"],
        "promotion_blocker_count": len(summary["promotion_blockers"]),
        "generated_expectations_match_current_packet": summary["expectation_assertions"]["all_generated_expectations_match_current_packet"],
        "review_status": summary["packet_preview"]["review_status"],
        "hard_gate_status": summary["packet_preview"]["hard_gate_status"],
        "best_entry_model": summary["packet_preview"]["best_entry_model"],
        "best_entry_status": summary["packet_preview"]["best_entry_status"],
        "markdown_report": rel_path(md_path) if args.write else None,
        "json_report": rel_path(json_path) if args.write else None,
        **SAFETY_FLAGS,
    }
    print(json.dumps(summary if args.print_json else status, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())