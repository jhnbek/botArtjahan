from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .safety import SAFETY_FLAGS, blocked_operation


SCENARIO_ID = "SCN-002-false-breakout-reversal"

DETECTOR_CHAIN = [
    (1, "FCD-001", "hard_gates_and_permission"),
    (2, "FCD-002", "level_selection_strength"),
    (3, "FCD-003", "trend_timeframe_context"),
    (4, "FCD-004", "market_mechanics_context"),
    (5, "FCD-009", "retest_room_atr"),
    (6, "FCD-013", "formations_momentum"),
    (7, "FCD-008", "false_breakout_reversal"),
    (8, "FCD-007", "breakout_failure"),
    (9, "FCD-011", "tbx_entry_models"),
    (10, "FCD-012", "risk_stop_take_management"),
    (11, "FCD-015", "workflow_review_data_quality"),
]

REQUIRED_OBSERVATION_FIELDS = [
    "scenario_id",
    "observation_id",
    "symbol",
    "decision_timestamp_utc",
    "level_snapshot",
    "false_breakout_evidence",
    "detector_results",
    "hard_reject_reasons",
    "manual_review_needed",
    "source_rule_ids",
    "source_candidate_ids",
    "artifact_versions",
]

FORBIDDEN_OUTCOME_FIELDS = {
    "label",
    "labels",
    "outcome",
    "outcomes",
    "future_return",
    "future_returns",
    "win_loss",
    "pnl",
    "tp_hit",
    "sl_hit",
    "take_profit_hit",
    "stop_loss_hit",
    "order",
    "orders",
    "fill",
    "fills",
    "balance",
    "balances",
    "position",
    "positions",
    "strategy_metric",
    "strategy_metrics",
}


def default_scn002_fixture_dir() -> Path:
    return Path(__file__).resolve().parent / "fixtures" / "scn002_false_breakout"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _add_error(errors: list[str], code: str) -> None:
    if code not in errors:
        errors.append(code)


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _path_is_safe_relative(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    lowered = value.lower()
    if lowered.startswith(("http://", "https://", "ftp://", "s3://")):
        return False
    candidate = Path(value)
    return not candidate.is_absolute() and ".." not in candidate.parts and not any(char in value for char in ["*", "?", "["])


def _scan_forbidden_outcome_fields(value: Any, errors: list[str]) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if str(key).lower() in FORBIDDEN_OUTCOME_FIELDS:
                _add_error(errors, "forbidden_outcome_field")
            _scan_forbidden_outcome_fields(nested, errors)
    elif isinstance(value, list):
        for item in value:
            _scan_forbidden_outcome_fields(item, errors)


def _scan_future_close_times(value: Any, decision_timestamp: datetime | None, errors: list[str]) -> None:
    if decision_timestamp is None:
        return
    if isinstance(value, dict):
        for key, nested in value.items():
            if key == "close_time_utc":
                close_time = _parse_timestamp(nested)
                if close_time is None:
                    _add_error(errors, "close_time_invalid")
                elif close_time > decision_timestamp:
                    _add_error(errors, "future_bar_after_decision")
            _scan_future_close_times(nested, decision_timestamp, errors)
    elif isinstance(value, list):
        for item in value:
            _scan_future_close_times(item, decision_timestamp, errors)


def _has_nonempty_string_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(item, str) and item for item in value)


def _validate_detector_chain(detector_results: Any, errors: list[str]) -> None:
    if not isinstance(detector_results, list):
        _add_error(errors, "detector_results_not_list")
        return
    actual_chain = [
        (row.get("sequence"), row.get("contract_id"), row.get("detector_key"))
        for row in detector_results
        if isinstance(row, dict)
    ]
    if actual_chain != DETECTOR_CHAIN or len(detector_results) != len(DETECTOR_CHAIN):
        _add_error(errors, "detector_chain_mismatch")
    for row in detector_results:
        if not isinstance(row, dict):
            _add_error(errors, "detector_result_not_object")
            continue
        if not _has_nonempty_string_list(row.get("source_rule_ids")) or not _has_nonempty_string_list(row.get("source_candidate_ids")):
            _add_error(errors, "missing_source_refs")


