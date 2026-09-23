from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from build_layer7_real_case_review_brief import build_summary, output_stem
from validate_layer7_real_chart_cases import (
    ROOT,
    SAFETY_FLAGS,
    validate_case_schema,
    validate_packet_expectations,
)


VERSION = "layer7_real_case_promotion_v1"
DEFAULT_CASES_DIR = ROOT / "_knowledge_base" / "scenario_review_casebook" / "layer7_real_chart_cases"


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


def load_case(path: Path) -> dict[str, Any]:
    case = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(case, dict):
        raise ValueError("case file must contain a JSON object")
    return case


def write_case(path: Path, case: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(case, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def default_target_path(source_path: Path, cases_dir: Path) -> Path:
    return cases_dir / f"{output_stem(source_path)}.json"


def review_flag_blockers(args: argparse.Namespace) -> list[str]:
    blockers: list[str] = []
    if not str(args.reviewed_by or "").strip():
        blockers.append("reviewed_by_required")
    if not args.confirm_ohlc_reviewed:
        blockers.append("confirm_ohlc_reviewed_required")
    if not args.confirm_levels_reviewed:
        blockers.append("confirm_levels_reviewed_required")
    if not args.confirm_expectations_reviewed:
        blockers.append("confirm_expectations_reviewed_required")
    return blockers


def promoted_case(case: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    now = utc_now()
    updated = json.loads(json.dumps(case, ensure_ascii=False))
    note = str(args.review_note or "").strip()
    updated["human_review"] = {
        "reviewed_by": str(args.reviewed_by).strip(),
        "reviewed_at": args.reviewed_at or now,
        "ohlc_reviewed": True,
        "levels_reviewed": True,
        "expectations_reviewed": True,
        "notes": note or "Layer 7 real-case OHLC, levels, and expectations were explicitly reviewed before promotion.",
    }
    updated["case_promotion"] = {
        "version": VERSION,
        "promoted_at": now,
        "source_template": rel_path(Path(args.case_file)),
        "review_note": note,
        "scope_note": "review-only case promotion; no outcome labels, PnL, order generation, paper/live trading, or backtest harness",
        **SAFETY_FLAGS,
    }
    return updated


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Promote a Layer 7 .template.json draft to a reviewed .json case only after explicit review confirmation")
    parser.add_argument("case_file", help="Path to a Layer 7 .template.json draft")
    parser.add_argument("--cases-dir", default=str(DEFAULT_CASES_DIR))
    parser.add_argument("--target-file", help="Target .json case path; defaults next to casebook cases")
    parser.add_argument("--reviewed-by", default="", help="Human reviewer name recorded into human_review.reviewed_by")
    parser.add_argument("--reviewed-at", default="", help="Optional ISO timestamp; defaults to current UTC time")
    parser.add_argument("--review-note", default="")
    parser.add_argument("--confirm-ohlc-reviewed", action="store_true")
    parser.add_argument("--confirm-levels-reviewed", action="store_true")
    parser.add_argument("--confirm-expectations-reviewed", action="store_true")
    parser.add_argument("--promote", action="store_true", help="Actually write the promoted .json file; default is dry-run")
    parser.add_argument("--overwrite", action="store_true", help="Allow overwriting an existing target .json")
    return parser


def main() -> int:
    configure_stdio()
    args = build_parser().parse_args()
    source_path = Path(args.case_file)
    target_path = Path(args.target_file) if args.target_file else default_target_path(source_path, Path(args.cases_dir))
    case = load_case(source_path)
    summary, _packet, assertions = build_summary(source_path)
    generated_expectations_match = summary["expectation_assertions"]["all_generated_expectations_match_current_packet"]

    blockers = []
    if not source_path.name.endswith(".template.json"):
        blockers.append("source_must_be_template_json")
    if target_path.name.endswith(".template.json") or target_path.suffix != ".json":
        blockers.append("target_must_be_non_template_json")
    if target_path.exists() and not args.overwrite:
        blockers.append("target_exists_use_overwrite_if_intended")
    if not generated_expectations_match:
        blockers.append("generated_expectations_do_not_match_current_packet")
    blockers.extend(review_flag_blockers(args))

    candidate = promoted_case(case, args)
    schema_errors = validate_case_schema(candidate, target_path)
    candidate_assertions = validate_packet_expectations(candidate, _packet)
    failed_candidate_assertions = [row for row in candidate_assertions if not row.get("passed")]
    blockers.extend(schema_errors)
    blockers.extend(f"assertion_failed:{row.get('check_id')}" for row in failed_candidate_assertions)

    ready = not blockers
    wrote = False
    if args.promote and ready:
        write_case(target_path, candidate)
        wrote = True

    status = {
        "version": VERSION,
        "source_file": rel_path(source_path),
        "target_file": rel_path(target_path),
        "dry_run": not args.promote,
        "ready_to_promote": ready,
        "promoted_case_written": wrote,
        "promotion_blockers": blockers,
        "case_id": case.get("case_id"),
        "generated_expectations_match_current_packet": generated_expectations_match,
        "source_assertion_count": len(assertions),
        "candidate_assertion_count": len(candidate_assertions),
        "candidate_failed_assertion_count": len(failed_candidate_assertions),
        "reviewed_by": str(args.reviewed_by or "").strip(),
        "review_flags": {
            "ohlc_reviewed": bool(args.confirm_ohlc_reviewed),
            "levels_reviewed": bool(args.confirm_levels_reviewed),
            "expectations_reviewed": bool(args.confirm_expectations_reviewed),
        },
        **SAFETY_FLAGS,
    }
    print(json.dumps(status, ensure_ascii=False, indent=2))
    if args.promote and not ready:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())