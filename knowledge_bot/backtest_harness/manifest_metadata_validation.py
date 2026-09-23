from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .manifest_inventory import validate_manifest_inventory_document
from .safety import SAFETY_FLAGS, blocked_operation


def default_manifest_metadata_validation_fixture_dir() -> Path:
    return Path(__file__).resolve().parent / "fixtures" / "manifest_metadata_validation"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _add_error(errors: list[str], code: str) -> None:
    if code not in errors:
        errors.append(code)


def _path_parts(value: str) -> list[str]:
    return [part for part in re.split(r"[\\/]+", value) if part]


def _validate_manifest_input_path(value: Any, errors: list[str]) -> bool:
    if not isinstance(value, str) or not value.strip():
        _add_error(errors, "manifest_input_invalid")
        return False
    lowered = value.lower()
    safe_to_open = True
    if lowered.startswith(("http://", "https://", "ftp://", "s3://", "file://")):
        _add_error(errors, "manifest_input_url")
        safe_to_open = False
    if any(char in value for char in ["*", "?", "["]):
        _add_error(errors, "manifest_input_wildcard")
        safe_to_open = False
    if value.endswith(("/", "\\")):
        _add_error(errors, "manifest_input_directory")
        safe_to_open = False
    if re.match(r"^[A-Za-z]:[\\/]", value) or Path(value).is_absolute():
        _add_error(errors, "manifest_input_absolute")
        safe_to_open = False
    if ".." in _path_parts(value):
        _add_error(errors, "manifest_input_parent_traversal")
        safe_to_open = False
    if Path(value).suffix.lower() != ".json" and safe_to_open:
        _add_error(errors, "manifest_input_not_json")
        safe_to_open = False
    return safe_to_open


def _validate_real_manifest_input_path(value: Any, errors: list[str]) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        _add_error(errors, "manifest_input_invalid")
        return None
    lowered = value.lower()
    safe_to_open = True
    if lowered.startswith(("http://", "https://", "ftp://", "s3://", "file://")):
        _add_error(errors, "manifest_input_url")
        safe_to_open = False
    if any(char in value for char in ["*", "?", "["]):
        _add_error(errors, "manifest_input_wildcard")
        safe_to_open = False
    if value.endswith(("/", "\\")):
        _add_error(errors, "manifest_input_directory")
        safe_to_open = False
    if ".." in _path_parts(value):
        _add_error(errors, "manifest_input_parent_traversal")
        safe_to_open = False

    path = Path(value)
    if path.suffix.lower() != ".json" and safe_to_open:
        _add_error(errors, "manifest_input_not_json")
        safe_to_open = False

    try:
        resolved = path.resolve(strict=False)
    except OSError:
        _add_error(errors, "manifest_input_unresolvable")
        return None

    if resolved.anchor and str(resolved) == resolved.anchor:
        _add_error(errors, "manifest_input_root")
        safe_to_open = False
    if resolved.exists() and resolved.is_dir():
        _add_error(errors, "manifest_input_directory")
        safe_to_open = False

    return resolved if safe_to_open else None


def _manifest_summary(manifest: Any) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        return {
            "manifest_id": "",
            "dataset_id": "",
            "inventory_profile": "",
            "listed_file_count": 0,
        }
    files = manifest.get("files")
    return {
        "manifest_id": str(manifest.get("manifest_id") or ""),
        "dataset_id": str(manifest.get("dataset_id") or ""),
        "inventory_profile": str(manifest.get("inventory_profile") or ""),
        "listed_file_count": len(files) if isinstance(files, list) else 0,
    }


