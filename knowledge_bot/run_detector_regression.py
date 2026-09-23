from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from detector_prototype import DetectorInputError, build_output
from validate_detector_casebook import configure_stdio, load_and_validate


AUTOMATED_DETECTORS: set[str] = {
    "hard_gates_and_permission",
    "level_selection_strength",
    "trend_context",
    "market_mechanics_context",
    "tbx_entry_models",
    "v_u_formations",
    "tail_bars_two_sided_limit",
    "near_far_retest",
    "fixation_return_entry",
    "bsu_bpu_entry",
    "breakout_preconditions",
    "breakout_failure",
    "false_breakout_reversal",
    "rebound_models",
    "workflow_review_data_quality",
    "risk_stop_take",
}
PASSING_RESULTS: set[str] = {"passed", "manual_pending"}


@dataclass(frozen=True)
class RegressionResult:
    case_id: str
    detector: str
    result: str
    expected_status: str
    actual_status: str | None
    expected_bias: str
    actual_bias: str | None
    reason: str
    fixture_path: str | None
    details: list[str]


def default_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_casebook_path() -> Path:
    return default_root() / "_knowledge_base" / "detector_casebook" / "seed_cases_v1.jsonl"


def default_report_path() -> Path:
    return default_root() / "_knowledge_base" / "detector_casebook" / "regression_report.md"


def default_json_report_path() -> Path:
    return default_root() / "_knowledge_base" / "detector_casebook" / "regression_report.json"


def resolve_workspace_path(path_value: str) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    return default_root() / path


def load_fixture(path_value: str) -> dict[str, Any]:
    path = resolve_workspace_path(path_value)
    with path.open("r", encoding="utf-8") as handle:
        parsed = json.load(handle)
    if not isinstance(parsed, dict):
        raise DetectorInputError(f"fixture must contain a JSON object: {path}")
    return parsed


def get_by_path(value: Any, path: str) -> Any:
    current = value
    for part in path.split("."):
        if isinstance(current, dict):
            current = current[part]
        elif isinstance(current, list):
            current = current[int(part)]
        else:
            raise KeyError(path)
    return current


