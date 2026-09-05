from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..safety import SAFETY_FLAGS, blocked_operation
from ..scn002_false_breakout_fixtures import SCENARIO_ID
from .scn002_false_breakout_outcome_labels import (
    SCAFFOLD_ROWS_FILE_NAME as LABEL_SCAFFOLD_ROWS_FILE_NAME,
    SUMMARY_FILE_NAME as LABEL_SUMMARY_FILE_NAME,
    VERSION as LABEL_SCAFFOLD_VERSION,
    validate_scn002_outcome_label_scaffold,
)


VERSION = "scn002_false_breakout_read_only_outcome_data_window_scaffold_implementation_v1"
IMPLEMENTED_MODE = "scn002_reviewed_outcome_label_read_only_data_window_boundary_scaffold"
WINDOW_ROWS_FILE_NAME = "outcome_data_window_scaffold_rows.jsonl"
SUMMARY_FILE_NAME = "summary.json"
WINDOW_CONTRACT_ID = "SCN002-ODW-CONTRACT-001"
MATERIALIZATION_VERSION = "scn002_false_breakout_read_only_outcome_data_window_materialization_scaffold_v1"
MATERIALIZATION_IMPLEMENTED_MODE = "scn002_reviewed_outcome_data_window_read_only_materialization_boundary_scaffold"
MATERIALIZATION_ROWS_FILE_NAME = "outcome_data_window_materialization_scaffold_rows.jsonl"
MATERIALIZATION_SUMMARY_FILE_NAME = "materialization_summary.json"
MATERIALIZATION_CONTRACT_ID = "SCN002-ODWM-CONTRACT-001"
BINDING_VERSION = "scn002_false_breakout_read_only_reviewed_manifest_binding_scaffold_v1"
BINDING_IMPLEMENTED_MODE = "scn002_reviewed_manifest_metadata_read_only_binding_scaffold"
BINDING_ROWS_FILE_NAME = "reviewed_manifest_binding_scaffold_rows.jsonl"
BINDING_SUMMARY_FILE_NAME = "reviewed_manifest_binding_summary.json"
BINDING_CONTRACT_ID = "SCN002-ODWMB-CONTRACT-001"
METADATA_VALIDATION_VERSION = "scn002_false_breakout_read_only_reviewed_manifest_metadata_validation_scaffold_v1"
METADATA_VALIDATION_IMPLEMENTED_MODE = "scn002_reviewed_manifest_metadata_read_only_validation_scaffold"
METADATA_VALIDATION_ROWS_FILE_NAME = "reviewed_manifest_metadata_validation_scaffold_rows.jsonl"
METADATA_VALIDATION_SUMMARY_FILE_NAME = "reviewed_manifest_metadata_validation_summary.json"
METADATA_VALIDATION_CONTRACT_ID = "SCN002-RMMV-CONTRACT-001"
FUTURE_WINDOW_STATES = [
    "awaiting_reviewed_manifest_data_gate",
    "no_data",
    "invalid_window",
    "post_decision_window_available",
    "ambiguous_window",
    "timeout",
]
FUTURE_MATERIALIZATION_STATES = [
    "awaiting_explicit_reviewed_manifest_metadata",
    "invalid_manifest_metadata",
    "no_manifest_coverage",
    "invalid_post_decision_window",
    "materialized_unscored_after_separate_data_gate",
    "ambiguous_window",
    "blocked",
]
FUTURE_BINDING_STATES = [
    "awaiting_reviewed_manifest_metadata",
    "metadata_bound_after_separate_review",
    "metadata_missing",
    "metadata_mismatch",
    "metadata_insufficient",
    "invalid_binding",
    "blocked",
]
FUTURE_BINDING_FIELDS = [
    "manifest_binding_id",
    "source_materialization_candidate_id",
    "source_materialization_implementation_review_id",
    "reviewed_manifest_metadata_id",
    "reviewed_manifest_metadata_review_id",
    "manifest_dataset_id",
    "manifest_symbol",
    "manifest_timeframe",
    "manifest_timezone",
    "coverage_start_utc",
    "coverage_end_utc",
    "requested_window_start_utc",
    "requested_window_end_utc",
    "manifest_binding_state",
]
FUTURE_METADATA_VALIDATION_STATES = [
    "awaiting_reviewed_metadata",
    "metadata_valid",
    "metadata_missing",
    "metadata_mismatch",
    "metadata_insufficient",
    "invalid_metadata",
    "blocked",
]
FUTURE_METADATA_VALIDATION_FIELDS = [
    "metadata_validation_id",
    "source_manifest_binding_id",
    "source_binding_implementation_review_id",
    "reviewed_manifest_metadata_id",
    "reviewed_manifest_metadata_review_id",
    "manifest_dataset_id",
    "instrument_profile",
    "candidate_symbol",
    "manifest_symbol",
    "manifest_timeframe",
    "manifest_timezone",
    "coverage_start_utc",
    "coverage_end_utc",
    "requested_window_start_utc",
    "requested_window_end_utc",
    "metadata_validation_state",
]

