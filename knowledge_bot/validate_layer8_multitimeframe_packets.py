from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from build_layer8_trade_review_brief import build_summary as build_layer8_brief_summary
from level_discovery import Level
from multitimeframe_trade_review_packet import (
    SAFETY_FLAGS,
    MultiTimeframeReviewParams,
    build_alignment_summary,
    build_multitimeframe_trade_review_packet,
)
from scn002_strict_kb_backtest import Bar


ROOT = Path(__file__).resolve().parents[1]
VERSION = "layer8_multitimeframe_packet_validation_v1"
OUTPUT_DIR = ROOT / "_knowledge_base" / "structured" / "consolidation" / "layer8_multitimeframe_packet_validation"
BTC_DRAFT_CASE_PATH = ROOT / "_knowledge_base" / "scenario_review_casebook" / "layer7_real_chart_cases" / "L7-USER-REAL-001-btcusdt-2024-01.template.json"
BASE_TIME_MS = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) for row in rows)
    path.write_text(text + ("\n" if text else ""), encoding="utf-8")


def table_cell(value: Any) -> str:
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    text = " ".join(str(value if value is not None else "").split()).replace("|", "\\|")
    return text or "-"


def make_bar(index: int, open_price: float, high: float, low: float, close: float) -> Bar:
    return Bar(
        open_time=BASE_TIME_MS + index * 60_000,
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=1000.0 + index,
    )


def synthetic_bars(count: int, start: float, drift: float, wave: float, spread: float) -> list[Bar]:
    bars: list[Bar] = []
    previous_close = start
    for index in range(count):
        close = start + drift * index + ((index % 9) - 4) * wave
        open_price = previous_close
        bars.append(make_bar(index, open_price, max(open_price, close) + spread, min(open_price, close) - spread, close))
        previous_close = close
    return bars


def level(price: float, side: str = "mirror", status: str = "pass", score: float = 4.0) -> Level:
    return Level(
        price=price,
        bsu_index=10,
        bsu_time=datetime.fromtimestamp((BASE_TIME_MS + 10 * 60_000) / 1000, tz=timezone.utc).isoformat(),
        side=side,
        basis_tags=["mirror_level", "strong_movement_stop"],
        touch_count=3,
        exact_touch_count=2,
        touch_indices=[10, 28, 55],
        active_after_last_touch=True,
        atr=1.0,
        distance_atr=0.0,
        touch_quality="good",
        last_reaction_atr=0.6,
        automation_confidence=0.8,
        kb_status=status,
        kb_score=score,
        kb_strength=["structural_level_basis", "multiple_touches"] if status == "pass" else [],
        kb_hard_rejects=[] if status == "pass" else ["synthetic_rejected_level"],
    )


def working_levels() -> list[Level]:
    return [
        level(100.0, "mirror", "pass", 4.0),
        level(104.5, "resistance", "pass", 3.5),
        level(95.5, "support", "pass", 3.5),
    ]


def add_assertion(rows: list[dict[str, Any]], case_id: str, check_id: str, passed: bool,
                  evidence: str, expected: Any = None, actual: Any = None) -> None:
    rows.append({
        "case_id": case_id,
        "check_id": check_id,
        "passed": bool(passed),
        "evidence": evidence,
        "expected": expected,
        "actual": actual,
    })


def assert_equal(rows: list[dict[str, Any]], case_id: str, check_id: str,
                 actual: Any, expected: Any, evidence: str = "") -> None:
    add_assertion(rows, case_id, check_id, actual == expected, evidence or f"expected {expected}, got {actual}", expected, actual)


def assert_in(rows: list[dict[str, Any]], case_id: str, check_id: str,
              actual: Any, expected: set[Any]) -> None:
    add_assertion(rows, case_id, check_id, actual in expected, f"expected one of {sorted(expected)}, got {actual}", sorted(expected), actual)


def assert_false(rows: list[dict[str, Any]], case_id: str, check_id: str, actual: Any) -> None:
    add_assertion(rows, case_id, check_id, actual is False, "Layer 8 packet must stay review-only", False, actual)


def build_working_packet() -> dict[str, Any]:
    higher_bars = synthetic_bars(90, 98.0, 0.035, 0.16, 0.48)
    setup_bars = synthetic_bars(120, 98.2, 0.018, 0.12, 0.24)
    execution_bars = synthetic_bars(140, 98.4, 0.010, 0.09, 0.18)
    return build_multitimeframe_trade_review_packet(
        "LAYER8USDT",
        "1d",
        "1h",
        "15m",
        higher_bars,
        setup_bars,
        execution_bars,
        "auto",
        {},
        MultiTimeframeReviewParams(execution_lookback_bars=96),
        working_levels(),
        working_levels(),
    )


