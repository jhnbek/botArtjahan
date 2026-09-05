from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from chart_review_packet import ChartReviewParams, build_chart_review_packet, build_review_structure_audit
from level_discovery import Level
from scn002_strict_kb_backtest import Bar


ROOT = Path(__file__).resolve().parents[1]
VERSION = "layer6_golden_packet_validation_v1"
OUTPUT_DIR = ROOT / "_knowledge_base" / "structured" / "consolidation" / "layer6_golden_packet_validation"
BASE_TIME_MS = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)

SAFETY_FLAGS: dict[str, bool] = {
    "execution_allowed": False,
    "runtime_signal_allowed": False,
    "order_generation_allowed": False,
    "pnl_computation_allowed": False,
    "paper_trading_allowed": False,
    "live_trading_allowed": False,
    "backtest_harness_allowed": False,
}


@dataclass(frozen=True)
class GoldenCase:
    case_id: str
    title: str
    levels: list[Level]
    manual_context: dict[str, Any] = field(default_factory=dict)
    context_bars: list[Bar] | None = None
    execution_bars: list[Bar] | None = None
    breakout_direction: str = "auto"


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
    if isinstance(value, list):
        value = "; ".join(str(item) for item in value)
    if isinstance(value, dict):
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


def synthetic_bars(count: int = 90, start: float = 97.5, drift: float = 0.035,
                   wave: float = 0.18, spread: float = 0.48) -> list[Bar]:
    bars: list[Bar] = []
    previous_close = start
    for index in range(count):
        close = start + drift * index + ((index % 8) - 3.5) * wave
        open_price = previous_close
        high = max(open_price, close) + spread
        low = min(open_price, close) - spread
        bars.append(make_bar(index, open_price, high, low, close))
        previous_close = close
    return bars


CONTEXT_BARS = synthetic_bars()
EXECUTION_BARS = synthetic_bars(count=120, start=98.2, drift=0.018, wave=0.12, spread=0.22)
BREAKOUT_FAILURE_EXECUTION_BARS = [
    make_bar(0, 99.55, 99.78, 99.35, 99.62),
    make_bar(1, 99.62, 99.92, 99.50, 99.85),
    make_bar(2, 99.85, 100.18, 99.70, 100.05),
    make_bar(3, 100.05, 100.82, 99.96, 100.62),
    make_bar(4, 100.62, 100.70, 99.54, 99.78),
]
REBOUND_EXECUTION_BARS = [
    make_bar(0, 100.18, 100.22, 100.08, 100.12),
    make_bar(1, 100.12, 100.16, 99.995, 100.04),
    make_bar(2, 100.04, 100.13, 99.99, 100.08),
    make_bar(3, 100.08, 100.18, 100.04, 100.12),
]
TAIL_BARS_EXECUTION_BARS = [
    make_bar(0, 98.85, 99.10, 98.70, 99.05),
    make_bar(1, 99.05, 99.46, 98.95, 99.38),
    make_bar(2, 99.38, 99.95, 99.30, 99.82),
    make_bar(3, 100.05, 100.45, 99.76, 100.02),
    make_bar(4, 100.03, 100.39, 99.74, 100.00),
]
DRAWN_LEVEL_CONTEXT_BARS = [
    make_bar(0, 101.00, 101.35, 100.78, 101.18),
    make_bar(1, 101.18, 101.44, 100.92, 101.30),
    make_bar(2, 101.30, 101.50, 100.88, 101.05),
    make_bar(3, 101.05, 101.20, 100.00, 100.28),
    make_bar(4, 100.28, 101.10, 100.18, 100.92),
    make_bar(5, 100.92, 101.36, 100.72, 101.14),
    make_bar(6, 101.14, 101.42, 100.86, 101.28),
    make_bar(7, 101.28, 101.56, 100.94, 101.40),
    make_bar(8, 101.40, 101.70, 101.02, 101.54),
    make_bar(9, 101.54, 101.80, 101.10, 101.62),
    make_bar(10, 101.62, 101.82, 100.00, 100.38),
    make_bar(11, 100.38, 101.12, 100.20, 100.96),
    make_bar(12, 100.96, 101.38, 100.78, 101.18),
    make_bar(13, 101.18, 101.52, 100.92, 101.34),
    make_bar(14, 101.34, 101.62, 101.02, 101.48),
    make_bar(15, 101.48, 101.76, 101.18, 101.58),
    make_bar(16, 101.58, 101.86, 101.22, 101.66),
    make_bar(17, 101.66, 101.92, 101.34, 101.74),
    make_bar(18, 101.74, 102.02, 101.42, 101.82),
    make_bar(19, 101.82, 102.08, 101.48, 101.88),
]
V_FORMATION_EXECUTION_BARS = [
    make_bar(0, 100.02, 100.12, 99.92, 100.01),
    make_bar(1, 100.01, 100.04, 99.52, 99.58),
    make_bar(2, 99.58, 99.66, 99.18, 99.34),
    make_bar(3, 99.34, 99.82, 99.28, 99.76),
    make_bar(4, 99.76, 100.12, 99.70, 100.03),
]
AUDIT_BARS = [
    make_bar(0, 100.0, 100.5, 99.6, 100.1),
    make_bar(1, 100.1, 100.7, 99.8, 100.4),
    make_bar(2, 100.4, 100.9, 99.9, 100.6),
    make_bar(3, 100.6, 101.0, 100.0, 100.8),
    make_bar(4, 100.8, 101.1, 100.2, 100.9),
    make_bar(5, 100.9, 101.2, 100.3, 101.0),
    make_bar(6, 101.0, 101.3, 100.4, 101.1),
    make_bar(7, 101.1, 101.4, 100.5, 101.2),
]

