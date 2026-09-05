from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from validate_layer7_real_chart_cases import (
    DEFAULT_CASES_DIR,
    ROOT,
    SAFETY_FLAGS,
    build_packet,
    case_files,
    load_json,
    rel_path,
    table_cell,
    write_json,
    write_jsonl,
)


VERSION = "layer9_trader_alignment_validation_v1"
OUTPUT_DIR = ROOT / "_knowledge_base" / "structured" / "consolidation" / "layer9_trader_alignment_validation"
LABEL_KEYS = ("ai_kb_trader_label", "human_confirmed_label")
DRAFT_ALIGNMENT_AUDIT_FILES = (
    "L7-USER-REAL-004.template.json",
    "L7-USER-REAL-005.template.json",
    "L7-USER-REAL-006.template.json",
    "L7-USER-REAL-007.template.json",
    "L7-USER-REAL-008.template.json",
    "L7-USER-REAL-009.template.json",
    "L7-USER-REAL-010.template.json",
    "L7-USER-REAL-011.template.json",
    "L7-USER-REAL-012.template.json",
    "L7-USER-REAL-013.template.json",
    "L7-USER-REAL-014.template.json",
    "L7-USER-REAL-015.template.json",
    "L7-USER-REAL-016.template.json",
    "L7-USER-REAL-017.template.json",
    "L7-USER-REAL-018.template.json",
    "L7-USER-REAL-019.template.json",
    "L7-USER-REAL-020.template.json",
    "L7-USER-REAL-021.template.json",
    "L7-USER-REAL-022.template.json",
    "L7-USER-REAL-023.template.json",
    "L7-USER-REAL-024.template.json",
    "L7-USER-REAL-025.template.json",
    "L7-USER-REAL-026.template.json",
    "L7-USER-REAL-027.template.json",
    "L7-USER-REAL-028.template.json",
    "L7-USER-REAL-029.template.json",
    "L7-USER-REAL-030.template.json",
    "L7-USER-REAL-031.template.json",
    "L7-USER-REAL-032.template.json",
    "L7-USER-REAL-033.template.json",
    "L7-USER-REAL-034.template.json",
    "L7-USER-REAL-035.template.json",
    "L7-USER-REAL-036.template.json",
    "L7-USER-REAL-037.template.json",
    "L7-USER-REAL-038.template.json",
    "L7-USER-REAL-039.template.json",
    "L7-USER-REAL-040.template.json",
    "L7-USER-REAL-041.template.json",
    "L7-USER-REAL-042.template.json",
    "L7-USER-REAL-043.template.json",
    "L7-USER-REAL-044.template.json",
    "L7-USER-REAL-045.template.json",
    "L7-USER-REAL-046.template.json",
    "L7-USER-REAL-047.template.json",
)
VALID_CONFIDENCE = {"high", "medium", "low"}
VALID_LABEL_ORIGINS = {"ai_kb_trader_review", "human_confirmed_review"}
FORBIDDEN_LABEL_KEYS = {
    "actual_pnl",
    "filled_order_id",
    "loss",
    "order_id",
    "outcome",
    "outcome_label",
    "pnl",
    "pnl_r",
    "profit",
    "sl_hit",
    "tp_hit",
    "trade_result",
}


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def scan_forbidden_label_keys(value: Any, path: str = "$", findings: list[str] | None = None) -> list[str]:
    findings = findings or []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if str(key).lower() in FORBIDDEN_LABEL_KEYS:
                findings.append(child_path)
            scan_forbidden_label_keys(child, child_path, findings)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            scan_forbidden_label_keys(child, f"{path}[{index}]", findings)
    return findings


