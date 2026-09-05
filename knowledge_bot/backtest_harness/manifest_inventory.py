from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .safety import SAFETY_FLAGS, blocked_operation


REQUIRED_MANIFEST_FIELDS = [
    "manifest_id",
    "dataset_id",
    "inventory_profile",
    "instrument_type",
    "symbols",
    "timeframes",
    "data_vendor",
    "dataset_version",
    "exported_at_utc",
    "source_format",
    "price_adjustment_policy",
    "session_calendar_id",
    "timezone_policy",
    "volume_unit",
    "gap_policy",
    "corporate_actions_policy",
    "futures_roll_policy",
    "license_or_source_notes",
    "expected_file_count",
    "files",
]

REQUIRED_FILE_ENTRY_FIELDS = [
    "relative_path",
    "symbol",
    "timeframe",
    "file_role",
    "expected_rows",
    "expected_sha256",
    "expected_size_bytes",
]

ALLOWED_SOURCE_FORMATS = {"csv", "jsonl", "parquet", "archive_manifest"}
ALLOWED_FILE_ROLES = {"bars", "metadata", "archive_member_index", "supplemental_manifest"}
ALLOWED_VOLUME_UNITS = {"base_asset", "quote_asset", "contracts", "tick_volume", None}

FORBIDDEN_SURFACE_FIELDS = {
    "label",
    "labels",
    "outcome",
    "future_return",
    "future_returns",
    "win_loss",
    "pnl",
    "order",
    "orders",
    "fill",
    "fills",
    "balance",
    "balances",
    "position",
    "positions",
    "detector",
    "detector_output",
    "execution",
    "runtime_signal",
}


def default_manifest_inventory_fixture_dir() -> Path:
    return Path(__file__).resolve().parent / "fixtures" / "manifest_inventory"


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


def _path_parts(value: str) -> list[str]:
    return [part for part in re.split(r"[\\/]+", value) if part]


def _validate_safe_relative_path(value: Any, error_prefix: str, errors: list[str]) -> None:
    if not isinstance(value, str) or not value.strip():
        _add_error(errors, f"{error_prefix}_invalid")
        return
    lowered = value.lower()
    if lowered.startswith(("http://", "https://", "ftp://", "s3://")):
        _add_error(errors, f"{error_prefix}_url")
    if any(char in value for char in ["*", "?", "["]):
        _add_error(errors, f"{error_prefix}_wildcard")
    if re.match(r"^[A-Za-z]:[\\/]", value):
        _add_error(errors, f"{error_prefix}_absolute")
    if Path(value).is_absolute():
        _add_error(errors, f"{error_prefix}_absolute")
    if ".." in _path_parts(value):
        _add_error(errors, f"{error_prefix}_parent_traversal")


def _validate_str_field(manifest: dict[str, Any], field: str, errors: list[str]) -> None:
    if field in manifest and not isinstance(manifest.get(field), str):
        _add_error(errors, f"manifest_field_type:{field}")


def _validate_int_or_null(value: Any, field: str, errors: list[str]) -> None:
    if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
        _add_error(errors, f"file_entry_field_type:{field}")


def _scan_forbidden_fields(value: Any, errors: list[str]) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if str(key).lower() in FORBIDDEN_SURFACE_FIELDS:
                _add_error(errors, f"forbidden_manifest_field:{str(key).lower()}")
            _scan_forbidden_fields(nested, errors)
    elif isinstance(value, list):
        for item in value:
            _scan_forbidden_fields(item, errors)