MARKET_FILE_SUFFIXES = {".csv", ".parquet", ".feather", ".h5", ".hdf", ".duckdb", ".zip", ".gz", ".tar"}
FORBIDDEN_COMPUTED_RESULT_FIELDS = {
    "balance",
    "balances",
    "bar",
    "bars",
    "expectancy",
    "fill",
    "fills",
    "future_return",
    "future_returns",
    "market_row",
    "market_rows",
    "ohlcv",
    "order",
    "orders",
    "pnl",
    "position",
    "positions",
    "profit_factor",
    "r",
    "r_multiple",
    "risk_reward",
    "sl_hit",
    "stop_loss_hit",
    "strategy_metric",
    "strategy_metrics",
    "take_profit_hit",
    "tp_hit",
    "win_loss",
    "winrate",
}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_jsonl(path: Path, errors: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        errors.append("outcome_window_source_label_rows_missing")
        return rows
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            errors.append(f"outcome_window_source_label_jsonl_parse_error:{line_number}")
            continue
        if not isinstance(row, dict):
            errors.append(f"outcome_window_source_label_row_not_object:{line_number}")
            continue
        rows.append(row)
    return rows


def _read_jsonl_named(path: Path, errors: list[str], missing_error: str, parse_prefix: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        errors.append(missing_error)
        return rows
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            errors.append(f"{parse_prefix}_jsonl_parse_error:{line_number}")
            continue
        if not isinstance(row, dict):
            errors.append(f"{parse_prefix}_row_not_object:{line_number}")
            continue
        rows.append(row)
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
    path.write_text(text + ("\n" if text else ""), encoding="utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _label_input_errors(root: Path) -> list[str]:
    errors: list[str] = []
    if root.is_file():
        errors.append("outcome_label_scaffold_dir_must_be_directory")
    if root.suffix.lower() in MARKET_FILE_SUFFIXES:
        errors.append("market_file_input_rejected")
    if not root.exists():
        errors.append("outcome_label_scaffold_dir_missing")
    return errors


def _window_input_errors(root: Path) -> list[str]:
    errors: list[str] = []
    if root.is_file():
        errors.append("outcome_data_window_scaffold_dir_must_be_directory")
    if root.suffix.lower() in MARKET_FILE_SUFFIXES:
        errors.append("market_file_input_rejected")
    if not root.exists():
        errors.append("outcome_data_window_scaffold_dir_missing")
    return errors


def _binding_input_errors(root: Path) -> list[str]:
    errors: list[str] = []
    if root.is_file():
        errors.append("outcome_data_window_materialization_scaffold_dir_must_be_directory")
    if root.suffix.lower() in MARKET_FILE_SUFFIXES:
        errors.append("market_file_input_rejected")
    if not root.exists():
        errors.append("outcome_data_window_materialization_scaffold_dir_missing")
    return errors


def _metadata_validation_input_errors(root: Path) -> list[str]:
    errors: list[str] = []
    if root.is_file():
        errors.append("reviewed_manifest_binding_scaffold_dir_must_be_directory")
    if root.suffix.lower() in MARKET_FILE_SUFFIXES:
        errors.append("market_file_input_rejected")
    if not root.exists():
        errors.append("reviewed_manifest_binding_scaffold_dir_missing")
    return errors


def _scan_forbidden_computed_fields(value: Any, errors: list[str]) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if str(key).lower() in FORBIDDEN_COMPUTED_RESULT_FIELDS:
                errors.append("forbidden_computed_result_field")
            _scan_forbidden_computed_fields(nested, errors)
    elif isinstance(value, list):
        for item in value:
            _scan_forbidden_computed_fields(item, errors)


def _window_state(label_row: dict[str, Any]) -> str:
    candidate_state = str(label_row.get("candidate_state") or "")
    if candidate_state == "candidate_waiting_for_separate_data_gate":
        return "candidate_waiting_for_reviewed_manifest_gate"
    if candidate_state == "manual_review_preserved_non_scored":
        return "manual_review_preserved_non_scored"
    if candidate_state == "hard_reject_preserved_non_scored":
        return "hard_reject_preserved_non_scored"
    return "invalid_source_candidate_state"


def _non_scored_reason(label_row: dict[str, Any]) -> str | None:
    candidate_state = str(label_row.get("candidate_state") or "")
    if candidate_state == "candidate_waiting_for_separate_data_gate":
        return None
    if candidate_state == "manual_review_preserved_non_scored":
        return str(label_row.get("non_scored_reason") or "manual_review_required_before_any_future_window")
    if candidate_state == "hard_reject_preserved_non_scored":
        return str(label_row.get("non_scored_reason") or "hard_reject_preserved_without_window")
    return "invalid_source_candidate_state"


def _window_row(label_row: dict[str, Any], row_index: int) -> dict[str, Any]:
    data_window_state = _window_state(label_row)
    eligible = data_window_state == "candidate_waiting_for_reviewed_manifest_gate"
    source_accepted = label_row.get("source_observation_accepted") is True
    return {
        "result_type": "scn002_read_only_outcome_data_window_scaffold",
        "scenario_id": SCENARIO_ID,
        "source_outcome_label_row_index": row_index + 1,
        "source_preflight_row_index": label_row.get("source_preflight_row_index"),
        "source_preflight_source_row_index": label_row.get("source_preflight_source_row_index"),
        "case_id": label_row.get("case_id"),
        "observation_id": label_row.get("observation_id"),
        "source_observation_accepted": source_accepted,
        "source_preflight_state": label_row.get("source_preflight_state"),
        "source_chronological_split_role": label_row.get("source_chronological_split_role"),
        "source_outcome_label_candidate_state": label_row.get("candidate_state"),
        "source_eligible_for_future_data_window": label_row.get("eligible_for_future_data_window") is True,
        "data_window_state": data_window_state,
        "eligible_for_future_window_materialization": eligible,
        "non_scored_reason": _non_scored_reason(label_row),
        "future_window_contract_id": WINDOW_CONTRACT_ID,
        "future_window_allowed_states": FUTURE_WINDOW_STATES if eligible else [],
        "symbol": label_row.get("symbol") if source_accepted else None,
        "decision_timestamp_utc": label_row.get("decision_timestamp_utc") if source_accepted else None,
        "strict_post_decision_boundary_required": eligible,
        "reviewed_manifest_required_before_materialization": eligible,
        "manual_review_needed": label_row.get("manual_review_needed") is True if source_accepted else False,
        "hard_reject_reasons": label_row.get("hard_reject_reasons") if isinstance(label_row.get("hard_reject_reasons"), list) else [],
        "error_codes": label_row.get("error_codes") if isinstance(label_row.get("error_codes"), list) else [],
        "source_rule_ids": label_row.get("source_rule_ids") if isinstance(label_row.get("source_rule_ids"), list) else [],
        "source_candidate_ids": label_row.get("source_candidate_ids") if isinstance(label_row.get("source_candidate_ids"), list) else [],
        "source_artifact_versions": label_row.get("source_artifact_versions") if isinstance(label_row.get("source_artifact_versions"), dict) else {},
        "source_detector_status_counts": label_row.get("source_detector_status_counts") if isinstance(label_row.get("source_detector_status_counts"), dict) else {},
        "source_outcome_label_generated_by": label_row.get("generated_by"),
        "generated_by": VERSION,
        "non_trading_output": True,
        "boundary_scaffold_only": True,
        "window_materialized": False,
        "market_data_loaded": False,
        "market_file_accessed": False,
        "market_rows_parsed": False,
        "detector_reexecuted": False,
        "label_computed": False,
        "outcome_computed": False,
        "pnl_computed": False,
        "performance_metrics_computed": False,
        "future_real_data_gate_required": eligible,
        "future_label_scoring_gate_required_after_window_review": eligible,
    }


def _window_counts(window_rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "window_scaffold_row_count": len(window_rows),
        "window_candidate_count": sum(
            1 for row in window_rows if row.get("data_window_state") == "candidate_waiting_for_reviewed_manifest_gate"
        ),
        "manual_non_scored_count": sum(
            1 for row in window_rows if row.get("data_window_state") == "manual_review_preserved_non_scored"
        ),
        "hard_reject_non_scored_count": sum(
            1 for row in window_rows if row.get("data_window_state") == "hard_reject_preserved_non_scored"
        ),
    }


def prepare_scn002_outcome_data_window_scaffold(labels_dir: Path, out_dir: Path | None = None) -> dict[str, Any]:
    root = labels_dir.resolve()
    input_errors = _label_input_errors(root)
    validation_payload: dict[str, Any] = {}
    source_rows: list[dict[str, Any]] = []
    source_summary: dict[str, Any] = {}
    errors = list(input_errors)

    if not input_errors:
        validation_payload = validate_scn002_outcome_label_scaffold(root)
        if validation_payload.get("all_scaffold_valid") is not True:
            errors.append("source_outcome_label_scaffold_not_valid")
        errors.extend(str(error) for error in validation_payload.get("validation_errors") or [])
        source_rows = _read_jsonl(root / LABEL_SCAFFOLD_ROWS_FILE_NAME, errors)
        try:
            loaded_summary = _read_json(root / LABEL_SUMMARY_FILE_NAME)
        except FileNotFoundError:
            loaded_summary = {}
            errors.append("source_outcome_label_summary_missing")
        except json.JSONDecodeError:
            loaded_summary = {}
            errors.append("source_outcome_label_summary_parse_error")
        source_summary = loaded_summary if isinstance(loaded_summary, dict) else {}
        if not isinstance(loaded_summary, dict):
            errors.append("source_outcome_label_summary_not_object")

    window_rows = [_window_row(row, row_index) for row_index, row in enumerate(source_rows) if not errors]
    for row in window_rows:
        _scan_forbidden_computed_fields(row, errors)

    counts = _window_counts(window_rows)
    scaffold_ready = bool(window_rows) and not errors
    outputs: dict[str, str] = {}
    if out_dir is not None:
        output_dir = out_dir.resolve()
        _write_jsonl(output_dir / WINDOW_ROWS_FILE_NAME, window_rows)
        outputs = {
            "window_scaffold_rows": (output_dir / WINDOW_ROWS_FILE_NAME).as_posix(),
            "summary": (output_dir / SUMMARY_FILE_NAME).as_posix(),
        }

    source_rows_path = root / LABEL_SCAFFOLD_ROWS_FILE_NAME
    payload = {
        "component": "scn002_false_breakout_read_only_outcome_data_window_scaffold",
        "implemented": True,
        "implemented_mode": IMPLEMENTED_MODE,
        "version": VERSION,
        "scenario_id": SCENARIO_ID,
        "labels_dir": root.as_posix(),
        "source_outcome_label_version": source_summary.get("version"),
        "source_outcome_label_rows_sha256": _sha256_file(source_rows_path) if source_rows_path.exists() else "",
        "source_outcome_label_validation_passed": validation_payload.get("all_scaffold_valid") is True,
        "source_outcome_label_scaffold_row_count": int(validation_payload.get("scaffold_row_count") or 0),
        "source_outcome_label_candidate_count": int(validation_payload.get("candidate_count") or 0),
        "source_outcome_label_manual_non_scored_count": int(validation_payload.get("manual_non_scored_count") or 0),
        "source_outcome_label_hard_reject_non_scored_count": int(validation_payload.get("hard_reject_non_scored_count") or 0),
        **counts,
        "future_window_contract_id": WINDOW_CONTRACT_ID,
        "future_window_allowed_states": FUTURE_WINDOW_STATES,
        "scaffold_ready": scaffold_ready,
        "scaffold_errors": sorted(set(errors)),
        "scaffold_error_count": len(set(errors)),
        "window_scaffold_rows": window_rows,
        "outputs": outputs,
        "real_historical_data_loading_allowed": False,
        "historical_data_loading_allowed": False,
        "market_file_access_allowed": False,
        "market_row_parsing_allowed": False,
        "outcome_data_window_materialization_allowed": False,
        "market_backtest_allowed": False,
        "build_split_manifest_allowed": False,
        "observe_offline_allowed": False,
        "detector_execution_allowed": False,
        "label_computation_allowed": False,
        "outcome_computation_allowed": False,
        "pnl_computation_allowed": False,
        "old_external_crypto_stats_used": False,
        **SAFETY_FLAGS,
    }
    if out_dir is not None:
        summary_payload = {key: value for key, value in payload.items() if key != "window_scaffold_rows"}
        _write_json(out_dir.resolve() / SUMMARY_FILE_NAME, summary_payload)
    return payload


def _validate_window_row(row: dict[str, Any], errors: list[str], row_index: int) -> None:
    required_fields = [
        "result_type",
        "scenario_id",
        "source_outcome_label_row_index",
        "case_id",
        "observation_id",
        "source_observation_accepted",
        "source_preflight_state",
        "source_chronological_split_role",
        "source_outcome_label_candidate_state",
        "source_eligible_for_future_data_window",
        "data_window_state",
        "eligible_for_future_window_materialization",
        "future_window_contract_id",
        "source_outcome_label_generated_by",
        "generated_by",
        "non_trading_output",
        "boundary_scaffold_only",
        "window_materialized",
        "market_data_loaded",
        "market_file_accessed",
        "market_rows_parsed",
        "detector_reexecuted",
        "label_computed",
        "outcome_computed",
        "pnl_computed",
        "performance_metrics_computed",
    ]
    for field in required_fields:
        if field not in row:
            errors.append(f"outcome_window_scaffold_row_missing_field:{row_index}:{field}")
    if row.get("result_type") != "scn002_read_only_outcome_data_window_scaffold":
        errors.append(f"outcome_window_scaffold_result_type_mismatch:{row_index}")
    if row.get("scenario_id") != SCENARIO_ID:
        errors.append(f"outcome_window_scaffold_scenario_id_mismatch:{row_index}")
    if row.get("generated_by") != VERSION:
        errors.append(f"outcome_window_scaffold_generated_by_mismatch:{row_index}")
    if row.get("source_outcome_label_generated_by") != LABEL_SCAFFOLD_VERSION:
        errors.append(f"outcome_window_scaffold_source_generated_by_mismatch:{row_index}")
    if row.get("non_trading_output") is not True:
        errors.append(f"outcome_window_scaffold_non_trading_output_missing:{row_index}")
    if row.get("boundary_scaffold_only") is not True:
        errors.append(f"outcome_window_scaffold_boundary_only_missing:{row_index}")
    for field in [
        "window_materialized",
        "market_data_loaded",
        "market_file_accessed",
        "market_rows_parsed",
        "detector_reexecuted",
        "label_computed",
        "outcome_computed",
        "pnl_computed",
        "performance_metrics_computed",
    ]:
        if row.get(field) is not False:
            errors.append(f"outcome_window_scaffold_unsafe_flag_not_false:{row_index}:{field}")
    if row.get("future_window_contract_id") != WINDOW_CONTRACT_ID:
        errors.append(f"outcome_window_scaffold_contract_mismatch:{row_index}")

    source_candidate_state = row.get("source_outcome_label_candidate_state")
    data_window_state = row.get("data_window_state")
    if source_candidate_state == "candidate_waiting_for_separate_data_gate":
        if data_window_state != "candidate_waiting_for_reviewed_manifest_gate":
            errors.append(f"outcome_window_scaffold_candidate_state_mismatch:{row_index}")
        if row.get("eligible_for_future_window_materialization") is not True:
            errors.append(f"outcome_window_scaffold_candidate_not_marked:{row_index}")
        if row.get("source_eligible_for_future_data_window") is not True:
            errors.append(f"outcome_window_scaffold_source_candidate_not_marked:{row_index}")
        if row.get("strict_post_decision_boundary_required") is not True:
            errors.append(f"outcome_window_scaffold_post_decision_not_required:{row_index}")
        if row.get("reviewed_manifest_required_before_materialization") is not True:
            errors.append(f"outcome_window_scaffold_manifest_gate_not_required:{row_index}")
        if row.get("source_chronological_split_role") not in {"train_preflight", "test_preflight"}:
            errors.append(f"outcome_window_scaffold_candidate_split_role_invalid:{row_index}")
        for field in ["symbol", "decision_timestamp_utc", "source_rule_ids", "source_candidate_ids", "source_artifact_versions"]:
            if not row.get(field):
                errors.append(f"outcome_window_scaffold_candidate_missing_trace_field:{row_index}:{field}")
    elif source_candidate_state == "manual_review_preserved_non_scored":
        if data_window_state != "manual_review_preserved_non_scored":
            errors.append(f"outcome_window_scaffold_manual_state_mismatch:{row_index}")
        if row.get("eligible_for_future_window_materialization") is not False:
            errors.append(f"outcome_window_scaffold_manual_eligible_not_false:{row_index}")
        if row.get("manual_review_needed") is not True:
            errors.append(f"outcome_window_scaffold_manual_review_not_preserved:{row_index}")
    elif source_candidate_state == "hard_reject_preserved_non_scored":
        if data_window_state != "hard_reject_preserved_non_scored":
            errors.append(f"outcome_window_scaffold_hard_reject_state_mismatch:{row_index}")
        if row.get("eligible_for_future_window_materialization") is not False:
            errors.append(f"outcome_window_scaffold_hard_reject_eligible_not_false:{row_index}")
        if not row.get("error_codes"):
            errors.append(f"outcome_window_scaffold_hard_reject_missing_error_codes:{row_index}")
    else:
        errors.append(f"outcome_window_scaffold_source_candidate_state_invalid:{row_index}")


def validate_scn002_outcome_data_window_scaffold(windows_dir: Path) -> dict[str, Any]:
    root = windows_dir.resolve()
    errors: list[str] = []
    if not root.exists() or not root.is_dir():
        errors.append("outcome_window_scaffold_dir_missing")
        rows: list[dict[str, Any]] = []
        summary: dict[str, Any] = {}
    else:
        try:
            loaded_summary = _read_json(root / SUMMARY_FILE_NAME)
        except FileNotFoundError:
            loaded_summary = {}
            errors.append("outcome_window_scaffold_summary_missing")
        except json.JSONDecodeError:
            loaded_summary = {}
            errors.append("outcome_window_scaffold_summary_parse_error")
        summary = loaded_summary if isinstance(loaded_summary, dict) else {}
        if not isinstance(loaded_summary, dict):
            errors.append("outcome_window_scaffold_summary_not_object")
        rows = _read_jsonl(root / WINDOW_ROWS_FILE_NAME, errors)

    for row_index, row in enumerate(rows, start=1):
        _scan_forbidden_computed_fields(row, errors)
        _validate_window_row(row, errors, row_index)

    counts = _window_counts(rows)
    state_counts: dict[str, int] = {}
    for row in rows:
        state = str(row.get("data_window_state") or "missing")
        state_counts[state] = state_counts.get(state, 0) + 1

    if summary:
        if summary.get("component") != "scn002_false_breakout_read_only_outcome_data_window_scaffold":
            errors.append("outcome_window_scaffold_summary_component_mismatch")
        if summary.get("version") != VERSION:
            errors.append("outcome_window_scaffold_summary_version_mismatch")
        for field, actual in counts.items():
            if int(summary.get(field) or -1) != actual:
                errors.append(f"outcome_window_scaffold_summary_{field}_mismatch")
        if summary.get("scaffold_ready") is not True:
            errors.append("outcome_window_scaffold_summary_ready_not_true")
        for field in [
            "real_historical_data_loading_allowed",
            "historical_data_loading_allowed",
            "market_file_access_allowed",
            "market_row_parsing_allowed",
            "outcome_data_window_materialization_allowed",
            "market_backtest_allowed",
            "build_split_manifest_allowed",
            "observe_offline_allowed",
            "detector_execution_allowed",
            "label_computation_allowed",
            "outcome_computation_allowed",
            "pnl_computation_allowed",
            "execution_allowed",
            "runtime_signal_allowed",
            "paper_trading_allowed",
            "live_trading_allowed",
            "backtest_harness_allowed",
        ]:
            if summary.get(field) is not False:
                errors.append(f"outcome_window_scaffold_summary_unsafe_flag_not_false:{field}")

    validation_passed = bool(rows) and not errors
    return {
        "component": "scn002_false_breakout_read_only_outcome_data_window_scaffold_validator",
        "implemented": True,
        "implemented_mode": IMPLEMENTED_MODE,
        "version": VERSION,
        "scenario_id": SCENARIO_ID,
        "windows_dir": root.as_posix(),
        **counts,
        "data_window_state_counts": dict(sorted(state_counts.items())),
        "validation_errors": sorted(set(errors)),
        "validation_error_count": len(set(errors)),
        "all_window_scaffold_valid": validation_passed,
        "real_historical_data_loading_allowed": False,
        "historical_data_loading_allowed": False,
        "market_file_access_allowed": False,
        "market_row_parsing_allowed": False,
        "outcome_data_window_materialization_allowed": False,
        "market_backtest_allowed": False,
        "detector_execution_allowed": False,
        "label_computation_allowed": False,
        "outcome_computation_allowed": False,
        "pnl_computation_allowed": False,
        "old_external_crypto_stats_used": False,
        **SAFETY_FLAGS,
    }


def _materialization_state(window_row: dict[str, Any]) -> str:
    data_window_state = str(window_row.get("data_window_state") or "")
    if data_window_state == "candidate_waiting_for_reviewed_manifest_gate":
        return "candidate_waiting_for_explicit_reviewed_manifest_metadata"
    if data_window_state == "manual_review_preserved_non_scored":
        return "manual_review_preserved_non_scored"
    if data_window_state == "hard_reject_preserved_non_scored":
        return "hard_reject_preserved_non_scored"
    return "invalid_source_data_window_state"


def _materialization_non_scored_reason(window_row: dict[str, Any]) -> str | None:
    data_window_state = str(window_row.get("data_window_state") or "")
    if data_window_state == "candidate_waiting_for_reviewed_manifest_gate":
        return None
    if data_window_state == "manual_review_preserved_non_scored":
        return str(window_row.get("non_scored_reason") or "manual_review_required_before_any_materialization")
    if data_window_state == "hard_reject_preserved_non_scored":
        return str(window_row.get("non_scored_reason") or "hard_reject_preserved_without_materialization")
    return "invalid_source_data_window_state"


def _materialization_row(window_row: dict[str, Any], row_index: int) -> dict[str, Any]:
    materialization_state = _materialization_state(window_row)
    eligible = materialization_state == "candidate_waiting_for_explicit_reviewed_manifest_metadata"
    return {
        "result_type": "scn002_read_only_outcome_data_window_materialization_scaffold",
        "scenario_id": SCENARIO_ID,
        "source_outcome_data_window_row_index": row_index + 1,
        "source_outcome_label_row_index": window_row.get("source_outcome_label_row_index"),
        "source_preflight_row_index": window_row.get("source_preflight_row_index"),
        "source_preflight_source_row_index": window_row.get("source_preflight_source_row_index"),
        "case_id": window_row.get("case_id"),
        "observation_id": window_row.get("observation_id"),
        "source_observation_accepted": window_row.get("source_observation_accepted") is True,
        "source_preflight_state": window_row.get("source_preflight_state"),
        "source_chronological_split_role": window_row.get("source_chronological_split_role"),
        "source_outcome_label_candidate_state": window_row.get("source_outcome_label_candidate_state"),
        "source_data_window_state": window_row.get("data_window_state"),
        "source_eligible_for_future_window_materialization": window_row.get(
            "eligible_for_future_window_materialization"
        ) is True,
        "materialization_boundary_state": materialization_state,
        "eligible_for_future_reviewed_manifest_materialization": eligible,
        "non_scored_reason": _materialization_non_scored_reason(window_row),
        "future_materialization_contract_id": MATERIALIZATION_CONTRACT_ID,
        "future_materialization_allowed_states": FUTURE_MATERIALIZATION_STATES if eligible else [],
        "symbol": window_row.get("symbol") if eligible else None,
        "decision_timestamp_utc": window_row.get("decision_timestamp_utc") if eligible else None,
        "strict_post_decision_boundary_required": eligible,
        "explicit_reviewed_manifest_metadata_required": eligible,
        "manifest_metadata_loaded": False,
        "manifest_metadata_validated": False,
        "manifest_metadata_reference_id": None,
        "market_file_reference_bound": False,
        "manual_review_needed": window_row.get("manual_review_needed") is True,
        "hard_reject_reasons": window_row.get("hard_reject_reasons") if isinstance(window_row.get("hard_reject_reasons"), list) else [],
        "error_codes": window_row.get("error_codes") if isinstance(window_row.get("error_codes"), list) else [],
        "source_rule_ids": window_row.get("source_rule_ids") if isinstance(window_row.get("source_rule_ids"), list) else [],
        "source_candidate_ids": window_row.get("source_candidate_ids") if isinstance(window_row.get("source_candidate_ids"), list) else [],
        "source_artifact_versions": window_row.get("source_artifact_versions") if isinstance(window_row.get("source_artifact_versions"), dict) else {},
        "source_detector_status_counts": window_row.get("source_detector_status_counts") if isinstance(window_row.get("source_detector_status_counts"), dict) else {},
        "source_outcome_data_window_generated_by": window_row.get("generated_by"),
        "generated_by": MATERIALIZATION_VERSION,
        "non_trading_output": True,
        "boundary_scaffold_only": True,
        "materialization_scaffold_only": True,
        "window_materialized": False,
        "market_data_loaded": False,
        "market_file_accessed": False,
        "market_rows_parsed": False,
        "detector_reexecuted": False,
        "label_computed": False,
        "outcome_computed": False,
        "pnl_computed": False,
        "performance_metrics_computed": False,
        "future_real_data_gate_required": eligible,
        "future_label_scoring_gate_required_after_materialization_review": eligible,
    }


def _materialization_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "materialization_scaffold_row_count": len(rows),
        "materialization_candidate_count": sum(
            1
            for row in rows
            if row.get("materialization_boundary_state") == "candidate_waiting_for_explicit_reviewed_manifest_metadata"
        ),
        "manual_non_scored_count": sum(
            1 for row in rows if row.get("materialization_boundary_state") == "manual_review_preserved_non_scored"
        ),
        "hard_reject_non_scored_count": sum(
            1 for row in rows if row.get("materialization_boundary_state") == "hard_reject_preserved_non_scored"
        ),
    }


def prepare_scn002_outcome_data_window_materialization_scaffold(
    windows_dir: Path,
    out_dir: Path | None = None,
) -> dict[str, Any]:
    root = windows_dir.resolve()
    input_errors = _window_input_errors(root)
    validation_payload: dict[str, Any] = {}
    source_rows: list[dict[str, Any]] = []
    source_summary: dict[str, Any] = {}
    errors = list(input_errors)

    if not input_errors:
        validation_payload = validate_scn002_outcome_data_window_scaffold(root)
        if validation_payload.get("all_window_scaffold_valid") is not True:
            errors.append("source_outcome_data_window_scaffold_not_valid")
        errors.extend(str(error) for error in validation_payload.get("validation_errors") or [])
        source_rows = _read_jsonl_named(
            root / WINDOW_ROWS_FILE_NAME,
            errors,
            "source_outcome_data_window_rows_missing",
            "source_outcome_data_window",
        )
        try:
            loaded_summary = _read_json(root / SUMMARY_FILE_NAME)
        except FileNotFoundError:
            loaded_summary = {}
            errors.append("source_outcome_data_window_summary_missing")
        except json.JSONDecodeError:
            loaded_summary = {}
            errors.append("source_outcome_data_window_summary_parse_error")
        source_summary = loaded_summary if isinstance(loaded_summary, dict) else {}
        if not isinstance(loaded_summary, dict):
            errors.append("source_outcome_data_window_summary_not_object")

    materialization_rows = [_materialization_row(row, row_index) for row_index, row in enumerate(source_rows) if not errors]
    for row in materialization_rows:
        _scan_forbidden_computed_fields(row, errors)

    source_rows_path = root / WINDOW_ROWS_FILE_NAME
    counts = _materialization_counts(materialization_rows)
    scaffold_ready = bool(materialization_rows) and not errors
    outputs: dict[str, str] = {}
    if out_dir is not None:
        output_dir = out_dir.resolve()
        _write_jsonl(output_dir / MATERIALIZATION_ROWS_FILE_NAME, materialization_rows)
        outputs = {
            "materialization_scaffold_rows": (output_dir / MATERIALIZATION_ROWS_FILE_NAME).as_posix(),
            "summary": (output_dir / MATERIALIZATION_SUMMARY_FILE_NAME).as_posix(),
        }

    payload = {
        "component": "scn002_false_breakout_read_only_outcome_data_window_materialization_scaffold",
        "implemented": True,
        "implemented_mode": MATERIALIZATION_IMPLEMENTED_MODE,
        "version": MATERIALIZATION_VERSION,
        "scenario_id": SCENARIO_ID,
        "windows_dir": root.as_posix(),
        "source_outcome_data_window_version": source_summary.get("version"),
        "source_outcome_data_window_rows_sha256": _sha256_file(source_rows_path) if source_rows_path.exists() else "",
        "source_outcome_data_window_validation_passed": validation_payload.get("all_window_scaffold_valid") is True,
        "source_outcome_data_window_scaffold_row_count": int(validation_payload.get("window_scaffold_row_count") or 0),
        "source_outcome_data_window_candidate_count": int(validation_payload.get("window_candidate_count") or 0),
        "source_outcome_data_window_manual_non_scored_count": int(validation_payload.get("manual_non_scored_count") or 0),
        "source_outcome_data_window_hard_reject_non_scored_count": int(
            validation_payload.get("hard_reject_non_scored_count") or 0
        ),
        **counts,
        "future_materialization_contract_id": MATERIALIZATION_CONTRACT_ID,
        "future_materialization_allowed_states": FUTURE_MATERIALIZATION_STATES,
        "materialization_scaffold_ready": scaffold_ready,
        "materialization_scaffold_errors": sorted(set(errors)),
        "materialization_scaffold_error_count": len(set(errors)),
        "materialization_scaffold_rows": materialization_rows,
        "outputs": outputs,
        "real_historical_data_loading_allowed": False,
        "historical_data_loading_allowed": False,
        "market_file_access_allowed": False,
        "market_row_parsing_allowed": False,
        "outcome_data_window_materialization_allowed": False,
        "market_backtest_allowed": False,
        "build_split_manifest_allowed": False,
        "observe_offline_allowed": False,
        "detector_execution_allowed": False,
        "label_computation_allowed": False,
        "outcome_computation_allowed": False,
        "pnl_computation_allowed": False,
        "old_external_crypto_stats_used": False,
        **SAFETY_FLAGS,
    }
    if out_dir is not None:
        summary_payload = {key: value for key, value in payload.items() if key != "materialization_scaffold_rows"}
        _write_json(out_dir.resolve() / MATERIALIZATION_SUMMARY_FILE_NAME, summary_payload)
    return payload


def _validate_materialization_row(row: dict[str, Any], errors: list[str], row_index: int) -> None:
    required_fields = [
        "result_type",
        "scenario_id",
        "source_outcome_data_window_row_index",
        "case_id",
        "observation_id",
        "source_observation_accepted",
        "source_data_window_state",
        "source_eligible_for_future_window_materialization",
        "materialization_boundary_state",
        "eligible_for_future_reviewed_manifest_materialization",
        "future_materialization_contract_id",
        "source_outcome_data_window_generated_by",
        "generated_by",
        "non_trading_output",
        "boundary_scaffold_only",
        "materialization_scaffold_only",
        "window_materialized",
        "market_data_loaded",
        "market_file_accessed",
        "market_rows_parsed",
        "detector_reexecuted",
        "label_computed",
        "outcome_computed",
        "pnl_computed",
        "performance_metrics_computed",
    ]
    for field in required_fields:
        if field not in row:
            errors.append(f"materialization_scaffold_row_missing_field:{row_index}:{field}")
    if row.get("result_type") != "scn002_read_only_outcome_data_window_materialization_scaffold":
        errors.append(f"materialization_scaffold_result_type_mismatch:{row_index}")
    if row.get("scenario_id") != SCENARIO_ID:
        errors.append(f"materialization_scaffold_scenario_id_mismatch:{row_index}")
    if row.get("generated_by") != MATERIALIZATION_VERSION:
        errors.append(f"materialization_scaffold_generated_by_mismatch:{row_index}")
    if row.get("source_outcome_data_window_generated_by") != VERSION:
        errors.append(f"materialization_scaffold_source_generated_by_mismatch:{row_index}")
    if row.get("non_trading_output") is not True:
        errors.append(f"materialization_scaffold_non_trading_output_missing:{row_index}")
    if row.get("boundary_scaffold_only") is not True:
        errors.append(f"materialization_scaffold_boundary_only_missing:{row_index}")
    if row.get("materialization_scaffold_only") is not True:
        errors.append(f"materialization_scaffold_only_missing:{row_index}")
    for field in [
        "window_materialized",
        "market_data_loaded",
        "market_file_accessed",
        "market_rows_parsed",
        "detector_reexecuted",
        "label_computed",
        "outcome_computed",
        "pnl_computed",
        "performance_metrics_computed",
        "manifest_metadata_loaded",
        "manifest_metadata_validated",
        "market_file_reference_bound",
    ]:
        if row.get(field) is not False:
            errors.append(f"materialization_scaffold_unsafe_flag_not_false:{row_index}:{field}")
    if row.get("future_materialization_contract_id") != MATERIALIZATION_CONTRACT_ID:
        errors.append(f"materialization_scaffold_contract_mismatch:{row_index}")

    source_state = row.get("source_data_window_state")
    materialization_state = row.get("materialization_boundary_state")
    if source_state == "candidate_waiting_for_reviewed_manifest_gate":
        if materialization_state != "candidate_waiting_for_explicit_reviewed_manifest_metadata":
            errors.append(f"materialization_scaffold_candidate_state_mismatch:{row_index}")
        if row.get("eligible_for_future_reviewed_manifest_materialization") is not True:
            errors.append(f"materialization_scaffold_candidate_not_marked:{row_index}")
        if row.get("source_eligible_for_future_window_materialization") is not True:
            errors.append(f"materialization_scaffold_source_candidate_not_marked:{row_index}")
        if row.get("strict_post_decision_boundary_required") is not True:
            errors.append(f"materialization_scaffold_post_decision_not_required:{row_index}")
        if row.get("explicit_reviewed_manifest_metadata_required") is not True:
            errors.append(f"materialization_scaffold_manifest_metadata_gate_not_required:{row_index}")
        for field in ["symbol", "decision_timestamp_utc", "source_rule_ids", "source_candidate_ids", "source_artifact_versions"]:
            if not row.get(field):
                errors.append(f"materialization_scaffold_candidate_missing_trace_field:{row_index}:{field}")
    elif source_state == "manual_review_preserved_non_scored":
        if materialization_state != "manual_review_preserved_non_scored":
            errors.append(f"materialization_scaffold_manual_state_mismatch:{row_index}")
        if row.get("eligible_for_future_reviewed_manifest_materialization") is not False:
            errors.append(f"materialization_scaffold_manual_eligible_not_false:{row_index}")
        if row.get("manual_review_needed") is not True:
            errors.append(f"materialization_scaffold_manual_review_not_preserved:{row_index}")
    elif source_state == "hard_reject_preserved_non_scored":
        if materialization_state != "hard_reject_preserved_non_scored":
            errors.append(f"materialization_scaffold_hard_reject_state_mismatch:{row_index}")
        if row.get("eligible_for_future_reviewed_manifest_materialization") is not False:
            errors.append(f"materialization_scaffold_hard_reject_eligible_not_false:{row_index}")
        if not row.get("error_codes"):
            errors.append(f"materialization_scaffold_hard_reject_missing_error_codes:{row_index}")
    else:
        errors.append(f"materialization_scaffold_source_data_window_state_invalid:{row_index}")


def validate_scn002_outcome_data_window_materialization_scaffold(materialization_dir: Path) -> dict[str, Any]:
    root = materialization_dir.resolve()
    errors: list[str] = []
    if not root.exists() or not root.is_dir():
        errors.append("materialization_scaffold_dir_missing")
        rows: list[dict[str, Any]] = []
        summary: dict[str, Any] = {}
    else:
        try:
            loaded_summary = _read_json(root / MATERIALIZATION_SUMMARY_FILE_NAME)
        except FileNotFoundError:
            loaded_summary = {}
            errors.append("materialization_scaffold_summary_missing")
        except json.JSONDecodeError:
            loaded_summary = {}
            errors.append("materialization_scaffold_summary_parse_error")
        summary = loaded_summary if isinstance(loaded_summary, dict) else {}
        if not isinstance(loaded_summary, dict):
            errors.append("materialization_scaffold_summary_not_object")
        rows = _read_jsonl_named(
            root / MATERIALIZATION_ROWS_FILE_NAME,
            errors,
            "materialization_scaffold_rows_missing",
            "materialization_scaffold",
        )

    for row_index, row in enumerate(rows, start=1):
        _scan_forbidden_computed_fields(row, errors)
        _validate_materialization_row(row, errors, row_index)

    counts = _materialization_counts(rows)
    state_counts: dict[str, int] = {}
    for row in rows:
        state = str(row.get("materialization_boundary_state") or "missing")
        state_counts[state] = state_counts.get(state, 0) + 1

    if summary:
        if summary.get("component") != "scn002_false_breakout_read_only_outcome_data_window_materialization_scaffold":
            errors.append("materialization_scaffold_summary_component_mismatch")
        if summary.get("version") != MATERIALIZATION_VERSION:
            errors.append("materialization_scaffold_summary_version_mismatch")
        for field, actual in counts.items():
            if int(summary.get(field) or -1) != actual:
                errors.append(f"materialization_scaffold_summary_{field}_mismatch")
        if summary.get("materialization_scaffold_ready") is not True:
            errors.append("materialization_scaffold_summary_ready_not_true")
        for field in [
            "real_historical_data_loading_allowed",
            "historical_data_loading_allowed",
            "market_file_access_allowed",
            "market_row_parsing_allowed",
            "outcome_data_window_materialization_allowed",
            "market_backtest_allowed",
            "build_split_manifest_allowed",
            "observe_offline_allowed",
            "detector_execution_allowed",
            "label_computation_allowed",
            "outcome_computation_allowed",
            "pnl_computation_allowed",
            "execution_allowed",
            "runtime_signal_allowed",
            "paper_trading_allowed",
            "live_trading_allowed",
            "backtest_harness_allowed",
        ]:
            if summary.get(field) is not False:
                errors.append(f"materialization_scaffold_summary_unsafe_flag_not_false:{field}")

    validation_passed = bool(rows) and not errors
    return {
        "component": "scn002_false_breakout_read_only_outcome_data_window_materialization_scaffold_validator",
        "implemented": True,
        "implemented_mode": MATERIALIZATION_IMPLEMENTED_MODE,
        "version": MATERIALIZATION_VERSION,
        "scenario_id": SCENARIO_ID,
        "materialization_dir": root.as_posix(),
        **counts,
        "materialization_boundary_state_counts": dict(sorted(state_counts.items())),
        "validation_errors": sorted(set(errors)),
        "validation_error_count": len(set(errors)),
        "all_materialization_scaffold_valid": validation_passed,
        "real_historical_data_loading_allowed": False,
        "historical_data_loading_allowed": False,
        "market_file_access_allowed": False,
        "market_row_parsing_allowed": False,
        "outcome_data_window_materialization_allowed": False,
        "market_backtest_allowed": False,
        "detector_execution_allowed": False,
        "label_computation_allowed": False,
        "outcome_computation_allowed": False,
        "pnl_computation_allowed": False,
        "old_external_crypto_stats_used": False,
        **SAFETY_FLAGS,
    }


def _binding_state(materialization_row: dict[str, Any]) -> str:
    materialization_state = str(materialization_row.get("materialization_boundary_state") or "")
    if materialization_state == "candidate_waiting_for_explicit_reviewed_manifest_metadata":
        return "awaiting_reviewed_manifest_metadata"
    if materialization_state == "manual_review_preserved_non_scored":
        return "manual_review_preserved_non_scored"
    if materialization_state == "hard_reject_preserved_non_scored":
        return "hard_reject_preserved_non_scored"
    return "invalid_source_materialization_state"


def _binding_non_scored_reason(materialization_row: dict[str, Any]) -> str | None:
    materialization_state = str(materialization_row.get("materialization_boundary_state") or "")
    if materialization_state == "candidate_waiting_for_explicit_reviewed_manifest_metadata":
        return None
    if materialization_state == "manual_review_preserved_non_scored":
        return str(materialization_row.get("non_scored_reason") or "manual_review_required_before_any_manifest_binding")
    if materialization_state == "hard_reject_preserved_non_scored":
        return str(materialization_row.get("non_scored_reason") or "hard_reject_preserved_without_manifest_binding")
    return "invalid_source_materialization_state"


def _binding_row(materialization_row: dict[str, Any], row_index: int) -> dict[str, Any]:
    binding_state = _binding_state(materialization_row)
    eligible = binding_state == "awaiting_reviewed_manifest_metadata"
    case_id = str(materialization_row.get("case_id") or f"row-{row_index + 1}")
    return {
        "result_type": "scn002_read_only_reviewed_manifest_binding_scaffold",
        "scenario_id": SCENARIO_ID,
        "manifest_binding_id": f"SCN002-ODWMB-{row_index + 1:04d}",
        "source_materialization_candidate_id": f"{case_id}:materialization:{row_index + 1}" if eligible else None,
        "source_outcome_data_window_materialization_row_index": row_index + 1,
        "source_outcome_data_window_row_index": materialization_row.get("source_outcome_data_window_row_index"),
        "source_outcome_label_row_index": materialization_row.get("source_outcome_label_row_index"),
        "source_preflight_row_index": materialization_row.get("source_preflight_row_index"),
        "source_preflight_source_row_index": materialization_row.get("source_preflight_source_row_index"),
        "case_id": materialization_row.get("case_id"),
        "observation_id": materialization_row.get("observation_id"),
        "source_observation_accepted": materialization_row.get("source_observation_accepted") is True,
        "source_preflight_state": materialization_row.get("source_preflight_state"),
        "source_chronological_split_role": materialization_row.get("source_chronological_split_role"),
        "source_data_window_state": materialization_row.get("source_data_window_state"),
        "source_materialization_boundary_state": materialization_row.get("materialization_boundary_state"),
        "source_eligible_for_future_reviewed_manifest_materialization": materialization_row.get(
            "eligible_for_future_reviewed_manifest_materialization"
        ) is True,
        "manifest_binding_state": binding_state,
        "eligible_for_future_reviewed_manifest_binding": eligible,
        "non_scored_reason": _binding_non_scored_reason(materialization_row),
        "future_binding_contract_id": BINDING_CONTRACT_ID,
        "future_binding_allowed_states": FUTURE_BINDING_STATES if eligible else [],
        "future_binding_required_fields": FUTURE_BINDING_FIELDS if eligible else [],
        "symbol": materialization_row.get("symbol") if eligible else None,
        "decision_timestamp_utc": materialization_row.get("decision_timestamp_utc") if eligible else None,
        "strict_post_decision_boundary_required": eligible,
        "explicit_reviewed_manifest_metadata_required": eligible,
        "source_materialization_implementation_review_id": None,
        "reviewed_manifest_metadata_id": None,
        "reviewed_manifest_metadata_review_id": None,
        "manifest_dataset_id": None,
        "manifest_symbol": None,
        "manifest_timeframe": None,
        "manifest_timezone": None,
        "coverage_start_utc": None,
        "coverage_end_utc": None,
        "requested_window_start_utc": None,
        "requested_window_end_utc": None,
        "manifest_metadata_bound": False,
        "manifest_metadata_loaded": False,
        "manifest_metadata_validated": False,
        "manifest_file_accessed": False,
        "manifest_rows_parsed": False,
        "market_file_reference_bound": False,
        "manual_review_needed": materialization_row.get("manual_review_needed") is True,
        "hard_reject_reasons": materialization_row.get("hard_reject_reasons") if isinstance(materialization_row.get("hard_reject_reasons"), list) else [],
        "error_codes": materialization_row.get("error_codes") if isinstance(materialization_row.get("error_codes"), list) else [],
        "source_rule_ids": materialization_row.get("source_rule_ids") if isinstance(materialization_row.get("source_rule_ids"), list) else [],
        "source_candidate_ids": materialization_row.get("source_candidate_ids") if isinstance(materialization_row.get("source_candidate_ids"), list) else [],
        "source_artifact_versions": materialization_row.get("source_artifact_versions") if isinstance(materialization_row.get("source_artifact_versions"), dict) else {},
        "source_detector_status_counts": materialization_row.get("source_detector_status_counts") if isinstance(materialization_row.get("source_detector_status_counts"), dict) else {},
        "source_outcome_data_window_materialization_generated_by": materialization_row.get("generated_by"),
        "generated_by": BINDING_VERSION,
        "non_trading_output": True,
        "boundary_scaffold_only": True,
        "reviewed_manifest_binding_scaffold_only": True,
        "window_materialized": False,
        "market_data_loaded": False,
        "market_file_accessed": False,
        "market_rows_parsed": False,
        "detector_reexecuted": False,
        "label_computed": False,
        "outcome_computed": False,
        "pnl_computed": False,
        "performance_metrics_computed": False,
        "future_real_data_gate_required": eligible,
        "future_window_materialization_gate_required_after_binding_review": eligible,
    }


def _binding_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "binding_scaffold_row_count": len(rows),
        "binding_candidate_count": sum(1 for row in rows if row.get("manifest_binding_state") == "awaiting_reviewed_manifest_metadata"),
        "manual_non_scored_count": sum(1 for row in rows if row.get("manifest_binding_state") == "manual_review_preserved_non_scored"),
        "hard_reject_non_scored_count": sum(1 for row in rows if row.get("manifest_binding_state") == "hard_reject_preserved_non_scored"),
    }


def prepare_scn002_outcome_data_window_reviewed_manifest_binding_scaffold(
    materialization_dir: Path,
    out_dir: Path | None = None,
) -> dict[str, Any]:
    root = materialization_dir.resolve()
    input_errors = _binding_input_errors(root)
    validation_payload: dict[str, Any] = {}
    source_rows: list[dict[str, Any]] = []
    source_summary: dict[str, Any] = {}
    errors = list(input_errors)

    if not input_errors:
        validation_payload = validate_scn002_outcome_data_window_materialization_scaffold(root)
        if validation_payload.get("all_materialization_scaffold_valid") is not True:
            errors.append("source_outcome_data_window_materialization_scaffold_not_valid")
        errors.extend(str(error) for error in validation_payload.get("validation_errors") or [])
        source_rows = _read_jsonl_named(
            root / MATERIALIZATION_ROWS_FILE_NAME,
            errors,
            "source_outcome_data_window_materialization_rows_missing",
            "source_outcome_data_window_materialization",
        )
        try:
            loaded_summary = _read_json(root / MATERIALIZATION_SUMMARY_FILE_NAME)
        except FileNotFoundError:
            loaded_summary = {}
            errors.append("source_outcome_data_window_materialization_summary_missing")
        except json.JSONDecodeError:
            loaded_summary = {}
            errors.append("source_outcome_data_window_materialization_summary_parse_error")
        source_summary = loaded_summary if isinstance(loaded_summary, dict) else {}
        if not isinstance(loaded_summary, dict):
            errors.append("source_outcome_data_window_materialization_summary_not_object")

    binding_rows = [_binding_row(row, row_index) for row_index, row in enumerate(source_rows) if not errors]
    for row in binding_rows:
        _scan_forbidden_computed_fields(row, errors)

    source_rows_path = root / MATERIALIZATION_ROWS_FILE_NAME
    counts = _binding_counts(binding_rows)
    scaffold_ready = bool(binding_rows) and not errors
    outputs: dict[str, str] = {}
    if out_dir is not None:
        output_dir = out_dir.resolve()
        _write_jsonl(output_dir / BINDING_ROWS_FILE_NAME, binding_rows)
        outputs = {
            "binding_scaffold_rows": (output_dir / BINDING_ROWS_FILE_NAME).as_posix(),
            "summary": (output_dir / BINDING_SUMMARY_FILE_NAME).as_posix(),
        }

    payload = {
        "component": "scn002_false_breakout_read_only_reviewed_manifest_binding_scaffold",
        "implemented": True,
        "implemented_mode": BINDING_IMPLEMENTED_MODE,
        "version": BINDING_VERSION,
        "scenario_id": SCENARIO_ID,
        "materialization_dir": root.as_posix(),
        "source_outcome_data_window_materialization_version": source_summary.get("version"),
        "source_outcome_data_window_materialization_rows_sha256": _sha256_file(source_rows_path) if source_rows_path.exists() else "",
        "source_outcome_data_window_materialization_validation_passed": validation_payload.get(
            "all_materialization_scaffold_valid"
        ) is True,
        "source_materialization_scaffold_row_count": int(validation_payload.get("materialization_scaffold_row_count") or 0),
        "source_materialization_candidate_count": int(validation_payload.get("materialization_candidate_count") or 0),
        "source_materialization_manual_non_scored_count": int(validation_payload.get("manual_non_scored_count") or 0),
        "source_materialization_hard_reject_non_scored_count": int(
            validation_payload.get("hard_reject_non_scored_count") or 0
        ),
        **counts,
        "future_binding_contract_id": BINDING_CONTRACT_ID,
        "future_binding_allowed_states": FUTURE_BINDING_STATES,
        "future_binding_fields": FUTURE_BINDING_FIELDS,
        "binding_scaffold_ready": scaffold_ready,
        "binding_scaffold_errors": sorted(set(errors)),
        "binding_scaffold_error_count": len(set(errors)),
        "binding_scaffold_rows": binding_rows,
        "outputs": outputs,
        "manifest_metadata_loaded": False,
        "manifest_metadata_validated": False,
        "manifest_file_accessed": False,
        "manifest_rows_parsed": False,
        "real_historical_data_loading_allowed": False,
        "historical_data_loading_allowed": False,
        "market_file_access_allowed": False,
        "market_row_parsing_allowed": False,
        "outcome_data_window_materialization_allowed": False,
        "market_backtest_allowed": False,
        "build_split_manifest_allowed": False,
        "observe_offline_allowed": False,
        "detector_execution_allowed": False,
        "label_computation_allowed": False,
        "outcome_computation_allowed": False,
        "pnl_computation_allowed": False,
        "old_external_crypto_stats_used": False,
        **SAFETY_FLAGS,
    }
    if out_dir is not None:
        summary_payload = {key: value for key, value in payload.items() if key != "binding_scaffold_rows"}
        _write_json(out_dir.resolve() / BINDING_SUMMARY_FILE_NAME, summary_payload)
    return payload


def _validate_binding_row(row: dict[str, Any], errors: list[str], row_index: int) -> None:
    required_fields = [
        "result_type",
        "scenario_id",
        "manifest_binding_id",
        "source_outcome_data_window_materialization_row_index",
        "case_id",
        "observation_id",
        "source_observation_accepted",
        "source_materialization_boundary_state",
        "source_eligible_for_future_reviewed_manifest_materialization",
        "manifest_binding_state",
        "eligible_for_future_reviewed_manifest_binding",
        "future_binding_contract_id",
        "source_outcome_data_window_materialization_generated_by",
        "generated_by",
        "non_trading_output",
        "boundary_scaffold_only",
        "reviewed_manifest_binding_scaffold_only",
        "manifest_metadata_bound",
        "manifest_metadata_loaded",
        "manifest_metadata_validated",
        "manifest_file_accessed",
        "manifest_rows_parsed",
        "market_file_reference_bound",
        "window_materialized",
        "market_data_loaded",
        "market_file_accessed",
        "market_rows_parsed",
        "detector_reexecuted",
        "label_computed",
        "outcome_computed",
        "pnl_computed",
        "performance_metrics_computed",
    ]
    for field in required_fields:
        if field not in row:
            errors.append(f"reviewed_manifest_binding_row_missing_field:{row_index}:{field}")
    if row.get("result_type") != "scn002_read_only_reviewed_manifest_binding_scaffold":
        errors.append(f"reviewed_manifest_binding_result_type_mismatch:{row_index}")
    if row.get("scenario_id") != SCENARIO_ID:
        errors.append(f"reviewed_manifest_binding_scenario_id_mismatch:{row_index}")
    if row.get("generated_by") != BINDING_VERSION:
        errors.append(f"reviewed_manifest_binding_generated_by_mismatch:{row_index}")
    if row.get("source_outcome_data_window_materialization_generated_by") != MATERIALIZATION_VERSION:
        errors.append(f"reviewed_manifest_binding_source_generated_by_mismatch:{row_index}")
    if row.get("non_trading_output") is not True:
        errors.append(f"reviewed_manifest_binding_non_trading_output_missing:{row_index}")
    if row.get("boundary_scaffold_only") is not True:
        errors.append(f"reviewed_manifest_binding_boundary_only_missing:{row_index}")
    if row.get("reviewed_manifest_binding_scaffold_only") is not True:
        errors.append(f"reviewed_manifest_binding_scaffold_only_missing:{row_index}")
    for field in [
        "manifest_metadata_bound",
        "manifest_metadata_loaded",
        "manifest_metadata_validated",
        "manifest_file_accessed",
        "manifest_rows_parsed",
        "market_file_reference_bound",
        "window_materialized",
        "market_data_loaded",
        "market_file_accessed",
        "market_rows_parsed",
        "detector_reexecuted",
        "label_computed",
        "outcome_computed",
        "pnl_computed",
        "performance_metrics_computed",
    ]:
        if row.get(field) is not False:
            errors.append(f"reviewed_manifest_binding_unsafe_flag_not_false:{row_index}:{field}")
    if row.get("future_binding_contract_id") != BINDING_CONTRACT_ID:
        errors.append(f"reviewed_manifest_binding_contract_mismatch:{row_index}")

    source_state = row.get("source_materialization_boundary_state")
    binding_state = row.get("manifest_binding_state")
    if source_state == "candidate_waiting_for_explicit_reviewed_manifest_metadata":
        if binding_state != "awaiting_reviewed_manifest_metadata":
            errors.append(f"reviewed_manifest_binding_candidate_state_mismatch:{row_index}")
        if row.get("eligible_for_future_reviewed_manifest_binding") is not True:
            errors.append(f"reviewed_manifest_binding_candidate_not_marked:{row_index}")
        if row.get("source_eligible_for_future_reviewed_manifest_materialization") is not True:
            errors.append(f"reviewed_manifest_binding_source_candidate_not_marked:{row_index}")
        if row.get("strict_post_decision_boundary_required") is not True:
            errors.append(f"reviewed_manifest_binding_post_decision_not_required:{row_index}")
        if row.get("explicit_reviewed_manifest_metadata_required") is not True:
            errors.append(f"reviewed_manifest_binding_manifest_metadata_gate_not_required:{row_index}")
        for field in ["symbol", "decision_timestamp_utc", "source_rule_ids", "source_candidate_ids", "source_artifact_versions"]:
            if not row.get(field):
                errors.append(f"reviewed_manifest_binding_candidate_missing_trace_field:{row_index}:{field}")
    elif source_state == "manual_review_preserved_non_scored":
        if binding_state != "manual_review_preserved_non_scored":
            errors.append(f"reviewed_manifest_binding_manual_state_mismatch:{row_index}")
        if row.get("eligible_for_future_reviewed_manifest_binding") is not False:
            errors.append(f"reviewed_manifest_binding_manual_eligible_not_false:{row_index}")
        if row.get("manual_review_needed") is not True:
            errors.append(f"reviewed_manifest_binding_manual_review_not_preserved:{row_index}")
    elif source_state == "hard_reject_preserved_non_scored":
        if binding_state != "hard_reject_preserved_non_scored":
            errors.append(f"reviewed_manifest_binding_hard_reject_state_mismatch:{row_index}")
        if row.get("eligible_for_future_reviewed_manifest_binding") is not False:
            errors.append(f"reviewed_manifest_binding_hard_reject_eligible_not_false:{row_index}")
        if not row.get("error_codes"):
            errors.append(f"reviewed_manifest_binding_hard_reject_missing_error_codes:{row_index}")
    else:
        errors.append(f"reviewed_manifest_binding_source_materialization_state_invalid:{row_index}")


def validate_scn002_outcome_data_window_reviewed_manifest_binding_scaffold(binding_dir: Path) -> dict[str, Any]:
    root = binding_dir.resolve()
    errors: list[str] = []
    if not root.exists() or not root.is_dir():
        errors.append("reviewed_manifest_binding_scaffold_dir_missing")
        rows: list[dict[str, Any]] = []
        summary: dict[str, Any] = {}
    else:
        try:
            loaded_summary = _read_json(root / BINDING_SUMMARY_FILE_NAME)
        except FileNotFoundError:
            loaded_summary = {}
            errors.append("reviewed_manifest_binding_scaffold_summary_missing")
        except json.JSONDecodeError:
            loaded_summary = {}
            errors.append("reviewed_manifest_binding_scaffold_summary_parse_error")
        summary = loaded_summary if isinstance(loaded_summary, dict) else {}
        if not isinstance(loaded_summary, dict):
            errors.append("reviewed_manifest_binding_scaffold_summary_not_object")
        rows = _read_jsonl_named(
            root / BINDING_ROWS_FILE_NAME,
            errors,
            "reviewed_manifest_binding_scaffold_rows_missing",
            "reviewed_manifest_binding_scaffold",
        )

    for row_index, row in enumerate(rows, start=1):
        _scan_forbidden_computed_fields(row, errors)
        _validate_binding_row(row, errors, row_index)

    counts = _binding_counts(rows)
    state_counts: dict[str, int] = {}
    for row in rows:
        state = str(row.get("manifest_binding_state") or "missing")
        state_counts[state] = state_counts.get(state, 0) + 1

    if summary:
        if summary.get("component") != "scn002_false_breakout_read_only_reviewed_manifest_binding_scaffold":
            errors.append("reviewed_manifest_binding_summary_component_mismatch")
        if summary.get("version") != BINDING_VERSION:
            errors.append("reviewed_manifest_binding_summary_version_mismatch")
        for field, actual in counts.items():
            if int(summary.get(field) or -1) != actual:
                errors.append(f"reviewed_manifest_binding_summary_{field}_mismatch")
        if summary.get("binding_scaffold_ready") is not True:
            errors.append("reviewed_manifest_binding_summary_ready_not_true")
        for field in [
            "manifest_metadata_loaded",
            "manifest_metadata_validated",
            "manifest_file_accessed",
            "manifest_rows_parsed",
            "real_historical_data_loading_allowed",
            "historical_data_loading_allowed",
            "market_file_access_allowed",
            "market_row_parsing_allowed",
            "outcome_data_window_materialization_allowed",
            "market_backtest_allowed",
            "build_split_manifest_allowed",
            "observe_offline_allowed",
            "detector_execution_allowed",
            "label_computation_allowed",
            "outcome_computation_allowed",
            "pnl_computation_allowed",
            "execution_allowed",
            "runtime_signal_allowed",
            "paper_trading_allowed",
            "live_trading_allowed",
            "backtest_harness_allowed",
        ]:
            if summary.get(field) is not False:
                errors.append(f"reviewed_manifest_binding_summary_unsafe_flag_not_false:{field}")

    validation_passed = bool(rows) and not errors
    return {
        "component": "scn002_false_breakout_read_only_reviewed_manifest_binding_scaffold_validator",
        "implemented": True,
        "implemented_mode": BINDING_IMPLEMENTED_MODE,
        "version": BINDING_VERSION,
        "scenario_id": SCENARIO_ID,
        "binding_dir": root.as_posix(),
        **counts,
        "manifest_binding_state_counts": dict(sorted(state_counts.items())),
        "validation_errors": sorted(set(errors)),
        "validation_error_count": len(set(errors)),
        "all_binding_scaffold_valid": validation_passed,
        "manifest_metadata_loaded": False,
        "manifest_metadata_validated": False,
        "manifest_file_accessed": False,
        "manifest_rows_parsed": False,
        "real_historical_data_loading_allowed": False,
        "historical_data_loading_allowed": False,
        "market_file_access_allowed": False,
        "market_row_parsing_allowed": False,
        "outcome_data_window_materialization_allowed": False,
        "market_backtest_allowed": False,
        "detector_execution_allowed": False,
        "label_computation_allowed": False,
        "outcome_computation_allowed": False,
        "pnl_computation_allowed": False,
        "old_external_crypto_stats_used": False,
        **SAFETY_FLAGS,
    }


def _metadata_validation_state(binding_row: dict[str, Any]) -> str:
    binding_state = str(binding_row.get("manifest_binding_state") or "")
    if binding_state == "awaiting_reviewed_manifest_metadata":
        return "awaiting_reviewed_metadata"
    if binding_state == "manual_review_preserved_non_scored":
        return "manual_review_preserved_non_scored"
    if binding_state == "hard_reject_preserved_non_scored":
        return "hard_reject_preserved_non_scored"
    return "invalid_source_binding_state"


def _metadata_validation_non_scored_reason(binding_row: dict[str, Any]) -> str | None:
    binding_state = str(binding_row.get("manifest_binding_state") or "")
    if binding_state == "awaiting_reviewed_manifest_metadata":
        return None
    if binding_state == "manual_review_preserved_non_scored":
        return str(binding_row.get("non_scored_reason") or "manual_review_required_before_any_metadata_validation")
    if binding_state == "hard_reject_preserved_non_scored":
        return str(binding_row.get("non_scored_reason") or "hard_reject_preserved_without_metadata_validation")
    return "invalid_source_binding_state"


def _metadata_validation_row(
    binding_row: dict[str, Any],
    row_index: int,
    source_binding_implementation_review_id: str | None,
) -> dict[str, Any]:
    validation_state = _metadata_validation_state(binding_row)
    eligible = validation_state == "awaiting_reviewed_metadata"
    return {
        "result_type": "scn002_read_only_reviewed_manifest_metadata_validation_scaffold",
        "scenario_id": SCENARIO_ID,
        "metadata_validation_id": f"SCN002-RMMV-{row_index + 1:04d}",
        "source_manifest_binding_id": binding_row.get("manifest_binding_id"),
        "source_binding_implementation_review_id": source_binding_implementation_review_id,
        "source_reviewed_manifest_binding_row_index": row_index + 1,
        "source_outcome_data_window_materialization_row_index": binding_row.get(
            "source_outcome_data_window_materialization_row_index"
        ),
        "source_outcome_data_window_row_index": binding_row.get("source_outcome_data_window_row_index"),
        "source_outcome_label_row_index": binding_row.get("source_outcome_label_row_index"),
        "source_preflight_row_index": binding_row.get("source_preflight_row_index"),
        "source_preflight_source_row_index": binding_row.get("source_preflight_source_row_index"),
        "case_id": binding_row.get("case_id"),
        "observation_id": binding_row.get("observation_id"),
        "source_observation_accepted": binding_row.get("source_observation_accepted") is True,
        "source_preflight_state": binding_row.get("source_preflight_state"),
        "source_chronological_split_role": binding_row.get("source_chronological_split_role"),
        "source_data_window_state": binding_row.get("source_data_window_state"),
        "source_materialization_boundary_state": binding_row.get("source_materialization_boundary_state"),
        "source_manifest_binding_state": binding_row.get("manifest_binding_state"),
        "source_eligible_for_future_reviewed_manifest_binding": binding_row.get(
            "eligible_for_future_reviewed_manifest_binding"
        ) is True,
        "metadata_validation_state": validation_state,
        "eligible_for_future_metadata_validation": eligible,
        "non_scored_reason": _metadata_validation_non_scored_reason(binding_row),
        "future_metadata_validation_contract_id": METADATA_VALIDATION_CONTRACT_ID,
        "future_metadata_validation_allowed_states": FUTURE_METADATA_VALIDATION_STATES if eligible else [],
        "future_metadata_validation_required_fields": FUTURE_METADATA_VALIDATION_FIELDS if eligible else [],
        "candidate_symbol": binding_row.get("symbol") if eligible else None,
        "decision_timestamp_utc": binding_row.get("decision_timestamp_utc") if eligible else None,
        "strict_post_decision_boundary_required": eligible,
        "explicit_reviewed_manifest_metadata_required": eligible,
        "reviewed_manifest_metadata_id": None,
        "reviewed_manifest_metadata_review_id": None,
        "manifest_dataset_id": None,
        "instrument_profile": None,
        "manifest_symbol": None,
        "manifest_timeframe": None,
        "manifest_timezone": None,
        "coverage_start_utc": None,
        "coverage_end_utc": None,
        "requested_window_start_utc": None,
        "requested_window_end_utc": None,
        "manifest_metadata_loaded": False,
        "manifest_metadata_validated": False,
        "manifest_metadata_validation_allowed": False,
        "manifest_metadata_validation_performed": False,
        "manifest_file_accessed": False,
        "manifest_rows_parsed": False,
        "market_file_reference_bound": False,
        "manual_review_needed": binding_row.get("manual_review_needed") is True,
        "hard_reject_reasons": binding_row.get("hard_reject_reasons") if isinstance(binding_row.get("hard_reject_reasons"), list) else [],
        "error_codes": binding_row.get("error_codes") if isinstance(binding_row.get("error_codes"), list) else [],
        "source_rule_ids": binding_row.get("source_rule_ids") if isinstance(binding_row.get("source_rule_ids"), list) else [],
        "source_candidate_ids": binding_row.get("source_candidate_ids") if isinstance(binding_row.get("source_candidate_ids"), list) else [],
        "source_artifact_versions": binding_row.get("source_artifact_versions") if isinstance(binding_row.get("source_artifact_versions"), dict) else {},
        "source_detector_status_counts": binding_row.get("source_detector_status_counts") if isinstance(binding_row.get("source_detector_status_counts"), dict) else {},
        "source_reviewed_manifest_binding_generated_by": binding_row.get("generated_by"),
        "generated_by": METADATA_VALIDATION_VERSION,
        "non_trading_output": True,
        "boundary_scaffold_only": True,
        "reviewed_manifest_metadata_validation_scaffold_only": True,
        "window_materialized": False,
        "market_data_loaded": False,
        "market_file_accessed": False,
        "market_rows_parsed": False,
        "detector_reexecuted": False,
        "label_computed": False,
        "outcome_computed": False,
        "pnl_computed": False,
        "performance_metrics_computed": False,
        "future_real_data_gate_required": eligible,
        "future_window_materialization_gate_required_after_metadata_validation_review": eligible,
    }


def _metadata_validation_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "metadata_validation_scaffold_row_count": len(rows),
        "metadata_validation_candidate_count": sum(
            1 for row in rows if row.get("metadata_validation_state") == "awaiting_reviewed_metadata"
        ),
        "manual_non_scored_count": sum(
            1 for row in rows if row.get("metadata_validation_state") == "manual_review_preserved_non_scored"
        ),
        "hard_reject_non_scored_count": sum(
            1 for row in rows if row.get("metadata_validation_state") == "hard_reject_preserved_non_scored"
        ),
    }