REVIEW_STRUCTURE_AUDIT_MODEL_CASES = [
    {
        "case_id": "audit_fixation_return_structure_window",
        "title": "Fixation return audit uses return-attempt structure window",
        "model": "fixation_return",
        "direction": "long",
        "structure_kind": "return_attempt_tail",
        "start_index": 1,
        "end_index": 2,
        "trigger_index": 2,
        "stop_price": 99.0,
    },
    {
        "case_id": "audit_bsu_bpu_structure_window",
        "title": "BSU/BPU audit uses BPU structure window",
        "model": "bsu_bpu_limit",
        "direction": "short",
        "structure_kind": "bpu_structure",
        "start_index": 1,
        "end_index": 2,
        "trigger_index": 2,
        "stop_price": 101.8,
    },
    {
        "case_id": "audit_primary_impulse_structure_window",
        "title": "Primary impulse audit uses pre-breakout base window",
        "model": "primary_impulse",
        "direction": "long",
        "structure_kind": "pre_breakout_base",
        "start_index": 3,
        "end_index": 5,
        "trigger_index": 5,
        "stop_price": 99.0,
    },
    {
        "case_id": "audit_false_breakout_structure_window",
        "title": "False-breakout return audit uses LP rejection structure window",
        "model": "false_breakout_return",
        "direction": "short",
        "structure_kind": "lp_rejection_structure",
        "start_index": 3,
        "end_index": 5,
        "trigger_index": 5,
        "stop_price": 101.8,
    },
]


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


def get_item(packet: dict[str, Any], item_id: str) -> dict[str, Any] | None:
    for item in (packet.get("checklist_matrix") or {}).get("items") or []:
        if item.get("item_id") == item_id:
            return item
    for item in packet.get("manual_review_queue") or []:
        if item.get("item_id") == item_id:
            return item
    for item in packet.get("blockers") or []:
        if item.get("item_id") == item_id:
            return item
    return None


