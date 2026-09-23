from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..safety import SAFETY_FLAGS, blocked_operation
from ..scn002_false_breakout_fixtures import (
    DETECTOR_CHAIN,
    FORBIDDEN_OUTCOME_FIELDS,
    SCENARIO_ID,
    default_scn002_fixture_dir,
    validate_scn002_observation,
)


VERSION = "scn002_false_breakout_read_only_detector_observation_implementation_v1"
IMPLEMENTED_MODE = "scn002_fixture_only_read_only_detector_observation"
RESULTS_FILE_NAME = "observation_results.jsonl"
SUMMARY_FILE_NAME = "summary.json"

MARKET_FILE_SUFFIXES = {".csv", ".parquet", ".feather", ".h5", ".hdf", ".duckdb", ".zip", ".gz", ".tar"}
FORBIDDEN_RESULT_FIELDS = FORBIDDEN_OUTCOME_FIELDS | {
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
}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def _read_jsonl(path: Path, errors: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        errors.append("observation_results_file_missing")
        return rows
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            errors.append(f"observation_results_jsonl_parse_error:{line_number}")
            continue
        if not isinstance(row, dict):
            errors.append(f"observation_result_not_object:{line_number}")
            continue
        rows.append(row)
    return rows


def _path_is_safe_relative(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    lowered = value.lower()
    if lowered.startswith(("http://", "https://", "ftp://", "s3://")):
        return False
    candidate = Path(value)
    return not candidate.is_absolute() and ".." not in candidate.parts and not any(char in value for char in ["*", "?", "["])


def _scan_forbidden_result_fields(value: Any, errors: list[str]) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if str(key).lower() in FORBIDDEN_RESULT_FIELDS:
                errors.append("forbidden_result_field")
            _scan_forbidden_result_fields(nested, errors)
    elif isinstance(value, list):
        for item in value:
            _scan_forbidden_result_fields(item, errors)


def _fixture_root_errors(root: Path) -> list[str]:
    errors: list[str] = []
    if root.is_file():
        errors.append("fixtures_dir_must_be_directory")
    if root.suffix.lower() in MARKET_FILE_SUFFIXES:
        errors.append("market_file_input_rejected")
    if not root.exists():
        errors.append("fixtures_dir_missing")
    return errors


def _case_dirs(root: Path) -> list[Path]:
    if not root.exists() or not root.is_dir():
        return []
    return sorted(path for path in root.iterdir() if path.is_dir() and (path / "case.json").exists())


def _load_case_observation(case_dir: Path) -> tuple[dict[str, Any], dict[str, Any] | None, Path, list[str]]:
    errors: list[str] = []
    case_path = case_dir / "case.json"
    try:
        metadata = _read_json(case_path)
    except json.JSONDecodeError:
        metadata = {}
        errors.append("case_metadata_parse_error")
    if not isinstance(metadata, dict):
        metadata = {}
        errors.append("case_metadata_not_object")

    observation_file = metadata.get("observation_file", "observation.json")
    if not _path_is_safe_relative(observation_file):
        errors.append("observation_file_path_invalid")
        observation_path = case_dir / "observation.json"
    else:
        observation_path = case_dir / str(observation_file)

    observation: dict[str, Any] | None
    if not observation_path.exists():
        observation = None
        errors.append("observation_file_missing")
    else:
        try:
            loaded = _read_json(observation_path)
        except json.JSONDecodeError:
            loaded = None
            errors.append("observation_parse_error")
        if isinstance(loaded, dict):
            observation = loaded
        else:
            observation = None
            errors.append("observation_not_object")
    return metadata, observation, observation_path, errors


def _detector_status_counts(detector_results: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    if not isinstance(detector_results, list):
        return counts
    for row in detector_results:
        if not isinstance(row, dict):
            continue
        status = str(row.get("observation_status") or "missing")
        counts[status] = counts.get(status, 0) + 1
    return dict(sorted(counts.items()))


def _artifact_versions(observation: dict[str, Any]) -> dict[str, Any]:
    versions = observation.get("artifact_versions") if isinstance(observation.get("artifact_versions"), dict) else {}
    return {
        **versions,
        "scn002_observation_implementation": VERSION,
        "scn002_detector_chain": "reviewed_11_contract_chain",
    }


def _accepted_observation_row(case_id: str, observation_path: Path, observation: dict[str, Any]) -> dict[str, Any]:
    detector_results = observation.get("detector_results")
    return {
        "result_type": "scn002_detector_observation",
        "scenario_id": SCENARIO_ID,
        "case_id": case_id,
        "accepted": True,
        "observation_path": observation_path.as_posix(),
        "observation_id": observation.get("observation_id"),
        "symbol": observation.get("symbol"),
        "decision_timestamp_utc": observation.get("decision_timestamp_utc"),
        "level_snapshot": observation.get("level_snapshot"),
        "false_breakout_evidence": observation.get("false_breakout_evidence"),
        "detector_results": detector_results,
        "detector_status_counts": _detector_status_counts(detector_results),
        "hard_reject_reasons": observation.get("hard_reject_reasons"),
        "manual_review_needed": observation.get("manual_review_needed"),
        "source_rule_ids": observation.get("source_rule_ids"),
        "source_candidate_ids": observation.get("source_candidate_ids"),
        "artifact_versions": _artifact_versions(observation),
        "generated_by": VERSION,
        "non_trading_output": True,
    }


def _rejected_observation_row(case_id: str, observation_path: Path, errors: list[str], observation: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "result_type": "scn002_detector_observation_reject",
        "scenario_id": SCENARIO_ID,
        "case_id": case_id,
        "accepted": False,
        "observation_path": observation_path.as_posix(),
        "observation_id": str(observation.get("observation_id") or "") if isinstance(observation, dict) else "",
        "error_codes": sorted(set(errors)),
        "generated_by": VERSION,
        "non_trading_output": True,
    }


def observe_scn002_fixture_directory(fixtures_dir: Path | None = None, out_dir: Path | None = None) -> dict[str, Any]:
    root = (fixtures_dir or default_scn002_fixture_dir()).resolve()
    root_errors = _fixture_root_errors(root)
    case_dirs = _case_dirs(root)
    if not root_errors and not case_dirs:
        root_errors.append("fixture_cases_missing")

    result_rows: list[dict[str, Any]] = []
    for case_dir in case_dirs:
        metadata, observation, observation_path, case_errors = _load_case_observation(case_dir)
        case_id = str(metadata.get("case_id") or case_dir.name)
        observation_errors = validate_scn002_observation(observation) if isinstance(observation, dict) else []
        errors = sorted(set(case_errors + observation_errors))
        if errors:
            result_rows.append(_rejected_observation_row(case_id, observation_path, errors, observation))
        else:
            result_rows.append(_accepted_observation_row(case_id, observation_path, observation or {}))

    accepted_count = sum(1 for row in result_rows if row.get("accepted") is True)
    rejected_count = sum(1 for row in result_rows if row.get("accepted") is False)
    all_cases_observed = bool(case_dirs) and not root_errors and len(result_rows) == len(case_dirs)
    outputs: dict[str, str] = {}
    if out_dir is not None:
        output_dir = out_dir.resolve()
        _write_jsonl(output_dir / RESULTS_FILE_NAME, result_rows)
        outputs = {
            "observation_results": (output_dir / RESULTS_FILE_NAME).as_posix(),
            "summary": (output_dir / SUMMARY_FILE_NAME).as_posix(),
        }

    payload = {
        "component": "scn002_false_breakout_read_only_detector_observation",
        "implemented": True,
        "implemented_mode": IMPLEMENTED_MODE,
        "version": VERSION,
        "scenario_id": SCENARIO_ID,
        "fixtures_dir": root.as_posix(),
        "case_count": len(case_dirs),
        "result_count": len(result_rows),
        "accepted_count": accepted_count,
        "rejected_count": rejected_count,
        "fixture_root_errors": root_errors,
        "all_cases_observed": all_cases_observed,
        "observation_results": result_rows,
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
        summary_payload = {key: value for key, value in payload.items() if key != "observation_results"}
        _write_json(out_dir.resolve() / SUMMARY_FILE_NAME, summary_payload)
    return payload


def _validate_accepted_result(row: dict[str, Any], errors: list[str], row_index: int) -> None:
    required_fields = [
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
    for field in required_fields:
        if field not in row:
            errors.append(f"accepted_result_missing_field:{row_index}:{field}")
    actual_chain = [
        (detector.get("sequence"), detector.get("contract_id"), detector.get("detector_key"))
        for detector in row.get("detector_results") or []
        if isinstance(detector, dict)
    ]
    if actual_chain != DETECTOR_CHAIN:
        errors.append(f"accepted_result_detector_chain_mismatch:{row_index}")
    if row.get("error_codes"):
        errors.append(f"accepted_result_has_error_codes:{row_index}")


def _validate_rejected_result(row: dict[str, Any], errors: list[str], row_index: int) -> None:
    error_codes = row.get("error_codes")
    if not isinstance(error_codes, list) or not error_codes:
        errors.append(f"rejected_result_missing_error_codes:{row_index}")


def validate_scn002_observation_results(results_dir: Path) -> dict[str, Any]:
    root = results_dir.resolve()
    errors: list[str] = []
    if not root.exists() or not root.is_dir():
        errors.append("results_dir_missing")
        rows: list[dict[str, Any]] = []
        summary: dict[str, Any] = {}
    else:
        try:
            summary_loaded = _read_json(root / SUMMARY_FILE_NAME)
        except FileNotFoundError:
            summary_loaded = {}
            errors.append("summary_file_missing")
        except json.JSONDecodeError:
            summary_loaded = {}
            errors.append("summary_parse_error")
        summary = summary_loaded if isinstance(summary_loaded, dict) else {}
        if not isinstance(summary_loaded, dict):
            errors.append("summary_not_object")
        rows = _read_jsonl(root / RESULTS_FILE_NAME, errors)

    for row_index, row in enumerate(rows, start=1):
        _scan_forbidden_result_fields(row, errors)
        if row.get("scenario_id") != SCENARIO_ID:
            errors.append(f"scenario_id_mismatch:{row_index}")
        if row.get("generated_by") != VERSION:
            errors.append(f"generated_by_mismatch:{row_index}")
        if row.get("non_trading_output") is not True:
            errors.append(f"non_trading_output_missing:{row_index}")
        if row.get("accepted") is True:
            _validate_accepted_result(row, errors, row_index)
        elif row.get("accepted") is False:
            _validate_rejected_result(row, errors, row_index)
        else:
            errors.append(f"accepted_flag_invalid:{row_index}")

    accepted_count = sum(1 for row in rows if row.get("accepted") is True)
    rejected_count = sum(1 for row in rows if row.get("accepted") is False)
    if summary:
        if int(summary.get("result_count") or -1) != len(rows):
            errors.append("summary_result_count_mismatch")
        if int(summary.get("accepted_count") or -1) != accepted_count:
            errors.append("summary_accepted_count_mismatch")
        if int(summary.get("rejected_count") or -1) != rejected_count:
            errors.append("summary_rejected_count_mismatch")
        if summary.get("all_cases_observed") is not True:
            errors.append("summary_all_cases_observed_not_true")

    validation_passed = bool(rows) and not errors
    return {
        "component": "scn002_false_breakout_read_only_detector_observation_result_validator",
        "implemented": True,
        "implemented_mode": IMPLEMENTED_MODE,
        "version": VERSION,
        "scenario_id": SCENARIO_ID,
        "results_dir": root.as_posix(),
        "result_count": len(rows),
        "accepted_count": accepted_count,
        "rejected_count": rejected_count,
        "validation_errors": sorted(set(errors)),
        "validation_error_count": len(set(errors)),
        "all_results_valid": validation_passed,
        "real_historical_data_loading_allowed": False,
        "market_backtest_allowed": False,
        "detector_execution_allowed": False,
        "pnl_computation_allowed": False,
        "old_external_crypto_stats_used": False,
        **SAFETY_FLAGS,
    }


def component_contract() -> dict[str, Any]:
    return {
        "component": "scn002_false_breakout_read_only_detector_observation",
        "implemented": True,
        "implemented_mode": IMPLEMENTED_MODE,
        "version": VERSION,
        "scenario_id": SCENARIO_ID,
        "allowed_now": "read explicit synthetic/curated SCN-002 fixture JSON documents and write non-trading observation rows only",
        "outputs": [RESULTS_FILE_NAME, SUMMARY_FILE_NAME],
        "blocked_operations": [
            blocked_operation("real_historical_data_loading"),
            blocked_operation("market_history_detector_execution"),
            blocked_operation("pnl_computation"),
            blocked_operation("market_backtest_run"),
            blocked_operation("execution"),
        ],
        "real_historical_data_loading_allowed": False,
        "market_backtest_allowed": False,
        "detector_execution_allowed": False,
        "pnl_computation_allowed": False,
        "observe_offline_allowed": False,
        **SAFETY_FLAGS,
    }
