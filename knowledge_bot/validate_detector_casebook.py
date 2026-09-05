from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


REQUIRED_FIELDS: tuple[str, ...] = (
    "case_id",
    "source_chunk",
    "lecture",
    "detector",
    "expected_status",
    "expected_bias",
    "must_detect",
    "must_reject",
    "manual_review_needed",
    "notes",
)

LIST_FIELDS: tuple[str, ...] = ("must_detect", "must_reject", "manual_review_needed")
OPTIONAL_LIST_FIELDS: tuple[str, ...] = ("source_unit_ids",)

VALID_STATUSES: set[str] = {"candidate", "setup", "trigger", "reject", "warn", "pass"}

VALID_DETECTORS: set[str] = {
    "hard_gates_and_permission",
    "level_selection_strength",
    "trend_context",
    "market_mechanics_context",
    "tbx_entry_models",
    "v_u_formations",
    "tail_bars_two_sided_limit",
    "breakout_preconditions",
    "breakout_failure",
    "false_breakout_reversal",
    "near_far_retest",
    "fixation_return_entry",
    "bsu_bpu_entry",
    "rebound_models",
    "workflow_review_data_quality",
    "risk_stop_take",
}


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def default_casebook_path() -> Path:
    return Path(__file__).resolve().parents[1] / "_knowledge_base" / "detector_casebook" / "seed_cases_v1.jsonl"


def validate_case(case: dict[str, Any], line_number: int, seen_case_ids: set[str]) -> list[str]:
    errors: list[str] = []

    for field in REQUIRED_FIELDS:
        if field not in case:
            errors.append(f"line {line_number}: missing required field {field!r}")

    case_id = case.get("case_id")
    if not isinstance(case_id, str) or not case_id.strip():
        errors.append(f"line {line_number}: case_id must be a non-empty string")
    elif case_id in seen_case_ids:
        errors.append(f"line {line_number}: duplicate case_id {case_id!r}")
    else:
        seen_case_ids.add(case_id)

    detector = case.get("detector")
    if detector not in VALID_DETECTORS:
        errors.append(f"line {line_number}: unknown detector {detector!r}")

    expected_status = case.get("expected_status")
    if expected_status not in VALID_STATUSES:
        errors.append(f"line {line_number}: unknown expected_status {expected_status!r}")

    for field in LIST_FIELDS:
        value = case.get(field)
        if not isinstance(value, list):
            errors.append(f"line {line_number}: {field} must be a list")
        elif not all(isinstance(item, str) and item for item in value):
            errors.append(f"line {line_number}: {field} must contain only non-empty strings")

    for field in OPTIONAL_LIST_FIELDS:
        if field not in case:
            continue
        value = case.get(field)
        if not isinstance(value, list):
            errors.append(f"line {line_number}: {field} must be a list")
        elif not value:
            errors.append(f"line {line_number}: {field} must not be empty when present")
        elif not all(isinstance(item, str) and item for item in value):
            errors.append(f"line {line_number}: {field} must contain only non-empty strings")

    for field in ("source_chunk", "lecture", "expected_bias", "notes"):
        value = case.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"line {line_number}: {field} must be a non-empty string")

    return errors


def load_and_validate(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    cases: list[dict[str, Any]] = []
    errors: list[str] = []
    seen_case_ids: set[str] = set()

    if not path.exists():
        return cases, [f"casebook file not found: {path}"]

    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append(f"line {line_number}: invalid JSON: {exc.msg}")
                continue

            if not isinstance(parsed, dict):
                errors.append(f"line {line_number}: JSON value must be an object")
                continue

            errors.extend(validate_case(parsed, line_number, seen_case_ids))
            cases.append(parsed)

    if not cases:
        errors.append("casebook is empty")

    return cases, errors


def print_summary(cases: list[dict[str, Any]], errors: list[str], path: Path) -> None:
    print(f"file: {path}")
    print(f"case_count: {len(cases)}")
    print(f"error_count: {len(errors)}")

    if cases:
        detector_counts = Counter(case["detector"] for case in cases if "detector" in case)
        status_counts = Counter(case["expected_status"] for case in cases if "expected_status" in case)

        print("detector_distribution:")
        for detector, count in sorted(detector_counts.items()):
            print(f"  {detector}: {count}")

        print("status_distribution:")
        for status, count in sorted(status_counts.items()):
            print(f"  {status}: {count}")

    if errors:
        print("errors:")
        for error in errors:
            print(f"  - {error}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate detector casebook JSONL.")
    parser.add_argument(
        "--path",
        type=Path,
        default=default_casebook_path(),
        help="Path to detector casebook JSONL file.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    args = parse_args(sys.argv[1:] if argv is None else argv)
    cases, errors = load_and_validate(args.path)
    print_summary(cases, errors, args.path)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
