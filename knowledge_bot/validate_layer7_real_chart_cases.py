from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from chart_review_packet import ChartReviewParams, build_chart_review_packet
from level_discovery import Level
from scn002_strict_kb_backtest import Bar


ROOT = Path(__file__).resolve().parents[1]
VERSION = "layer7_real_chart_casebook_validation_v1"
DEFAULT_CASES_DIR = ROOT / "_knowledge_base" / "scenario_review_casebook" / "layer7_real_chart_cases"
OUTPUT_DIR = ROOT / "_knowledge_base" / "structured" / "consolidation" / "layer7_real_chart_casebook_validation"
BASE_TIME_MS = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)

DRAFT_REVIEW_AUDITS = {
    "L7-USER-REAL-001-btcusdt-2024-01.template.json": {
        "case_id": "L7-USER-REAL-001-btcusdt-2024-01",
        "expected_review_status": "blocked_review_only",
        "expected_best_entry_model": "bsu_bpu_limit",
        "expected_audit_status": "structural_conflict",
        "expected_audit_blockers": ["planned_stop_crossed_in_review_window"],
    },
}

VALID_CASE_ORIGINS = {"synthetic_seed", "user_real_reviewed"}
VALID_REVIEW_STATUSES = {"blocked_review_only", "manual_review_required", "checklist_complete_review_only"}
VALID_CHECKLIST_STATUSES = {"pass", "block", "manual_review"}

SAFETY_FLAGS: dict[str, bool] = {
    "execution_allowed": False,
    "runtime_signal_allowed": False,
    "order_generation_allowed": False,
    "pnl_computation_allowed": False,
    "paper_trading_allowed": False,
    "live_trading_allowed": False,
    "backtest_harness_allowed": False,
}