def add_result(rows: list[dict[str, Any]], case: GoldenCase, check_id: str, passed: bool,
               evidence: str, expected: Any = None, actual: Any = None) -> None:
    rows.append({
        "case_id": case.case_id,
        "title": case.title,
        "check_id": check_id,
        "passed": bool(passed),
        "evidence": evidence,
        "expected": expected,
        "actual": actual,
    })


def assert_equal(rows: list[dict[str, Any]], case: GoldenCase, check_id: str,
                 actual: Any, expected: Any, evidence: str = "") -> None:
    add_result(rows, case, check_id, actual == expected, evidence or f"expected {expected}, got {actual}", expected, actual)


def assert_in(rows: list[dict[str, Any]], case: GoldenCase, check_id: str,
              actual: Any, expected_values: set[Any], evidence: str = "") -> None:
    add_result(rows, case, check_id, actual in expected_values, evidence or f"expected one of {sorted(expected_values)}, got {actual}", sorted(expected_values), actual)


def assert_false(rows: list[dict[str, Any]], case: GoldenCase, check_id: str,
                 actual: Any, evidence: str = "") -> None:
    add_result(rows, case, check_id, actual is False, evidence or f"expected false, got {actual}", False, actual)


def assert_item_status(rows: list[dict[str, Any]], case: GoldenCase, packet: dict[str, Any],
                       item_id: str, expected_status: str) -> None:
    item = get_item(packet, item_id)
    actual = None if item is None else item.get("status")
    add_result(
        rows,
        case,
        f"{item_id}_status",
        actual == expected_status,
        f"{item_id} status should be {expected_status}; evidence={(item or {}).get('evidence')}",
        expected_status,
        actual,
    )


def build_packet(case: GoldenCase) -> dict[str, Any]:
    return build_chart_review_packet(
        symbol="GOLDENUSDT",
        context_timeframe="1d",
        execution_timeframe="15m",
        context_bars=case.context_bars or CONTEXT_BARS,
        execution_bars=case.execution_bars or EXECUTION_BARS,
        levels=case.levels,
        higher_timeframe="1w",
        breakout_direction_arg=case.breakout_direction,
        manual_context=case.manual_context,
        params=ChartReviewParams(execution_lookback_bars=96),
    )


def validate_common_packet_contract(rows: list[dict[str, Any]], case: GoldenCase, packet: dict[str, Any]) -> None:
    assert_equal(rows, case, "detector_name", packet.get("detector"), "layer6_chart_review_packet")
    assert_equal(rows, case, "checklist_item_count", (packet.get("checklist_matrix") or {}).get("summary", {}).get("item_count"), 73)
    for flag in SAFETY_FLAGS:
        assert_false(rows, case, f"safety_{flag}", packet.get(flag), "Layer 6 golden packet must stay review-only")
    assert_in(rows, case, "review_status_known", packet.get("review_status"), {"blocked_review_only", "manual_review_required", "checklist_complete_review_only"})