def _hard_reject_requires_reason(observation: dict[str, Any], errors: list[str]) -> None:
    detector_results = observation.get("detector_results")
    hard_reject_fired = isinstance(detector_results, list) and any(
        isinstance(row, dict) and row.get("observation_status") == "hard_reject" for row in detector_results
    )
    if not hard_reject_fired:
        return
    reasons = observation.get("hard_reject_reasons")
    if not isinstance(reasons, list) or not reasons:
        _add_error(errors, "hard_reject_reason_required")
        return
    for reason in reasons:
        if not isinstance(reason, dict) or not reason.get("gate_id") or not reason.get("reason"):
            _add_error(errors, "hard_reject_reason_required")


def _manual_review_required(observation: dict[str, Any], errors: list[str]) -> None:
    level_snapshot = observation.get("level_snapshot") if isinstance(observation.get("level_snapshot"), dict) else {}
    false_breakout = observation.get("false_breakout_evidence") if isinstance(observation.get("false_breakout_evidence"), dict) else {}
    ambiguous = bool(
        observation.get("manual_ambiguity_present")
        or level_snapshot.get("ambiguous") is True
        or false_breakout.get("ambiguous") is True
    )
    if ambiguous and observation.get("manual_review_needed") is not True:
        _add_error(errors, "manual_review_required")