def validate_real_manifest_metadata_document(manifest_values: list[str]) -> dict[str, Any]:
    errors: list[str] = []
    manifest_document_open_attempt_count = 0
    manifest: Any = {}
    manifest_path: Path | None = None

    if len(manifest_values) != 1:
        _add_error(errors, "manifest_input_count_not_one")
    else:
        manifest_path = _validate_real_manifest_input_path(manifest_values[0], errors)

    if manifest_path is not None:
        manifest_document_open_attempt_count += 1
        if not manifest_path.exists():
            _add_error(errors, "manifest_path_missing")
        elif not manifest_path.is_file():
            _add_error(errors, "manifest_input_not_file")
        else:
            try:
                manifest = _read_json(manifest_path)
            except json.JSONDecodeError:
                _add_error(errors, "manifest_parse_error")

    if manifest_path is not None and "manifest_parse_error" not in errors and "manifest_path_missing" not in errors:
        errors.extend(code for code in validate_manifest_inventory_document(manifest) if code not in errors)

    error_codes = sorted(errors)
    valid = not error_codes
    summary = _manifest_summary(manifest)
    return {
        "component": "real_manifest_metadata_validation",
        "implemented": True,
        "implemented_mode": "one_explicit_manifest_metadata_only",
        "metadata_only": True,
        "manifest_input_count": len(manifest_values),
        "manifest_input": manifest_values[0] if len(manifest_values) == 1 else "",
        "manifest_path": manifest_path.as_posix() if manifest_path is not None else "",
        "manifest_id": summary["manifest_id"],
        "dataset_id": summary["dataset_id"],
        "inventory_profile": summary["inventory_profile"],
        "listed_file_count": summary["listed_file_count"],
        "valid": valid,
        "error_codes": error_codes,
        "accepted_manifest_count": 1 if valid else 0,
        "rejected_manifest_count": 0 if valid else 1,
        "real_manifest_metadata_validation_ready": valid,
        "real_manifest_metadata_validation_command_ready": True,
        "real_manifest_metadata_validation_allowed": False,
        "real_manifest_metadata_validation_execution_allowed": False,
        "real_manifest_inventory_execution_allowed": False,
        "source_directory_scanning_allowed": False,
        "listed_file_hashing_allowed": False,
        "listed_file_size_probe_allowed": False,
        "listed_market_file_open_allowed": False,
        "market_row_parsing_allowed": False,
        "real_historical_data_loading_allowed": False,
        "build_split_manifest_allowed": False,
        "observe_offline_allowed": False,
        "detector_execution_allowed": False,
        "pnl_computation_allowed": False,
        "manifest_document_open_attempt_count": manifest_document_open_attempt_count,
        "listed_market_file_open_attempt_count": 0,
        "listed_file_hash_attempt_count": 0,
        "listed_file_size_probe_attempt_count": 0,
        "market_row_parse_attempt_count": 0,
        **SAFETY_FLAGS,
    }


def real_manifest_metadata_capability_scan() -> dict[str, Any]:
    return {
        "component": "real_manifest_metadata_validation_capability_scan",
        "source_directory_scanning_allowed": False,
        "listed_market_file_open_allowed": False,
        "listed_file_hashing_allowed": False,
        "listed_file_size_probe_allowed": False,
        "market_row_parsing_allowed": False,
        "real_historical_data_loading_allowed": False,
        "detector_execution_allowed": False,
        "pnl_computation_allowed": False,
        "build_split_manifest_allowed": False,
        "observe_offline_allowed": False,
        "passed": True,
        **SAFETY_FLAGS,
    }


def real_manifest_metadata_safety_counters(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "manifest_document_open_attempt_count": int(payload.get("manifest_document_open_attempt_count") or 0),
        "listed_market_file_open_attempt_count": int(payload.get("listed_market_file_open_attempt_count") or 0),
        "listed_file_hash_attempt_count": int(payload.get("listed_file_hash_attempt_count") or 0),
        "listed_file_size_probe_attempt_count": int(payload.get("listed_file_size_probe_attempt_count") or 0),
        "market_row_parse_attempt_count": int(payload.get("market_row_parse_attempt_count") or 0),
    }