def validate_working_packet(rows: list[dict[str, Any]], packet: dict[str, Any]) -> dict[str, Any]:
    case_id = "layer8_working_multitimeframe_packet"
    assert_equal(rows, case_id, "detector_name", packet.get("detector"), "layer8_multitimeframe_trade_review_packet")
    assert_in(rows, case_id, "review_status_known", packet.get("review_status"), {"blocked_review_only", "manual_review_required", "checklist_complete_review_only"})
    assert_equal(rows, case_id, "higher_timeframe", (packet.get("timeframes") or {}).get("higher"), "1d")
    assert_equal(rows, case_id, "setup_timeframe", (packet.get("timeframes") or {}).get("setup"), "1h")
    assert_equal(rows, case_id, "execution_timeframe", (packet.get("timeframes") or {}).get("execution"), "15m")
    for key in ["higher", "setup", "execution"]:
        add_assertion(rows, case_id, f"decision_{key}_present", key in (packet.get("timeframe_decisions") or {}), f"{key} decision should exist")
    for flag in SAFETY_FLAGS:
        assert_false(rows, case_id, f"safety_{flag}", packet.get(flag))
        assert_false(rows, case_id, f"execution_packet_safety_{flag}", (packet.get("execution_packet") or {}).get(flag))
    assert_equal(rows, case_id, "execution_packet_detector", (packet.get("execution_packet") or {}).get("detector"), "layer6_chart_review_packet")
    assert_equal(rows, case_id, "checklist_item_count", (((packet.get("execution_packet") or {}).get("checklist_matrix") or {}).get("summary") or {}).get("item_count"), 73)
    return {
        "case_id": case_id,
        "review_status": packet.get("review_status"),
        "alignment_status": (packet.get("alignment_summary") or {}).get("status"),
        "execution_status": ((packet.get("timeframe_decisions") or {}).get("execution") or {}).get("status"),
    }


def validate_alignment_conflict(rows: list[dict[str, Any]]) -> dict[str, Any]:
    case_id = "layer8_alignment_conflict_blocks_execution_mismatch"
    alignment = build_alignment_summary(
        {"status": "context_ready", "family": "breakout", "direction": "long"},
        {"status": "context_ready", "family": "breakout", "direction": "long"},
        {"status": "manual_review_required", "family": "breakout", "direction": "short"},
    )
    assert_equal(rows, case_id, "alignment_status", alignment.get("status"), "structural_conflict")
    add_assertion(
        rows,
        case_id,
        "alignment_blocker_present",
        "execution_direction_conflicts_with_setup_context" in alignment.get("blockers", []),
        "execution direction conflict should be a Layer 8 blocker",
    )
    return {"case_id": case_id, "alignment_status": alignment.get("status"), "blockers": alignment.get("blockers", [])}


def validate_btc_draft_layer8_regression(rows: list[dict[str, Any]]) -> dict[str, Any]:
    case_id = "layer8_btc_draft_multitimeframe_regression"
    if not BTC_DRAFT_CASE_PATH.exists():
        add_assertion(rows, case_id, "btc_draft_exists", False, f"missing {BTC_DRAFT_CASE_PATH}", True, False)
        return {"case_id": case_id, "passed": False, "error": "btc_draft_missing"}
    summary, _packet = build_layer8_brief_summary(BTC_DRAFT_CASE_PATH)
    preview = summary.get("packet_preview") or {}
    decisions = summary.get("timeframe_decisions") or {}
    higher = decisions.get("higher") or {}
    setup = decisions.get("setup") or {}
    execution = decisions.get("execution") or {}
    alignment = summary.get("alignment_summary") or {}
    assert_equal(rows, case_id, "template_ignored", summary.get("template_ignored_by_validator"), True)
    assert_equal(rows, case_id, "human_review_incomplete", summary.get("human_review_complete"), False)
    assert_equal(rows, case_id, "review_status", preview.get("review_status"), "blocked_review_only")
    assert_equal(rows, case_id, "alignment_status", preview.get("alignment_status"), "structural_conflict")
    assert_equal(rows, case_id, "execution_review_status", preview.get("execution_review_status"), "blocked_review_only")
    assert_equal(rows, case_id, "higher_status", higher.get("status"), "context_ready")
    assert_equal(rows, case_id, "higher_family", higher.get("family"), "false_breakout")
    assert_equal(rows, case_id, "higher_direction", higher.get("direction"), "short")
    assert_equal(rows, case_id, "setup_status", setup.get("status"), "blocked_review_only")
    add_assertion(rows, case_id, "setup_no_working_level", "no_working_level" in setup.get("blockers", []), "H1 setup context should be blocked by missing working level")
    assert_equal(rows, case_id, "execution_structure_audit_status", execution.get("structure_audit_status"), "manual_review_required")
    add_assertion(rows, case_id, "alignment_blocks_execution_packet", "execution_packet_blocked" in alignment.get("blockers", []), "alignment should block when execution packet is blocked")
    for flag in SAFETY_FLAGS:
        assert_false(rows, case_id, f"safety_{flag}", summary.get(flag))
    return {
        "case_id": case_id,
        "review_status": preview.get("review_status"),
        "alignment_status": preview.get("alignment_status"),
        "higher_status": higher.get("status"),
        "setup_status": setup.get("status"),
        "execution_status": execution.get("status"),
    }