def prepare_scn002_reviewed_manifest_metadata_validation_scaffold(
    binding_dir: Path,
    out_dir: Path | None = None,
    source_binding_implementation_review_id: str | None = None,
) -> dict[str, Any]:
    root = binding_dir.resolve()
    input_errors = _metadata_validation_input_errors(root)
    validation_payload: dict[str, Any] = {}
    source_rows: list[dict[str, Any]] = []
    source_summary: dict[str, Any] = {}
    errors = list(input_errors)

    if not input_errors:
        validation_payload = validate_scn002_outcome_data_window_reviewed_manifest_binding_scaffold(root)
        if validation_payload.get("all_binding_scaffold_valid") is not True:
            errors.append("source_reviewed_manifest_binding_scaffold_not_valid")
        errors.extend(str(error) for error in validation_payload.get("validation_errors") or [])
        source_rows = _read_jsonl_named(
            root / BINDING_ROWS_FILE_NAME,
            errors,
            "source_reviewed_manifest_binding_rows_missing",
            "source_reviewed_manifest_binding",
        )
        try:
            loaded_summary = _read_json(root / BINDING_SUMMARY_FILE_NAME)
        except FileNotFoundError:
            loaded_summary = {}
            errors.append("source_reviewed_manifest_binding_summary_missing")
        except json.JSONDecodeError:
            loaded_summary = {}
            errors.append("source_reviewed_manifest_binding_summary_parse_error")
        source_summary = loaded_summary if isinstance(loaded_summary, dict) else {}
        if not isinstance(loaded_summary, dict):
            errors.append("source_reviewed_manifest_binding_summary_not_object")

    metadata_validation_rows = [
        _metadata_validation_row(row, row_index, source_binding_implementation_review_id)
        for row_index, row in enumerate(source_rows)
        if not errors
    ]
    for row in metadata_validation_rows:
        _scan_forbidden_computed_fields(row, errors)

    source_rows_path = root / BINDING_ROWS_FILE_NAME
    counts = _metadata_validation_counts(metadata_validation_rows)
    scaffold_ready = bool(metadata_validation_rows) and not errors
    outputs: dict[str, str] = {}
    if out_dir is not None:
        output_dir = out_dir.resolve()
        _write_jsonl(output_dir / METADATA_VALIDATION_ROWS_FILE_NAME, metadata_validation_rows)
        outputs = {
            "metadata_validation_scaffold_rows": (output_dir / METADATA_VALIDATION_ROWS_FILE_NAME).as_posix(),
            "summary": (output_dir / METADATA_VALIDATION_SUMMARY_FILE_NAME).as_posix(),
        }

    payload = {
        "component": "scn002_false_breakout_read_only_reviewed_manifest_metadata_validation_scaffold",
        "implemented": True,
        "implemented_mode": METADATA_VALIDATION_IMPLEMENTED_MODE,
        "version": METADATA_VALIDATION_VERSION,
        "scenario_id": SCENARIO_ID,
        "binding_dir": root.as_posix(),
        "source_reviewed_manifest_binding_version": source_summary.get("version"),
        "source_reviewed_manifest_binding_rows_sha256": _sha256_file(source_rows_path) if source_rows_path.exists() else "",
        "source_reviewed_manifest_binding_validation_passed": validation_payload.get("all_binding_scaffold_valid") is True,
        "source_binding_scaffold_row_count": int(validation_payload.get("binding_scaffold_row_count") or 0),
        "source_binding_candidate_count": int(validation_payload.get("binding_candidate_count") or 0),
        "source_binding_manual_non_scored_count": int(validation_payload.get("manual_non_scored_count") or 0),
        "source_binding_hard_reject_non_scored_count": int(validation_payload.get("hard_reject_non_scored_count") or 0),
        **counts,
        "source_binding_implementation_review_id": source_binding_implementation_review_id,
        "future_metadata_validation_contract_id": METADATA_VALIDATION_CONTRACT_ID,
        "future_metadata_validation_allowed_states": FUTURE_METADATA_VALIDATION_STATES,
        "future_metadata_validation_fields": FUTURE_METADATA_VALIDATION_FIELDS,
        "metadata_validation_scaffold_ready": scaffold_ready,
        "metadata_validation_scaffold_errors": sorted(set(errors)),
        "metadata_validation_scaffold_error_count": len(set(errors)),
        "metadata_validation_scaffold_rows": metadata_validation_rows,
        "outputs": outputs,
        "manifest_metadata_loaded": False,
        "manifest_metadata_validated": False,
        "manifest_metadata_validation_allowed": False,
        "manifest_file_accessed": False,
        "manifest_rows_parsed": False,
        "real_historical_data_loading_allowed": False,
        "historical_data_loading_allowed": False,
        "market_file_access_allowed": False,
        "market_row_parsing_allowed": False,
        "outcome_data_window_materialization_allowed": False,
        "market_backtest_allowed": False,
        "build_split_manifest_allowed": False,
        "observe_offline_allowed": False,
        "detector_execution_allowed": False,
        "label_computation_allowed": False,
        "outcome_computation_allowed": False,
        "pnl_computation_allowed": False,
        "old_external_crypto_stats_used": False,
        **SAFETY_FLAGS,
    }
    if out_dir is not None:
        summary_payload = {key: value for key, value in payload.items() if key != "metadata_validation_scaffold_rows"}
        _write_json(out_dir.resolve() / METADATA_VALIDATION_SUMMARY_FILE_NAME, summary_payload)
    return payload