def validate_case_specific(rows: list[dict[str, Any]], case: GoldenCase, packet: dict[str, Any]) -> None:
    if case.case_id == "no_working_level_blocks":
        assert_equal(rows, case, "review_status", packet.get("review_status"), "blocked_review_only")
        assert_equal(rows, case, "entry_status", (packet.get("layer_reports") or {}).get("entry", {}).get("status"), "no_working_level")
        assert_item_status(rows, case, packet, "RSCD-000-002", "block")
        assert_item_status(rows, case, packet, "RSCD-000-003", "block")
    elif case.case_id == "drawn_level_becomes_validated_candidate":
        assert_equal(rows, case, "drawn_level_preserved", len((packet.get("chart_context") or {}).get("drawn_levels") or []), 1)
        level_summary = ((packet.get("layer_reports") or {}).get("levels") or {}).get("summary") or {}
        assert_equal(rows, case, "drawn_level_candidate_count", level_summary.get("candidate_count"), 1)
        assert_equal(rows, case, "drawn_level_working_count", level_summary.get("working_level_count"), 1)
        assert_item_status(rows, case, packet, "RSCD-000-002", "pass")
    elif case.case_id == "drawn_level_without_structure_rejected":
        assert_equal(rows, case, "drawn_level_preserved", len((packet.get("chart_context") or {}).get("drawn_levels") or []), 1)
        level_report = ((packet.get("layer_reports") or {}).get("levels") or {})
        level_rows = level_report.get("levels") or []
        assert_equal(rows, case, "drawn_level_candidate_count", len([level for level in level_rows if level.get("source") == "drawn_level"]), 1)
        assert_equal(rows, case, "drawn_level_working_count", (level_report.get("summary") or {}).get("working_level_count"), 0)
        assert_item_status(rows, case, packet, "RSCD-000-002", "block")
    elif case.case_id == "screenshot_ref_does_not_auto_pass_timing":
        assert_equal(rows, case, "screenshot_ref_preserved", len((packet.get("chart_context") or {}).get("screenshot_refs") or []), 1)
        assert_item_status(rows, case, packet, "RSCD-001-002", "manual_review")
    elif case.case_id == "manual_false_answer_blocks":
        assert_equal(rows, case, "review_status", packet.get("review_status"), "blocked_review_only")
        assert_item_status(rows, case, packet, "RSCD-001-002", "block")
    elif case.case_id == "manual_true_answers_pass":
        assert_item_status(rows, case, packet, "RSCD-001-002", "pass")
        assert_item_status(rows, case, packet, "RSCD-001-003", "pass")
        assert_item_status(rows, case, packet, "RSCD-001-004", "pass")
    elif case.case_id == "optional_context_absent_stays_manual":
        for item_id in ["RSCD-009-001", "RSCD-010-001", "RSCD-013-001", "RSCD-015-001"]:
            assert_item_status(rows, case, packet, item_id, "manual_review")
    elif case.case_id == "discipline_violation_blocks":
        assert_equal(rows, case, "review_status", packet.get("review_status"), "blocked_review_only")
        assert_item_status(rows, case, packet, "RSCD-014-001", "block")
    elif case.case_id == "auto_breakout_failure_context_from_ohlc":
        optional = (((packet.get("layer_reports") or {}).get("permission") or {}).get("optional_context_validations") or {})
        failures = optional.get("breakout_failures") or []
        mechanics = optional.get("market_mechanics") or []
        assert_equal(rows, case, "auto_breakout_failure_count", len(failures), 1)
        assert_equal(rows, case, "auto_breakout_failure_detector", (failures[0] if failures else {}).get("detector"), "breakout_failure")
        assert_equal(rows, case, "auto_breakout_failure_status", (failures[0] if failures else {}).get("status"), "warn")
        assert_equal(rows, case, "auto_market_mechanics_count", len(mechanics), 1)
        assert_equal(rows, case, "auto_market_mechanics_detector", (mechanics[0] if mechanics else {}).get("detector"), "market_mechanics_context")
        assert_equal(rows, case, "auto_market_mechanics_status", (mechanics[0] if mechanics else {}).get("status"), "setup")
        assert_item_status(rows, case, packet, "RSCD-013-001", "pass")
        assert_item_status(rows, case, packet, "FCD-007-000", "manual_review")
    elif case.case_id == "auto_rebound_context_from_ohlc":
        permission = ((packet.get("layer_reports") or {}).get("permission") or {})
        rebounds = (permission.get("optional_context_validations") or {}).get("rebounds") or []
        assert_equal(rows, case, "auto_rebound_count", len(rebounds), 1)
        assert_equal(rows, case, "auto_rebound_detector", (rebounds[0] if rebounds else {}).get("detector"), "rebound_models")
        assert_equal(rows, case, "auto_rebound_status", (rebounds[0] if rebounds else {}).get("status"), "setup")
        assert_item_status(rows, case, packet, "RSCD-015-001", "pass")
        rebound_item = next((item for item in permission.get("manual_checklist", []) if item.get("item_id") == "RSCD-015-000"), None)
        assert_equal(rows, case, "RSCD-015-000_status", (rebound_item or {}).get("status"), "pass")
    elif case.case_id == "auto_tail_bars_context_from_ohlc":
        permission = ((packet.get("layer_reports") or {}).get("permission") or {})
        tail_bars = (permission.get("optional_context_validations") or {}).get("tail_bars") or []
        assert_equal(rows, case, "auto_tail_bar_count", len(tail_bars), 1)
        assert_equal(rows, case, "auto_tail_bar_detector", (tail_bars[0] if tail_bars else {}).get("detector"), "tail_bars_two_sided_limit")
        assert_equal(rows, case, "auto_tail_bar_status", (tail_bars[0] if tail_bars else {}).get("status"), "pass")
        assert_item_status(rows, case, packet, "RSCD-010-001", "pass")
        tail_item = next((item for item in permission.get("manual_checklist", []) if item.get("item_id") == "RSCD-010-000"), None)
        assert_equal(rows, case, "RSCD-010-000_status", (tail_item or {}).get("status"), "pass")
    elif case.case_id == "auto_v_formation_context_from_ohlc":
        permission = ((packet.get("layer_reports") or {}).get("permission") or {})
        formations = (permission.get("optional_context_validations") or {}).get("formations") or []
        assert_equal(rows, case, "auto_formation_count", len(formations), 1)
        assert_equal(rows, case, "auto_formation_detector", (formations[0] if formations else {}).get("detector"), "v_u_formations")
        assert_equal(rows, case, "auto_formation_status", (formations[0] if formations else {}).get("status"), "pass")
        assert_item_status(rows, case, packet, "RSCD-009-001", "pass")
        formation_item = next((item for item in permission.get("manual_checklist", []) if item.get("item_id") == "RSCD-009-000"), None)
        assert_equal(rows, case, "RSCD-009-000_status", (formation_item or {}).get("status"), "pass")


