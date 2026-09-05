from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
VERSION = "kb_usage_correctness_validation_v1"
OUTPUT_DIR = ROOT / "_knowledge_base" / "structured" / "consolidation" / "kb_usage_correctness_validation"

KB_COVERAGE_STATUS_PATH = ROOT / "_knowledge_base" / "structured" / "consolidation" / "kb_coverage_audit" / "kb_coverage_audit_status.json"
KB_COVERAGE_MATRIX_PATH = ROOT / "_knowledge_base" / "structured" / "consolidation" / "kb_coverage_audit" / "kb_coverage_audit_matrix.jsonl"
FEATURE_CONTRACTS_PATH = ROOT / "_knowledge_base" / "structured" / "consolidation" / "feature_contracts_validation" / "feature_contracts_validation.md"
REGRESSION_REPORT_PATH = ROOT / "_knowledge_base" / "detector_casebook" / "regression_report.json"

SAFETY_FLAGS = [
    "execution_allowed",
    "runtime_signal_allowed",
    "order_generation_allowed",
    "pnl_computation_allowed",
    "paper_trading_allowed",
    "live_trading_allowed",
    "backtest_harness_allowed",
]

EXPECTED_COUNTS = {
    "crd": 27,
    "fcd": 15,
    "rscd_checklists": 16,
    "rscd_items": 73,
}

EXPECTED_USAGE_BY_COVERAGE = {
    "automated": "fully_used",
    "automated_partial": "partially_used",
    "manual_context_supported": "manual_context_supported",
    "manual_review_only": "manual_review_only",
}

CONTRACT_TO_REGRESSION_DETECTORS = {
    "hard_gates_and_permission": ["hard_gates_and_permission"],
    "level_selection_strength": ["level_selection_strength"],
    "trend_timeframe_context": ["trend_context"],
    "market_mechanics_context": ["market_mechanics_context"],
    "breakout_preconditions": ["breakout_preconditions"],
    "breakout_confirmation_fixation": ["fixation_return_entry"],
    "breakout_failure": ["breakout_failure"],
    "false_breakout_reversal": ["false_breakout_reversal"],
    "retest_room_atr": ["near_far_retest"],
    "bsu_bpu_limit_player": ["bsu_bpu_entry"],
    "tbx_entry_models": ["tbx_entry_models"],
    "risk_stop_take_management": ["risk_stop_take"],
    "formations_momentum": ["v_u_formations", "tail_bars_two_sided_limit"],
    "rebound_models": ["rebound_models"],
    "workflow_review_data_quality": ["workflow_review_data_quality"],
}

MANUAL_CONTEXT_GROUP_MARKERS = [
    'optional_context_state(optional.get("formations", []), "formations")',
    'optional_context_state(optional.get("tail_bars", []), "tail_bars")',
    'optional_context_state(optional.get("market_mechanics", []), "market_mechanics")',
    'optional_context_state(optional.get("rebounds", []), "rebounds")',
    'answer_state(manual_context',
]


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) for row in rows)
    path.write_text(text + ("\n" if text else ""), encoding="utf-8")


def table_cell(value: Any) -> str:
    if isinstance(value, list):
        value = "; ".join(str(item) for item in value)
    text = " ".join(str(value if value is not None else "").split()).replace("|", "\\|")
    return text or "-"


def add_check(checks: list[dict[str, Any]], check_id: str, category: str, passed: bool,
              severity: str, evidence: str, blockers: list[str] | None = None) -> None:
    checks.append({
        "check_id": check_id,
        "category": category,
        "passed": bool(passed),
        "severity": severity,
        "evidence": evidence,
        "blockers": blockers or ([] if passed else [check_id]),
    })


def artifact_type_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return dict(Counter(str(row.get("artifact_type")) for row in rows))