FORBIDDEN_CASE_KEYS = {
    "actual_pnl",
    "filled_order_id",
    "label",
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


def rel_path(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


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


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def case_files(cases_dir: Path) -> list[Path]:
    if not cases_dir.exists():
        return []
    return sorted(path for path in cases_dir.rglob("*.json") if not path.name.endswith(".template.json"))


def scan_forbidden_case_keys(value: Any, path: str = "$", findings: list[str] | None = None) -> list[str]:
    findings = findings or []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if str(key).lower() in FORBIDDEN_CASE_KEYS:
                findings.append(child_path)
            scan_forbidden_case_keys(child, child_path, findings)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            scan_forbidden_case_keys(child, f"{path}[{index}]", findings)
    return findings


def parse_open_time(value: Any, fallback_index: int) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip():
        return int(datetime.fromisoformat(value.strip().replace("Z", "+00:00")).timestamp() * 1000)
    return BASE_TIME_MS + fallback_index * 60_000


def bar_from_dict(row: dict[str, Any], index: int) -> Bar:
    return Bar(
        open_time=parse_open_time(row.get("open_time") or row.get("time"), index),
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=float(row.get("volume", 0.0)),
    )


def synthetic_bars(config: dict[str, Any]) -> list[Bar]:
    kind = config.get("kind", "synthetic_wave")
    if kind != "synthetic_wave":
        raise ValueError(f"unsupported bar fixture kind: {kind}")
    count = int(config.get("count", 90))
    start = float(config.get("start", 97.5))
    drift = float(config.get("drift", 0.035))
    wave = float(config.get("wave", 0.18))
    spread = float(config.get("spread", 0.48))
    bars: list[Bar] = []
    previous_close = start
    for index in range(count):
        close = start + drift * index + ((index % 8) - 3.5) * wave
        open_price = previous_close
        high = max(open_price, close) + spread
        low = min(open_price, close) - spread
        bars.append(Bar(
            open_time=BASE_TIME_MS + index * 60_000,
            open=open_price,
            high=high,
            low=low,
            close=close,
            volume=1000.0 + index,
        ))
        previous_close = close
    return bars


def bars_from_case(case: dict[str, Any], side: str) -> list[Bar]:
    bars = (case.get("bars") or {}).get(side)
    if bars is not None:
        if not isinstance(bars, list):
            raise ValueError(f"bars.{side} must be a list")
        return [bar_from_dict(row, index) for index, row in enumerate(bars)]
    fixture = (case.get("bar_fixture") or {}).get(side)
    if fixture is None:
        raise ValueError(f"missing bars.{side} or bar_fixture.{side}")
    if case.get("case_origin") != "synthetic_seed":
        raise ValueError("bar_fixture is allowed only for synthetic_seed cases")
    if not isinstance(fixture, dict):
        raise ValueError(f"bar_fixture.{side} must be an object")
    return synthetic_bars(fixture)


def level_from_dict(row: dict[str, Any], index: int) -> Level:
    status = str(row.get("kb_status", "pass"))
    return Level(
        price=float(row["price"]),
        bsu_index=int(row.get("bsu_index", 10)),
        bsu_time=str(row.get("bsu_time") or datetime.fromtimestamp((BASE_TIME_MS + (10 + index) * 60_000) / 1000, tz=timezone.utc).isoformat()),
        side=str(row.get("side", "mirror")),
        basis_tags=list(row.get("basis_tags", ["manual_reviewed_level"])),
        touch_count=int(row.get("touch_count", 3)),
        false_breakout_count=int(row.get("false_breakout_count", 0)),
        touch_indices=list(row.get("touch_indices", [10, 28, 55])),
        inside_channel=bool(row.get("inside_channel", False)),
        local_noise=bool(row.get("local_noise", False)),
        short_tail_without_confirmation=bool(row.get("short_tail_without_confirmation", False)),
        repeated_chop=bool(row.get("repeated_chop", False)),
        post_chop_acceptance=bool(row.get("post_chop_acceptance", False)),
        higher_timeframe_confirmed=bool(row.get("higher_timeframe_confirmed", False)),
        higher_timeframe=str(row.get("higher_timeframe", "")),
        scope=str(row.get("scope", "local")),
        distance_atr=float(row.get("distance_atr", 0.0)),
        atr=float(row.get("atr", 1.0)),
        exact_touch_count=int(row.get("exact_touch_count", 2)),
        touch_error_atr=float(row.get("touch_error_atr", 0.0)),
        touch_quality=str(row.get("touch_quality", "good")),
        close_side_switches=int(row.get("close_side_switches", 0)),
        close_balance_ratio=float(row.get("close_balance_ratio", 0.0)),
        active_after_last_touch=bool(row.get("active_after_last_touch", True)),
        last_reaction_atr=float(row.get("last_reaction_atr", 0.6)),
        automation_confidence=float(row.get("automation_confidence", 0.8)),
        kb_status=status,
        kb_score=float(row.get("kb_score", 4.0 if status == "pass" else 0.0)),
        kb_hard_rejects=list(row.get("kb_hard_rejects", [] if status == "pass" else ["case_marked_rejected_level"])),
        kb_strength=list(row.get("kb_strength", ["case_reviewed_level"] if status == "pass" else [])),
    )


def levels_from_case(case: dict[str, Any]) -> list[Level]:
    levels = case.get("levels", [])
    if not isinstance(levels, list):
        raise ValueError("levels must be a list")
    return [level_from_dict(row, index) for index, row in enumerate(levels)]


def validate_case_schema(case: dict[str, Any], path: Path) -> list[str]:
    errors: list[str] = []
    for field in ["case_id", "case_origin", "title", "symbol", "timeframes", "expectations"]:
        if field not in case:
            errors.append(f"{path.name}: missing required field {field}")
    if case.get("case_origin") not in VALID_CASE_ORIGINS:
        errors.append(f"{path.name}: case_origin must be one of {sorted(VALID_CASE_ORIGINS)}")
    if not isinstance(case.get("timeframes"), dict):
        errors.append(f"{path.name}: timeframes must be an object")
    else:
        for key in ["context", "execution", "higher"]:
            if not case["timeframes"].get(key):
                errors.append(f"{path.name}: timeframes.{key} is required")
    if not isinstance(case.get("expectations"), dict):
        errors.append(f"{path.name}: expectations must be an object")
    forbidden = scan_forbidden_case_keys(case)
    if forbidden:
        errors.append(f"{path.name}: forbidden outcome/PnL/order fields present: {', '.join(forbidden)}")
    if case.get("case_origin") == "user_real_reviewed":
        if case.get("bar_fixture"):
            errors.append(f"{path.name}: user_real_reviewed cases must use supplied bars, not synthetic fixtures")
        bars = case.get("bars")
        if not isinstance(bars, dict):
            errors.append(f"{path.name}: user_real_reviewed cases must supply bars.context and bars.execution")
        else:
            for key in ["context", "execution"]:
                if not isinstance(bars.get(key), list) or not bars.get(key):
                    errors.append(f"{path.name}: user_real_reviewed cases must supply non-empty bars.{key}")
        human_review = case.get("human_review")
        if not isinstance(human_review, dict):
            errors.append(f"{path.name}: user_real_reviewed cases require a human_review object")
        else:
            for key in ["ohlc_reviewed", "levels_reviewed", "expectations_reviewed"]:
                if human_review.get(key) is not True:
                    errors.append(f"{path.name}: human_review.{key} must be true before real chart behavior can be verified")
            if not str(human_review.get("reviewed_by") or "").strip():
                errors.append(f"{path.name}: human_review.reviewed_by is required")
            if not str(human_review.get("reviewed_at") or "").strip():
                errors.append(f"{path.name}: human_review.reviewed_at is required")
    expectations = case.get("expectations") or {}
    review_status = expectations.get("review_status")
    if review_status is not None and review_status not in VALID_REVIEW_STATUSES:
        errors.append(f"{path.name}: expectations.review_status is unknown: {review_status}")
    checklist_statuses = expectations.get("checklist_item_statuses", {})
    if checklist_statuses is not None and not isinstance(checklist_statuses, dict):
        errors.append(f"{path.name}: expectations.checklist_item_statuses must be an object")
    elif isinstance(checklist_statuses, dict):
        for item_id, status in checklist_statuses.items():
            if not isinstance(item_id, str) or not item_id:
                errors.append(f"{path.name}: checklist item id must be a non-empty string")
            if status not in VALID_CHECKLIST_STATUSES:
                errors.append(f"{path.name}: expected status for {item_id} is unknown: {status}")
    return errors


def get_item(packet: dict[str, Any], item_id: str) -> dict[str, Any] | None:
    for item in (packet.get("checklist_matrix") or {}).get("items") or []:
        if item.get("item_id") == item_id:
            return item
    return None


def add_assertion(rows: list[dict[str, Any]], case: dict[str, Any], check_id: str, passed: bool,
                  evidence: str, expected: Any = None, actual: Any = None) -> None:
    rows.append({
        "case_id": case.get("case_id"),
        "case_origin": case.get("case_origin"),
        "title": case.get("title"),
        "check_id": check_id,
        "passed": bool(passed),
        "evidence": evidence,
        "expected": expected,
        "actual": actual,
    })


def assert_equal(rows: list[dict[str, Any]], case: dict[str, Any], check_id: str,
                 actual: Any, expected: Any, evidence: str = "") -> None:
    add_assertion(rows, case, check_id, actual == expected, evidence or f"expected {expected}, got {actual}", expected, actual)


def assert_false(rows: list[dict[str, Any]], case: dict[str, Any], check_id: str, actual: Any) -> None:
    add_assertion(rows, case, check_id, actual is False, "Layer 7 packet must stay review-only", False, actual)


def build_packet(case: dict[str, Any]) -> dict[str, Any]:
    timeframes = case["timeframes"]
    return build_chart_review_packet(
        symbol=str(case["symbol"]),
        context_timeframe=str(timeframes["context"]),
        execution_timeframe=str(timeframes["execution"]),
        context_bars=bars_from_case(case, "context"),
        execution_bars=bars_from_case(case, "execution"),
        levels=levels_from_case(case),
        higher_timeframe=str(timeframes["higher"]),
        breakout_direction_arg=str(case.get("breakout_direction", "auto")),
        manual_context=case.get("manual_context") or {},
        params=ChartReviewParams(execution_lookback_bars=int(case.get("execution_lookback_bars", 96))),
    )


def validate_packet_expectations(case: dict[str, Any], packet: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    expectations = case.get("expectations") or {}
    assert_equal(rows, case, "detector_name", packet.get("detector"), "layer6_chart_review_packet")
    assert_equal(rows, case, "checklist_item_count", (packet.get("checklist_matrix") or {}).get("summary", {}).get("item_count"), 73)
    if expectations.get("safety_flags_false", True):
        for flag in SAFETY_FLAGS:
            assert_false(rows, case, f"safety_{flag}", packet.get(flag))
    if "review_status" in expectations:
        assert_equal(rows, case, "review_status", packet.get("review_status"), expectations["review_status"])
    if "entry_status" in expectations:
        actual = (packet.get("layer_reports") or {}).get("entry", {}).get("status")
        assert_equal(rows, case, "entry_status", actual, expectations["entry_status"])
    permission_summary = packet.get("permission_summary") or {}
    for key in ["hard_gate_status", "best_entry_model", "best_entry_status"]:
        if key in expectations:
            assert_equal(rows, case, key, permission_summary.get(key), expectations[key])
    for item_id, expected_status in (expectations.get("checklist_item_statuses") or {}).items():
        item = get_item(packet, item_id)
        actual = None if item is None else item.get("status")
        evidence = f"{item_id} should be {expected_status}; evidence={(item or {}).get('evidence')}"
        assert_equal(rows, case, f"{item_id}_status", actual, expected_status, evidence)
    chart_context = packet.get("chart_context") or {}
    for key, expected_count in (expectations.get("chart_context_counts") or {}).items():
        value = chart_context.get(key)
        actual_count = len(value) if isinstance(value, list) else 0 if value is None else 1
        assert_equal(rows, case, f"chart_context_{key}_count", actual_count, int(expected_count))
    counts = (packet.get("checklist_matrix") or {}).get("summary", {}).get("status_counts", {})
    for status, minimum in (expectations.get("status_counts_at_least") or {}).items():
        actual = int(counts.get(status, 0))
        add_assertion(rows, case, f"status_count_{status}_at_least", actual >= int(minimum), f"{status} count should be >= {minimum}", int(minimum), actual)
    for item_id in expectations.get("contains_blocker_items", []):
        blockers = {item.get("item_id") for item in packet.get("blockers", [])}
        add_assertion(rows, case, f"blockers_contains_{item_id}", item_id in blockers, f"blockers should contain {item_id}", item_id, sorted(blockers))
    for item_id in expectations.get("contains_manual_review_items", []):
        manual = {item.get("item_id") for item in packet.get("manual_review_queue", [])}
        add_assertion(rows, case, f"manual_review_contains_{item_id}", item_id in manual, f"manual_review_queue should contain {item_id}", item_id, sorted(manual))
    return rows


def validate_draft_review_audits(cases_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    results: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    for filename, expected in DRAFT_REVIEW_AUDITS.items():
        path = cases_dir / filename
        case_stub = {
            "case_id": expected["case_id"],
            "case_origin": "draft_review_audit",
            "title": f"Draft review audit regression for {filename}",
        }
        if not path.exists():
            add_assertion(rows, case_stub, "draft_file_exists", False, f"draft file must exist: {rel_path(path)}", True, False)
            results.append({"case_file": rel_path(path), "case_id": expected["case_id"], "passed": False, "error": "draft_file_missing"})
            continue
        try:
            case = load_json(path)
            if not isinstance(case, dict):
                raise ValueError("case file must contain a JSON object")
            case.setdefault("case_id", expected["case_id"])
            case.setdefault("case_origin", "draft_review_audit")
            packet = build_packet(case)
        except Exception as exc:  # noqa: BLE001 - regression records packet build failures.
            add_assertion(rows, case_stub, "draft_packet_build", False, str(exc), "packet_build_success", "packet_build_error")
            results.append({"case_file": rel_path(path), "case_id": expected["case_id"], "passed": False, "error": str(exc)})
            continue

        audit = packet.get("review_structure_audit") or {}
        permission = packet.get("permission_summary") or {}
        assert_equal(rows, case, "draft_review_status", packet.get("review_status"), expected["expected_review_status"])
        assert_equal(rows, case, "draft_best_entry_model", permission.get("best_entry_model"), expected["expected_best_entry_model"])
        assert_equal(rows, case, "draft_review_structure_audit_status", audit.get("status"), expected["expected_audit_status"])
        blockers = set(audit.get("blockers") or [])
        for blocker in expected["expected_audit_blockers"]:
            add_assertion(rows, case, f"draft_review_audit_blocker_{blocker}", blocker in blockers, f"draft review audit should contain {blocker}", blocker, sorted(blockers))
        case_rows = [row for row in rows if row.get("case_id") == case.get("case_id") and str(row.get("check_id", "")).startswith("draft_")]
        results.append({
            "case_file": rel_path(path),
            "case_id": case.get("case_id"),
            "case_origin": "draft_review_audit",
            "passed": all(row.get("passed") for row in case_rows),
            "review_status": packet.get("review_status"),
            "best_entry_model": permission.get("best_entry_model"),
            "best_entry_status": permission.get("best_entry_status"),
            "review_structure_audit_status": audit.get("status"),
            "review_structure_audit_blockers": audit.get("blockers") or [],
        })
    return results, rows


def run_validation(cases_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    loaded_cases: list[dict[str, Any]] = []
    case_results: list[dict[str, Any]] = []
    assertions: list[dict[str, Any]] = []
    schema_errors: list[dict[str, Any]] = []
    seen_case_ids: set[str] = set()

    for path in case_files(cases_dir):
        try:
            case = load_json(path)
        except json.JSONDecodeError as exc:
            schema_errors.append({"case_file": rel_path(path), "case_id": None, "error": f"invalid JSON: {exc.msg}"})
            continue
        if not isinstance(case, dict):
            schema_errors.append({"case_file": rel_path(path), "case_id": None, "error": "case file must contain a JSON object"})
            continue
        case["_case_file"] = rel_path(path)
        errors = validate_case_schema(case, path)
        case_id = case.get("case_id")
        if isinstance(case_id, str) and case_id:
            if case_id in seen_case_ids:
                errors.append(f"{path.name}: duplicate case_id {case_id}")
            seen_case_ids.add(case_id)
        for error in errors:
            schema_errors.append({"case_file": rel_path(path), "case_id": case.get("case_id"), "error": error})
        if errors:
            continue
        loaded_cases.append(case)
        try:
            packet = build_packet(case)
            case_assertions = validate_packet_expectations(case, packet)
            assertions.extend(case_assertions)
            failed = [row for row in case_assertions if not row["passed"]]
            case_results.append({
                "case_file": case["_case_file"],
                "case_id": case["case_id"],
                "case_origin": case["case_origin"],
                "title": case["title"],
                "passed": not failed,
                "assertion_count": len(case_assertions),
                "failed_assertion_count": len(failed),
                "review_status": packet.get("review_status"),
                "hard_gate_status": (packet.get("permission_summary") or {}).get("hard_gate_status"),
                "best_entry_model": (packet.get("permission_summary") or {}).get("best_entry_model"),
                "best_entry_status": (packet.get("permission_summary") or {}).get("best_entry_status"),
                "checklist_status_counts": (packet.get("checklist_matrix") or {}).get("summary", {}).get("status_counts", {}),
            })
        except Exception as exc:  # noqa: BLE001 - validator records packet build failures as case failures.
            case_results.append({
                "case_file": case["_case_file"],
                "case_id": case.get("case_id"),
                "case_origin": case.get("case_origin"),
                "title": case.get("title"),
                "passed": False,
                "assertion_count": 0,
                "failed_assertion_count": 1,
                "packet_build_error": str(exc),
            })
            add_assertion(assertions, case, "packet_build", False, str(exc), "packet_build_success", "packet_build_error")
    draft_review_results, draft_review_assertions = validate_draft_review_audits(cases_dir)
    assertions.extend(draft_review_assertions)
    failed_assertions = [row for row in assertions if not row["passed"]]
    failed_draft_review_assertions = [row for row in draft_review_assertions if not row["passed"]]
    synthetic_seed_count = sum(1 for case in loaded_cases if case.get("case_origin") == "synthetic_seed")
    real_reviewed_count = sum(1 for case in loaded_cases if case.get("case_origin") == "user_real_reviewed")
    blockers = [row["error"] for row in schema_errors] + [f"{row['case_id']}:{row['check_id']}" for row in failed_assertions]
    if not loaded_cases:
        blockers.append("no_layer7_case_files_loaded")
    ready = not blockers
    status = {
        "version": VERSION,
        "generated_at": utc_now(),
        "mode": "read_only_real_chart_casebook_validation",
        "cases_dir": rel_path(cases_dir),
        "case_count": len(loaded_cases),
        "synthetic_seed_case_count": synthetic_seed_count,
        "user_real_reviewed_case_count": real_reviewed_count,
        "schema_error_count": len(schema_errors),
        "assertion_count": len(assertions),
        "passed_assertion_count": len(assertions) - len(failed_assertions),
        "failed_assertion_count": len(failed_assertions),
        "draft_review_audit_count": len(draft_review_results),
        "draft_review_audit_assertion_count": len(draft_review_assertions),
        "draft_review_audit_passed_assertion_count": len(draft_review_assertions) - len(failed_draft_review_assertions),
        "draft_review_audit_failed_assertion_count": len(failed_draft_review_assertions),
        "draft_review_audits": draft_review_results,
        "layer7_casebook_validation_ready": ready,
        "ready_for_user_real_case_intake": ready,
        "real_chart_behavior_verified": ready and real_reviewed_count > 0,
        "scope_note": "review-only chart packet validation; no outcome labels, PnL, order generation, paper/live trading, or backtest harness",
        "blockers": blockers,
        **SAFETY_FLAGS,
    }
    return status, case_results, assertions, schema_errors


def write_report(path: Path, status: dict[str, Any], case_results: list[dict[str, Any]],
                 assertions: list[dict[str, Any]], schema_errors: list[dict[str, Any]]) -> None:
    lines = [
        "# Layer 7 Real Chart Casebook Validation",
        "",
        "## Verdict",
        "",
        f"- Generated: `{status['generated_at']}`",
        f"- Casebook validation ready: `{str(status['layer7_casebook_validation_ready']).lower()}`",
        f"- Ready for user real case intake: `{str(status['ready_for_user_real_case_intake']).lower()}`",
        f"- Real chart behavior verified: `{str(status['real_chart_behavior_verified']).lower()}`",
        f"- Cases: {status['case_count']} (synthetic seeds: {status['synthetic_seed_case_count']}, user real reviewed: {status['user_real_reviewed_case_count']})",
        f"- Draft review audits: {status['draft_review_audit_count']} ({status['draft_review_audit_passed_assertion_count']} / {status['draft_review_audit_assertion_count']} assertions passed)",
        f"- Assertions passed: {status['passed_assertion_count']} / {status['assertion_count']}",
        f"- Schema errors: {status['schema_error_count']}",
        f"- Failed assertions: {status['failed_assertion_count']}",
        f"- Execution allowed: `{str(status['execution_allowed']).lower()}`",
        f"- PnL computation allowed: `{str(status['pnl_computation_allowed']).lower()}`",
        f"- Backtest harness allowed: `{str(status['backtest_harness_allowed']).lower()}`",
        "",
        "Layer 7 validates complete Layer 6 packets against reviewed chart/trade-review cases. Synthetic seed cases prove the harness; real behavior is not considered verified until at least one `user_real_reviewed` case passes.",
        "",
        "## Case Results",
        "",
        "| Case | Origin | Passed | Review | Hard gate | Best entry | Checklist counts |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in case_results:
        best = f"{row.get('best_entry_model') or '-'} / {row.get('best_entry_status') or '-'}"
        lines.append("| " + " | ".join([
            table_cell(row.get("case_id")),
            table_cell(row.get("case_origin")),
            table_cell(str(row.get("passed")).lower()),
            table_cell(row.get("review_status")),
            table_cell(row.get("hard_gate_status")),
            table_cell(best),
            table_cell(row.get("checklist_status_counts")),
        ]) + " |")
    if status.get("draft_review_audits"):
        lines.extend([
            "",
            "## Draft Review Audits",
            "",
            "These template checks are regression guards only. They do not promote draft charts and do not count as verified real-chart behavior.",
            "",
            "| Case | Passed | Review | Best entry | Audit status | Audit blockers |",
            "| --- | --- | --- | --- | --- | --- |",
        ])
        for row in status["draft_review_audits"]:
            best = f"{row.get('best_entry_model') or '-'} / {row.get('best_entry_status') or '-'}"
            lines.append("| " + " | ".join([
                table_cell(row.get("case_id")),
                table_cell(str(row.get("passed")).lower()),
                table_cell(row.get("review_status")),
                table_cell(best),
                table_cell(row.get("review_structure_audit_status")),
                table_cell(row.get("review_structure_audit_blockers")),
            ]) + " |")
    if schema_errors:
        lines.extend(["", "## Schema Errors", ""])
        for error in schema_errors:
            lines.append(f"- `{error.get('case_file')}` `{error.get('case_id') or '-'}`: {error['error']}")
    lines.extend(["", "## Assertions", "", "| Case | Check | Passed | Evidence |", "| --- | --- | --- | --- |"])
    for row in assertions:
        lines.append("| " + " | ".join([
            table_cell(row.get("case_id")),
            table_cell(row.get("check_id")),
            table_cell(str(row.get("passed")).lower()),
            table_cell(row.get("evidence")),
        ]) + " |")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def build_outputs(cases_dir: Path, output_dir: Path) -> dict[str, Any]:
    status, case_results, assertions, schema_errors = run_validation(cases_dir)
    failures = [row for row in assertions if not row["passed"]]
    write_json(output_dir / "layer7_real_chart_casebook_validation_status.json", status)
    write_jsonl(output_dir / "layer7_real_chart_case_results.jsonl", case_results)
    write_jsonl(output_dir / "layer7_draft_review_audit_results.jsonl", status.get("draft_review_audits") or [])
    write_jsonl(output_dir / "layer7_real_chart_case_assertions.jsonl", assertions)
    write_jsonl(output_dir / "layer7_real_chart_case_failures.jsonl", failures)
    write_jsonl(output_dir / "layer7_real_chart_case_schema_errors.jsonl", schema_errors)
    write_report(output_dir / "layer7_real_chart_casebook_validation.md", status, case_results, assertions, schema_errors)
    return status


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate Layer 7 real chart/trade review casebook against Layer 6 packets")
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
        "layer7_casebook_validation_ready": status["layer7_casebook_validation_ready"],
        "ready_for_user_real_case_intake": status["ready_for_user_real_case_intake"],
        "real_chart_behavior_verified": status["real_chart_behavior_verified"],
        "case_count": status["case_count"],
        "synthetic_seed_case_count": status["synthetic_seed_case_count"],
        "user_real_reviewed_case_count": status["user_real_reviewed_case_count"],
        "draft_review_audit_count": status["draft_review_audit_count"],
        "draft_review_audit_assertion_count": status["draft_review_audit_assertion_count"],
        "draft_review_audit_passed_assertion_count": status["draft_review_audit_passed_assertion_count"],
        "draft_review_audit_failed_assertion_count": status["draft_review_audit_failed_assertion_count"],
        "assertion_count": status["assertion_count"],
        "passed_assertion_count": status["passed_assertion_count"],
        "failed_assertion_count": status["failed_assertion_count"],
        "schema_error_count": status["schema_error_count"],
        "output_dir": str(Path(args.out_dir)),
        **SAFETY_FLAGS,
    }, ensure_ascii=False, indent=2))
    if args.strict_exit_code and not status["layer7_casebook_validation_ready"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())