def audit_entry_report(case: dict[str, Any]) -> dict[str, Any]:
    structure_window = {
        "kind": case["structure_kind"],
        "start_time": datetime.fromtimestamp(AUDIT_BARS[case["start_index"]].open_time / 1000, tz=timezone.utc).isoformat(),
        "end_time": datetime.fromtimestamp(AUDIT_BARS[case["end_index"]].open_time / 1000, tz=timezone.utc).isoformat(),
        "trigger_time": datetime.fromtimestamp(AUDIT_BARS[case["trigger_index"]].open_time / 1000, tz=timezone.utc).isoformat(),
        "bar_count": case["end_index"] - case["start_index"] + 1,
    }
    return {
        "best_entry": {
            "model": case["model"],
            "direction": case["direction"],
            "entry_price": 100.6,
            "stop_price": case["stop_price"],
            "target_price": None,
            "entry_detector": {
                "detector": f"{case['model']}_audit_fixture",
                "status": "trigger",
                "direction": case["direction"],
                "structure_window": structure_window,
            },
        }
    }


def validate_review_structure_audit_model_support(rows: list[dict[str, Any]]) -> None:
    for raw_case in REVIEW_STRUCTURE_AUDIT_MODEL_CASES:
        case = GoldenCase(case_id=raw_case["case_id"], title=raw_case["title"], levels=[])
        audit = build_review_structure_audit(audit_entry_report(raw_case), AUDIT_BARS, ChartReviewParams(execution_lookback_bars=0))
        assert_equal(rows, case, "audit_status", audit.get("status"), "pass")
        assert_equal(rows, case, "audit_model", audit.get("model"), raw_case["model"])
        assert_equal(rows, case, "audit_structure_kind", audit.get("structure_kind"), raw_case["structure_kind"])
        assert_equal(rows, case, "audit_stop_protected", audit.get("trigger_structure_stop_protected"), True)
        assert_equal(rows, case, "audit_stop_not_crossed", audit.get("planned_stop_crossed_in_review_window"), False)


