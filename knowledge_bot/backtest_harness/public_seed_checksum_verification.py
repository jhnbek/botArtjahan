from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .safety import SAFETY_FLAGS, blocked_operation


def _has_checksum_fixture_cases(path: Path) -> bool:
    return path.exists() and any(
        child.is_dir() and (child / "case.json").exists()
        for child in path.iterdir()
    )


def default_public_seed_checksum_fixture_dir() -> Path:
    module_root = Path(__file__).resolve().parent
    packaged_fixture_dir = module_root / "fixtures" / "public_seed_checksum_verification"
    if _has_checksum_fixture_cases(packaged_fixture_dir):
        return packaged_fixture_dir

    generated_fixture_dir = (
        module_root.parents[1]
        / "_knowledge_base"
        / "structured"
        / "consolidation"
        / "public_crypto_spot_seed_checksum_verification"
        / "synthetic_fixture_sandbox"
    )
    if _has_checksum_fixture_cases(generated_fixture_dir):
        return generated_fixture_dir

    return packaged_fixture_dir


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _add_error(errors: list[str], code: str) -> None:
    if code not in errors:
        errors.append(code)


def _path_parts(value: str) -> list[str]:
    return [part for part in re.split(r"[\\/]+", value) if part]


def _safe_relative_path(value: Any, prefix: str, required_suffix: str, errors: list[str]) -> bool:
    if not isinstance(value, str) or not value.strip():
        _add_error(errors, f"{prefix}_invalid")
        return False
    safe = True
    lowered = value.lower()
    if lowered.startswith(("http://", "https://", "ftp://", "s3://", "file://")):
        _add_error(errors, f"{prefix}_url")
        safe = False
    if any(char in value for char in ["*", "?", "["]):
        _add_error(errors, f"{prefix}_wildcard")
        safe = False
    if re.match(r"^[A-Za-z]:[\\/]", value) or Path(value).is_absolute():
        _add_error(errors, f"{prefix}_absolute")
        safe = False
    if ".." in _path_parts(value):
        _add_error(errors, f"{prefix}_parent_traversal")
        safe = False
    if not value.endswith(required_suffix):
        _add_error(errors, f"{prefix}_suffix")
        safe = False
    return safe


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_checksum_token(text: str) -> str | None:
    tokens = re.findall(r"\b[a-fA-F0-9]{64}\b", text)
    if len(tokens) != 1:
        return None
    return tokens[0].lower()