def _validate_metadata_validation_row(row: dict[str, Any], errors: list[str], row_index: int) -> None:
    required_fields = [
        "result_type",
        "scenario_id",
        "metadata_validation_id",
        "source_manifest_binding_id",
        "source_binding_implementation_review_id",
        "source_reviewed_manifest_binding_row_index",
        "case_id",
        "observation_id",
        "source_observation_accepted",
        "source_manifest_binding_state",
        "source_eligible_for_future_reviewed_manifest_binding",
        "metadata_validation_state",
        "eligible_for_future_metadata_validation",
        "future_metadata_validation_contract_id",
        "source_reviewed_manifest_binding_generated_by",
        "generated_by",
        "non_trading_output",
        "boundary_scaffold_only",
        "reviewed_manifest_metadata_validation_scaffold_only",
        "manifest_metadata_loaded",
        "manifest_metadata_validated",
        "manifest_metadata_validation_allowed",
        "manifest_metadata_validation_performed",
        "manifest_file_accessed",
        "manifest_rows_parsed",
        "market_file_reference_bound",
        "window_materialized",
        "market_data_loaded",
        "market_file_accessed",
        "market_rows_parsed",
        "detector_reexecuted",
        "label_computed",
        "outcome_computed",
        "pnl_computed",
        "performance_metrics_computed",
    ]
    for field in required_fields:
        if field not in row:
            errors.append(f"reviewed_manifest_metadata_validation_row_missing_field:{row_index}:{field}")
    if row.get("result_type") != "scn002_read_only_reviewed_manifest_metadata_validation_scaffold":
        errors.append(f"reviewed_manifest_metadata_validation_result_type_mismatch:{row_index}")
    if row.get("scenario_id") != SCENARIO_ID:
        errors.append(f"reviewed_manifest_metadata_validation_scenario_id_mismatch:{row_index}")
    if row.get("generated_by") != METADATA_VALIDATION_VERSION:
        errors.append(f"reviewed_manifest_metadata_validation_generated_by_mismatch:{row_index}")
    if row.get("source_reviewed_manifest_binding_generated_by") != BINDING_VERSION:
        errors.append(f"reviewed_manifest_metadata_validation_source_generated_by_mismatch:{row_index}")
    if row.get("non_trading_output") is not True:
        errors.append(f"reviewed_manifest_metadata_validation_non_trading_output_missing:{row_index}")
    if row.get("boundary_scaffold_only") is not True:
        errors.append(f"reviewed_manifest_metadata_validation_boundary_only_missing:{row_index}")
    if row.get("reviewed_manifest_metadata_validation_scaffold_only") is not True:
        errors.append(f"reviewed_manifest_metadata_validation_scaffold_only_missing:{row_index}")
    for field in [
        "manifest_metadata_loaded",
        "manifest_metadata_validated",
        "manifest_metadata_validation_allowed",
        "manifest_metadata_validation_performed",
        "manifest_file_accessed",
        "manifest_rows_parsed",
        "market_file_reference_bound",
        "window_materialized",
        "market_data_loaded",
        "market_file_accessed",
        "market_rows_parsed",
        "detector_reexecuted",
        "label_computed",
        "outcome_computed",
        "pnl_computed",
        "performance_metrics_computed",
    ]:
        if row.get(field) is not False:
            errors.append(f"reviewed_manifest_metadata_validation_unsafe_flag_not_false:{row_index}:{field}")
    if row.get("future_metadata_validation_contract_id") != METADATA_VALIDATION_CONTRACT_ID:
        errors.append(f"reviewed_manifest_metadata_validation_contract_mismatch:{row_index}")

    source_state = row.get("source_manifest_binding_state")
    validation_state = row.get("metadata_validation_state")
    if source_state == "awaiting_reviewed_manifest_metadata":
        if validation_state != "awaiting_reviewed_metadata":
            errors.append(f"reviewed_manifest_metadata_validation_candidate_state_mismatch:{row_index}")
        if row.get("eligible_for_future_metadata_validation") is not True:
            errors.append(f"reviewed_manifest_metadata_validation_candidate_not_marked:{row_index}")
        if row.get("source_eligible_for_future_reviewed_manifest_binding") is not True:
            errors.append(f"reviewed_manifest_metadata_validation_source_candidate_not_marked:{row_index}")
        if row.get("strict_post_decision_boundary_required") is not True:
            errors.append(f"reviewed_manifest_metadata_validation_post_decision_not_required:{row_index}")
        if row.get("explicit_reviewed_manifest_metadata_required") is not True:
            errors.append(f"reviewed_manifest_metadata_validation_manifest_metadata_gate_not_required:{row_index}")
        for field in [
            "source_binding_implementation_review_id",
            "candidate_symbol",
            "decision_timestamp_utc",
            "source_rule_ids",
            "source_candidate_ids",
            "source_artifact_versions",
        ]:
            if not row.get(field):
                errors.append(f"reviewed_manifest_metadata_validation_candidate_missing_trace_field:{row_index}:{field}")
    elif source_state == "manual_review_preserved_non_scored":
        if validation_state != "manual_review_preserved_non_scored":
            errors.append(f"reviewed_manifest_metadata_validation_manual_state_mismatch:{row_index}")
        if row.get("eligible_for_future_metadata_validation") is not False:
            errors.append(f"reviewed_manifest_metadata_validation_manual_eligible_not_false:{row_index}")
        if row.get("manual_review_needed") is not True:
            errors.append(f"reviewed_manifest_metadata_validation_manual_review_not_preserved:{row_index}")
    elif source_state == "hard_reject_preserved_non_scored":
        if validation_state != "hard_reject_preserved_non_scored":
            errors.append(f"reviewed_manifest_metadata_validation_hard_reject_state_mismatch:{row_index}")
        if row.get("eligible_for_future_metadata_validation") is not False:
            errors.append(f"reviewed_manifest_metadata_validation_hard_reject_eligible_not_false:{row_index}")
        if not row.get("error_codes"):
            errors.append(f"reviewed_manifest_metadata_validation_hard_reject_missing_error_codes:{row_index}")
    else:
        errors.append(f"reviewed_manifest_metadata_validation_source_binding_state_invalid:{row_index}")