def _validate_manifest_contract(manifest: dict[str, Any], errors: list[str]) -> None:
    for field in REQUIRED_MANIFEST_FIELDS:
        if field not in manifest:
            _add_error(errors, f"manifest_missing_field:{field}")

    for field in [
        "manifest_id",
        "dataset_id",
        "inventory_profile",
        "instrument_type",
        "data_vendor",
        "dataset_version",
        "source_format",
        "price_adjustment_policy",
        "session_calendar_id",
        "timezone_policy",
        "gap_policy",
        "corporate_actions_policy",
        "futures_roll_policy",
        "license_or_source_notes",
    ]:
        _validate_str_field(manifest, field, errors)

    for field in ["symbols", "timeframes"]:
        value = manifest.get(field)
        if field in manifest and (not isinstance(value, list) or not value or not all(isinstance(item, str) and item for item in value)):
            _add_error(errors, f"manifest_field_type:{field}")

    if "exported_at_utc" in manifest and _parse_timestamp(manifest.get("exported_at_utc")) is None:
        _add_error(errors, "manifest_field_type:exported_at_utc")

    if "source_format" in manifest and manifest.get("source_format") not in ALLOWED_SOURCE_FORMATS:
        _add_error(errors, "source_format_not_allowed")

    if "volume_unit" in manifest and manifest.get("volume_unit") not in ALLOWED_VOLUME_UNITS:
        _add_error(errors, "volume_unit_not_allowed")

    expected_file_count = manifest.get("expected_file_count")
    if "expected_file_count" in manifest and (not isinstance(expected_file_count, int) or isinstance(expected_file_count, bool) or expected_file_count < 0):
        _add_error(errors, "manifest_field_type:expected_file_count")

    files = manifest.get("files")
    if "files" in manifest and not isinstance(files, list):
        _add_error(errors, "manifest_field_type:files")
    elif isinstance(files, list) and isinstance(expected_file_count, int) and expected_file_count != len(files):
        _add_error(errors, "expected_file_count_mismatch")


def _validate_crypto_spot_profile(manifest: dict[str, Any], errors: list[str]) -> None:
    if manifest.get("inventory_profile") != "crypto_spot_24_7_utc_manifest":
        _add_error(errors, "inventory_profile_not_allowed")
    if manifest.get("instrument_type") != "crypto_spot":
        _add_error(errors, "instrument_type_not_crypto_spot")
    if manifest.get("session_calendar_id") != "crypto_24_7_utc":
        _add_error(errors, "session_calendar_not_crypto_24_7_utc")
    if manifest.get("timezone_policy") != "UTC":
        _add_error(errors, "timezone_policy_not_utc")
    if manifest.get("price_adjustment_policy") != "exchange_raw_unadjusted":
        _add_error(errors, "price_adjustment_policy_not_exchange_raw_unadjusted")
    if manifest.get("corporate_actions_policy") != "null_not_applicable":
        _add_error(errors, "corporate_actions_policy_not_null_not_applicable")
    if manifest.get("futures_roll_policy") != "null_not_applicable":
        _add_error(errors, "futures_roll_policy_not_null_not_applicable")


def _validate_file_entries(manifest: dict[str, Any], errors: list[str]) -> None:
    files = manifest.get("files")
    if not isinstance(files, list):
        return
    symbols = set(manifest.get("symbols") if isinstance(manifest.get("symbols"), list) else [])
    timeframes = set(manifest.get("timeframes") if isinstance(manifest.get("timeframes"), list) else [])
    for index, entry in enumerate(files, start=1):
        if not isinstance(entry, dict):
            _add_error(errors, f"file_entry_not_object:{index}")
            continue
        for field in REQUIRED_FILE_ENTRY_FIELDS:
            if field not in entry:
                _add_error(errors, f"file_entry_missing_field:{field}")

        _validate_safe_relative_path(entry.get("relative_path"), "file_entry_path", errors)

        if "symbol" in entry and not isinstance(entry.get("symbol"), str):
            _add_error(errors, "file_entry_field_type:symbol")
        elif "symbol" in entry and symbols and entry.get("symbol") not in symbols and entry.get("symbol") != "multi_symbol":
            _add_error(errors, "file_entry_symbol_not_declared")

        if "timeframe" in entry and not isinstance(entry.get("timeframe"), str):
            _add_error(errors, "file_entry_field_type:timeframe")
        elif "timeframe" in entry and timeframes and entry.get("timeframe") not in timeframes:
            _add_error(errors, "file_entry_timeframe_not_declared")

        if "file_role" in entry and entry.get("file_role") not in ALLOWED_FILE_ROLES:
            _add_error(errors, "file_role_not_allowed")

        _validate_int_or_null(entry.get("expected_rows"), "expected_rows", errors)
        _validate_int_or_null(entry.get("expected_size_bytes"), "expected_size_bytes", errors)
        if "expected_sha256" in entry and entry.get("expected_sha256") is not None and not isinstance(entry.get("expected_sha256"), str):
            _add_error(errors, "file_entry_field_type:expected_sha256")