def write_real_manifest_metadata_validation_outputs(out_dir: Path, payload: dict[str, Any]) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    status = {
        "real_manifest_metadata_validation_ready": payload.get("real_manifest_metadata_validation_ready") is True,
        "accepted_manifest_count": int(payload.get("accepted_manifest_count") or 0),
        "rejected_manifest_count": int(payload.get("rejected_manifest_count") or 0),
        "real_manifest_metadata_validation_allowed": False,
        "real_manifest_metadata_validation_execution_allowed": False,
        **SAFETY_FLAGS,
    }
    results = {
        "manifest_path": str(payload.get("manifest_path") or ""),
        "manifest_id": str(payload.get("manifest_id") or ""),
        "dataset_id": str(payload.get("dataset_id") or ""),
        "inventory_profile": str(payload.get("inventory_profile") or ""),
        "valid": payload.get("valid") is True,
        "error_codes": list(payload.get("error_codes") or []),
        "metadata_only": True,
    }
    counters = real_manifest_metadata_safety_counters(payload)
    capability_scan = real_manifest_metadata_capability_scan()
    review_packet = [
        "# Real Manifest Metadata Validation Review Packet",
        "",
        f"- Metadata validation ready: `{str(status['real_manifest_metadata_validation_ready']).lower()}`",
        f"- Accepted manifests: {status['accepted_manifest_count']}",
        f"- Rejected manifests: {status['rejected_manifest_count']}",
        f"- Manifest path: `{results['manifest_path']}`",
        f"- Manifest ID: `{results['manifest_id']}`",
        f"- Dataset ID: `{results['dataset_id']}`",
        f"- Inventory profile: `{results['inventory_profile']}`",
        f"- Error codes: {', '.join(results['error_codes']) if results['error_codes'] else '-'}",
        f"- Manifest document opens: {counters['manifest_document_open_attempt_count']}",
        f"- Listed market file opens: {counters['listed_market_file_open_attempt_count']}",
        f"- Listed file hashes: {counters['listed_file_hash_attempt_count']}",
        f"- Listed file size probes: {counters['listed_file_size_probe_attempt_count']}",
        f"- Market row parses: {counters['market_row_parse_attempt_count']}",
        "",
        "Listed market files were not opened, hashed, size-probed, extracted, or parsed. Real loading, split generation, detector execution, PnL, backtest, and execution remain blocked.",
        "",
    ]

    paths = {
        "status": (out_dir / "real_manifest_metadata_validation_status.json").as_posix(),
        "results": (out_dir / "real_manifest_metadata_validation_results.json").as_posix(),
        "safety_counters": (out_dir / "real_manifest_metadata_validation_safety_counters.json").as_posix(),
        "capability_scan": (out_dir / "real_manifest_metadata_validation_capability_scan.json").as_posix(),
        "review_packet": (out_dir / "real_manifest_metadata_validation_review_packet.md").as_posix(),
    }
    (out_dir / "real_manifest_metadata_validation_status.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "real_manifest_metadata_validation_results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "real_manifest_metadata_validation_safety_counters.json").write_text(
        json.dumps(counters, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "real_manifest_metadata_validation_capability_scan.json").write_text(
        json.dumps(capability_scan, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "real_manifest_metadata_validation_review_packet.md").write_text(
        "\n".join(review_packet),
        encoding="utf-8",
    )
    return paths


def _validate_case(case_dir: Path) -> dict[str, Any]:
    metadata_path = case_dir / "case.json"
    errors: list[str] = []
    manifest_document_open_attempt_count = 0
    try:
        metadata = _read_json(metadata_path)
    except json.JSONDecodeError:
        metadata = {}
        _add_error(errors, "case_metadata_parse_error")

    if not isinstance(metadata, dict):
        metadata = {}
        _add_error(errors, "case_metadata_not_object")

    manifest_file = metadata.get("manifest_file", "manifest.json")
    safe_to_open_manifest = _validate_manifest_input_path(manifest_file, errors)
    manifest_path = case_dir / str(manifest_file)
    manifest: Any = {}
    if safe_to_open_manifest:
        manifest_document_open_attempt_count += 1
        if not manifest_path.exists():
            _add_error(errors, "manifest_path_missing")
        else:
            try:
                manifest = _read_json(manifest_path)
            except json.JSONDecodeError:
                _add_error(errors, "manifest_parse_error")

    if safe_to_open_manifest and "manifest_parse_error" not in errors and "manifest_path_missing" not in errors:
        errors.extend(code for code in validate_manifest_inventory_document(manifest) if code not in errors)

    expected_error_codes = metadata.get("expected_error_codes") if isinstance(metadata.get("expected_error_codes"), list) else []
    expected_errors = sorted(str(code) for code in expected_error_codes)
    actual_errors = sorted(errors)
    expected_valid = metadata.get("expected_valid") is True
    actual_valid = not actual_errors
    expected_listed_file_open_attempt_count = int(metadata.get("expected_listed_market_file_open_attempt_count") or 0)
    expected_listed_file_hash_attempt_count = int(metadata.get("expected_listed_file_hash_attempt_count") or 0)
    expected_listed_file_size_probe_attempt_count = int(metadata.get("expected_listed_file_size_probe_attempt_count") or 0)
    passed = (
        actual_valid == expected_valid
        and actual_errors == expected_errors
        and expected_listed_file_open_attempt_count == 0
        and expected_listed_file_hash_attempt_count == 0
        and expected_listed_file_size_probe_attempt_count == 0
    )

    return {
        "case_id": str(metadata.get("case_id") or case_dir.name),
        "expected_valid": expected_valid,
        "actual_valid": actual_valid,
        "expected_error_codes": expected_errors,
        "actual_error_codes": actual_errors,
        "passed": passed,
        "manifest_path": manifest_path.as_posix(),
        "manifest_document_open_attempt_count": manifest_document_open_attempt_count,
        "listed_market_file_open_attempt_count": 0,
        "listed_file_hash_attempt_count": 0,
        "listed_file_size_probe_attempt_count": 0,
        "manifest_id": str(manifest.get("manifest_id") or "") if isinstance(manifest, dict) else "",
        "listed_file_count": len(manifest.get("files") or []) if isinstance(manifest, dict) and isinstance(manifest.get("files"), list) else 0,
    }


def validate_manifest_metadata_fixture_directory(fixtures_dir: Path | None = None) -> dict[str, Any]:
    root = (fixtures_dir or default_manifest_metadata_validation_fixture_dir()).resolve()
    case_dirs = sorted(path for path in root.iterdir() if path.is_dir() and (path / "case.json").exists()) if root.exists() else []
    cases = [_validate_case(case_dir) for case_dir in case_dirs]
    passed_count = sum(1 for case in cases if case["passed"])
    return {
        "component": "manifest_metadata_validation",
        "implemented": True,
        "implemented_mode": "fixture_first_manifest_metadata_validation",
        "fixture_first_manifest_metadata_validation_allowed": True,
        "real_manifest_metadata_validation_allowed": False,
        "real_manifest_inventory_execution_allowed": False,
        "source_directory_scanning_allowed": False,
        "listed_file_hashing_allowed": False,
        "listed_file_size_probe_allowed": False,
        "listed_market_file_open_allowed": False,
        "market_row_parsing_allowed": False,
        "real_historical_data_loading_allowed": False,
        "build_split_manifest_allowed": False,
        "observe_offline_allowed": False,
        "detector_execution_allowed": False,
        "pnl_computation_allowed": False,
        "fixtures_dir": root.as_posix(),
        "case_count": len(cases),
        "passed_count": passed_count,
        "failed_count": len(cases) - passed_count,
        "all_cases_passed": bool(cases) and passed_count == len(cases),
        "manifest_document_open_attempt_count": sum(case["manifest_document_open_attempt_count"] for case in cases),
        "listed_market_file_open_attempt_count": 0,
        "listed_file_hash_attempt_count": 0,
        "listed_file_size_probe_attempt_count": 0,
        "cases": cases,
        **SAFETY_FLAGS,
    }


def validate_manifest_metadata_execution_fixture_directory(fixtures_dir: Path | None = None) -> dict[str, Any]:
    fixture_payload = validate_manifest_metadata_fixture_directory(fixtures_dir)
    cases = list(fixture_payload.get("cases") or [])
    accepted_manifest_count = sum(1 for case in cases if case.get("actual_valid") is True)
    rejected_manifest_count = len(cases) - accepted_manifest_count
    return {
        "component": "manifest_metadata_validation_execution",
        "implemented": True,
        "implemented_mode": "reviewed_real_manifest_metadata_validation_execution_path_fixture_mode",
        "metadata_only": True,
        "fixture_first_manifest_metadata_validation_execution_allowed": True,
        "ready_for_real_historical_data_manifest_metadata_validation_execution": False,
        "real_manifest_metadata_validation_allowed": False,
        "real_manifest_metadata_validation_execution_allowed": False,
        "real_manifest_inventory_execution_allowed": False,
        "source_directory_scanning_allowed": False,
        "listed_file_hashing_allowed": False,
        "listed_file_size_probe_allowed": False,
        "listed_market_file_open_allowed": False,
        "market_row_parsing_allowed": False,
        "real_historical_data_loading_allowed": False,
        "build_split_manifest_allowed": False,
        "observe_offline_allowed": False,
        "detector_execution_allowed": False,
        "pnl_computation_allowed": False,
        "fixtures_dir": str(fixture_payload.get("fixtures_dir") or ""),
        "manifest_count": len(cases),
        "accepted_manifest_count": accepted_manifest_count,
        "rejected_manifest_count": rejected_manifest_count,
        "case_count": int(fixture_payload.get("case_count") or 0),
        "passed_count": int(fixture_payload.get("passed_count") or 0),
        "failed_count": int(fixture_payload.get("failed_count") or 0),
        "all_cases_passed": fixture_payload.get("all_cases_passed") is True,
        "manifest_document_open_attempt_count": int(
            fixture_payload.get("manifest_document_open_attempt_count") or 0
        ),
        "listed_market_file_open_attempt_count": 0,
        "listed_file_hash_attempt_count": 0,
        "listed_file_size_probe_attempt_count": 0,
        "market_row_parse_attempt_count": 0,
        "cases": cases,
        **SAFETY_FLAGS,
    }


def component_contract() -> dict[str, Any]:
    return {
        "component": "manifest_metadata_validation",
        "implemented": True,
        "implemented_mode": "fixture_first_manifest_metadata_validation_with_one_explicit_manifest_command",
        "allowed_now": "validate synthetic manifest metadata fixtures and one explicit manifest JSON document metadata only",
        "blocked_operations": [
            blocked_operation("real_manifest_inventory_execution"),
            blocked_operation("source_directory_scanning"),
            blocked_operation("listed_market_file_open"),
            blocked_operation("listed_file_hashing"),
            blocked_operation("listed_file_size_probe"),
            blocked_operation("market_row_parsing"),
            blocked_operation("historical_data_loading"),
            blocked_operation("detector_execution"),
            blocked_operation("pnl_computation"),
        ],
        "fixture_first_manifest_metadata_validation_allowed": True,
        "fixture_first_manifest_metadata_validation_execution_allowed": True,
        "one_explicit_real_manifest_metadata_validation_command_allowed": True,
        "real_manifest_metadata_validation_command_ready": True,
        "ready_for_real_historical_data_manifest_metadata_validation_execution": False,
        "real_manifest_metadata_validation_allowed": False,
        "real_manifest_metadata_validation_execution_allowed": False,
        "real_manifest_inventory_execution_allowed": False,
        "source_directory_scanning_allowed": False,
        "listed_market_file_open_allowed": False,
        "listed_file_hashing_allowed": False,
        "listed_file_size_probe_allowed": False,
        "market_row_parsing_allowed": False,
        **SAFETY_FLAGS,
    }