def validate_scn002_reviewed_manifest_metadata_validation_scaffold(metadata_validation_dir: Path) -> dict[str, Any]:
    root = metadata_validation_dir.resolve()
    errors: list[str] = []
    if not root.exists() or not root.is_dir():
        errors.append("reviewed_manifest_metadata_validation_scaffold_dir_missing")
        rows: list[dict[str, Any]] = []
        summary: dict[str, Any] = {}
    else:
        try:
            loaded_summary = _read_json(root / METADATA_VALIDATION_SUMMARY_FILE_NAME)
        except FileNotFoundError:
            loaded_summary = {}
            errors.append("reviewed_manifest_metadata_validation_scaffold_summary_missing")
        except json.JSONDecodeError:
            loaded_summary = {}
            errors.append("reviewed_manifest_metadata_validation_scaffold_summary_parse_error")
        summary = loaded_summary if isinstance(loaded_summary, dict) else {}
        if not isinstance(loaded_summary, dict):
            errors.append("reviewed_manifest_metadata_validation_scaffold_summary_not_object")
        rows = _read_jsonl_named(
            root / METADATA_VALIDATION_ROWS_FILE_NAME,
            errors,
            "reviewed_manifest_metadata_validation_scaffold_rows_missing",
            "reviewed_manifest_metadata_validation_scaffold",
        )

    for row_index, row in enumerate(rows, start=1):
        _scan_forbidden_computed_fields(row, errors)
        _validate_metadata_validation_row(row, errors, row_index)

    counts = _metadata_validation_counts(rows)
    state_counts: dict[str, int] = {}
    for row in rows:
        state = str(row.get("metadata_validation_state") or "missing")
        state_counts[state] = state_counts.get(state, 0) + 1

    if summary:
        if summary.get("component") != "scn002_false_breakout_read_only_reviewed_manifest_metadata_validation_scaffold":
            errors.append("reviewed_manifest_metadata_validation_summary_component_mismatch")
        if summary.get("version") != METADATA_VALIDATION_VERSION:
            errors.append("reviewed_manifest_metadata_validation_summary_version_mismatch")
        for field, actual in counts.items():
            if int(summary.get(field) or -1) != actual:
                errors.append(f"reviewed_manifest_metadata_validation_summary_{field}_mismatch")
        if summary.get("metadata_validation_scaffold_ready") is not True:
            errors.append("reviewed_manifest_metadata_validation_summary_ready_not_true")
        for field in [
            "manifest_metadata_loaded",
            "manifest_metadata_validated",
            "manifest_metadata_validation_allowed",
            "manifest_file_accessed",
            "manifest_rows_parsed",
            "real_historical_data_loading_allowed",
            "historical_data_loading_allowed",
            "market_file_access_allowed",
            "market_row_parsing_allowed",
            "outcome_data_window_materialization_allowed",
            "market_backtest_allowed",
            "build_split_manifest_allowed",
            "observe_offline_allowed",
            "detector_execution_allowed",
            "label_computation_allowed",
            "outcome_computation_allowed",
            "pnl_computation_allowed",
            "execution_allowed",
            "runtime_signal_allowed",
            "paper_trading_allowed",
            "live_trading_allowed",
            "backtest_harness_allowed",
        ]:
            if summary.get(field) is not False:
                errors.append(f"reviewed_manifest_metadata_validation_summary_unsafe_flag_not_false:{field}")

    validation_passed = bool(rows) and not errors
    return {
        "component": "scn002_false_breakout_read_only_reviewed_manifest_metadata_validation_scaffold_validator",
        "implemented": True,
        "implemented_mode": METADATA_VALIDATION_IMPLEMENTED_MODE,
        "version": METADATA_VALIDATION_VERSION,
        "scenario_id": SCENARIO_ID,
        "metadata_validation_dir": root.as_posix(),
        **counts,
        "metadata_validation_state_counts": dict(sorted(state_counts.items())),
        "validation_errors": sorted(set(errors)),
        "validation_error_count": len(set(errors)),
        "all_metadata_validation_scaffold_valid": validation_passed,
        "manifest_metadata_loaded": False,
        "manifest_metadata_validated": False,
        "manifest_metadata_validation_allowed": False,
        "manifest_file_accessed": False,
        "manifest_rows_parsed": False,
        "real_historical_data_loading_allowed": False,
        "historical_data_loading_allowed": False,
        "market_file_access_allowed": False,
        "market_row_parsing_allowed": False,
        "outcome_data_window_materialization_allowed": False,
        "market_backtest_allowed": False,
        "detector_execution_allowed": False,
        "label_computation_allowed": False,
        "outcome_computation_allowed": False,
        "pnl_computation_allowed": False,
        "old_external_crypto_stats_used": False,
        **SAFETY_FLAGS,
    }