def validate_manifest_inventory_document(manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(manifest, dict):
        return ["manifest_not_object"]
    _scan_forbidden_fields(manifest, errors)
    _validate_manifest_contract(manifest, errors)
    _validate_crypto_spot_profile(manifest, errors)
    _validate_file_entries(manifest, errors)
    return sorted(errors)


def _validate_case(case_dir: Path) -> dict[str, Any]:
    metadata_path = case_dir / "case.json"
    errors: list[str] = []
    try:
        metadata = _read_json(metadata_path)
    except json.JSONDecodeError:
        metadata = {}
        _add_error(errors, "case_metadata_parse_error")

    if not isinstance(metadata, dict):
        metadata = {}
        _add_error(errors, "case_metadata_not_object")

    manifest_file = metadata.get("manifest_file", "manifest.json")
    _validate_safe_relative_path(manifest_file, "manifest_path", errors)
    manifest_path = case_dir / str(manifest_file)
    if not manifest_path.exists():
        _add_error(errors, "manifest_path_missing")
        manifest = {}
    else:
        try:
            manifest = _read_json(manifest_path)
        except json.JSONDecodeError:
            manifest = {}
            _add_error(errors, "manifest_parse_error")

    errors.extend(code for code in validate_manifest_inventory_document(manifest) if code not in errors)

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
        "manifest_path": manifest_path.as_posix(),
        "manifest_id": str(manifest.get("manifest_id") or "") if isinstance(manifest, dict) else "",
        "listed_file_count": len(manifest.get("files") or []) if isinstance(manifest, dict) and isinstance(manifest.get("files"), list) else 0,
    }


def validate_manifest_inventory_fixture_directory(fixtures_dir: Path | None = None) -> dict[str, Any]:
    root = (fixtures_dir or default_manifest_inventory_fixture_dir()).resolve()
    case_dirs = sorted(path for path in root.iterdir() if path.is_dir() and (path / "case.json").exists()) if root.exists() else []
    cases = [_validate_case(case_dir) for case_dir in case_dirs]
    passed_count = sum(1 for case in cases if case["passed"])
    return {
        "component": "manifest_inventory",
        "implemented": True,
        "implemented_mode": "synthetic_fixture_only_manifest_validation",
        "synthetic_fixture_only_validation_allowed": True,
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
        "listed_market_file_open_attempt_count": 0,
        "listed_file_hash_attempt_count": 0,
        "cases": cases,
        **SAFETY_FLAGS,
    }


def component_contract() -> dict[str, Any]:
    return {
        "component": "manifest_inventory",
        "implemented": True,
        "implemented_mode": "synthetic_fixture_only_manifest_validation",
        "allowed_now": "validate tiny synthetic manifest metadata fixtures only",
        "blocked_operations": [
            blocked_operation("real_manifest_inventory_execution"),
            blocked_operation("source_directory_scanning"),
            blocked_operation("listed_file_hashing"),
            blocked_operation("market_row_parsing"),
            blocked_operation("historical_data_loading"),
            blocked_operation("detector_execution"),
            blocked_operation("pnl_computation"),
        ],
        "synthetic_fixture_only_validation_allowed": True,
        "real_manifest_inventory_execution_allowed": False,
        "source_directory_scanning_allowed": False,
        "listed_file_hashing_allowed": False,
        "market_row_parsing_allowed": False,
        **SAFETY_FLAGS,
    }