def run_validation() -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    case_results = [
        validate_working_packet(rows, build_working_packet()),
        validate_alignment_conflict(rows),
        validate_btc_draft_layer8_regression(rows),
    ]
    failed = [row for row in rows if not row["passed"]]
    status = {
        "version": VERSION,
        "generated_at": utc_now(),
        "mode": "read_only_layer8_multitimeframe_packet_validation",
        "case_count": len(case_results),
        "assertion_count": len(rows),
        "passed_assertion_count": len(rows) - len(failed),
        "failed_assertion_count": len(failed),
        "layer8_multitimeframe_packet_validation_ready": not failed,
        "case_results": case_results,
        "scope_note": "validates read-only multi-timeframe review packet contracts; no PnL, labels, orders, runtime signals, paper trading, live trading, or backtest harness",
        **SAFETY_FLAGS,
    }
    return status, rows, failed


def write_report(path: Path, status: dict[str, Any], rows: list[dict[str, Any]], failed: list[dict[str, Any]]) -> None:
    lines = [
        "# Layer 8 Multi-Timeframe Packet Validation",
        "",
        "## Verdict",
        "",
        f"- Generated: `{status['generated_at']}`",
        f"- Ready: `{str(status['layer8_multitimeframe_packet_validation_ready']).lower()}`",
        f"- Cases: {status['case_count']}",
        f"- Assertions passed: {status['passed_assertion_count']} / {status['assertion_count']}",
        f"- Failed assertions: {status['failed_assertion_count']}",
        f"- Execution allowed: `{str(status['execution_allowed']).lower()}`",
        f"- PnL computation allowed: `{str(status['pnl_computation_allowed']).lower()}`",
        f"- Backtest harness allowed: `{str(status['backtest_harness_allowed']).lower()}`",
        "",
        "Layer 8 validates the D1/H1/execution workflow wrapper around Layer 6. It remains review-only and does not promote any trade signal.",
        "",
        "## Cases",
        "",
        "| Case | Result | Detail |",
        "| --- | --- | --- |",
    ]
    for case in status["case_results"]:
        lines.append("| " + " | ".join([
            table_cell(case.get("case_id")),
            table_cell(case.get("review_status") or case.get("alignment_status")),
            table_cell(case),
        ]) + " |")
    lines.extend(["", "## Assertions", "", "| Case | Check | Passed | Evidence |", "| --- | --- | --- | --- |"])
    for row in rows:
        lines.append("| " + " | ".join([
            table_cell(row["case_id"]),
            table_cell(row["check_id"]),
            table_cell(str(row["passed"]).lower()),
            table_cell(row["evidence"]),
        ]) + " |")
    if failed:
        lines.extend(["", "## Failed Assertions", ""])
        for row in failed:
            lines.append(f"- `{row['case_id']}` `{row['check_id']}` expected `{row['expected']}` got `{row['actual']}`: {row['evidence']}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def build_outputs(output_dir: Path) -> dict[str, Any]:
    status, rows, failed = run_validation()
    write_json(output_dir / "layer8_multitimeframe_packet_validation_status.json", status)
    write_jsonl(output_dir / "layer8_multitimeframe_packet_validation_assertions.jsonl", rows)
    write_jsonl(output_dir / "layer8_multitimeframe_packet_validation_failures.jsonl", failed)
    write_report(output_dir / "layer8_multitimeframe_packet_validation.md", status, rows, failed)
    return status


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate Layer 8 multi-timeframe packet behavior")
    parser.add_argument("--out-dir", default=str(OUTPUT_DIR), help="Output directory for validation artifacts")
    parser.add_argument("--strict-exit-code", action="store_true", help="Return non-zero when assertions fail")
    return parser


def main() -> int:
    configure_stdio()
    args = build_parser().parse_args()
    status = build_outputs(Path(args.out_dir))
    print(json.dumps({
        "version": status["version"],
        "layer8_multitimeframe_packet_validation_ready": status["layer8_multitimeframe_packet_validation_ready"],
        "case_count": status["case_count"],
        "assertion_count": status["assertion_count"],
        "passed_assertion_count": status["passed_assertion_count"],
        "failed_assertion_count": status["failed_assertion_count"],
        "output_dir": str(Path(args.out_dir)),
        **SAFETY_FLAGS,
    }, ensure_ascii=False, indent=2))
    if args.strict_exit_code and status["failed_assertion_count"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())