def validate_scn002_observation(observation: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(observation, dict):
        return ["observation_not_object"]
    for field in REQUIRED_OBSERVATION_FIELDS:
        if field not in observation:
            _add_error(errors, f"observation_missing_field:{field}")

    if observation.get("scenario_id") != SCENARIO_ID:
        _add_error(errors, "scenario_id_mismatch")
    if not isinstance(observation.get("observation_id"), str) or not observation.get("observation_id"):
        _add_error(errors, "observation_id_invalid")
    if not isinstance(observation.get("symbol"), str) or not observation.get("symbol"):
        _add_error(errors, "symbol_invalid")

    decision_timestamp = _parse_timestamp(observation.get("decision_timestamp_utc"))
    if decision_timestamp is None and "decision_timestamp_utc" in observation:
        _add_error(errors, "decision_timestamp_invalid")

    if not isinstance(observation.get("level_snapshot"), dict):
        _add_error(errors, "level_snapshot_not_object")
    if not isinstance(observation.get("false_breakout_evidence"), dict):
        _add_error(errors, "false_breakout_evidence_not_object")
    if not isinstance(observation.get("artifact_versions"), dict) or not observation.get("artifact_versions"):
        _add_error(errors, "artifact_versions_invalid")
    if not isinstance(observation.get("hard_reject_reasons"), list):
        _add_error(errors, "hard_reject_reasons_not_list")
    if not isinstance(observation.get("manual_review_needed"), bool):
        _add_error(errors, "manual_review_needed_not_bool")
    if not _has_nonempty_string_list(observation.get("source_rule_ids")) or not _has_nonempty_string_list(observation.get("source_candidate_ids")):
        _add_error(errors, "missing_source_refs")

    level_snapshot = observation.get("level_snapshot") if isinstance(observation.get("level_snapshot"), dict) else {}
    snapshot_time = _parse_timestamp(level_snapshot.get("snapshot_timestamp_utc"))
    if decision_timestamp is not None and snapshot_time is not None and snapshot_time > decision_timestamp:
        _add_error(errors, "post_outcome_level_redraw")
    if level_snapshot.get("redrawn_after_outcome") is True:
        _add_error(errors, "post_outcome_level_redraw")

    _scan_forbidden_outcome_fields(observation, errors)
    _scan_future_close_times(observation, decision_timestamp, errors)
    _validate_detector_chain(observation.get("detector_results"), errors)
    _hard_reject_requires_reason(observation, errors)
    _manual_review_required(observation, errors)
    return sorted(errors)


def _validate_case(case_dir: Path) -> dict[str, Any]:
    case_path = case_dir / "case.json"
    errors: list[str] = []
    try:
        metadata = _read_json(case_path)
    except json.JSONDecodeError:
        metadata = {}
        _add_error(errors, "case_metadata_parse_error")
    if not isinstance(metadata, dict):
        metadata = {}
        _add_error(errors, "case_metadata_not_object")

    observation_file = metadata.get("observation_file", "observation.json")
    if not _path_is_safe_relative(observation_file):
        _add_error(errors, "observation_file_path_invalid")
        observation_path = case_dir / "observation.json"
    else:
        observation_path = case_dir / str(observation_file)

    if not observation_path.exists():
        observation = {}
        _add_error(errors, "observation_file_missing")
    else:
        try:
            observation = _read_json(observation_path)
        except json.JSONDecodeError:
            observation = {}
            _add_error(errors, "observation_parse_error")

    if isinstance(observation, dict):
        for error in validate_scn002_observation(observation):
            _add_error(errors, error)
    else:
        _add_error(errors, "observation_not_object")

    expected_error_codes = metadata.get("expected_error_codes") if isinstance(metadata.get("expected_error_codes"), list) else []
    expected_errors = sorted(str(code) for code in expected_error_codes)
    actual_errors = sorted(errors)
    expected_valid = metadata.get("expected_valid") is True
    actual_valid = not actual_errors
    passed = actual_valid == expected_valid and actual_errors == expected_errors
    return {
        "case_id": str(metadata.get("case_id") or case_dir.name),
        "expected_valid": expected_valid,
        "actual_valid": actual_valid,
        "expected_error_codes": expected_errors,
        "actual_error_codes": actual_errors,
        "passed": passed,
        "observation_path": observation_path.as_posix(),
        "observation_id": str(observation.get("observation_id") or "") if isinstance(observation, dict) else "",
    }


def validate_scn002_fixture_directory(fixtures_dir: Path | None = None) -> dict[str, Any]:
    root = (fixtures_dir or default_scn002_fixture_dir()).resolve()
    case_dirs = sorted(path for path in root.iterdir() if path.is_dir() and (path / "case.json").exists()) if root.exists() else []
    cases = [_validate_case(case_dir) for case_dir in case_dirs]
    passed_count = sum(1 for case in cases if case["passed"])
    return {
        "component": "scn002_false_breakout_fixture_validator",
        "implemented": True,
        "implemented_mode": "scn002_fixture_only_validation",
        "scenario_id": SCENARIO_ID,
        "scn002_fixture_only_validation_allowed": True,
        "real_historical_data_loading_allowed": False,
        "market_backtest_allowed": False,
        "build_split_manifest_allowed": False,
        "observe_offline_allowed": False,
        "detector_execution_allowed": False,
        "pnl_computation_allowed": False,
        "old_external_crypto_stats_used": False,
        "fixtures_dir": root.as_posix(),
        "case_count": len(cases),
        "passed_count": passed_count,
        "failed_count": len(cases) - passed_count,
        "all_cases_passed": bool(cases) and passed_count == len(cases),
        "cases": cases,
        **SAFETY_FLAGS,
    }


def component_contract() -> dict[str, Any]:
    return {
        "component": "scn002_false_breakout_fixture_validator",
        "implemented": True,
        "implemented_mode": "scn002_fixture_only_validation",
        "scenario_id": SCENARIO_ID,
        "allowed_now": "validate explicit synthetic/curated SCN-002 fixture JSON documents only",
        "blocked_operations": [
            blocked_operation("real_historical_data_loading"),
            blocked_operation("detector_execution"),
            blocked_operation("pnl_computation"),
            blocked_operation("market_backtest_run"),
            blocked_operation("execution"),
        ],
        "scn002_fixture_only_validation_allowed": True,
        "real_historical_data_loading_allowed": False,
        "market_backtest_allowed": False,
        "observe_offline_allowed": False,
        **SAFETY_FLAGS,
    }