def golden_cases() -> list[GoldenCase]:
    return [
        GoldenCase(
            case_id="no_working_level_blocks",
            title="No level means no scenario, no entry, and blocked review-only packet",
            levels=[],
        ),
        GoldenCase(
            case_id="drawn_level_becomes_validated_candidate",
            title="Drawn chart level is promoted only after Layer 1 validation",
            levels=[],
            context_bars=DRAWN_LEVEL_CONTEXT_BARS,
            manual_context={"drawn_levels": [{"price": 100.0, "source": "synthetic_chart"}]},
        ),
        GoldenCase(
            case_id="drawn_level_without_structure_rejected",
            title="Drawn chart level without OHLC structure is preserved but rejected",
            levels=[],
            manual_context={"drawn_levels": [{"price": 140.0, "source": "synthetic_chart"}]},
        ),
        GoldenCase(
            case_id="screenshot_ref_does_not_auto_pass_timing",
            title="Screenshot reference alone does not prove before-scenario timing",
            levels=working_levels(),
            manual_context={"screenshot_refs": ["synthetic_before.png"]},
        ),
        GoldenCase(
            case_id="manual_false_answer_blocks",
            title="Explicit negative manual checklist answer becomes a blocker",
            levels=working_levels(),
            manual_context={"checklist_answers": {"screenshot_before_scenario": False}},
        ),
        GoldenCase(
            case_id="manual_true_answers_pass",
            title="Explicit positive manual checklist answers can pass only their own manual items",
            levels=working_levels(),
            manual_context={"checklist_answers": {
                "screenshot_before_scenario": True,
                "no_opposite_entry_after_scenario": True,
                "separate_scenario_and_trade_result": True,
            }},
        ),
        GoldenCase(
            case_id="optional_context_absent_stays_manual",
            title="Optional visual/context families stay manual when context is not supplied",
            levels=working_levels(),
        ),
        GoldenCase(
            case_id="discipline_violation_blocks",
            title="Discipline violation remains a hard manual blocker",
            levels=working_levels(),
            manual_context={"discipline_violations": ["revenge_trade_after_stop"]},
        ),
        GoldenCase(
            case_id="auto_breakout_failure_context_from_ohlc",
            title="Breakout failure context is generated from execution OHLC, not only supplied manually",
            levels=working_levels(),
            execution_bars=BREAKOUT_FAILURE_EXECUTION_BARS,
            breakout_direction="long",
        ),
        GoldenCase(
            case_id="auto_rebound_context_from_ohlc",
            title="Rebound context is generated from compact reaction to a working level",
            levels=working_levels(),
            execution_bars=REBOUND_EXECUTION_BARS,
        ),
        GoldenCase(
            case_id="auto_tail_bars_context_from_ohlc",
            title="Tail-bar context is generated from two-sided tails near a working level",
            levels=working_levels(),
            execution_bars=TAIL_BARS_EXECUTION_BARS,
        ),
        GoldenCase(
            case_id="auto_v_formation_context_from_ohlc",
            title="V formation context is generated from sharp move out and return to a working level",
            levels=working_levels(),
            execution_bars=V_FORMATION_EXECUTION_BARS,
        ),
    ]