def reviewed_manifest_metadata_validation_component_contract() -> dict[str, Any]:
    return {
        "component": "scn002_false_breakout_read_only_reviewed_manifest_metadata_validation_scaffold",
        "implemented": True,
        "implemented_mode": METADATA_VALIDATION_IMPLEMENTED_MODE,
        "version": METADATA_VALIDATION_VERSION,
        "scenario_id": SCENARIO_ID,
        "allowed_now": "read reviewed SCN-002 binding scaffold artifacts and write non-trading reviewed-manifest metadata validation scaffold rows only",
        "outputs": [METADATA_VALIDATION_ROWS_FILE_NAME, METADATA_VALIDATION_SUMMARY_FILE_NAME],
        "future_metadata_validation_contract_id": METADATA_VALIDATION_CONTRACT_ID,
        "future_metadata_validation_allowed_states": FUTURE_METADATA_VALIDATION_STATES,
        "future_metadata_validation_fields": FUTURE_METADATA_VALIDATION_FIELDS,
        "blocked_operations": [
            blocked_operation("manifest_file_access"),
            blocked_operation("manifest_row_parsing"),
            blocked_operation("actual_manifest_metadata_validation_over_real_files"),
            blocked_operation("real_historical_data_loading"),
            blocked_operation("market_file_access"),
            blocked_operation("market_row_parsing"),
            blocked_operation("actual_window_materialization"),
            blocked_operation("detector_execution_on_market_history"),
            blocked_operation("actual_outcome_label_computation"),
            blocked_operation("tp_sl_outcome_calculation"),
            blocked_operation("r_or_pnl_computation"),
            blocked_operation("market_backtest_run"),
            blocked_operation("execution"),
        ],
        "manifest_metadata_loaded": False,
        "manifest_metadata_validated": False,
        "manifest_metadata_validation_allowed": False,
        "manifest_file_accessed": False,
        "manifest_rows_parsed": False,
        "real_historical_data_loading_allowed": False,
        "historical_data_loading_allowed": False,
        "market_file_access_allowed": False,
        "market_row_parsing_allowed": False,
        "outcome_data_window_materialization_allowed": False,
        "market_backtest_allowed": False,
        "build_split_manifest_allowed": False,
        "observe_offline_allowed": False,
        "detector_execution_allowed": False,
        "label_computation_allowed": False,
        "outcome_computation_allowed": False,
        "pnl_computation_allowed": False,
        **SAFETY_FLAGS,
    }