def select_detector_output(output: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    detector = case["detector"]
    detectors = output.get("detectors")
    if not isinstance(detectors, dict) or detector not in detectors:
        raise KeyError(f"fixture output does not contain detector {detector!r}")

    detector_output = detectors[detector]
    if isinstance(detector_output, list):
        index = int(case.get("fixture_result_index", 0))
        return detector_output[index]
    if isinstance(detector_output, dict):
        return detector_output
    raise KeyError(f"detector output has unsupported shape for {detector!r}")


def actual_bias_for(detector_output: dict[str, Any]) -> str | None:
    bias = detector_output.get("bias")
    if isinstance(bias, str):
        return bias
    direction = detector_output.get("direction")
    if isinstance(direction, str):
        return direction
    return None


def compare_expected_output(detector_output: dict[str, Any], expected_output: Any) -> list[str]:
    if expected_output is None:
        return []
    if not isinstance(expected_output, dict):
        return ["expected_output must be an object"]

    failures: list[str] = []
    for path, expected_value in sorted(expected_output.items()):
        try:
            actual_value = get_by_path(detector_output, path)
        except (KeyError, IndexError, ValueError, TypeError):
            failures.append(f"missing output path {path!r}")
            continue
        if actual_value != expected_value:
            failures.append(f"{path}: expected {expected_value!r}, got {actual_value!r}")
    return failures


def run_case(case: dict[str, Any]) -> RegressionResult:
    case_id = case["case_id"]
    detector = case["detector"]
    expected_status = case["expected_status"]
    expected_bias = case["expected_bias"]
    fixture_path = case.get("fixture_path")

    if detector not in AUTOMATED_DETECTORS:
        return RegressionResult(
            case_id=case_id,
            detector=detector,
            result="manual_pending",
            expected_status=expected_status,
            actual_status=None,
            expected_bias=expected_bias,
            actual_bias=None,
            reason="detector_not_implemented_yet",
            fixture_path=fixture_path if isinstance(fixture_path, str) else None,
            details=[],
        )

    if not isinstance(fixture_path, str) or not fixture_path.strip():
        return RegressionResult(
            case_id=case_id,
            detector=detector,
            result="manual_pending",
            expected_status=expected_status,
            actual_status=None,
            expected_bias=expected_bias,
            actual_bias=None,
            reason="missing_machine_fixture",
            fixture_path=None,
            details=[],
        )

    try:
        fixture = load_fixture(fixture_path)
        output = build_output(fixture)
        detector_output = select_detector_output(output, case)
    except (OSError, json.JSONDecodeError, DetectorInputError, KeyError, IndexError, ValueError, TypeError) as exc:
        return RegressionResult(
            case_id=case_id,
            detector=detector,
            result="failed",
            expected_status=expected_status,
            actual_status=None,
            expected_bias=expected_bias,
            actual_bias=None,
            reason="fixture_error",
            fixture_path=fixture_path,
            details=[str(exc)],
        )

    actual_status = detector_output.get("status")
    actual_status_text = actual_status if isinstance(actual_status, str) else None
    actual_bias = actual_bias_for(detector_output)

    failures: list[str] = []
    if actual_status_text != expected_status:
        failures.append(f"status: expected {expected_status!r}, got {actual_status_text!r}")

    if expected_bias not in {"any", "unknown"} and actual_bias != expected_bias:
        failures.append(f"bias: expected {expected_bias!r}, got {actual_bias!r}")

    failures.extend(compare_expected_output(detector_output, case.get("expected_output")))

    return RegressionResult(
        case_id=case_id,
        detector=detector,
        result="failed" if failures else "passed",
        expected_status=expected_status,
        actual_status=actual_status_text,
        expected_bias=expected_bias,
        actual_bias=actual_bias,
        reason="assertion_failed" if failures else "ok",
        fixture_path=fixture_path,
        details=failures,
    )


def render_markdown(results: list[RegressionResult], casebook_path: Path) -> str:
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    counts: dict[str, int] = {}
    for result in results:
        counts[result.result] = counts.get(result.result, 0) + 1

    lines = [
        "# Detector Regression Report",
        "",
        f"Generated at: `{generated_at}`",
        f"Casebook: `{casebook_path}`",
        "",
        "## Summary",
        "",
        f"- Total cases: {len(results)}",
        f"- Passed: {counts.get('passed', 0)}",
        f"- Manual pending: {counts.get('manual_pending', 0)}",
        f"- Failed: {counts.get('failed', 0)}",
        "",
        "## Cases",
        "",
        "| Result | Case | Detector | Expected | Actual | Reason | Fixture |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]

    for result in results:
        fixture = result.fixture_path or ""
        expected = f"{result.expected_status}/{result.expected_bias}"
        actual_status = result.actual_status or ""
        actual_bias = result.actual_bias or ""
        actual = f"{actual_status}/{actual_bias}" if actual_status or actual_bias else ""
        lines.append(
            f"| {result.result} | `{result.case_id}` | `{result.detector}` | `{expected}` | `{actual}` | {result.reason} | `{fixture}` |"
        )

    failed = [result for result in results if result.result == "failed"]
    if failed:
        lines.extend(["", "## Failures", ""])
        for result in failed:
            lines.append(f"### `{result.case_id}`")
            lines.append("")
            for detail in result.details:
                lines.append(f"- {detail}")
            lines.append("")

    return "\n".join(lines) + "\n"


def write_json_report(results: list[RegressionResult], path: Path) -> None:
    serializable = [
        {
            "case_id": result.case_id,
            "detector": result.detector,
            "result": result.result,
            "expected_status": result.expected_status,
            "actual_status": result.actual_status,
            "expected_bias": result.expected_bias,
            "actual_bias": result.actual_bias,
            "reason": result.reason,
            "fixture_path": result.fixture_path,
            "details": result.details,
        }
        for result in results
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(serializable, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_regression(casebook_path: Path) -> tuple[list[RegressionResult], list[str]]:
    cases, validation_errors = load_and_validate(casebook_path)
    if validation_errors:
        return [], validation_errors
    return [run_case(case) for case in cases], []


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run detector regression cases against machine fixtures.")
    parser.add_argument("--casebook", type=Path, default=default_casebook_path(), help="Path to casebook JSONL.")
    parser.add_argument("--report", type=Path, default=default_report_path(), help="Markdown report path.")
    parser.add_argument("--json-report", type=Path, default=default_json_report_path(), help="JSON report path.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    args = parse_args(sys.argv[1:] if argv is None else argv)
    results, errors = run_regression(args.casebook)

    if errors:
        print("casebook validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(render_markdown(results, args.casebook), encoding="utf-8")
    write_json_report(results, args.json_report)

    failed_count = sum(1 for result in results if result.result == "failed")
    passed_count = sum(1 for result in results if result.result == "passed")
    manual_count = sum(1 for result in results if result.result == "manual_pending")
    print(f"cases: {len(results)}")
    print(f"passed: {passed_count}")
    print(f"manual_pending: {manual_count}")
    print(f"failed: {failed_count}")
    print(f"report: {args.report}")
    return 1 if failed_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
