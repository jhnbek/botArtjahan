from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..safety import SAFETY_FLAGS, blocked_operation
from ..scn002_false_breakout_fixtures import SCENARIO_ID
from .scn002_false_breakout_backtest import (
    PREFLIGHT_ROWS_FILE_NAME,
    SUMMARY_FILE_NAME as PREFLIGHT_SUMMARY_FILE_NAME,
    VERSION as PREFLIGHT_VERSION,
    validate_scn002_backtest_preflight,
)


VERSION = "scn002_false_breakout_read_only_outcome_label_scaffold_implementation_v1"
IMPLEMENTED_MODE = "scn002_reviewed_preflight_read_only_outcome_label_scaffold"
SCAFFOLD_ROWS_FILE_NAME = "outcome_label_scaffold_rows.jsonl"
SUMMARY_FILE_NAME = "summary.json"
TAXONOMY_CONTRACT_ID = "SCN002-OL-TAX-001"
FUTURE_TAXONOMY_STATES = [
    "unresolved",
    "no_data",
    "invalid_window",
    "tp_before_sl",
    "sl_before_tp",
    "both_same_bar_ambiguous",
    "timeout",
]

MARKET_FILE_SUFFIXES = {".csv", ".parquet", ".feather", ".h5", ".hdf", ".duckdb", ".zip", ".gz", ".tar"}
FORBIDDEN_COMPUTED_RESULT_FIELDS = {
    "balance",
    "balances",
    "expectancy",
    "fill",
    "fills",
    "future_return",
    "future_returns",
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
        errors.append("outcome_label_source_preflight_rows_missing")
        return rows
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            errors.append(f"outcome_label_source_preflight_jsonl_parse_error:{line_number}")
            continue
        if not isinstance(row, dict):
            errors.append(f"outcome_label_source_preflight_row_not_object:{line_number}")
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


def _preflight_input_errors(root: Path) -> list[str]:
    errors: list[str] = []
    if root.is_file():
        errors.append("preflight_dir_must_be_directory")
    if root.suffix.lower() in MARKET_FILE_SUFFIXES:
        errors.append("market_file_input_rejected")
    if not root.exists():
        errors.append("preflight_dir_missing")
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


def _candidate_state(preflight_row: dict[str, Any]) -> str:
    preflight_state = str(preflight_row.get("preflight_state") or "")
    if preflight_state == "eligible_observation_queue":
        return "candidate_waiting_for_separate_data_gate"
    if preflight_state == "manual_review_queue":
        return "manual_review_preserved_non_scored"
    if preflight_state == "hard_reject_queue":
        return "hard_reject_preserved_non_scored"
    return "invalid_preflight_state"


def _non_scored_reason(preflight_row: dict[str, Any]) -> str | None:
    preflight_state = str(preflight_row.get("preflight_state") or "")
    if preflight_state == "manual_review_queue":
        return "manual_review_required_before_any_future_scoring"
    if preflight_state == "hard_reject_queue":
        return "hard_reject_or_source_error_preserved"
    if preflight_state == "eligible_observation_queue":
        return None
    return "invalid_preflight_state"


def _scaffold_row(preflight_row: dict[str, Any], row_index: int) -> dict[str, Any]:
    candidate_state = _candidate_state(preflight_row)
    eligible = candidate_state == "candidate_waiting_for_separate_data_gate"
    source_accepted = preflight_row.get("source_observation_accepted") is True
    return {
        "result_type": "scn002_read_only_outcome_label_scaffold",
        "scenario_id": SCENARIO_ID,
        "source_preflight_row_index": row_index + 1,
        "source_preflight_source_row_index": preflight_row.get("source_row_index"),
        "case_id": preflight_row.get("case_id"),
        "observation_id": preflight_row.get("observation_id"),
        "source_observation_accepted": source_accepted,
        "source_preflight_state": preflight_row.get("preflight_state"),
        "source_chronological_split_role": preflight_row.get("chronological_split_role"),
        "candidate_state": candidate_state,
        "eligible_for_future_data_window": eligible,
        "non_scored_reason": _non_scored_reason(preflight_row),
        "future_taxonomy_contract_id": TAXONOMY_CONTRACT_ID,
        "future_taxonomy_allowed_states": FUTURE_TAXONOMY_STATES if eligible else [],
        "symbol": preflight_row.get("symbol") if source_accepted else None,
        "decision_timestamp_utc": preflight_row.get("decision_timestamp_utc") if source_accepted else None,
        "manual_review_needed": preflight_row.get("manual_review_needed") is True if source_accepted else False,
        "hard_reject_reasons": preflight_row.get("hard_reject_reasons") if isinstance(preflight_row.get("hard_reject_reasons"), list) else [],
        "error_codes": preflight_row.get("error_codes") if isinstance(preflight_row.get("error_codes"), list) else [],
        "source_rule_ids": preflight_row.get("source_rule_ids") if isinstance(preflight_row.get("source_rule_ids"), list) else [],
        "source_candidate_ids": preflight_row.get("source_candidate_ids") if isinstance(preflight_row.get("source_candidate_ids"), list) else [],
        "source_artifact_versions": preflight_row.get("source_artifact_versions") if isinstance(preflight_row.get("source_artifact_versions"), dict) else {},
        "source_detector_status_counts": preflight_row.get("source_detector_status_counts") if isinstance(preflight_row.get("source_detector_status_counts"), dict) else {},
        "source_preflight_generated_by": preflight_row.get("generated_by"),
        "generated_by": VERSION,
        "non_trading_output": True,
        "market_data_loaded": False,
        "detector_reexecuted": False,
        "label_computed": False,
        "outcome_computed": False,
        "pnl_computed": False,
        "future_data_gate_required": eligible,
    }


def _scaffold_counts(scaffold_rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "scaffold_row_count": len(scaffold_rows),
        "candidate_count": sum(
            1 for row in scaffold_rows if row.get("candidate_state") == "candidate_waiting_for_separate_data_gate"
        ),
        "manual_non_scored_count": sum(
            1 for row in scaffold_rows if row.get("candidate_state") == "manual_review_preserved_non_scored"
        ),
        "hard_reject_non_scored_count": sum(
            1 for row in scaffold_rows if row.get("candidate_state") == "hard_reject_preserved_non_scored"
        ),
    }


def prepare_scn002_outcome_label_scaffold(preflight_dir: Path, out_dir: Path | None = None) -> dict[str, Any]:
    root = preflight_dir.resolve()
    input_errors = _preflight_input_errors(root)
    validation_payload: dict[str, Any] = {}
    source_rows: list[dict[str, Any]] = []
    source_summary: dict[str, Any] = {}
    errors = list(input_errors)

    if not input_errors:
        validation_payload = validate_scn002_backtest_preflight(root)
        if validation_payload.get("all_preflight_valid") is not True:
            errors.append("source_preflight_not_valid")
        errors.extend(str(error) for error in validation_payload.get("validation_errors") or [])
        source_rows = _read_jsonl(root / PREFLIGHT_ROWS_FILE_NAME, errors)
        try:
            loaded_summary = _read_json(root / PREFLIGHT_SUMMARY_FILE_NAME)
        except FileNotFoundError:
            loaded_summary = {}
            errors.append("source_preflight_summary_missing")
        except json.JSONDecodeError:
            loaded_summary = {}
            errors.append("source_preflight_summary_parse_error")
        source_summary = loaded_summary if isinstance(loaded_summary, dict) else {}
        if not isinstance(loaded_summary, dict):
            errors.append("source_preflight_summary_not_object")

    scaffold_rows = [_scaffold_row(row, row_index) for row_index, row in enumerate(source_rows) if not errors]
    for row in scaffold_rows:
        _scan_forbidden_computed_fields(row, errors)

    counts = _scaffold_counts(scaffold_rows)
    scaffold_ready = bool(scaffold_rows) and not errors
    outputs: dict[str, str] = {}
    if out_dir is not None:
        output_dir = out_dir.resolve()
        _write_jsonl(output_dir / SCAFFOLD_ROWS_FILE_NAME, scaffold_rows)
        outputs = {
            "scaffold_rows": (output_dir / SCAFFOLD_ROWS_FILE_NAME).as_posix(),
            "summary": (output_dir / SUMMARY_FILE_NAME).as_posix(),
        }

    source_rows_path = root / PREFLIGHT_ROWS_FILE_NAME
    payload = {
        "component": "scn002_false_breakout_read_only_outcome_label_scaffold",
        "implemented": True,
        "implemented_mode": IMPLEMENTED_MODE,
        "version": VERSION,
        "scenario_id": SCENARIO_ID,
        "preflight_dir": root.as_posix(),
        "source_preflight_version": source_summary.get("version"),
        "source_preflight_rows_sha256": _sha256_file(source_rows_path) if source_rows_path.exists() else "",
        "source_preflight_validation_passed": validation_payload.get("all_preflight_valid") is True,
        "source_preflight_row_count": int(validation_payload.get("preflight_row_count") or 0),
        "source_preflight_accepted_count": int(validation_payload.get("preflight_accepted_count") or 0),
        "source_preflight_rejected_count": int(validation_payload.get("preflight_rejected_count") or 0),
        "source_preflight_manual_review_count": int(validation_payload.get("preflight_manual_review_count") or 0),
        "source_preflight_eligible_observation_count": int(validation_payload.get("preflight_eligible_observation_count") or 0),
        "source_preflight_hard_reject_count": int(validation_payload.get("preflight_hard_reject_count") or 0),
        **counts,
        "future_taxonomy_contract_id": TAXONOMY_CONTRACT_ID,
        "future_taxonomy_allowed_states": FUTURE_TAXONOMY_STATES,
        "scaffold_ready": scaffold_ready,
        "scaffold_errors": sorted(set(errors)),
        "scaffold_error_count": len(set(errors)),
        "scaffold_rows": scaffold_rows,
        "outputs": outputs,
        "real_historical_data_loading_allowed": False,
        "historical_data_loading_allowed": False,
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
        summary_payload = {key: value for key, value in payload.items() if key != "scaffold_rows"}
        _write_json(out_dir.resolve() / SUMMARY_FILE_NAME, summary_payload)
    return payload


def _validate_scaffold_row(row: dict[str, Any], errors: list[str], row_index: int) -> None:
    required_fields = [
        "result_type",
        "scenario_id",
        "source_preflight_row_index",
        "case_id",
        "observation_id",
        "source_observation_accepted",
        "source_preflight_state",
        "source_chronological_split_role",
        "candidate_state",
        "eligible_for_future_data_window",
        "future_taxonomy_contract_id",
        "source_preflight_generated_by",
        "generated_by",
        "non_trading_output",
        "market_data_loaded",
        "detector_reexecuted",
        "label_computed",
        "outcome_computed",
        "pnl_computed",
    ]
    for field in required_fields:
        if field not in row:
            errors.append(f"outcome_label_scaffold_row_missing_field:{row_index}:{field}")
    if row.get("result_type") != "scn002_read_only_outcome_label_scaffold":
        errors.append(f"outcome_label_scaffold_result_type_mismatch:{row_index}")
    if row.get("scenario_id") != SCENARIO_ID:
        errors.append(f"outcome_label_scaffold_scenario_id_mismatch:{row_index}")
    if row.get("generated_by") != VERSION:
        errors.append(f"outcome_label_scaffold_generated_by_mismatch:{row_index}")
    if row.get("source_preflight_generated_by") != PREFLIGHT_VERSION:
        errors.append(f"outcome_label_scaffold_source_generated_by_mismatch:{row_index}")
    if row.get("non_trading_output") is not True:
        errors.append(f"outcome_label_scaffold_non_trading_output_missing:{row_index}")
    for field in [
        "market_data_loaded",
        "detector_reexecuted",
        "label_computed",
        "outcome_computed",
        "pnl_computed",
    ]:
        if row.get(field) is not False:
            errors.append(f"outcome_label_scaffold_unsafe_flag_not_false:{row_index}:{field}")
    if row.get("future_taxonomy_contract_id") != TAXONOMY_CONTRACT_ID:
        errors.append(f"outcome_label_scaffold_taxonomy_contract_mismatch:{row_index}")

    candidate_state = row.get("candidate_state")
    source_state = row.get("source_preflight_state")
    if source_state == "eligible_observation_queue":
        if candidate_state != "candidate_waiting_for_separate_data_gate":
            errors.append(f"outcome_label_scaffold_eligible_candidate_state_mismatch:{row_index}")
        if row.get("eligible_for_future_data_window") is not True:
            errors.append(f"outcome_label_scaffold_eligible_not_marked:{row_index}")
        if row.get("source_chronological_split_role") not in {"train_preflight", "test_preflight"}:
            errors.append(f"outcome_label_scaffold_eligible_split_role_invalid:{row_index}")
        for field in ["symbol", "decision_timestamp_utc", "source_rule_ids", "source_candidate_ids", "source_artifact_versions"]:
            if not row.get(field):
                errors.append(f"outcome_label_scaffold_eligible_missing_trace_field:{row_index}:{field}")
    elif source_state == "manual_review_queue":
        if candidate_state != "manual_review_preserved_non_scored":
            errors.append(f"outcome_label_scaffold_manual_candidate_state_mismatch:{row_index}")
        if row.get("eligible_for_future_data_window") is not False:
            errors.append(f"outcome_label_scaffold_manual_eligible_not_false:{row_index}")
        if row.get("manual_review_needed") is not True:
            errors.append(f"outcome_label_scaffold_manual_review_not_preserved:{row_index}")
    elif source_state == "hard_reject_queue":
        if candidate_state != "hard_reject_preserved_non_scored":
            errors.append(f"outcome_label_scaffold_hard_reject_candidate_state_mismatch:{row_index}")
        if row.get("eligible_for_future_data_window") is not False:
            errors.append(f"outcome_label_scaffold_hard_reject_eligible_not_false:{row_index}")
        if not row.get("error_codes"):
            errors.append(f"outcome_label_scaffold_hard_reject_missing_error_codes:{row_index}")
    else:
        errors.append(f"outcome_label_scaffold_source_state_invalid:{row_index}")


def validate_scn002_outcome_label_scaffold(scaffold_dir: Path) -> dict[str, Any]:
    root = scaffold_dir.resolve()
    errors: list[str] = []
    if not root.exists() or not root.is_dir():
        errors.append("outcome_label_scaffold_dir_missing")
        rows: list[dict[str, Any]] = []
        summary: dict[str, Any] = {}
    else:
        try:
            loaded_summary = _read_json(root / SUMMARY_FILE_NAME)
        except FileNotFoundError:
            loaded_summary = {}
            errors.append("outcome_label_scaffold_summary_missing")
        except json.JSONDecodeError:
            loaded_summary = {}
            errors.append("outcome_label_scaffold_summary_parse_error")
        summary = loaded_summary if isinstance(loaded_summary, dict) else {}
        if not isinstance(loaded_summary, dict):
            errors.append("outcome_label_scaffold_summary_not_object")
        rows = _read_jsonl(root / SCAFFOLD_ROWS_FILE_NAME, errors)

    for row_index, row in enumerate(rows, start=1):
        _scan_forbidden_computed_fields(row, errors)
        _validate_scaffold_row(row, errors, row_index)

    counts = _scaffold_counts(rows)
    source_state_counts: dict[str, int] = {}
    for row in rows:
        source_state = str(row.get("source_preflight_state") or "missing")
        source_state_counts[source_state] = source_state_counts.get(source_state, 0) + 1

    if summary:
        if summary.get("component") != "scn002_false_breakout_read_only_outcome_label_scaffold":
            errors.append("outcome_label_scaffold_summary_component_mismatch")
        if summary.get("version") != VERSION:
            errors.append("outcome_label_scaffold_summary_version_mismatch")
        for field, actual in counts.items():
            if int(summary.get(field) or -1) != actual:
                errors.append(f"outcome_label_scaffold_summary_{field}_mismatch")
        if summary.get("scaffold_ready") is not True:
            errors.append("outcome_label_scaffold_summary_ready_not_true")
        for field in [
            "real_historical_data_loading_allowed",
            "historical_data_loading_allowed",
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
                errors.append(f"outcome_label_scaffold_summary_unsafe_flag_not_false:{field}")

    validation_passed = bool(rows) and not errors
    return {
        "component": "scn002_false_breakout_read_only_outcome_label_scaffold_validator",
        "implemented": True,
        "implemented_mode": IMPLEMENTED_MODE,
        "version": VERSION,
        "scenario_id": SCENARIO_ID,
        "scaffold_dir": root.as_posix(),
        **counts,
        "source_state_counts": dict(sorted(source_state_counts.items())),
        "validation_errors": sorted(set(errors)),
        "validation_error_count": len(set(errors)),
        "all_scaffold_valid": validation_passed,
        "real_historical_data_loading_allowed": False,
        "historical_data_loading_allowed": False,
        "market_backtest_allowed": False,
        "detector_execution_allowed": False,
        "label_computation_allowed": False,
        "outcome_computation_allowed": False,
        "pnl_computation_allowed": False,
        "old_external_crypto_stats_used": False,
        **SAFETY_FLAGS,
    }


def component_contract() -> dict[str, Any]:
    return {
        "component": "scn002_false_breakout_read_only_outcome_label_scaffold",
        "implemented": True,
        "implemented_mode": IMPLEMENTED_MODE,
        "version": VERSION,
        "scenario_id": SCENARIO_ID,
        "allowed_now": "read explicit reviewed SCN-002 preflight artifacts and write non-trading candidate/non-scored scaffold rows only",
        "outputs": [SCAFFOLD_ROWS_FILE_NAME, SUMMARY_FILE_NAME],
        "future_taxonomy_contract_id": TAXONOMY_CONTRACT_ID,
        "future_taxonomy_allowed_states": FUTURE_TAXONOMY_STATES,
        "blocked_operations": [
            blocked_operation("real_historical_data_loading"),
            blocked_operation("market_history_detector_execution"),
            blocked_operation("actual_outcome_label_computation"),
            blocked_operation("tp_sl_outcome_calculation"),
            blocked_operation("r_or_pnl_computation"),
            blocked_operation("market_backtest_run"),
            blocked_operation("execution"),
        ],
        "real_historical_data_loading_allowed": False,
        "historical_data_loading_allowed": False,
        "market_backtest_allowed": False,
        "build_split_manifest_allowed": False,
        "observe_offline_allowed": False,
        "detector_execution_allowed": False,
        "label_computation_allowed": False,
        "outcome_computation_allowed": False,
        "pnl_computation_allowed": False,
        **SAFETY_FLAGS,
    }