def reviewed_manifest_binding_component_contract() -> dict[str, Any]:
    return {
        "component": "scn002_false_breakout_read_only_reviewed_manifest_binding_scaffold",
        "implemented": True,
        "implemented_mode": BINDING_IMPLEMENTED_MODE,
        "version": BINDING_VERSION,
        "scenario_id": SCENARIO_ID,
        "allowed_now": "read reviewed SCN-002 materialization scaffold artifacts and write non-trading reviewed-manifest binding metadata rows only",
        "outputs": [BINDING_ROWS_FILE_NAME, BINDING_SUMMARY_FILE_NAME],
        "future_binding_contract_id": BINDING_CONTRACT_ID,
        "future_binding_allowed_states": FUTURE_BINDING_STATES,
        "future_binding_fields": FUTURE_BINDING_FIELDS,
        "blocked_operations": [
            blocked_operation("manifest_file_access"),
            blocked_operation("manifest_row_parsing"),
            blocked_operation("real_historical_data_loading"),
            blocked_operation("market_file_access"),
            blocked_operation("market_row_parsing"),
            blocked_operation("actual_manifest_binding_over_real_files"),
            blocked_operation("actual_window_materialization"),
            blocked_operation("detector_execution_on_market_history"),
            blocked_operation("actual_outcome_label_computation"),
            blocked_operation("tp_sl_outcome_calculation"),
            blocked_operation("r_or_pnl_computation"),
            blocked_operation("market_backtest_run"),
            blocked_operation("execution"),
        ],
        "manifest_metadata_loaded": False,
        "manifest_metadata_validated": False,
        "manifest_file_accessed": False,
        "manifest_rows_parsed": False,
        "real_historical_data_loading_allowed": False,
        "historical_data_loading_allowed": False,
        "market_file_access_allowed": False,
        "market_row_parsing_allowed": False,
        "outcome_data_window_materialization_allowed": False,
        "market_backtest_allowed": False,
        "build_split_manifest_allowed": False,
        "observe_offline_allowed": False,
        "detector_execution_allowed": False,
        "label_computation_allowed": False,
        "outcome_computation_allowed": False,
        "pnl_computation_allowed": False,
        **SAFETY_FLAGS,
    }