def _validate_entry(case_dir: Path, entry: dict[str, Any], index: int) -> dict[str, Any]:
    errors: list[str] = []
    archive_value = entry.get("archive_file")
    checksum_value = entry.get("checksum_file")
    archive_safe = _safe_relative_path(archive_value, "archive_path", ".zip", errors)
    checksum_safe = _safe_relative_path(checksum_value, "checksum_path", ".CHECKSUM", errors)

    archive_path = case_dir / str(archive_value)
    checksum_path = case_dir / str(checksum_value)
    source_checksum_open_attempts = 0
    archive_hash_attempts = 0
    archive_size_probe_attempts = 0
    source_checksum = ""
    local_sha256 = ""
    local_size: int | None = None

    if checksum_safe:
        source_checksum_open_attempts += 1
        if not checksum_path.exists():
            _add_error(errors, "checksum_path_missing")
        elif not checksum_path.is_file():
            _add_error(errors, "checksum_path_not_file")
        else:
            source_checksum = _parse_checksum_token(checksum_path.read_text(encoding="utf-8"))
            if source_checksum is None:
                source_checksum = ""
                _add_error(errors, "checksum_parse_error")

    if archive_safe and source_checksum:
        if not archive_path.exists():
            _add_error(errors, "archive_path_missing")
        elif not archive_path.is_file():
            _add_error(errors, "archive_path_not_file")
        else:
            archive_hash_attempts += 1
            archive_size_probe_attempts += 1
            local_sha256 = _sha256_file(archive_path)
            local_size = archive_path.stat().st_size

    manifest_expected = entry.get("manifest_expected_sha256")
    if not isinstance(manifest_expected, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", manifest_expected or ""):
        _add_error(errors, "manifest_expected_sha256_invalid")
        manifest_expected_text = ""
    else:
        manifest_expected_text = manifest_expected.lower()

    expected_size = entry.get("expected_size_bytes")
    if expected_size is not None and (not isinstance(expected_size, int) or isinstance(expected_size, bool) or expected_size < 0):
        _add_error(errors, "expected_size_bytes_invalid")

    if source_checksum and manifest_expected_text and source_checksum != manifest_expected_text:
        _add_error(errors, "manifest_checksum_mismatch")
    if source_checksum and local_sha256 and local_sha256 != source_checksum:
        _add_error(errors, "local_archive_hash_mismatch")
    if isinstance(expected_size, int) and local_size is not None and local_size != expected_size:
        _add_error(errors, "size_mismatch")

    return {
        "entry_index": index,
        "archive_file": str(archive_value or ""),
        "checksum_file": str(checksum_value or ""),
        "source_checksum_sha256": source_checksum,
        "manifest_expected_sha256": manifest_expected_text,
        "local_sha256": local_sha256,
        "local_size_bytes": local_size,
        "expected_size_bytes": expected_size if isinstance(expected_size, int) else None,
        "hash_match": bool(source_checksum and local_sha256 and local_sha256 == source_checksum),
        "size_match": bool(isinstance(expected_size, int) and local_size == expected_size),
        "error_codes": sorted(errors),
        "fixture_source_checksum_file_open_attempt_count": source_checksum_open_attempts,
        "fixture_archive_hash_attempt_count": archive_hash_attempts,
        "fixture_archive_size_probe_attempt_count": archive_size_probe_attempts,
        "archive_extraction_attempt_count": 0,
        "market_row_parse_attempt_count": 0,
    }


def _validate_case(case_dir: Path) -> dict[str, Any]:
    metadata_path = case_dir / "case.json"
    case_errors: list[str] = []
    try:
        metadata = _read_json(metadata_path)
    except json.JSONDecodeError:
        metadata = {}
        _add_error(case_errors, "case_metadata_parse_error")

    if not isinstance(metadata, dict):
        metadata = {}
        _add_error(case_errors, "case_metadata_not_object")

    entries = metadata.get("entries")
    if not isinstance(entries, list) or not entries:
        entries = []
        _add_error(case_errors, "entries_missing_or_empty")

    entry_results: list[dict[str, Any]] = []
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            _add_error(case_errors, f"entry_not_object:{index}")
            continue
        entry_results.append(_validate_entry(case_dir, entry, index))

    actual_errors = sorted(set(case_errors + [code for result in entry_results for code in result["error_codes"]]))
    expected_error_codes = metadata.get("expected_error_codes") if isinstance(metadata.get("expected_error_codes"), list) else []
    expected_errors = sorted(str(code) for code in expected_error_codes)
    expected_valid = metadata.get("expected_valid") is True
    actual_valid = not actual_errors

    expected_archive_checked = metadata.get("expected_archive_checked_count")
    expected_checksum_checked = metadata.get("expected_checksum_file_checked_count")
    archive_checked = sum(int(result.get("fixture_archive_hash_attempt_count") or 0) for result in entry_results)
    checksum_checked = sum(int(result.get("fixture_source_checksum_file_open_attempt_count") or 0) for result in entry_results)
    archive_size_probed = sum(int(result.get("fixture_archive_size_probe_attempt_count") or 0) for result in entry_results)
    counter_match = True
    if isinstance(expected_archive_checked, int):
        counter_match = counter_match and archive_checked == expected_archive_checked
    if isinstance(expected_checksum_checked, int):
        counter_match = counter_match and checksum_checked == expected_checksum_checked

    passed = (
        expected_valid == actual_valid
        and expected_errors == actual_errors
        and counter_match
    )

    return {
        "case_id": str(metadata.get("case_id") or case_dir.name),
        "expected_valid": expected_valid,
        "actual_valid": actual_valid,
        "expected_error_codes": expected_errors,
        "actual_error_codes": actual_errors,
        "passed": passed,
        "entry_count": len(entry_results),
        "fixture_archive_hash_attempt_count": archive_checked,
        "fixture_archive_size_probe_attempt_count": archive_size_probed,
        "fixture_source_checksum_file_open_attempt_count": checksum_checked,
        "listed_market_file_open_attempt_count": 0,
        "listed_file_hash_attempt_count": 0,
        "listed_file_size_probe_attempt_count": 0,
        "source_checksum_file_open_attempt_count": 0,
        "archive_extraction_attempt_count": 0,
        "market_row_parse_attempt_count": 0,
        "entries": entry_results,
    }


def validate_public_seed_checksum_fixture_directory(fixtures_dir: Path | None = None) -> dict[str, Any]:
    root = (fixtures_dir or default_public_seed_checksum_fixture_dir()).resolve()
    case_dirs = sorted(path for path in root.iterdir() if path.is_dir() and (path / "case.json").exists()) if root.exists() else []
    cases = [_validate_case(case_dir) for case_dir in case_dirs]
    passed_count = sum(1 for case in cases if case["passed"])
    return {
        "component": "public_crypto_spot_seed_checksum_verification",
        "implemented": True,
        "implemented_mode": "fixture_first_public_seed_checksum_verification",
        "fixture_first_public_seed_checksum_verification_allowed": True,
        "ready_for_public_crypto_spot_seed_byte_verification": False,
        "real_public_seed_checksum_verification_allowed": False,
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
        "fixtures_dir": root.as_posix(),
        "case_count": len(cases),
        "passed_count": passed_count,
        "failed_count": len(cases) - passed_count,
        "all_cases_passed": bool(cases) and passed_count == len(cases),
        "fixture_archive_hash_attempt_count": sum(int(case.get("fixture_archive_hash_attempt_count") or 0) for case in cases),
        "fixture_archive_size_probe_attempt_count": sum(int(case.get("fixture_archive_size_probe_attempt_count") or 0) for case in cases),
        "fixture_source_checksum_file_open_attempt_count": sum(int(case.get("fixture_source_checksum_file_open_attempt_count") or 0) for case in cases),
        "listed_market_file_open_attempt_count": 0,
        "listed_file_hash_attempt_count": 0,
        "listed_file_size_probe_attempt_count": 0,
        "source_checksum_file_open_attempt_count": 0,
        "archive_extraction_attempt_count": 0,
        "market_row_parse_attempt_count": 0,
        "cases": cases,
        **SAFETY_FLAGS,
    }


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _validate_real_manifest_input(value: Any, errors: list[str]) -> Path | None:
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
    if resolved.exists() and resolved.is_dir():
        _add_error(errors, "manifest_input_directory")
        safe_to_open = False
    return resolved if safe_to_open else None


def verify_public_seed_checksum_manifest(manifest_values: list[str]) -> dict[str, Any]:
    errors: list[str] = []
    manifest_document_open_attempt_count = 0
    manifest: Any = {}
    manifest_path: Path | None = None

    if len(manifest_values) != 1:
        _add_error(errors, "manifest_input_count_not_one")
    else:
        manifest_path = _validate_real_manifest_input(manifest_values[0], errors)

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

    data_root = manifest_path.parent if manifest_path is not None else Path()

    files = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(files, list):
        files = []
        if "manifest_parse_error" not in errors and "manifest_path_missing" not in errors:
            _add_error(errors, "manifest_files_missing_or_invalid")

    if isinstance(manifest, dict):
        if manifest.get("dataset_id") != "binance_spot_seed_2024q1_btc_eth_h1_m15":
            _add_error(errors, "dataset_id_unexpected")
        if manifest.get("inventory_profile") != "crypto_spot_24_7_utc_manifest":
            _add_error(errors, "inventory_profile_unexpected")
        if manifest.get("source_format") != "archive_manifest":
            _add_error(errors, "source_format_unexpected")
        if int(manifest.get("expected_file_count") or 0) != 12:
            _add_error(errors, "expected_file_count_not_12")
        if len(files) != 12:
            _add_error(errors, "manifest_file_count_not_12")

    archive_results: list[dict[str, Any]] = []
    source_checksum_file_open_attempt_count = 0
    listed_market_file_open_attempt_count = 0
    listed_file_hash_attempt_count = 0
    listed_file_size_probe_attempt_count = 0
    checksum_parse_failure_count = 0

    for index, entry in enumerate(files, start=1):
        entry_errors: list[str] = []
        if not isinstance(entry, dict):
            archive_results.append({"entry_index": index, "error_codes": ["file_entry_not_object"]})
            _add_error(errors, "file_entry_not_object")
            continue

        relative_path = str(entry.get("relative_path") or "")
        archive_safe = _safe_relative_path(relative_path, "archive_path", ".zip", entry_errors)
        archive_path = (data_root / relative_path).resolve(strict=False) if archive_safe else None
        if archive_path is not None and not _is_relative_to(archive_path, data_root.resolve(strict=False)):
            _add_error(entry_errors, "archive_path_escape")
            archive_safe = False

        checksum_relative_path = f"{relative_path}.CHECKSUM"
        checksum_safe = _safe_relative_path(checksum_relative_path, "checksum_path", ".CHECKSUM", entry_errors)
        checksum_path = (data_root / checksum_relative_path).resolve(strict=False) if checksum_safe else None
        if checksum_path is not None and not _is_relative_to(checksum_path, data_root.resolve(strict=False)):
            _add_error(entry_errors, "checksum_path_escape")
            checksum_safe = False

        source_checksum = ""
        if checksum_safe and checksum_path is not None:
            source_checksum_file_open_attempt_count += 1
            if not checksum_path.exists():
                _add_error(entry_errors, "checksum_path_missing")
            elif not checksum_path.is_file():
                _add_error(entry_errors, "checksum_path_not_file")
            else:
                parsed = _parse_checksum_token(checksum_path.read_text(encoding="utf-8"))
                if parsed is None:
                    checksum_parse_failure_count += 1
                    _add_error(entry_errors, "checksum_parse_error")
                else:
                    source_checksum = parsed

        manifest_expected = entry.get("expected_sha256")
        if not isinstance(manifest_expected, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", manifest_expected or ""):
            _add_error(entry_errors, "manifest_expected_sha256_invalid")
            manifest_expected_text = ""
        else:
            manifest_expected_text = manifest_expected.lower()

        expected_size = entry.get("expected_size_bytes")
        if expected_size is not None and (not isinstance(expected_size, int) or isinstance(expected_size, bool) or expected_size < 0):
            _add_error(entry_errors, "expected_size_bytes_invalid")

        local_sha256 = ""
        local_size: int | None = None
        if archive_safe and archive_path is not None and source_checksum:
            if not archive_path.exists():
                _add_error(entry_errors, "archive_path_missing")
            elif not archive_path.is_file():
                _add_error(entry_errors, "archive_path_not_file")
            else:
                listed_market_file_open_attempt_count += 1
                listed_file_hash_attempt_count += 1
                listed_file_size_probe_attempt_count += 1
                local_sha256 = _sha256_file(archive_path)
                local_size = archive_path.stat().st_size

        if source_checksum and manifest_expected_text and source_checksum != manifest_expected_text:
            _add_error(entry_errors, "manifest_checksum_mismatch")
        if source_checksum and local_sha256 and local_sha256 != source_checksum:
            _add_error(entry_errors, "local_archive_hash_mismatch")
        if isinstance(expected_size, int) and local_size is not None and local_size != expected_size:
            _add_error(entry_errors, "size_mismatch")

        for code in entry_errors:
            _add_error(errors, code)

        archive_results.append(
            {
                "entry_index": index,
                "relative_path": relative_path,
                "checksum_relative_path": checksum_relative_path,
                "symbol": str(entry.get("symbol") or ""),
                "timeframe": str(entry.get("timeframe") or ""),
                "source_checksum_sha256": source_checksum,
                "manifest_expected_sha256": manifest_expected_text,
                "local_sha256": local_sha256,
                "hash_match": bool(source_checksum and local_sha256 and local_sha256 == source_checksum),
                "expected_size_bytes": expected_size if isinstance(expected_size, int) else None,
                "local_size_bytes": local_size,
                "size_match": bool(isinstance(expected_size, int) and local_size == expected_size),
                "error_codes": sorted(entry_errors),
            }
        )

    error_codes = sorted(errors)
    hash_match_count = sum(1 for row in archive_results if row.get("hash_match") is True)
    size_match_count = sum(1 for row in archive_results if row.get("size_match") is True)
    archive_checked_count = sum(1 for row in archive_results if row.get("local_sha256"))
    checksum_file_checked_count = sum(1 for row in archive_results if row.get("source_checksum_sha256"))
    valid = not error_codes and archive_checked_count == 12 and checksum_file_checked_count == 12
    return {
        "component": "public_crypto_spot_seed_checksum_byte_verification",
        "implemented": True,
        "implemented_mode": "one_reviewed_public_seed_checksum_byte_verification_run",
        "metadata_only": False,
        "manifest_input_count": len(manifest_values),
        "manifest_input": manifest_values[0] if len(manifest_values) == 1 else "",
        "manifest_path": manifest_path.as_posix() if manifest_path is not None else "",
        "data_root": data_root.as_posix() if manifest_path is not None else "",
        "manifest_id": str(manifest.get("manifest_id") or "") if isinstance(manifest, dict) else "",
        "dataset_id": str(manifest.get("dataset_id") or "") if isinstance(manifest, dict) else "",
        "valid": valid,
        "error_codes": error_codes,
        "archive_checked_count": archive_checked_count,
        "checksum_file_checked_count": checksum_file_checked_count,
        "hash_match_count": hash_match_count,
        "hash_mismatch_count": archive_checked_count - hash_match_count,
        "size_match_count": size_match_count,
        "size_mismatch_count": archive_checked_count - size_match_count,
        "checksum_parse_failure_count": checksum_parse_failure_count,
        "manifest_document_open_attempt_count": manifest_document_open_attempt_count,
        "listed_market_file_open_attempt_count": listed_market_file_open_attempt_count,
        "listed_file_hash_attempt_count": listed_file_hash_attempt_count,
        "listed_file_size_probe_attempt_count": listed_file_size_probe_attempt_count,
        "source_checksum_file_open_attempt_count": source_checksum_file_open_attempt_count,
        "archive_extraction_attempt_count": 0,
        "market_row_parse_attempt_count": 0,
        "archive_results": archive_results,
        "public_crypto_spot_seed_checksum_byte_verification_ready": valid,
        "ready_for_public_crypto_spot_seed_checksum_byte_verification_review": valid,
        "ready_for_real_historical_data_loading": False,
        "real_public_seed_checksum_verification_allowed": False,
        "source_directory_scanning_allowed": False,
        "listed_file_hashing_allowed": False,
        "listed_file_size_probe_allowed": False,
        "listed_market_file_open_allowed": False,
        "market_row_parsing_allowed": False,
        "archive_extraction_allowed": False,
        "real_historical_data_loading_allowed": False,
        "build_split_manifest_allowed": False,
        "observe_offline_allowed": False,
        "detector_execution_allowed": False,
        "pnl_computation_allowed": False,
        **SAFETY_FLAGS,
    }


def public_seed_checksum_capability_scan() -> dict[str, Any]:
    return {
        "component": "public_crypto_spot_seed_checksum_byte_verification_capability_scan",
        "source_directory_scanning_allowed": False,
        "archive_extraction_allowed": False,
        "market_row_parsing_allowed": False,
        "real_historical_data_loading_allowed": False,
        "detector_execution_allowed": False,
        "pnl_computation_allowed": False,
        "build_split_manifest_allowed": False,
        "observe_offline_allowed": False,
        "passed": True,
        **SAFETY_FLAGS,
    }


def public_seed_checksum_safety_counters(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "manifest_document_open_attempt_count": int(payload.get("manifest_document_open_attempt_count") or 0),
        "source_checksum_file_open_attempt_count": int(payload.get("source_checksum_file_open_attempt_count") or 0),
        "listed_market_file_open_attempt_count": int(payload.get("listed_market_file_open_attempt_count") or 0),
        "listed_file_hash_attempt_count": int(payload.get("listed_file_hash_attempt_count") or 0),
        "listed_file_size_probe_attempt_count": int(payload.get("listed_file_size_probe_attempt_count") or 0),
        "archive_extraction_attempt_count": int(payload.get("archive_extraction_attempt_count") or 0),
        "market_row_parse_attempt_count": int(payload.get("market_row_parse_attempt_count") or 0),
    }


def write_public_seed_checksum_verification_outputs(out_dir: Path, payload: dict[str, Any]) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    status = {
        "public_crypto_spot_seed_checksum_byte_verification_ready": payload.get("valid") is True,
        "archive_checked_count": int(payload.get("archive_checked_count") or 0),
        "checksum_file_checked_count": int(payload.get("checksum_file_checked_count") or 0),
        "hash_match_count": int(payload.get("hash_match_count") or 0),
        "size_match_count": int(payload.get("size_match_count") or 0),
        "archive_extraction_attempt_count": 0,
        "market_row_parse_attempt_count": 0,
        **SAFETY_FLAGS,
    }
    safety_counters = public_seed_checksum_safety_counters(payload)
    capability_scan = public_seed_checksum_capability_scan()
    paths = {
        "status": (out_dir / "public_seed_checksum_verification_status.json").as_posix(),
        "archive_checksum_results": (out_dir / "archive_checksum_results.json").as_posix(),
        "safety_counters": (out_dir / "safety_counters.json").as_posix(),
        "capability_scan": (out_dir / "capability_scan.json").as_posix(),
        "review_packet": (out_dir / "review_packet.md").as_posix(),
    }
    (out_dir / "public_seed_checksum_verification_status.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "archive_checksum_results.json").write_text(
        json.dumps(payload.get("archive_results") or [], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "safety_counters.json").write_text(
        json.dumps(safety_counters, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "capability_scan.json").write_text(
        json.dumps(capability_scan, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    review_packet = [
        "# Public Seed Checksum Byte Verification Review Packet",
        "",
        f"- Verification ready: `{str(status['public_crypto_spot_seed_checksum_byte_verification_ready']).lower()}`",
        f"- Archive checks: {status['archive_checked_count']}",
        f"- Source CHECKSUM reads: {status['checksum_file_checked_count']}",
        f"- Hash matches: {status['hash_match_count']}",
        f"- Manifest document opens: {safety_counters['manifest_document_open_attempt_count']}",
        f"- Listed archive byte opens: {safety_counters['listed_market_file_open_attempt_count']}",
        f"- Listed archive hashes: {safety_counters['listed_file_hash_attempt_count']}",
        f"- Listed archive size probes: {safety_counters['listed_file_size_probe_attempt_count']}",
        f"- Archive extraction attempts: {safety_counters['archive_extraction_attempt_count']}",
        f"- Market row parses: {safety_counters['market_row_parse_attempt_count']}",
        "",
        "The verification opened raw archive bytes only for hashing and size checks. It did not extract archives, parse market rows, load historical data, run detectors, compute PnL, run backtests, or enable execution.",
        "",
    ]
    (out_dir / "review_packet.md").write_text("\n".join(review_packet), encoding="utf-8")
    return paths


def component_contract() -> dict[str, Any]:
    return {
        "component": "public_crypto_spot_seed_checksum_verification",
        "implemented": True,
        "implemented_mode": "fixture_first_public_seed_checksum_verification",
        "allowed_now": "validate generated synthetic checksum fixtures only",
        "blocked_operations": [
            blocked_operation("real_public_seed_checksum_verification"),
            blocked_operation("source_directory_scanning"),
            blocked_operation("archive_extraction"),
            blocked_operation("market_row_parsing"),
            blocked_operation("historical_data_loading"),
            blocked_operation("detector_execution"),
            blocked_operation("pnl_computation"),
        ],
        "fixture_first_public_seed_checksum_verification_allowed": True,
        "ready_for_public_crypto_spot_seed_byte_verification": False,
        "real_public_seed_checksum_verification_allowed": False,
        "source_directory_scanning_allowed": False,
        "listed_market_file_open_allowed": False,
        "listed_file_hashing_allowed": False,
        "listed_file_size_probe_allowed": False,
        "market_row_parsing_allowed": False,
        **SAFETY_FLAGS,
    }