def label_entries(case: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    labels: list[tuple[str, dict[str, Any]]] = []
    for key in LABEL_KEYS:
        value = case.get(key)
        if isinstance(value, dict):
            labels.append((key, value))
        elif value is not None:
            labels.append((key, {"_schema_error": f"{key} must be an object"}))
    return labels


def validate_label_schema(case: dict[str, Any], label_key: str, label: dict[str, Any]) -> list[str]:
    if label.get("_schema_error"):
        return [str(label["_schema_error"])]
    errors: list[str] = []
    label_origin = str(label.get("label_origin") or "")
    if label_origin not in VALID_LABEL_ORIGINS:
        errors.append(f"{label_key}.label_origin must be one of {sorted(VALID_LABEL_ORIGINS)}")
    if label_key == "ai_kb_trader_label" and label_origin != "ai_kb_trader_review":
        errors.append("ai_kb_trader_label must use label_origin=ai_kb_trader_review")
    if label_key == "human_confirmed_label" and label_origin != "human_confirmed_review":
        errors.append("human_confirmed_label must use label_origin=human_confirmed_review")
    if str(label.get("confidence") or "") not in VALID_CONFIDENCE:
        errors.append(f"{label_key}.confidence must be one of {sorted(VALID_CONFIDENCE)}")
    if not str(label.get("reviewed_by") or "").strip():
        errors.append(f"{label_key}.reviewed_by is required")
    if not str(label.get("reviewed_at") or "").strip():
        errors.append(f"{label_key}.reviewed_at is required")
    expected = label.get("expected")
    if not isinstance(expected, dict) or not expected:
        errors.append(f"{label_key}.expected must be a non-empty object")
    forbidden = scan_forbidden_label_keys(label)
    if forbidden:
        errors.append(f"{label_key} contains forbidden outcome/PnL/order fields: {', '.join(forbidden)}")
    if case.get("case_origin") == "user_real_reviewed" and label_key == "ai_kb_trader_label":
        if not str(label.get("blind_review_notes") or "").strip():
            errors.append("user_real_reviewed ai_kb_trader_label requires blind_review_notes")
    return errors


def checklist_statuses(packet: dict[str, Any]) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for item in (packet.get("checklist_matrix") or {}).get("items") or []:
        item_id = item.get("item_id")
        status = item.get("status")
        if isinstance(item_id, str) and isinstance(status, str):
            statuses[item_id] = status
    return statuses


def item_ids(rows: list[dict[str, Any]]) -> list[str]:
    return sorted({str(row.get("item_id")) for row in rows if row.get("item_id")})


def main_working_level(packet: dict[str, Any]) -> dict[str, Any] | None:
    levels = ((packet.get("layer_reports") or {}).get("levels") or {}).get("levels") or []
    working = [level for level in levels if isinstance(level, dict) and level.get("decision") == "working_level"]
    if not working:
        return None
    return min(working, key=lambda level: float(level.get("distance_atr") or 999999.0))


def bot_summary(packet: dict[str, Any]) -> dict[str, Any]:
    entry = ((packet.get("layer_reports") or {}).get("entry") or {})
    scenario = entry.get("scenario") or {}
    best_entry = entry.get("best_entry") or {}
    permission = packet.get("permission_summary") or {}
    audit = packet.get("review_structure_audit") or {}
    level_summary = packet.get("level_summary") or {}
    main_level = main_working_level(packet)
    chart_context = packet.get("chart_context") or {}
    return {
        "review_status": packet.get("review_status"),
        "hard_gate_status": permission.get("hard_gate_status"),
        "best_entry_model": permission.get("best_entry_model"),
        "best_entry_status": permission.get("best_entry_status"),
        "entry_status": entry.get("status"),
        "scenario_family": scenario.get("family"),
        "entry_direction": best_entry.get("direction") or scenario.get("direction"),
        "main_level_status": "working_level" if main_level else "no_working_level",
        "main_level_price": None if main_level is None else main_level.get("price"),
        "working_level_count": level_summary.get("working_level_count"),
        "candidate_level_count": level_summary.get("candidate_count"),
        "blocker_items": item_ids(packet.get("blockers") or []),
        "manual_review_items": item_ids(packet.get("manual_review_queue") or []),
        "checklist_item_statuses": checklist_statuses(packet),
        "structure_audit_status": audit.get("status"),
        "structure_audit_blockers": sorted(str(item) for item in audit.get("blockers") or []),
        "chart_context_counts": {
            key: len(value) if isinstance(value, list) else 0 if value is None else 1
            for key, value in chart_context.items()
        },
    }


def add_assertion(rows: list[dict[str, Any]], case: dict[str, Any], label_key: str,
                  check_id: str, passed: bool, evidence: str,
                  expected: Any = None, actual: Any = None) -> None:
    rows.append({
        "case_id": case.get("case_id"),
        "case_origin": case.get("case_origin"),
        "label_key": label_key,
        "title": case.get("title"),
        "check_id": check_id,
        "passed": bool(passed),
        "evidence": evidence,
        "expected": expected,
        "actual": actual,
    })


def assert_equal(rows: list[dict[str, Any]], case: dict[str, Any], label_key: str,
                 check_id: str, actual: Any, expected: Any) -> None:
    add_assertion(rows, case, label_key, check_id, actual == expected, f"expected {expected}, got {actual}", expected, actual)


def assert_contains_all(rows: list[dict[str, Any]], case: dict[str, Any], label_key: str,
                        check_id: str, actual: list[str], expected: list[str]) -> None:
    missing = sorted(set(expected) - set(actual))
    add_assertion(rows, case, label_key, check_id, not missing, f"missing={missing or '-'}", sorted(expected), sorted(actual))


def compare_label(case: dict[str, Any], label_key: str, label: dict[str, Any], packet: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    actual = bot_summary(packet)
    expected = label.get("expected") or {}

    for flag in SAFETY_FLAGS:
        assert_equal(rows, case, label_key, f"safety_{flag}", packet.get(flag), False)

    scalar_fields = [
        "review_status",
        "hard_gate_status",
        "best_entry_model",
        "best_entry_status",
        "entry_status",
        "scenario_family",
        "entry_direction",
        "main_level_status",
        "working_level_count",
        "candidate_level_count",
        "structure_audit_status",
    ]
    for field in scalar_fields:
        if field in expected:
            assert_equal(rows, case, label_key, f"align_{field}", actual.get(field), expected[field])

    if "main_level_price" in expected and expected.get("main_level_price") is not None:
        tolerance = float(expected.get("main_level_tolerance", 0.0001))
        actual_price = actual.get("main_level_price")
        expected_price = float(expected["main_level_price"])
        passed = actual_price is not None and abs(float(actual_price) - expected_price) <= tolerance
        add_assertion(rows, case, label_key, "align_main_level_price", passed, f"tolerance={tolerance}", expected_price, actual_price)

    if "blocker_items" in expected:
        assert_contains_all(rows, case, label_key, "align_blocker_items", actual["blocker_items"], list(expected["blocker_items"]))
    if "manual_review_items" in expected:
        assert_contains_all(rows, case, label_key, "align_manual_review_items", actual["manual_review_items"], list(expected["manual_review_items"]))
    if "structure_audit_blockers" in expected:
        assert_contains_all(rows, case, label_key, "align_structure_audit_blockers", actual["structure_audit_blockers"], list(expected["structure_audit_blockers"]))
    for item_id, expected_status in (expected.get("checklist_item_statuses") or {}).items():
        assert_equal(rows, case, label_key, f"align_checklist_{item_id}", actual["checklist_item_statuses"].get(item_id), expected_status)
    for key, expected_count in (expected.get("chart_context_counts") or {}).items():
        assert_equal(rows, case, label_key, f"align_chart_context_{key}_count", actual["chart_context_counts"].get(key, 0), int(expected_count))
    return rows


def append_case_alignment(path: Path, *, draft_alignment_audit: bool,
                          case_results: list[dict[str, Any]], assertions: list[dict[str, Any]],
                          schema_errors: list[dict[str, Any]]) -> tuple[int, int, int]:
    try:
        case = load_json(path)
    except json.JSONDecodeError as exc:
        schema_errors.append({"case_file": rel_path(path), "case_id": None, "label_key": None, "error": f"invalid JSON: {exc.msg}"})
        return 0, 0, 0
    if not isinstance(case, dict):
        schema_errors.append({"case_file": rel_path(path), "case_id": None, "label_key": None, "error": "case file must contain a JSON object"})
        return 0, 0, 0
    labels = label_entries(case)
    if not labels:
        return 0, 0, 0

    packet: dict[str, Any] | None = None
    packet_error: str | None = None
    try:
        packet = build_packet(case)
    except Exception as exc:  # noqa: BLE001 - alignment validator records packet build failures.
        packet_error = f"{type(exc).__name__}: {exc}"

    label_count = 0
    ai_label_count = 0
    human_label_count = 0
    for label_key, label in labels:
        label_count += 1
        ai_label_count += int(label_key == "ai_kb_trader_label")
        human_label_count += int(label_key == "human_confirmed_label")
        errors = validate_label_schema(case, label_key, label)
        for error in errors:
            schema_errors.append({"case_file": rel_path(path), "case_id": case.get("case_id"), "label_key": label_key, "error": error})
        if errors:
            continue
        if packet_error or packet is None:
            build_rows: list[dict[str, Any]] = []
            add_assertion(build_rows, case, label_key, "packet_build", False, packet_error or "packet build failed", "packet_build_success", "packet_build_error")
            for row in build_rows:
                row["draft_alignment_audit"] = draft_alignment_audit
            assertions.extend(build_rows)
            continue
        label_assertions = compare_label(case, label_key, label, packet)
        for row in label_assertions:
            row["draft_alignment_audit"] = draft_alignment_audit
        assertions.extend(label_assertions)
        failed = [row for row in label_assertions if not row["passed"]]
        semantic = [row for row in label_assertions if row["check_id"].startswith("align_")]
        semantic_failed = [row for row in semantic if not row["passed"]]
        score = None if not semantic else round((len(semantic) - len(semantic_failed)) / len(semantic), 4)
        case_results.append({
            "case_file": rel_path(path),
            "case_id": case.get("case_id"),
            "case_origin": case.get("case_origin"),
            "draft_alignment_audit": draft_alignment_audit,
            "label_key": label_key,
            "label_origin": label.get("label_origin"),
            "confidence": label.get("confidence"),
            "passed": not failed,
            "assertion_count": len(label_assertions),
            "failed_assertion_count": len(failed),
            "semantic_alignment_score": score,
            "bot_summary": bot_summary(packet),
        })
    return label_count, ai_label_count, human_label_count


def run_validation(cases_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    case_results: list[dict[str, Any]] = []
    assertions: list[dict[str, Any]] = []
    schema_errors: list[dict[str, Any]] = []
    label_count = 0
    ai_label_count = 0
    human_label_count = 0
    verified_human_label_count = 0

    for path in case_files(cases_dir):
        counts = append_case_alignment(
            path,
            draft_alignment_audit=False,
            case_results=case_results,
            assertions=assertions,
            schema_errors=schema_errors,
        )
        label_count += counts[0]
        ai_label_count += counts[1]
        human_label_count += counts[2]
        verified_human_label_count += counts[2]

    draft_alignment_audit_count = 0
    for filename in DRAFT_ALIGNMENT_AUDIT_FILES:
        path = cases_dir / filename
        if not path.exists():
            schema_errors.append({"case_file": rel_path(path), "case_id": None, "label_key": None, "error": "draft_alignment_audit_file_missing"})
            continue
        counts = append_case_alignment(
            path,
            draft_alignment_audit=True,
            case_results=case_results,
            assertions=assertions,
            schema_errors=schema_errors,
        )
        if counts[0] > 0:
            draft_alignment_audit_count += 1
        label_count += counts[0]
        ai_label_count += counts[1]
        human_label_count += counts[2]

    failed_assertions = [row for row in assertions if not row["passed"]]
    strict_failed = [row for row in failed_assertions if not row.get("draft_alignment_audit")]
    draft_divergences = [row for row in failed_assertions if row.get("draft_alignment_audit")]
    blockers = [row["error"] for row in schema_errors] + [f"{row['case_id']}:{row['label_key']}:{row['check_id']}" for row in strict_failed]
    if label_count == 0:
        blockers.append("no_trader_alignment_labels_found")
    ready = not blockers
    status = {
        "version": VERSION,
        "generated_at": utc_now(),
        "mode": "read_only_trader_alignment_validation",
        "cases_dir": rel_path(cases_dir),
        "label_count": label_count,
        "ai_kb_trader_label_count": ai_label_count,
        "human_confirmed_label_count": human_label_count,
        "verified_human_confirmed_label_count": verified_human_label_count,
        "draft_alignment_audit_count": draft_alignment_audit_count,
        "case_result_count": len(case_results),
        "assertion_count": len(assertions),
        "passed_assertion_count": len(assertions) - len(failed_assertions),
        "failed_assertion_count": len(failed_assertions),
        "strict_failed_assertion_count": len(strict_failed),
        "draft_alignment_divergence_count": len(draft_divergences),
        "draft_alignment_divergences": [
            {
                "case_id": row.get("case_id"),
                "label_key": row.get("label_key"),
                "check_id": row.get("check_id"),
                "expected": row.get("expected"),
                "actual": row.get("actual"),
            }
            for row in draft_divergences
        ],
        "schema_error_count": len(schema_errors),
        "layer9_trader_alignment_ready": ready,
        "ai_kb_trader_alignment_ready": ready and ai_label_count > 0,
        "human_confirmed_alignment_ready": ready and verified_human_label_count > 0,
        "real_trader_alignment_verified": ready and verified_human_label_count > 0,
        "scope_note": "compares read-only Layer 6/7 bot packets against AI-KB or human-confirmed trader labels; seed/real labels gate readiness strictly, draft template labels record divergences without blocking; no outcome labels, PnL, orders, runtime signals, paper/live trading, or backtest harness",
        "blockers": blockers,
        **SAFETY_FLAGS,
    }
    return status, case_results, assertions, schema_errors


def write_report(path: Path, status: dict[str, Any], case_results: list[dict[str, Any]],
                 assertions: list[dict[str, Any]], schema_errors: list[dict[str, Any]]) -> None:
    lines = [
        "# Layer 9 Trader Alignment Validation",
        "",
        "## Verdict",
        "",
        f"- Generated: `{status['generated_at']}`",
        f"- Layer 9 ready: `{str(status['layer9_trader_alignment_ready']).lower()}`",
        f"- AI KB trader alignment ready: `{str(status['ai_kb_trader_alignment_ready']).lower()}`",
        f"- Human confirmed alignment ready: `{str(status['human_confirmed_alignment_ready']).lower()}`",
        f"- Real trader alignment verified: `{str(status['real_trader_alignment_verified']).lower()}`",
        f"- Labels: {status['label_count']} (AI KB: {status['ai_kb_trader_label_count']}, human: {status['human_confirmed_label_count']}, verified human: {status['verified_human_confirmed_label_count']})",
        f"- Draft alignment audits: {status['draft_alignment_audit_count']}",
        f"- Draft alignment divergences (non-blocking): {status['draft_alignment_divergence_count']}",
        f"- Assertions passed: {status['passed_assertion_count']} / {status['assertion_count']}",
        f"- Schema errors: {status['schema_error_count']}",
        f"- Execution allowed: `{str(status['execution_allowed']).lower()}`",
        f"- PnL computation allowed: `{str(status['pnl_computation_allowed']).lower()}`",
        f"- Backtest harness allowed: `{str(status['backtest_harness_allowed']).lower()}`",
        "",
        "Layer 9 does not decide market outcomes. It checks whether the bot packet agrees with a blinded KB-trader label on level state, scenario, entry status, blockers, checklist items, and safety gates.",
        "",
        "## Case Results",
        "",
        "| Case | Label | Origin | Draft | Passed | Score | Confidence | Bot review | Bot entry |",
        "| --- | --- | --- | --- | --- | ---: | --- | --- | --- |",
    ]
    for row in case_results:
        bot = row.get("bot_summary") or {}
        entry = f"{bot.get('best_entry_model') or '-'} / {bot.get('best_entry_status') or '-'}"
        lines.append("| " + " | ".join([
            table_cell(row.get("case_id")),
            table_cell(row.get("label_key")),
            table_cell(row.get("label_origin")),
            table_cell(str(row.get("draft_alignment_audit", False)).lower()),
            table_cell(str(row.get("passed")).lower()),
            table_cell(row.get("semantic_alignment_score")),
            table_cell(row.get("confidence")),
            table_cell(bot.get("review_status")),
            table_cell(entry),
        ]) + " |")
    if schema_errors:
        lines.extend(["", "## Schema Errors", ""])
        for error in schema_errors:
            lines.append(f"- `{error.get('case_file')}` `{error.get('case_id') or '-'}` `{error.get('label_key') or '-'}`: {error['error']}")
    lines.extend(["", "## Assertions", "", "| Case | Label | Check | Passed | Evidence |", "| --- | --- | --- | --- | --- |"])
    for row in assertions:
        lines.append("| " + " | ".join([
            table_cell(row.get("case_id")),
            table_cell(row.get("label_key")),
            table_cell(row.get("check_id")),
            table_cell(str(row.get("passed")).lower()),
            table_cell(row.get("evidence")),
        ]) + " |")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def build_outputs(cases_dir: Path, output_dir: Path) -> dict[str, Any]:
    status, case_results, assertions, schema_errors = run_validation(cases_dir)
    failures = [row for row in assertions if not row["passed"]]
    write_json(output_dir / "layer9_trader_alignment_validation_status.json", status)
    write_jsonl(output_dir / "layer9_trader_alignment_case_results.jsonl", case_results)
    write_jsonl(output_dir / "layer9_trader_alignment_assertions.jsonl", assertions)
    write_jsonl(output_dir / "layer9_trader_alignment_failures.jsonl", failures)
    write_jsonl(output_dir / "layer9_trader_alignment_schema_errors.jsonl", schema_errors)
    write_report(output_dir / "layer9_trader_alignment_validation.md", status, case_results, assertions, schema_errors)
    return status


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate bot packets against AI-KB/human trader alignment labels")
    parser.add_argument("--cases-dir", default=str(DEFAULT_CASES_DIR), help="Directory containing Layer 7 case JSON files")
    parser.add_argument("--out-dir", default=str(OUTPUT_DIR), help="Output directory for validation artifacts")
    parser.add_argument("--strict-exit-code", action="store_true", help="Return non-zero when schema/errors/assertions fail")
    return parser


def main() -> int:
    configure_stdio()
    args = build_parser().parse_args()
    status = build_outputs(Path(args.cases_dir), Path(args.out_dir))
    print(json.dumps({
        "version": status["version"],
        "layer9_trader_alignment_ready": status["layer9_trader_alignment_ready"],
        "ai_kb_trader_alignment_ready": status["ai_kb_trader_alignment_ready"],
        "human_confirmed_alignment_ready": status["human_confirmed_alignment_ready"],
        "real_trader_alignment_verified": status["real_trader_alignment_verified"],
        "label_count": status["label_count"],
        "ai_kb_trader_label_count": status["ai_kb_trader_label_count"],
        "human_confirmed_label_count": status["human_confirmed_label_count"],
        "verified_human_confirmed_label_count": status["verified_human_confirmed_label_count"],
        "draft_alignment_audit_count": status["draft_alignment_audit_count"],
        "draft_alignment_divergence_count": status["draft_alignment_divergence_count"],
        "assertion_count": status["assertion_count"],
        "passed_assertion_count": status["passed_assertion_count"],
        "failed_assertion_count": status["failed_assertion_count"],
        "schema_error_count": status["schema_error_count"],
        "blockers": status["blockers"],
        "execution_allowed": status["execution_allowed"],
        "runtime_signal_allowed": status["runtime_signal_allowed"],
        "order_generation_allowed": status["order_generation_allowed"],
        "pnl_computation_allowed": status["pnl_computation_allowed"],
        "paper_trading_allowed": status["paper_trading_allowed"],
        "live_trading_allowed": status["live_trading_allowed"],
        "backtest_harness_allowed": status["backtest_harness_allowed"],
    }, ensure_ascii=False, indent=2))
    if args.strict_exit_code and not status["layer9_trader_alignment_ready"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())