def materialization_component_contract() -> dict[str, Any]:
    return {
        "component": "scn002_false_breakout_read_only_outcome_data_window_materialization_scaffold",
        "implemented": True,
        "implemented_mode": MATERIALIZATION_IMPLEMENTED_MODE,
        "version": MATERIALIZATION_VERSION,
        "scenario_id": SCENARIO_ID,
        "allowed_now": "read reviewed SCN-002 outcome data-window scaffold artifacts and write non-trading materialization boundary metadata rows only",
        "outputs": [MATERIALIZATION_ROWS_FILE_NAME, MATERIALIZATION_SUMMARY_FILE_NAME],
        "future_materialization_contract_id": MATERIALIZATION_CONTRACT_ID,
        "future_materialization_allowed_states": FUTURE_MATERIALIZATION_STATES,
        "blocked_operations": [
            blocked_operation("real_historical_data_loading"),
            blocked_operation("market_file_access"),
            blocked_operation("market_row_parsing"),
            blocked_operation("actual_window_materialization"),
            blocked_operation("detector_execution_on_market_history"),
            blocked_operation("actual_outcome_label_computation"),
            blocked_operation("tp_sl_outcome_calculation"),
            blocked_operation("r_or_pnl_computation"),
            blocked_operation("market_backtest_run"),
            blocked_operation("execution"),
        ],
        "real_historical_data_loading_allowed": False,
        "historical_data_loading_allowed": False,
        "market_file_access_allowed": False,
        "market_row_parsing_allowed": False,
        "outcome_data_window_materialization_allowed": False,
        "market_backtest_allowed": False,
        "build_split_manifest_allowed": False,
        "observe_offline_allowed": False,
        "detector_execution_allowed": False,
        "label_computation_allowed": False,
        "outcome_computation_allowed": False,
        "pnl_computation_allowed": False,
        **SAFETY_FLAGS,
    }


def component_contract() -> dict[str, Any]:
    return {
        "component": "scn002_false_breakout_read_only_outcome_data_window_scaffold",
        "implemented": True,
        "implemented_mode": IMPLEMENTED_MODE,
        "version": VERSION,
        "scenario_id": SCENARIO_ID,
        "allowed_now": "read explicit reviewed SCN-002 outcome-label scaffold artifacts and write non-trading data-window boundary scaffold rows only",
        "outputs": [WINDOW_ROWS_FILE_NAME, SUMMARY_FILE_NAME],
        "future_window_contract_id": WINDOW_CONTRACT_ID,
        "future_window_allowed_states": FUTURE_WINDOW_STATES,
        "blocked_operations": [
            blocked_operation("real_historical_data_loading"),
            blocked_operation("market_file_access"),
            blocked_operation("market_row_parsing"),
            blocked_operation("detector_execution_on_market_history"),
            blocked_operation("actual_outcome_label_computation"),
            blocked_operation("tp_sl_outcome_calculation"),
            blocked_operation("r_or_pnl_computation"),
            blocked_operation("market_backtest_run"),
            blocked_operation("execution"),
        ],
        "real_historical_data_loading_allowed": False,
        "historical_data_loading_allowed": False,
        "market_file_access_allowed": False,
        "market_row_parsing_allowed": False,
        "outcome_data_window_materialization_allowed": False,
        "market_backtest_allowed": False,
        "build_split_manifest_allowed": False,
        "observe_offline_allowed": False,
        "detector_execution_allowed": False,
        "label_computation_allowed": False,
        "outcome_computation_allowed": False,
        "pnl_computation_allowed": False,
        **SAFETY_FLAGS,
    }
