from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .safety import SAFETY_FLAGS, blocked_operation


ALLOWED_INSTRUMENT_TYPES = {"stock", "crypto_spot", "crypto_perp", "futures", "fx"}
ALLOWED_FIXTURE_TIMEFRAMES = {"D1", "H1", "M15"}

REQUIRED_MANIFEST_FIELDS = [
    "case_id",
    "expected_valid",
    "expected_error_codes",
    "instrument_type",
    "timeframe",
    "price_adjustment_policy",
    "session_calendar_id",
    "data_vendor",
    "dataset_version",
    "source_path",
    "row_count",
    "file_sha256",
    "decision_timestamp_utc",
]

REQUIRED_ROW_FIELDS = [
    "symbol",
    "timeframe",
    "open_time_utc",
    "close_time_utc",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "data_source_id",
    "ingested_at_utc",
]

FORBIDDEN_ROW_FIELDS = {
    "label",
    "labels",
    "outcome",
    "future_return",
    "win_loss",
    "pnl",
    "order",
    "fill",
    "balance",
    "position",
}


def default_fixture_dir() -> Path:
    return Path(__file__).resolve().parent / "fixtures" / "historical_data"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def _source_path_allowed(source_path: Any) -> bool:
    if not isinstance(source_path, str) or not source_path:
        return False
    lowered = source_path.lower()
    if lowered.startswith(("http://", "https://", "ftp://", "s3://")):
        return False
    candidate = Path(source_path)
    if candidate.is_absolute() or ".." in candidate.parts:
        return False
    return True


def _add_error(errors: list[str], code: str) -> None:
    if code not in errors:
        errors.append(code)


