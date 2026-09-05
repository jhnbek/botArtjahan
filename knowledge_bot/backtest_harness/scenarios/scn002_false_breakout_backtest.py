from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..safety import SAFETY_FLAGS, blocked_operation
from ..scn002_false_breakout_fixtures import FORBIDDEN_OUTCOME_FIELDS, SCENARIO_ID
from .scn002_false_breakout_observation import (
    RESULTS_FILE_NAME as OBSERVATION_RESULTS_FILE_NAME,
    SUMMARY_FILE_NAME as OBSERVATION_SUMMARY_FILE_NAME,
    VERSION as OBSERVATION_VERSION,
    validate_scn002_observation_results,
)


VERSION = "scn002_false_breakout_read_only_backtest_preflight_implementation_v1"
IMPLEMENTED_MODE = "scn002_reviewed_observation_read_only_preflight"
PREFLIGHT_ROWS_FILE_NAME = "preflight_rows.jsonl"
SUMMARY_FILE_NAME = "summary.json"
SPLIT_RULE_ID = "SCN002-PREFLIGHT-SPLIT-001"
TRAIN_FRACTION = 0.8

MARKET_FILE_SUFFIXES = {".csv", ".parquet", ".feather", ".h5", ".hdf", ".duckdb", ".zip", ".gz", ".tar"}
FORBIDDEN_PREFLIGHT_FIELDS = FORBIDDEN_OUTCOME_FIELDS | {
    "entry_signal",
    "exit_signal",
    "market_order",
    "order_request",
    "performance_metric",
    "runtime_signal",
    "signal",
    "signals",
    "strategy_performance",
    "trade_signal",
    "winrate",
    "expectancy",
    "r_multiple",
}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_jsonl(path: Path, errors: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        errors.append("preflight_source_observation_results_missing")
        return rows
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            errors.append(f"preflight_source_observation_jsonl_parse_error:{line_number}")
            continue
        if not isinstance(row, dict):
            errors.append(f"preflight_source_observation_row_not_object:{line_number}")
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


def _observation_input_errors(root: Path) -> list[str]:
    errors: list[str] = []
    if root.is_file():
        errors.append("observations_dir_must_be_directory")
    if root.suffix.lower() in MARKET_FILE_SUFFIXES:
        errors.append("market_file_input_rejected")
    if not root.exists():
        errors.append("observations_dir_missing")
    return errors


def _scan_forbidden_preflight_fields(value: Any, errors: list[str]) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if str(key).lower() in FORBIDDEN_PREFLIGHT_FIELDS:
                errors.append("forbidden_preflight_field")
            _scan_forbidden_preflight_fields(nested, errors)
    elif isinstance(value, list):
        for item in value:
            _scan_forbidden_preflight_fields(item, errors)


def _accepted_sort_key(item: tuple[int, dict[str, Any]]) -> tuple[str, str, str, int]:
    row_index, row = item
    return (
        str(row.get("decision_timestamp_utc") or ""),
        str(row.get("observation_id") or ""),
        str(row.get("case_id") or ""),
        row_index,
    )


def _split_roles(rows: list[dict[str, Any]]) -> dict[int, str]:
    accepted = sorted(
        (
            (index, row)
            for index, row in enumerate(rows)
            if row.get("accepted") is True
            and row.get("manual_review_needed") is not True
            and not row.get("hard_reject_reasons")
        ),
        key=_accepted_sort_key,
    )
    if not accepted:
        return {}
    if len(accepted) == 1:
        train_count = 1
    else:
        train_count = max(1, int(len(accepted) * TRAIN_FRACTION))
        train_count = min(train_count, len(accepted) - 1)
    roles: dict[int, str] = {}
    for position, (row_index, _row) in enumerate(accepted):
        roles[row_index] = "train_preflight" if position < train_count else "test_preflight"
    return roles


def _preflight_state(row: dict[str, Any]) -> str:
    if row.get("accepted") is not True:
        return "hard_reject_queue"
    hard_reject_reasons = row.get("hard_reject_reasons") if isinstance(row.get("hard_reject_reasons"), list) else []
    if hard_reject_reasons:
        return "hard_reject_queue"
    if row.get("manual_review_needed") is True:
        return "manual_review_queue"
    return "eligible_observation_queue"


def _preflight_row(row: dict[str, Any], row_index: int, split_role: str) -> dict[str, Any]:
    accepted = row.get("accepted") is True
    return {
        "result_type": "scn002_read_only_backtest_preflight",
        "scenario_id": SCENARIO_ID,
        "source_row_index": row_index + 1,
        "case_id": row.get("case_id"),
        "observation_id": row.get("observation_id"),
        "source_observation_accepted": accepted,
        "preflight_state": _preflight_state(row),
        "chronological_split_role": split_role if accepted else "not_in_split",
        "split_rule_id": SPLIT_RULE_ID,
        "symbol": row.get("symbol") if accepted else None,
        "decision_timestamp_utc": row.get("decision_timestamp_utc") if accepted else None,
        "manual_review_needed": row.get("manual_review_needed") is True if accepted else False,
        "hard_reject_reasons": row.get("hard_reject_reasons") if isinstance(row.get("hard_reject_reasons"), list) else [],
        "error_codes": row.get("error_codes") if isinstance(row.get("error_codes"), list) else [],
        "source_rule_ids": row.get("source_rule_ids") if isinstance(row.get("source_rule_ids"), list) else [],
        "source_candidate_ids": row.get("source_candidate_ids") if isinstance(row.get("source_candidate_ids"), list) else [],
        "source_artifact_versions": row.get("artifact_versions") if isinstance(row.get("artifact_versions"), dict) else {},
        "source_detector_status_counts": row.get("detector_status_counts") if isinstance(row.get("detector_status_counts"), dict) else {},
        "source_observation_path": row.get("observation_path"),
        "source_observation_generated_by": row.get("generated_by"),
        "generated_by": VERSION,
        "non_trading_output": True,
        "no_label_or_scoring_gate": True,
        "market_data_loaded": False,
        "detector_reexecuted": False,
    }


def _split_summary(preflight_rows: list[dict[str, Any]]) -> dict[str, Any]:
    split_counts: dict[str, int] = {}
    state_counts: dict[str, int] = {}
    for row in preflight_rows:
        split_role = str(row.get("chronological_split_role") or "missing")
        split_counts[split_role] = split_counts.get(split_role, 0) + 1
        state = str(row.get("preflight_state") or "missing")
        state_counts[state] = state_counts.get(state, 0) + 1
    return {
        "split_rule_id": SPLIT_RULE_ID,
        "split_rule": "accepted observation rows sorted by decision_timestamp_utc, observation_id, case_id, source_row_index; first 80 percent train, remainder test; rejected rows excluded from split",
        "train_fraction": TRAIN_FRACTION,
        "split_counts": dict(sorted(split_counts.items())),
        "preflight_state_counts": dict(sorted(state_counts.items())),
    }


def prepare_scn002_backtest_preflight(observations_dir: Path, out_dir: Path | None = None) -> dict[str, Any]:
    root = observations_dir.resolve()
    input_errors = _observation_input_errors(root)
    validation_payload: dict[str, Any] = {}
    source_rows: list[dict[str, Any]] = []
    source_summary: dict[str, Any] = {}
    errors = list(input_errors)

    if not input_errors:
        validation_payload = validate_scn002_observation_results(root)
        if validation_payload.get("all_results_valid") is not True:
            errors.append("source_observation_results_not_valid")
        errors.extend(str(error) for error in validation_payload.get("validation_errors") or [])
        source_rows = _read_jsonl(root / OBSERVATION_RESULTS_FILE_NAME, errors)
        try:
            loaded_summary = _read_json(root / OBSERVATION_SUMMARY_FILE_NAME)
        except FileNotFoundError:
            loaded_summary = {}
            errors.append("source_observation_summary_missing")
        except json.JSONDecodeError:
            loaded_summary = {}
            errors.append("source_observation_summary_parse_error")
        source_summary = loaded_summary if isinstance(loaded_summary, dict) else {}
        if not isinstance(loaded_summary, dict):
            errors.append("source_observation_summary_not_object")

    split_roles = _split_roles(source_rows) if not errors else {}
    preflight_rows = [
        _preflight_row(row, row_index, split_roles.get(row_index, "not_in_split"))
        for row_index, row in enumerate(source_rows)
        if not errors
    ]

    for row in preflight_rows:
        _scan_forbidden_preflight_fields(row, errors)

    accepted_count = sum(1 for row in preflight_rows if row.get("source_observation_accepted") is True)
    rejected_count = sum(1 for row in preflight_rows if row.get("source_observation_accepted") is False)
    manual_review_count = sum(1 for row in preflight_rows if row.get("preflight_state") == "manual_review_queue")
    eligible_count = sum(1 for row in preflight_rows if row.get("preflight_state") == "eligible_observation_queue")
    hard_reject_count = sum(1 for row in preflight_rows if row.get("preflight_state") == "hard_reject_queue")
    preflight_ready = bool(preflight_rows) and not errors

    outputs: dict[str, str] = {}
    if out_dir is not None:
        output_dir = out_dir.resolve()
        _write_jsonl(output_dir / PREFLIGHT_ROWS_FILE_NAME, preflight_rows)
        outputs = {
            "preflight_rows": (output_dir / PREFLIGHT_ROWS_FILE_NAME).as_posix(),
            "summary": (output_dir / SUMMARY_FILE_NAME).as_posix(),
        }

    source_results_path = root / OBSERVATION_RESULTS_FILE_NAME
    payload = {
        "component": "scn002_false_breakout_read_only_backtest_preflight",
        "implemented": True,
        "implemented_mode": IMPLEMENTED_MODE,
        "version": VERSION,
        "scenario_id": SCENARIO_ID,
        "observations_dir": root.as_posix(),
        "source_observation_version": source_summary.get("version"),
        "source_observation_results_sha256": _sha256_file(source_results_path) if source_results_path.exists() else "",
        "source_observation_result_validation_passed": validation_payload.get("all_results_valid") is True,
        "source_observation_result_count": int(validation_payload.get("result_count") or 0),
        "source_observation_accepted_count": int(validation_payload.get("accepted_count") or 0),
        "source_observation_rejected_count": int(validation_payload.get("rejected_count") or 0),
        "preflight_row_count": len(preflight_rows),
        "preflight_accepted_count": accepted_count,
        "preflight_rejected_count": rejected_count,
        "preflight_manual_review_count": manual_review_count,
        "preflight_eligible_observation_count": eligible_count,
        "preflight_hard_reject_count": hard_reject_count,
        "preflight_ready": preflight_ready,
        "preflight_errors": sorted(set(errors)),
        "preflight_error_count": len(set(errors)),
        "split_summary": _split_summary(preflight_rows),
        "preflight_rows": preflight_rows,
        "outputs": outputs,
        "real_historical_data_loading_allowed": False,
        "market_backtest_allowed": False,
        "build_split_manifest_allowed": False,
        "observe_offline_allowed": False,
        "detector_execution_allowed": False,
        "pnl_computation_allowed": False,
        "old_external_crypto_stats_used": False,
        **SAFETY_FLAGS,
    }
    if out_dir is not None:
        summary_payload = {key: value for key, value in payload.items() if key != "preflight_rows"}
        _write_json(out_dir.resolve() / SUMMARY_FILE_NAME, summary_payload)
    return payload


def _validate_preflight_row(row: dict[str, Any], errors: list[str], row_index: int) -> None:
    required_fields = [
        "result_type",
        "scenario_id",
        "source_row_index",
        "case_id",
        "observation_id",
        "source_observation_accepted",
        "preflight_state",
        "chronological_split_role",
        "split_rule_id",
        "source_observation_generated_by",
        "generated_by",
        "non_trading_output",
        "market_data_loaded",
        "detector_reexecuted",
    ]
    for field in required_fields:
        if field not in row:
            errors.append(f"preflight_row_missing_field:{row_index}:{field}")
    if row.get("result_type") != "scn002_read_only_backtest_preflight":
        errors.append(f"preflight_result_type_mismatch:{row_index}")
    if row.get("scenario_id") != SCENARIO_ID:
        errors.append(f"preflight_scenario_id_mismatch:{row_index}")
    if row.get("generated_by") != VERSION:
        errors.append(f"preflight_generated_by_mismatch:{row_index}")
    if row.get("source_observation_generated_by") != OBSERVATION_VERSION:
        errors.append(f"preflight_source_generated_by_mismatch:{row_index}")
    if row.get("non_trading_output") is not True:
        errors.append(f"preflight_non_trading_output_missing:{row_index}")
    if row.get("market_data_loaded") is not False:
        errors.append(f"preflight_market_data_loaded_not_false:{row_index}")
    if row.get("detector_reexecuted") is not False:
        errors.append(f"preflight_detector_reexecuted_not_false:{row_index}")
    if row.get("split_rule_id") != SPLIT_RULE_ID:
        errors.append(f"preflight_split_rule_id_mismatch:{row_index}")
    if row.get("source_observation_accepted") is True:
        if row.get("preflight_state") == "eligible_observation_queue":
            if row.get("chronological_split_role") not in {"train_preflight", "test_preflight"}:
                errors.append(f"preflight_eligible_split_role_invalid:{row_index}")
        elif row.get("chronological_split_role") != "not_in_split":
            errors.append(f"preflight_non_eligible_split_role_invalid:{row_index}")
        for field in ["symbol", "decision_timestamp_utc", "source_rule_ids", "source_candidate_ids", "source_artifact_versions"]:
            if not row.get(field):
                errors.append(f"preflight_accepted_missing_trace_field:{row_index}:{field}")
    elif row.get("source_observation_accepted") is False:
        if row.get("chronological_split_role") != "not_in_split":
            errors.append(f"preflight_rejected_split_role_invalid:{row_index}")
        if not row.get("error_codes"):
            errors.append(f"preflight_rejected_missing_error_codes:{row_index}")
    else:
        errors.append(f"preflight_source_accepted_flag_invalid:{row_index}")
    if row.get("preflight_state") == "manual_review_queue" and row.get("manual_review_needed") is not True:
        errors.append(f"preflight_manual_review_not_preserved:{row_index}")


def validate_scn002_backtest_preflight(preflight_dir: Path) -> dict[str, Any]:
    root = preflight_dir.resolve()
    errors: list[str] = []
    if not root.exists() or not root.is_dir():
        errors.append("preflight_dir_missing")
        rows: list[dict[str, Any]] = []
        summary: dict[str, Any] = {}
    else:
        try:
            loaded_summary = _read_json(root / SUMMARY_FILE_NAME)
        except FileNotFoundError:
            loaded_summary = {}
            errors.append("preflight_summary_missing")
        except json.JSONDecodeError:
            loaded_summary = {}
            errors.append("preflight_summary_parse_error")
        summary = loaded_summary if isinstance(loaded_summary, dict) else {}
        if not isinstance(loaded_summary, dict):
            errors.append("preflight_summary_not_object")
        rows = _read_jsonl(root / PREFLIGHT_ROWS_FILE_NAME, errors)

    for row_index, row in enumerate(rows, start=1):
        _scan_forbidden_preflight_fields(row, errors)
        _validate_preflight_row(row, errors, row_index)

    accepted_count = sum(1 for row in rows if row.get("source_observation_accepted") is True)
    rejected_count = sum(1 for row in rows if row.get("source_observation_accepted") is False)
    manual_review_count = sum(1 for row in rows if row.get("preflight_state") == "manual_review_queue")
    eligible_count = sum(1 for row in rows if row.get("preflight_state") == "eligible_observation_queue")
    hard_reject_count = sum(1 for row in rows if row.get("preflight_state") == "hard_reject_queue")
    split_counts: dict[str, int] = {}
    for row in rows:
        role = str(row.get("chronological_split_role") or "missing")
        split_counts[role] = split_counts.get(role, 0) + 1

    if summary:
        if summary.get("component") != "scn002_false_breakout_read_only_backtest_preflight":
            errors.append("preflight_summary_component_mismatch")
        if summary.get("version") != VERSION:
            errors.append("preflight_summary_version_mismatch")
        if int(summary.get("preflight_row_count") or -1) != len(rows):
            errors.append("preflight_summary_row_count_mismatch")
        if int(summary.get("preflight_accepted_count") or -1) != accepted_count:
            errors.append("preflight_summary_accepted_count_mismatch")
        if int(summary.get("preflight_rejected_count") or -1) != rejected_count:
            errors.append("preflight_summary_rejected_count_mismatch")
        if int(summary.get("preflight_manual_review_count") or -1) != manual_review_count:
            errors.append("preflight_summary_manual_review_count_mismatch")
        if summary.get("preflight_ready") is not True:
            errors.append("preflight_summary_ready_not_true")
        for field in [
            "real_historical_data_loading_allowed",
            "market_backtest_allowed",
            "build_split_manifest_allowed",
            "observe_offline_allowed",
            "detector_execution_allowed",
            "pnl_computation_allowed",
            "execution_allowed",
            "runtime_signal_allowed",
            "paper_trading_allowed",
            "live_trading_allowed",
            "backtest_harness_allowed",
        ]:
            if summary.get(field) is not False:
                errors.append(f"preflight_summary_unsafe_flag_not_false:{field}")

    validation_passed = bool(rows) and not errors
    return {
        "component": "scn002_false_breakout_read_only_backtest_preflight_validator",
        "implemented": True,
        "implemented_mode": IMPLEMENTED_MODE,
        "version": VERSION,
        "scenario_id": SCENARIO_ID,
        "preflight_dir": root.as_posix(),
        "preflight_row_count": len(rows),
        "preflight_accepted_count": accepted_count,
        "preflight_rejected_count": rejected_count,
        "preflight_manual_review_count": manual_review_count,
        "preflight_eligible_observation_count": eligible_count,
        "preflight_hard_reject_count": hard_reject_count,
        "split_counts": dict(sorted(split_counts.items())),
        "validation_errors": sorted(set(errors)),
        "validation_error_count": len(set(errors)),
        "all_preflight_valid": validation_passed,
        "real_historical_data_loading_allowed": False,
        "market_backtest_allowed": False,
        "detector_execution_allowed": False,
        "pnl_computation_allowed": False,
        "old_external_crypto_stats_used": False,
        **SAFETY_FLAGS,
    }


def component_contract() -> dict[str, Any]:
    return {
        "component": "scn002_false_breakout_read_only_backtest_preflight",
        "implemented": True,
        "implemented_mode": IMPLEMENTED_MODE,
        "version": VERSION,
        "scenario_id": SCENARIO_ID,
        "allowed_now": "read explicit reviewed SCN-002 detector-observation result artifacts and write non-trading preflight rows only",
        "outputs": [PREFLIGHT_ROWS_FILE_NAME, SUMMARY_FILE_NAME],
        "blocked_operations": [
            blocked_operation("real_historical_data_loading"),
            blocked_operation("market_history_detector_execution"),
            blocked_operation("outcome_labeling"),
            blocked_operation("pnl_computation"),
            blocked_operation("market_backtest_run"),
            blocked_operation("execution"),
        ],
        "real_historical_data_loading_allowed": False,
        "market_backtest_allowed": False,
        "build_split_manifest_allowed": False,
        "observe_offline_allowed": False,
        "detector_execution_allowed": False,
        "pnl_computation_allowed": False,
        **SAFETY_FLAGS,
    }