def run_validation() -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    packet_summaries: list[dict[str, Any]] = []
    for case in golden_cases():
        packet = build_packet(case)
        validate_common_packet_contract(rows, case, packet)
        validate_case_specific(rows, case, packet)
        counts = (packet.get("checklist_matrix") or {}).get("summary", {}).get("status_counts", {})
        packet_summaries.append({
            "case_id": case.case_id,
            "review_status": packet.get("review_status"),
            "hard_gate_status": (packet.get("permission_summary") or {}).get("hard_gate_status"),
            "best_entry_model": (packet.get("permission_summary") or {}).get("best_entry_model"),
            "best_entry_status": (packet.get("permission_summary") or {}).get("best_entry_status"),
            "checklist_status_counts": counts,
            **SAFETY_FLAGS,
        })
    validate_review_structure_audit_model_support(rows)
    failed = [row for row in rows if not row["passed"]]
    status = {
        "version": VERSION,
        "generated_at": utc_now(),
        "mode": "read_only_layer6_golden_packet_validation",
        "case_count": len(golden_cases()),
        "review_structure_audit_model_case_count": len(REVIEW_STRUCTURE_AUDIT_MODEL_CASES),
        "assertion_count": len(rows),
        "passed_assertion_count": len(rows) - len(failed),
        "failed_assertion_count": len(failed),
        "layer6_golden_packet_validation_ready": not failed,
        "scope_note": "synthetic closed-bar packet contracts only; no PnL, labels, orders, runtime signals, paper trading, live trading, or backtest harness",
        "packet_summaries": packet_summaries,
        **SAFETY_FLAGS,
    }
    return status, rows, failed


def write_report(path: Path, status: dict[str, Any], rows: list[dict[str, Any]], failed: list[dict[str, Any]]) -> None:
    lines = [
        "# Layer 6 Golden Packet Validation",
        "",
        "## Verdict",
        "",
        f"- Generated: `{status['generated_at']}`",
        f"- Ready: `{str(status['layer6_golden_packet_validation_ready']).lower()}`",
        f"- Cases: {status['case_count']}",
        f"- Review structure audit model cases: {status['review_structure_audit_model_case_count']}",
        f"- Assertions passed: {status['passed_assertion_count']} / {status['assertion_count']}",
        f"- Failed assertions: {status['failed_assertion_count']}",
        f"- Execution allowed: `{str(status['execution_allowed']).lower()}`",
        f"- PnL computation allowed: `{str(status['pnl_computation_allowed']).lower()}`",
        f"- Backtest harness allowed: `{str(status['backtest_harness_allowed']).lower()}`",
        "",
        "This artifact validates complete Layer 6 chart-review packets on synthetic golden cases. It checks packet-level application of KB boundaries rather than isolated detector functions.",
        "",
        "## Case Summary",
        "",
        "| Case | Review | Hard gate | Best entry | Checklist counts |",
        "| --- | --- | --- | --- | --- |",
    ]
    for packet in status["packet_summaries"]:
        best = f"{packet.get('best_entry_model') or '-'} / {packet.get('best_entry_status') or '-'}"
        lines.append("| " + " | ".join([
            table_cell(packet["case_id"]),
            table_cell(packet.get("review_status")),
            table_cell(packet.get("hard_gate_status")),
            table_cell(best),
            table_cell(packet.get("checklist_status_counts")),
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
    write_json(output_dir / "layer6_golden_packet_validation_status.json", status)
    write_jsonl(output_dir / "layer6_golden_packet_validation_assertions.jsonl", rows)
    write_jsonl(output_dir / "layer6_golden_packet_validation_failures.jsonl", failed)
    write_report(output_dir / "layer6_golden_packet_validation.md", status, rows, failed)
    return status


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate Layer 6 chart-review packet behavior on synthetic golden cases")
    parser.add_argument("--out-dir", default=str(OUTPUT_DIR), help="Output directory for validation artifacts")
    parser.add_argument("--strict-exit-code", action="store_true", help="Return non-zero when golden assertions fail")
    return parser


def main() -> int:
    configure_stdio()
    args = build_parser().parse_args()
    status = build_outputs(Path(args.out_dir))
    print(json.dumps({
        "version": status["version"],
        "layer6_golden_packet_validation_ready": status["layer6_golden_packet_validation_ready"],
        "case_count": status["case_count"],
        "review_structure_audit_model_case_count": status["review_structure_audit_model_case_count"],
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