def _read_jsonl_rows(path: Path, errors: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        _add_error(errors, "source_path_missing")
        return rows
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            _add_error(errors, f"jsonl_parse_error:{line_number}")
            continue
        if not isinstance(row, dict):
            _add_error(errors, f"row_not_object:{line_number}")
            continue
        rows.append(row)
    return rows


def _validate_manifest(manifest: dict[str, Any], source_file: Path | None, source_hash: str | None, errors: list[str]) -> None:
    for field in REQUIRED_MANIFEST_FIELDS:
        if field not in manifest:
            _add_error(errors, f"manifest_missing_field:{field}")

    instrument_type = manifest.get("instrument_type")
    if instrument_type is not None and instrument_type not in ALLOWED_INSTRUMENT_TYPES:
        _add_error(errors, "instrument_type_not_allowed")

    timeframe = manifest.get("timeframe")
    if timeframe is not None and timeframe not in ALLOWED_FIXTURE_TIMEFRAMES:
        _add_error(errors, "timeframe_not_allowed")

    if "expected_valid" in manifest and not isinstance(manifest.get("expected_valid"), bool):
        _add_error(errors, "expected_valid_not_boolean")

    if "expected_error_codes" in manifest and not isinstance(manifest.get("expected_error_codes"), list):
        _add_error(errors, "expected_error_codes_not_list")

    if "source_path" in manifest and not _source_path_allowed(manifest.get("source_path")):
        _add_error(errors, "source_path_invalid")

    if source_file is not None and source_hash is not None:
        expected_hash = manifest.get("file_sha256")
        if isinstance(expected_hash, str) and expected_hash and expected_hash != source_hash:
            _add_error(errors, "file_sha256_mismatch")


def _validate_rows(manifest: dict[str, Any], rows: list[dict[str, Any]], errors: list[str]) -> None:
    if "row_count" in manifest and manifest.get("row_count") != len(rows):
        _add_error(errors, "row_count_mismatch")

    decision_timestamp = _parse_timestamp(manifest.get("decision_timestamp_utc"))
    if decision_timestamp is None and "decision_timestamp_utc" in manifest:
        _add_error(errors, "decision_timestamp_invalid")

    expected_timeframe = manifest.get("timeframe")
    previous_close: datetime | None = None
    seen_close_times: set[datetime] = set()

    for index, row in enumerate(rows, start=1):
        for field in REQUIRED_ROW_FIELDS:
            if field not in row:
                _add_error(errors, f"row_missing_field:{field}")

        for field in sorted(FORBIDDEN_ROW_FIELDS.intersection(row)):
            _add_error(errors, f"forbidden_row_field:{field}")

        if expected_timeframe is not None and row.get("timeframe") != expected_timeframe:
            _add_error(errors, "row_timeframe_mismatch")

        prices = [row.get(field) for field in ["open", "high", "low", "close"]]
        if not all(isinstance(value, (int, float)) and value > 0 for value in prices):
            _add_error(errors, "price_value_invalid")
        else:
            open_price, high_price, low_price, close_price = [float(value) for value in prices]
            if low_price > high_price or not (low_price <= open_price <= high_price) or not (low_price <= close_price <= high_price):
                _add_error(errors, "price_bounds_invalid")

        volume = row.get("volume")
        if volume is not None and (not isinstance(volume, (int, float)) or volume < 0):
            _add_error(errors, "volume_invalid")

        open_time = _parse_timestamp(row.get("open_time_utc"))
        close_time = _parse_timestamp(row.get("close_time_utc"))
        if open_time is None or close_time is None:
            _add_error(errors, f"row_timestamp_invalid:{index}")
            continue

        if open_time >= close_time:
            _add_error(errors, "bar_time_order_invalid")

        if decision_timestamp is not None and close_time > decision_timestamp:
            _add_error(errors, "future_bar_after_decision")

        if close_time in seen_close_times:
            _add_error(errors, "duplicate_close_time")
        elif previous_close is not None and close_time <= previous_close:
            _add_error(errors, "close_time_not_monotonic")
        seen_close_times.add(close_time)
        previous_close = close_time


def _validate_case(case_dir: Path) -> dict[str, Any]:
    manifest_path = case_dir / "manifest.json"
    errors: list[str] = []
    source_hash: str | None = None
    source_file: Path | None = None
    row_count = 0

    try:
        manifest = _read_json(manifest_path)
    except json.JSONDecodeError:
        manifest = {}
        _add_error(errors, "manifest_parse_error")

    if not isinstance(manifest, dict):
        manifest = {}
        _add_error(errors, "manifest_not_object")

    source_path = manifest.get("source_path")
    if _source_path_allowed(source_path):
        source_file = case_dir / str(source_path)
        if source_file.exists():
            source_hash = _sha256_file(source_file)
    _validate_manifest(manifest, source_file, source_hash, errors)

    rows = _read_jsonl_rows(source_file, errors) if source_file is not None else []
    row_count = len(rows)
    _validate_rows(manifest, rows, errors)

    expected_error_codes = manifest.get("expected_error_codes") if isinstance(manifest.get("expected_error_codes"), list) else []
    expected_errors = sorted(str(code) for code in expected_error_codes)
    actual_errors = sorted(errors)
    expected_valid = manifest.get("expected_valid") is True
    actual_valid = not actual_errors
    passed = actual_valid == expected_valid and actual_errors == expected_errors

    return {
        "case_id": str(manifest.get("case_id") or case_dir.name),
        "expected_valid": expected_valid,
        "actual_valid": actual_valid,
        "expected_error_codes": expected_errors,
        "actual_error_codes": actual_errors,
        "passed": passed,
        "row_count": row_count,
        "file_sha256": source_hash,
        "source_path": str(source_path or ""),
    }


def validate_fixture_directory(fixtures_dir: Path | None = None) -> dict[str, Any]:
    root = (fixtures_dir or default_fixture_dir()).resolve()
    case_dirs = sorted(path for path in root.iterdir() if path.is_dir() and (path / "manifest.json").exists()) if root.exists() else []
    cases = [_validate_case(case_dir) for case_dir in case_dirs]
    passed_count = sum(1 for case in cases if case["passed"])
    return {
        "component": "historical_data_adapter",
        "implemented": True,
        "implemented_mode": "fixture_only_validation",
        "fixture_only_validation_allowed": True,
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
        "cases": cases,
        **SAFETY_FLAGS,
    }


def component_contract() -> dict[str, Any]:
    return {
        "component": "historical_data_adapter",
        "implemented": True,
        "implemented_mode": "fixture_only_validation",
        "allowed_now": "validate tiny synthetic local fixture files only",
        "blocked_operations": [
            blocked_operation("historical_data_loading"),
            blocked_operation("build_split_manifest"),
            blocked_operation("observe_offline"),
            blocked_operation("detector_execution"),
            blocked_operation("pnl_computation"),
        ],
        "fixture_only_validation_allowed": True,
        "real_historical_data_loading_allowed": False,
        "build_split_manifest_allowed": False,
        "observe_offline_allowed": False,
        **SAFETY_FLAGS,
    }