def validate_coverage_status(checks: list[dict[str, Any]], status: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    add_check(
        checks,
        "coverage_audit_ready",
        "coverage_integrity",
        status.get("kb_coverage_audit_ready") is True and not status.get("blockers"),
        "blocking",
        f"ready={status.get('kb_coverage_audit_ready')} blockers={status.get('blockers')}",
    )
    counts = status.get("counts") or {}
    add_check(
        checks,
        "expected_artifact_counts",
        "coverage_integrity",
        all(int(counts.get(key) or 0) == expected for key, expected in EXPECTED_COUNTS.items()),
        "blocking",
        f"counts={counts} expected={EXPECTED_COUNTS}",
    )
    bad_flags = [flag for flag in SAFETY_FLAGS if status.get(flag) is not False]
    add_check(
        checks,
        "coverage_safety_flags_false",
        "safety",
        not bad_flags,
        "blocking",
        f"unsafe_flags_true_or_missing={bad_flags}",
    )
    expected_matrix_counts = {"CRD": 27, "FCD": 15, "RSCD_GROUP": 16, "RSCD_ITEM": 73}
    actual_matrix_counts = artifact_type_counts(rows)
    add_check(
        checks,
        "matrix_artifact_counts",
        "coverage_integrity",
        all(actual_matrix_counts.get(key, 0) == expected for key, expected in expected_matrix_counts.items()),
        "blocking",
        f"matrix_counts={actual_matrix_counts} expected={expected_matrix_counts}",
    )


def validate_usage_semantics(checks: list[dict[str, Any]], rows: list[dict[str, Any]]) -> None:
    marker_violations = [
        row for row in rows
        if row.get("usage_check_status") != "verified" or row.get("missing_code_markers")
    ]
    add_check(
        checks,
        "all_expected_code_markers_verified",
        "usage_correctness",
        not marker_violations,
        "blocking",
        f"marker_violations={len(marker_violations)}",
    )
    not_used = [row for row in rows if row.get("knowledge_usage_status") in {"not_used", "unverified"}]
    add_check(
        checks,
        "no_not_used_or_unverified_rows",
        "usage_correctness",
        not not_used,
        "blocking",
        f"not_used_or_unverified={len(not_used)}",
    )
    semantic_violations = []
    for row in rows:
        expected = EXPECTED_USAGE_BY_COVERAGE.get(str(row.get("coverage_status")))
        actual = str(row.get("knowledge_usage_status"))
        if expected and actual != expected:
            semantic_violations.append({
                "artifact_type": row.get("artifact_type"),
                "artifact_id": row.get("artifact_id"),
                "coverage_status": row.get("coverage_status"),
                "expected_usage_status": expected,
                "actual_usage_status": actual,
            })
    add_check(
        checks,
        "coverage_usage_status_consistency",
        "usage_correctness",
        not semantic_violations,
        "blocking",
        f"semantic_violations={len(semantic_violations)}",
        [f"usage_status_mismatch:{item['artifact_type']}:{item['artifact_id']}" for item in semantic_violations[:20]],
    )


def validate_feature_contracts(checks: list[dict[str, Any]], rows: list[dict[str, Any]]) -> None:
    fcd_rows = [row for row in rows if row.get("artifact_type") == "FCD"]
    missing_case_support = []
    for row in fcd_rows:
        if int(row.get("seed_cases") or 0) <= 0 or int(row.get("fixtures") or 0) <= 0:
            missing_case_support.append(row.get("artifact_id"))
        if int(row.get("generated_cases") or 0) <= 0 or int(row.get("retrieval_tests") or 0) <= 0:
            missing_case_support.append(row.get("artifact_id"))
        if row.get("coverage_state") != "existing_machine_seed_casebook_plus_generated_validation":
            missing_case_support.append(row.get("artifact_id"))
    add_check(
        checks,
        "fcd_casebook_and_retrieval_support",
        "contract_correctness",
        not missing_case_support and len(fcd_rows) == 15,
        "blocking",
        f"fcd_rows={len(fcd_rows)} unsupported={sorted(set(str(item) for item in missing_case_support))}",
    )


def validate_regression_support(checks: list[dict[str, Any]], rows: list[dict[str, Any]], regression_rows: list[dict[str, Any]]) -> None:
    failed = [row for row in regression_rows if row.get("result") != "passed"]
    add_check(
        checks,
        "detector_regression_all_passed",
        "contract_correctness",
        len(regression_rows) == 59 and not failed,
        "blocking",
        f"cases={len(regression_rows)} failed={len(failed)}",
    )
    passed_counts = Counter(str(row.get("detector") or "") for row in regression_rows if row.get("result") == "passed")
    fcd_detectors = [str(row.get("title") or "") for row in rows if row.get("artifact_type") == "FCD"]
    missing_regression = []
    for detector in fcd_detectors:
        expected = CONTRACT_TO_REGRESSION_DETECTORS.get(detector)
        if not expected:
            missing_regression.append(f"missing_mapping:{detector}")
            continue
        for regression_detector in expected:
            if passed_counts.get(regression_detector, 0) <= 0:
                missing_regression.append(f"{detector}->{regression_detector}")
    add_check(
        checks,
        "every_fcd_has_passing_regression_family",
        "contract_correctness",
        not missing_regression,
        "blocking",
        f"passed_detector_families={dict(sorted(passed_counts.items()))} missing={missing_regression}",
    )


def validate_manual_boundary(checks: list[dict[str, Any]], rows: list[dict[str, Any]]) -> None:
    manual_rows = [row for row in rows if row.get("coverage_status") in {"manual_context_supported", "manual_review_only"}]
    promoted = [row for row in manual_rows if row.get("knowledge_usage_status") in {"fully_used", "partially_used"}]
    add_check(
        checks,
        "manual_knowledge_not_promoted_to_auto",
        "manual_boundary",
        not promoted,
        "blocking",
        f"manual_rows={len(manual_rows)} promoted={len(promoted)}",
    )
    chart_packet_text = (ROOT / "knowledge_bot" / "chart_review_packet.py").read_text(encoding="utf-8")
    missing_markers = [marker for marker in MANUAL_CONTEXT_GROUP_MARKERS if marker not in chart_packet_text]
    add_check(
        checks,
        "manual_context_items_remain_context_gated",
        "manual_boundary",
        not missing_markers,
        "blocking",
        f"missing_manual_context_markers={missing_markers}",
    )


def validate_runtime_safety(checks: list[dict[str, Any]]) -> None:
    files = [
        ROOT / "knowledge_bot" / "chart_review_packet.py",
        ROOT / "knowledge_bot" / "market_universe_review.py",
    ]
    unsafe_markers = [
        "execution_allowed=True",
        "runtime_signal_allowed=True",
        "order_generation_allowed=True",
        "pnl_computation_allowed=True",
        "paper_trading_allowed=True",
        "live_trading_allowed=True",
        "backtest_harness_allowed=True",
    ]
    hits: list[str] = []
    for path in files:
        if not path.exists():
            hits.append(f"missing_file:{path.relative_to(ROOT).as_posix()}")
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for marker in unsafe_markers:
            if marker in text:
                hits.append(f"{path.relative_to(ROOT).as_posix()}:{marker}")
    add_check(
        checks,
        "no_runtime_or_execution_capability_enabled",
        "safety",
        not hits,
        "blocking",
        f"unsafe_marker_hits={hits}",
    )


def correctness_summary(checks: list[dict[str, Any]]) -> dict[str, Any]:
    blocking = [check for check in checks if check["severity"] == "blocking" and not check["passed"]]
    warnings = [check for check in checks if check["severity"] == "warning" and not check["passed"]]
    return {
        "check_count": len(checks),
        "passed_count": sum(1 for check in checks if check["passed"]),
        "blocking_error_count": len(blocking),
        "warning_count": len(warnings),
        "blocking_errors": blocking,
        "warnings": warnings,
    }


def write_markdown_report(path: Path, status: dict[str, Any], checks: list[dict[str, Any]]) -> None:
    lines = [
        "# KB Usage Correctness Validation",
        "",
        "## Verdict",
        "",
        f"- Generated: `{status['generated_at']}`",
        f"- Correctness validation ready: `{str(status['kb_usage_correctness_validation_ready']).lower()}`",
        f"- Checks passed: {status['passed_count']} / {status['check_count']}",
        f"- Blocking errors: {status['blocking_error_count']}",
        f"- Warnings: {status['warning_count']}",
        f"- Regression cases passed: {status['regression_passed_count']} / {status['regression_case_count']}",
        f"- Expected code markers verified: {status['expected_code_markers_verified']}",
        f"- Manual rows not promoted: `{str(status['manual_rows_not_promoted']).lower()}`",
        f"- Execution allowed: `{str(status['execution_allowed']).lower()}`",
        f"- PnL computation allowed: `{str(status['pnl_computation_allowed']).lower()}`",
        f"- Backtest harness allowed: `{str(status['backtest_harness_allowed']).lower()}`",
        "",
        "This artifact validates whether the KB is used with the correct boundaries: automated knowledge must have marker and casebook support, manual knowledge must remain manual/context-gated, and no runtime/execution capability may be enabled.",
        "",
        "## Checks",
        "",
        "| Check | Category | Passed | Severity | Evidence |",
        "| --- | --- | --- | --- | --- |",
    ]
    for check in checks:
        lines.append("| " + " | ".join([
            table_cell(check["check_id"]),
            table_cell(check["category"]),
            table_cell(str(check["passed"]).lower()),
            table_cell(check["severity"]),
            table_cell(check["evidence"]),
        ]) + " |")
    if status["blocking_errors"]:
        lines.extend(["", "## Blocking Errors", ""])
        for check in status["blocking_errors"]:
            lines.append(f"- `{check['check_id']}`: {check['evidence']}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def build_validation(output_dir: Path) -> dict[str, Any]:
    coverage_status = read_json(KB_COVERAGE_STATUS_PATH)
    matrix_rows = read_jsonl(KB_COVERAGE_MATRIX_PATH)
    regression_rows = read_json(REGRESSION_REPORT_PATH)
    if not isinstance(regression_rows, list):
        regression_rows = []

    checks: list[dict[str, Any]] = []
    validate_coverage_status(checks, coverage_status, matrix_rows)
    validate_usage_semantics(checks, matrix_rows)
    validate_feature_contracts(checks, matrix_rows)
    validate_regression_support(checks, matrix_rows, regression_rows)
    validate_manual_boundary(checks, matrix_rows)
    validate_runtime_safety(checks)

    summary = correctness_summary(checks)
    marker_verified = int((coverage_status.get("usage_check_counts") or {}).get("verified") or 0)
    regression_passed = sum(1 for row in regression_rows if row.get("result") == "passed")
    status = {
        "version": VERSION,
        "generated_at": utc_now(),
        "mode": "read_only_kb_usage_correctness_validation",
        "source_artifacts": [
            KB_COVERAGE_STATUS_PATH.relative_to(ROOT).as_posix(),
            KB_COVERAGE_MATRIX_PATH.relative_to(ROOT).as_posix(),
            FEATURE_CONTRACTS_PATH.relative_to(ROOT).as_posix(),
            REGRESSION_REPORT_PATH.relative_to(ROOT).as_posix(),
        ],
        "kb_usage_correctness_validation_ready": summary["blocking_error_count"] == 0,
        "check_count": summary["check_count"],
        "passed_count": summary["passed_count"],
        "blocking_error_count": summary["blocking_error_count"],
        "warning_count": summary["warning_count"],
        "blocking_errors": summary["blocking_errors"],
        "warnings": summary["warnings"],
        "coverage_audit_version": coverage_status.get("version"),
        "coverage_counts": coverage_status.get("counts"),
        "knowledge_usage_counts": coverage_status.get("knowledge_usage_counts"),
        "usage_check_counts": coverage_status.get("usage_check_counts"),
        "expected_code_markers_verified": marker_verified,
        "not_used_or_unverified_count": coverage_status.get("not_used_or_unverified_count"),
        "control_queue_count": coverage_status.get("control_queue_count"),
        "regression_case_count": len(regression_rows),
        "regression_passed_count": regression_passed,
        "manual_rows_not_promoted": not any(
            row.get("coverage_status") in {"manual_context_supported", "manual_review_only"}
            and row.get("knowledge_usage_status") in {"fully_used", "partially_used"}
            for row in matrix_rows
        ),
        "execution_allowed": False,
        "runtime_signal_allowed": False,
        "order_generation_allowed": False,
        "pnl_computation_allowed": False,
        "paper_trading_allowed": False,
        "live_trading_allowed": False,
        "backtest_harness_allowed": False,
    }
    write_json(output_dir / "kb_usage_correctness_validation_status.json", status)
    write_jsonl(output_dir / "kb_usage_correctness_validation_checks.jsonl", checks)
    write_markdown_report(output_dir / "kb_usage_correctness_validation.md", status, checks)
    return status


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate whether KB usage is correct, not only present")
    parser.add_argument("--out-dir", default=str(OUTPUT_DIR), help="Output directory for validation artifacts")
    parser.add_argument("--strict-exit-code", action="store_true", help="Return non-zero when blocking correctness errors exist")
    return parser


def main() -> int:
    configure_stdio()
    args = build_parser().parse_args()
    status = build_validation(Path(args.out_dir))
    print(json.dumps({
        "version": status["version"],
        "kb_usage_correctness_validation_ready": status["kb_usage_correctness_validation_ready"],
        "check_count": status["check_count"],
        "passed_count": status["passed_count"],
        "blocking_error_count": status["blocking_error_count"],
        "warning_count": status["warning_count"],
        "regression": {
            "cases": status["regression_case_count"],
            "passed": status["regression_passed_count"],
        },
        "expected_code_markers_verified": status["expected_code_markers_verified"],
        "not_used_or_unverified_count": status["not_used_or_unverified_count"],
        "manual_rows_not_promoted": status["manual_rows_not_promoted"],
        "output_dir": str(Path(args.out_dir)),
        "execution_allowed": False,
        "runtime_signal_allowed": False,
        "pnl_computation_allowed": False,
        "backtest_harness_allowed": False,
    }, ensure_ascii=False, indent=2))
    if args.strict_exit_code and status["blocking_error_count"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())