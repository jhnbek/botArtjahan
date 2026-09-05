from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .historical_data_adapter import default_fixture_dir, validate_fixture_directory
from .manifest_inventory import default_manifest_inventory_fixture_dir, validate_manifest_inventory_fixture_directory
from .manifest_metadata_validation import (
    default_manifest_metadata_validation_fixture_dir,
    validate_real_manifest_metadata_document,
    validate_manifest_metadata_execution_fixture_directory,
    validate_manifest_metadata_fixture_directory,
    write_real_manifest_metadata_validation_outputs,
)
from .prohibited_capability_scanner import scan_path
from .public_seed_checksum_verification import (
    default_public_seed_checksum_fixture_dir,
    validate_public_seed_checksum_fixture_directory,
)
from .run_manifest import create_scaffold_manifest
from .safety import blocked_operation, scaffold_safety_status
from .scn002_false_breakout_fixtures import (
    component_contract as scn002_fixture_component_contract,
    default_scn002_fixture_dir,
    validate_scn002_fixture_directory,
)
from .scenarios.scn002_false_breakout_observation import (
    component_contract as scn002_observation_component_contract,
    observe_scn002_fixture_directory,
    validate_scn002_observation_results,
)
from .scenarios.scn002_false_breakout_backtest import (
    component_contract as scn002_backtest_component_contract,
    prepare_scn002_backtest_preflight,
    validate_scn002_backtest_preflight,
)
from .scenarios.scn002_false_breakout_outcome_labels import (
    component_contract as scn002_outcome_label_component_contract,
    prepare_scn002_outcome_label_scaffold,
    validate_scn002_outcome_label_scaffold,
)
from .scenarios.scn002_false_breakout_outcome_windows import (
    component_contract as scn002_outcome_data_window_component_contract,
    materialization_component_contract as scn002_outcome_data_window_materialization_component_contract,
    reviewed_manifest_binding_component_contract as scn002_outcome_data_window_reviewed_manifest_binding_component_contract,
    reviewed_manifest_metadata_validation_component_contract as scn002_reviewed_manifest_metadata_validation_component_contract,
    prepare_scn002_outcome_data_window_scaffold,
    prepare_scn002_outcome_data_window_materialization_scaffold,
    prepare_scn002_outcome_data_window_reviewed_manifest_binding_scaffold,
    prepare_scn002_reviewed_manifest_metadata_validation_scaffold,
    validate_scn002_outcome_data_window_scaffold,
    validate_scn002_outcome_data_window_materialization_scaffold,
    validate_scn002_outcome_data_window_reviewed_manifest_binding_scaffold,
    validate_scn002_reviewed_manifest_metadata_validation_scaffold,
)


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def command_scan_capabilities(args: argparse.Namespace) -> int:
    payload = scan_path(args.path.resolve())
    emit(payload)
    return 0 if payload["passed"] else 1


def command_manifest(args: argparse.Namespace) -> int:
    del args
    emit(create_scaffold_manifest(source="cli"))
    return 0


def command_status(args: argparse.Namespace) -> int:
    del args
    emit(scaffold_safety_status())
    return 0


def command_validate_fixtures(args: argparse.Namespace) -> int:
    payload = validate_fixture_directory(args.fixtures_dir)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    emit(payload)
    return 0 if payload["all_cases_passed"] else 1


def command_validate_manifest_inventory_fixtures(args: argparse.Namespace) -> int:
    payload = validate_manifest_inventory_fixture_directory(args.fixtures_dir)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    emit(payload)
    return 0 if payload["all_cases_passed"] else 1


def command_validate_manifest_metadata_fixtures(args: argparse.Namespace) -> int:
    payload = validate_manifest_metadata_fixture_directory(args.fixtures_dir)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    emit(payload)
    return 0 if payload["all_cases_passed"] else 1


def command_validate_manifest_metadata_execution_fixtures(args: argparse.Namespace) -> int:
    payload = validate_manifest_metadata_execution_fixture_directory(args.fixtures_dir)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    emit(payload)
    return 0 if payload["all_cases_passed"] else 1


def command_validate_real_manifest_metadata(args: argparse.Namespace) -> int:
    payload = validate_real_manifest_metadata_document(args.manifest)
    if args.out:
        paths = write_real_manifest_metadata_validation_outputs(args.out, payload)
        payload = {**payload, "outputs": paths}
    emit(payload)
    return 0 if payload["valid"] else 1


def command_validate_public_seed_checksum_fixtures(args: argparse.Namespace) -> int:
    payload = validate_public_seed_checksum_fixture_directory(args.fixtures_dir)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    emit(payload)
    return 0 if payload["all_cases_passed"] else 1


def command_validate_scn002_fixtures(args: argparse.Namespace) -> int:
    payload = validate_scn002_fixture_directory(args.fixtures_dir)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    emit(payload)
    return 0 if payload["all_cases_passed"] else 1


def command_scan_scn002_fixture_capabilities(args: argparse.Namespace) -> int:
    payload = scan_path(args.path.resolve())
    payload = {
        **payload,
        "component": "scn002_false_breakout_fixture_validator",
        "real_historical_data_loading_allowed": False,
        "market_backtest_allowed": False,
        "detector_execution_allowed": False,
        "pnl_computation_allowed": False,
    }
    emit(payload)
    return 0 if payload["passed"] else 1


def command_show_scn002_fixture_contract(args: argparse.Namespace) -> int:
    del args
    emit(scn002_fixture_component_contract())
    return 0


def command_observe_scn002_fixtures(args: argparse.Namespace) -> int:
    payload = observe_scn002_fixture_directory(args.fixtures_dir, args.out)
    emit(payload)
    return 0 if payload["all_cases_observed"] else 1


def command_validate_scn002_observation_results(args: argparse.Namespace) -> int:
    payload = validate_scn002_observation_results(args.results)
    emit(payload)
    return 0 if payload["all_results_valid"] else 1


def command_scan_scn002_observation_capabilities(args: argparse.Namespace) -> int:
    payload = scan_path(args.path.resolve())
    payload = {
        **payload,
        "component": "scn002_false_breakout_read_only_detector_observation",
        "real_historical_data_loading_allowed": False,
        "market_backtest_allowed": False,
        "detector_execution_allowed": False,
        "pnl_computation_allowed": False,
    }
    emit(payload)
    return 0 if payload["passed"] else 1


def command_show_scn002_observation_contract(args: argparse.Namespace) -> int:
    del args
    emit(scn002_observation_component_contract())
    return 0


def command_prepare_scn002_backtest_preflight(args: argparse.Namespace) -> int:
    payload = prepare_scn002_backtest_preflight(args.observations, args.out)
    emit(payload)
    return 0 if payload["preflight_ready"] else 1


def command_validate_scn002_backtest_preflight(args: argparse.Namespace) -> int:
    payload = validate_scn002_backtest_preflight(args.preflight)
    emit(payload)
    return 0 if payload["all_preflight_valid"] else 1


def command_scan_scn002_backtest_capabilities(args: argparse.Namespace) -> int:
    payload = scan_path(args.path.resolve())
    payload = {
        **payload,
        "component": "scn002_false_breakout_read_only_backtest_preflight",
        "real_historical_data_loading_allowed": False,
        "market_backtest_allowed": False,
        "detector_execution_allowed": False,
        "pnl_computation_allowed": False,
    }
    emit(payload)
    return 0 if payload["passed"] else 1


def command_show_scn002_backtest_contract(args: argparse.Namespace) -> int:
    del args
    emit(scn002_backtest_component_contract())
    return 0


def command_prepare_scn002_outcome_labels(args: argparse.Namespace) -> int:
    payload = prepare_scn002_outcome_label_scaffold(args.preflight, args.out)
    emit(payload)
    return 0 if payload["scaffold_ready"] else 1


def command_validate_scn002_outcome_labels(args: argparse.Namespace) -> int:
    payload = validate_scn002_outcome_label_scaffold(args.labels)
    emit(payload)
    return 0 if payload["all_scaffold_valid"] else 1


def command_scan_scn002_outcome_label_capabilities(args: argparse.Namespace) -> int:
    payload = scan_path(args.path.resolve())
    payload = {
        **payload,
        "component": "scn002_false_breakout_read_only_outcome_label_scaffold",
        "real_historical_data_loading_allowed": False,
        "market_backtest_allowed": False,
        "detector_execution_allowed": False,
        "label_computation_allowed": False,
        "outcome_computation_allowed": False,
        "pnl_computation_allowed": False,
    }
    emit(payload)
    return 0 if payload["passed"] else 1


def command_show_scn002_outcome_label_contract(args: argparse.Namespace) -> int:
    del args
    emit(scn002_outcome_label_component_contract())
    return 0


def command_prepare_scn002_outcome_data_windows(args: argparse.Namespace) -> int:
    payload = prepare_scn002_outcome_data_window_scaffold(args.labels, args.out)
    emit(payload)
    return 0 if payload["scaffold_ready"] else 1


def command_validate_scn002_outcome_data_windows(args: argparse.Namespace) -> int:
    payload = validate_scn002_outcome_data_window_scaffold(args.windows)
    emit(payload)
    return 0 if payload["all_window_scaffold_valid"] else 1


def command_scan_scn002_outcome_data_window_capabilities(args: argparse.Namespace) -> int:
    payload = scan_path(args.path.resolve())
    payload = {
        **payload,
        "component": "scn002_false_breakout_read_only_outcome_data_window_scaffold",
        "real_historical_data_loading_allowed": False,
        "market_file_access_allowed": False,
        "market_row_parsing_allowed": False,
        "market_backtest_allowed": False,
        "detector_execution_allowed": False,
        "label_computation_allowed": False,
        "outcome_computation_allowed": False,
        "pnl_computation_allowed": False,
    }
    emit(payload)
    return 0 if payload["passed"] else 1


def command_show_scn002_outcome_data_window_contract(args: argparse.Namespace) -> int:
    del args
    emit(scn002_outcome_data_window_component_contract())
    return 0


def command_prepare_scn002_outcome_data_window_materialization(args: argparse.Namespace) -> int:
    payload = prepare_scn002_outcome_data_window_materialization_scaffold(args.windows, args.out)
    emit(payload)
    return 0 if payload["materialization_scaffold_ready"] else 1


def command_validate_scn002_outcome_data_window_materialization(args: argparse.Namespace) -> int:
    payload = validate_scn002_outcome_data_window_materialization_scaffold(args.materialization)
    emit(payload)
    return 0 if payload["all_materialization_scaffold_valid"] else 1


def command_scan_scn002_outcome_data_window_materialization_capabilities(args: argparse.Namespace) -> int:
    payload = scan_path(args.path.resolve())
    payload = {
        **payload,
        "component": "scn002_false_breakout_read_only_outcome_data_window_materialization_scaffold",
        "real_historical_data_loading_allowed": False,
        "market_file_access_allowed": False,
        "market_row_parsing_allowed": False,
        "outcome_data_window_materialization_allowed": False,
        "market_backtest_allowed": False,
        "detector_execution_allowed": False,
        "label_computation_allowed": False,
        "outcome_computation_allowed": False,
        "pnl_computation_allowed": False,
    }
    emit(payload)
    return 0 if payload["passed"] else 1


def command_show_scn002_outcome_data_window_materialization_contract(args: argparse.Namespace) -> int:
    del args
    emit(scn002_outcome_data_window_materialization_component_contract())
    return 0


def command_prepare_scn002_outcome_data_window_reviewed_manifest_binding(args: argparse.Namespace) -> int:
    payload = prepare_scn002_outcome_data_window_reviewed_manifest_binding_scaffold(args.materialization, args.out)
    emit(payload)
    return 0 if payload["binding_scaffold_ready"] else 1


def command_validate_scn002_outcome_data_window_reviewed_manifest_binding(args: argparse.Namespace) -> int:
    payload = validate_scn002_outcome_data_window_reviewed_manifest_binding_scaffold(args.binding)
    emit(payload)
    return 0 if payload["all_binding_scaffold_valid"] else 1


def command_scan_scn002_outcome_data_window_reviewed_manifest_binding_capabilities(args: argparse.Namespace) -> int:
    payload = scan_path(args.path.resolve())
    payload = {
        **payload,
        "component": "scn002_false_breakout_read_only_reviewed_manifest_binding_scaffold",
        "manifest_metadata_loaded": False,
        "manifest_file_accessed": False,
        "manifest_rows_parsed": False,
        "real_historical_data_loading_allowed": False,
        "market_file_access_allowed": False,
        "market_row_parsing_allowed": False,
        "outcome_data_window_materialization_allowed": False,
        "market_backtest_allowed": False,
        "detector_execution_allowed": False,
        "label_computation_allowed": False,
        "outcome_computation_allowed": False,
        "pnl_computation_allowed": False,
    }
    emit(payload)
    return 0 if payload["passed"] else 1


def command_show_scn002_outcome_data_window_reviewed_manifest_binding_contract(args: argparse.Namespace) -> int:
    del args
    emit(scn002_outcome_data_window_reviewed_manifest_binding_component_contract())
    return 0


def command_prepare_scn002_reviewed_manifest_metadata_validation(args: argparse.Namespace) -> int:
    payload = prepare_scn002_reviewed_manifest_metadata_validation_scaffold(
        args.binding,
        args.out,
        args.source_binding_implementation_review_id,
    )
    emit(payload)
    return 0 if payload["metadata_validation_scaffold_ready"] else 1


def command_validate_scn002_reviewed_manifest_metadata_validation(args: argparse.Namespace) -> int:
    payload = validate_scn002_reviewed_manifest_metadata_validation_scaffold(args.metadata_validation)
    emit(payload)
    return 0 if payload["all_metadata_validation_scaffold_valid"] else 1


def command_scan_scn002_reviewed_manifest_metadata_validation_capabilities(args: argparse.Namespace) -> int:
    payload = scan_path(args.path.resolve())
    payload = {
        **payload,
        "component": "scn002_false_breakout_read_only_reviewed_manifest_metadata_validation_scaffold",
        "manifest_metadata_loaded": False,
        "manifest_metadata_validated": False,
        "manifest_metadata_validation_allowed": False,
        "manifest_file_accessed": False,
        "manifest_rows_parsed": False,
        "real_historical_data_loading_allowed": False,
        "market_file_access_allowed": False,
        "market_row_parsing_allowed": False,
        "outcome_data_window_materialization_allowed": False,
        "market_backtest_allowed": False,
        "detector_execution_allowed": False,
        "label_computation_allowed": False,
        "outcome_computation_allowed": False,
        "pnl_computation_allowed": False,
    }
    emit(payload)
    return 0 if payload["passed"] else 1


def command_show_scn002_reviewed_manifest_metadata_validation_contract(args: argparse.Namespace) -> int:
    del args
    emit(scn002_reviewed_manifest_metadata_validation_component_contract())
    return 0


def command_blocked(args: argparse.Namespace) -> int:
    emit(blocked_operation(args.command_name))
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Safety-gated fixture-first scaffold for a future read-only backtest harness."
    )
    subparsers = parser.add_subparsers(dest="command_name", required=True)

    status_parser = subparsers.add_parser("status", help="Print scaffold safety flags.")
    status_parser.set_defaults(func=command_status)

    manifest_parser = subparsers.add_parser("write-run-manifest", help="Print a scaffold-only run manifest.")
    manifest_parser.set_defaults(func=command_manifest)

    scan_parser = subparsers.add_parser("scan-capabilities", help="Scan local scaffold code for prohibited capabilities.")
    scan_parser.add_argument("--path", type=Path, default=Path(__file__).resolve().parent)
    scan_parser.set_defaults(func=command_scan_capabilities)

    validate_parser = subparsers.add_parser(
        "validate-fixtures",
        help="Validate tiny synthetic local historical-data fixtures without loading real market history.",
    )
    validate_parser.add_argument("--fixtures-dir", type=Path, default=default_fixture_dir())
    validate_parser.add_argument("--out", type=Path, default=None)
    validate_parser.set_defaults(func=command_validate_fixtures)

    manifest_inventory_parser = subparsers.add_parser(
        "validate-manifest-inventory-fixtures",
        help="Validate synthetic manifest-inventory metadata fixtures without scanning data roots or opening listed market files.",
    )
    manifest_inventory_parser.add_argument("--fixtures-dir", type=Path, default=default_manifest_inventory_fixture_dir())
    manifest_inventory_parser.add_argument("--out", type=Path, default=None)
    manifest_inventory_parser.set_defaults(func=command_validate_manifest_inventory_fixtures)

    manifest_metadata_parser = subparsers.add_parser(
        "validate-manifest-metadata-fixtures",
        help="Validate synthetic explicit-manifest metadata fixtures without running against real manifest documents.",
    )
    manifest_metadata_parser.add_argument("--fixtures-dir", type=Path, default=default_manifest_metadata_validation_fixture_dir())
    manifest_metadata_parser.add_argument("--out", type=Path, default=None)
    manifest_metadata_parser.set_defaults(func=command_validate_manifest_metadata_fixtures)

    manifest_metadata_execution_parser = subparsers.add_parser(
        "validate-manifest-metadata-execution-fixtures",
        help="Validate the reviewed real-manifest metadata execution path against synthetic fixtures only.",
    )
    manifest_metadata_execution_parser.add_argument(
        "--fixtures-dir",
        type=Path,
        default=default_manifest_metadata_validation_fixture_dir(),
    )
    manifest_metadata_execution_parser.add_argument("--out", type=Path, default=None)
    manifest_metadata_execution_parser.set_defaults(func=command_validate_manifest_metadata_execution_fixtures)

    real_manifest_metadata_parser = subparsers.add_parser(
        "validate-real-manifest-metadata",
        help="Validate one explicit real manifest JSON document without touching listed market files.",
    )
    real_manifest_metadata_parser.add_argument("--manifest", action="append", default=[])
    real_manifest_metadata_parser.add_argument("--out", type=Path, default=None)
    real_manifest_metadata_parser.set_defaults(func=command_validate_real_manifest_metadata)

    public_seed_checksum_fixtures_parser = subparsers.add_parser(
        "validate-public-seed-checksum-fixtures",
        help="Validate generated synthetic public seed checksum fixtures without touching real seed archives.",
    )
    public_seed_checksum_fixtures_parser.add_argument(
        "--fixtures-dir",
        type=Path,
        default=default_public_seed_checksum_fixture_dir(),
    )
    public_seed_checksum_fixtures_parser.add_argument("--out", type=Path, default=None)
    public_seed_checksum_fixtures_parser.set_defaults(func=command_validate_public_seed_checksum_fixtures)

    scn002_fixtures_parser = subparsers.add_parser(
        "validate-scn002-fixtures",
        help="Validate synthetic SCN-002 false-breakout observation fixtures without market data, detector execution, or PnL.",
    )
    scn002_fixtures_parser.add_argument("--fixtures-dir", type=Path, default=default_scn002_fixture_dir())
    scn002_fixtures_parser.add_argument("--out", type=Path, default=None)
    scn002_fixtures_parser.set_defaults(func=command_validate_scn002_fixtures)

    scn002_scan_parser = subparsers.add_parser(
        "scan-scn002-fixture-capabilities",
        help="Scan SCN-002 fixture-validator code for prohibited runtime, broker, detector, and PnL capabilities.",
    )
    scn002_scan_parser.add_argument(
        "--path",
        type=Path,
        default=Path(__file__).resolve().parent / "scn002_false_breakout_fixtures.py",
    )
    scn002_scan_parser.set_defaults(func=command_scan_scn002_fixture_capabilities)

    scn002_contract_parser = subparsers.add_parser(
        "show-scn002-fixture-contract",
        help="Print the SCN-002 fixture-validator contract without reading fixture files.",
    )
    scn002_contract_parser.set_defaults(func=command_show_scn002_fixture_contract)

    scn002_observation_contract_parser = subparsers.add_parser(
        "show-scn002-observation-contract",
        help="Print the SCN-002 fixture-only detector-observation contract without reading fixture or market files.",
    )
    scn002_observation_contract_parser.set_defaults(func=command_show_scn002_observation_contract)

    scn002_observe_parser = subparsers.add_parser(
        "observe-scn002-fixtures",
        help="Write non-trading SCN-002 detector-observation rows from reviewed synthetic/curated fixture JSON documents only.",
    )
    scn002_observe_parser.add_argument("--fixtures-dir", type=Path, default=default_scn002_fixture_dir())
    scn002_observe_parser.add_argument("--out", type=Path, required=True)
    scn002_observe_parser.set_defaults(func=command_observe_scn002_fixtures)

    scn002_validate_observation_parser = subparsers.add_parser(
        "validate-scn002-observation-results",
        help="Validate SCN-002 fixture-only detector-observation result rows and reject trading/outcome fields.",
    )
    scn002_validate_observation_parser.add_argument("--results", type=Path, required=True)
    scn002_validate_observation_parser.set_defaults(func=command_validate_scn002_observation_results)

    scn002_observation_scan_parser = subparsers.add_parser(
        "scan-scn002-observation-capabilities",
        help="Scan SCN-002 detector-observation code for prohibited real-data, broker, runtime, and PnL capabilities.",
    )
    scn002_observation_scan_parser.add_argument(
        "--path",
        type=Path,
        default=Path(__file__).resolve().parent / "scenarios" / "scn002_false_breakout_observation.py",
    )
    scn002_observation_scan_parser.set_defaults(func=command_scan_scn002_observation_capabilities)

    scn002_backtest_contract_parser = subparsers.add_parser(
        "show-scn002-backtest-contract",
        help="Print the SCN-002 read-only backtest preflight contract without reading market files.",
    )
    scn002_backtest_contract_parser.set_defaults(func=command_show_scn002_backtest_contract)

    scn002_backtest_preflight_parser = subparsers.add_parser(
        "prepare-scn002-backtest-preflight",
        help="Write non-trading SCN-002 read-only backtest preflight rows from reviewed observation artifacts only.",
    )
    scn002_backtest_preflight_parser.add_argument("--observations", type=Path, required=True)
    scn002_backtest_preflight_parser.add_argument("--out", type=Path, required=True)
    scn002_backtest_preflight_parser.set_defaults(func=command_prepare_scn002_backtest_preflight)

    scn002_backtest_validate_parser = subparsers.add_parser(
        "validate-scn002-backtest-preflight",
        help="Validate SCN-002 read-only backtest preflight rows and reject trading/outcome fields.",
    )
    scn002_backtest_validate_parser.add_argument("--preflight", type=Path, required=True)
    scn002_backtest_validate_parser.set_defaults(func=command_validate_scn002_backtest_preflight)

    scn002_backtest_scan_parser = subparsers.add_parser(
        "scan-scn002-backtest-capabilities",
        help="Scan SCN-002 read-only backtest preflight code for prohibited real-data, broker, runtime, and PnL capabilities.",
    )
    scn002_backtest_scan_parser.add_argument(
        "--path",
        type=Path,
        default=Path(__file__).resolve().parent / "scenarios" / "scn002_false_breakout_backtest.py",
    )
    scn002_backtest_scan_parser.set_defaults(func=command_scan_scn002_backtest_capabilities)

    scn002_outcome_label_contract_parser = subparsers.add_parser(
        "show-scn002-outcome-label-contract",
        help="Print the SCN-002 read-only outcome-label scaffold contract without reading market files.",
    )
    scn002_outcome_label_contract_parser.set_defaults(func=command_show_scn002_outcome_label_contract)

    scn002_outcome_label_prepare_parser = subparsers.add_parser(
        "prepare-scn002-outcome-labels",
        help="Write non-trading SCN-002 outcome-label scaffold rows from reviewed preflight artifacts only.",
    )
    scn002_outcome_label_prepare_parser.add_argument("--preflight", type=Path, required=True)
    scn002_outcome_label_prepare_parser.add_argument("--out", type=Path, required=True)
    scn002_outcome_label_prepare_parser.set_defaults(func=command_prepare_scn002_outcome_labels)

    scn002_outcome_label_validate_parser = subparsers.add_parser(
        "validate-scn002-outcome-labels",
        help="Validate SCN-002 outcome-label scaffold rows and reject computed outcome/PnL fields.",
    )
    scn002_outcome_label_validate_parser.add_argument("--labels", type=Path, required=True)
    scn002_outcome_label_validate_parser.set_defaults(func=command_validate_scn002_outcome_labels)

    scn002_outcome_label_scan_parser = subparsers.add_parser(
        "scan-scn002-outcome-label-capabilities",
        help="Scan SCN-002 outcome-label scaffold code for prohibited real-data, broker, runtime, label computation, and PnL capabilities.",
    )
    scn002_outcome_label_scan_parser.add_argument(
        "--path",
        type=Path,
        default=Path(__file__).resolve().parent / "scenarios" / "scn002_false_breakout_outcome_labels.py",
    )
    scn002_outcome_label_scan_parser.set_defaults(func=command_scan_scn002_outcome_label_capabilities)

    scn002_outcome_data_window_contract_parser = subparsers.add_parser(
        "show-scn002-outcome-data-window-contract",
        help="Print the SCN-002 read-only outcome data-window scaffold contract without reading market files.",
    )
    scn002_outcome_data_window_contract_parser.set_defaults(func=command_show_scn002_outcome_data_window_contract)

    scn002_outcome_data_window_prepare_parser = subparsers.add_parser(
        "prepare-scn002-outcome-data-windows",
        help="Write non-trading SCN-002 outcome data-window boundary scaffold rows from reviewed label scaffold artifacts only.",
    )
    scn002_outcome_data_window_prepare_parser.add_argument("--labels", type=Path, required=True)
    scn002_outcome_data_window_prepare_parser.add_argument("--out", type=Path, required=True)
    scn002_outcome_data_window_prepare_parser.set_defaults(func=command_prepare_scn002_outcome_data_windows)

    scn002_outcome_data_window_validate_parser = subparsers.add_parser(
        "validate-scn002-outcome-data-windows",
        help="Validate SCN-002 outcome data-window scaffold rows and reject market/outcome/PnL fields.",
    )
    scn002_outcome_data_window_validate_parser.add_argument("--windows", type=Path, required=True)
    scn002_outcome_data_window_validate_parser.set_defaults(func=command_validate_scn002_outcome_data_windows)

    scn002_outcome_data_window_scan_parser = subparsers.add_parser(
        "scan-scn002-outcome-data-window-capabilities",
        help="Scan SCN-002 outcome data-window scaffold code for prohibited real-data, broker, runtime, label computation, and PnL capabilities.",
    )
    scn002_outcome_data_window_scan_parser.add_argument(
        "--path",
        type=Path,
        default=Path(__file__).resolve().parent / "scenarios" / "scn002_false_breakout_outcome_windows.py",
    )
    scn002_outcome_data_window_scan_parser.set_defaults(func=command_scan_scn002_outcome_data_window_capabilities)

    scn002_outcome_data_window_materialization_contract_parser = subparsers.add_parser(
        "show-scn002-outcome-data-window-materialization-contract",
        help="Print the SCN-002 read-only outcome data-window materialization boundary contract without reading market files.",
    )
    scn002_outcome_data_window_materialization_contract_parser.set_defaults(
        func=command_show_scn002_outcome_data_window_materialization_contract
    )

    scn002_outcome_data_window_materialization_prepare_parser = subparsers.add_parser(
        "prepare-scn002-outcome-data-window-materialization",
        help="Write non-trading SCN-002 materialization boundary scaffold rows from reviewed data-window artifacts only.",
    )
    scn002_outcome_data_window_materialization_prepare_parser.add_argument("--windows", type=Path, required=True)
    scn002_outcome_data_window_materialization_prepare_parser.add_argument("--out", type=Path, required=True)
    scn002_outcome_data_window_materialization_prepare_parser.set_defaults(
        func=command_prepare_scn002_outcome_data_window_materialization
    )

    scn002_outcome_data_window_materialization_validate_parser = subparsers.add_parser(
        "validate-scn002-outcome-data-window-materialization",
        help="Validate SCN-002 materialization boundary scaffold rows and reject market/outcome/PnL fields.",
    )
    scn002_outcome_data_window_materialization_validate_parser.add_argument(
        "--materialization",
        type=Path,
        required=True,
    )
    scn002_outcome_data_window_materialization_validate_parser.set_defaults(
        func=command_validate_scn002_outcome_data_window_materialization
    )

    scn002_outcome_data_window_materialization_scan_parser = subparsers.add_parser(
        "scan-scn002-outcome-data-window-materialization-capabilities",
        help="Scan SCN-002 materialization scaffold code for prohibited real-data, broker, runtime, label computation, and PnL capabilities.",
    )
    scn002_outcome_data_window_materialization_scan_parser.add_argument(
        "--path",
        type=Path,
        default=Path(__file__).resolve().parent / "scenarios" / "scn002_false_breakout_outcome_windows.py",
    )
    scn002_outcome_data_window_materialization_scan_parser.set_defaults(
        func=command_scan_scn002_outcome_data_window_materialization_capabilities
    )

    scn002_outcome_data_window_binding_contract_parser = subparsers.add_parser(
        "show-scn002-outcome-data-window-reviewed-manifest-binding-contract",
        help="Print the SCN-002 read-only reviewed-manifest binding metadata contract without reading manifest or market files.",
    )
    scn002_outcome_data_window_binding_contract_parser.set_defaults(
        func=command_show_scn002_outcome_data_window_reviewed_manifest_binding_contract
    )

    scn002_outcome_data_window_binding_prepare_parser = subparsers.add_parser(
        "prepare-scn002-outcome-data-window-reviewed-manifest-binding",
        help="Write non-trading SCN-002 reviewed-manifest binding metadata rows from reviewed materialization artifacts only.",
    )
    scn002_outcome_data_window_binding_prepare_parser.add_argument("--materialization", type=Path, required=True)
    scn002_outcome_data_window_binding_prepare_parser.add_argument("--out", type=Path, required=True)
    scn002_outcome_data_window_binding_prepare_parser.set_defaults(
        func=command_prepare_scn002_outcome_data_window_reviewed_manifest_binding
    )

    scn002_outcome_data_window_binding_validate_parser = subparsers.add_parser(
        "validate-scn002-outcome-data-window-reviewed-manifest-binding",
        help="Validate SCN-002 reviewed-manifest binding metadata rows and reject manifest/market/outcome/PnL fields.",
    )
    scn002_outcome_data_window_binding_validate_parser.add_argument("--binding", type=Path, required=True)
    scn002_outcome_data_window_binding_validate_parser.set_defaults(
        func=command_validate_scn002_outcome_data_window_reviewed_manifest_binding
    )

    scn002_outcome_data_window_binding_scan_parser = subparsers.add_parser(
        "scan-scn002-outcome-data-window-reviewed-manifest-binding-capabilities",
        help="Scan SCN-002 reviewed-manifest binding scaffold code for prohibited data, broker, runtime, label computation, and PnL capabilities.",
    )
    scn002_outcome_data_window_binding_scan_parser.add_argument(
        "--path",
        type=Path,
        default=Path(__file__).resolve().parent / "scenarios" / "scn002_false_breakout_outcome_windows.py",
    )
    scn002_outcome_data_window_binding_scan_parser.set_defaults(
        func=command_scan_scn002_outcome_data_window_reviewed_manifest_binding_capabilities
    )

    scn002_reviewed_manifest_metadata_validation_contract_parser = subparsers.add_parser(
        "show-scn002-reviewed-manifest-metadata-validation-contract",
        help="Print the SCN-002 read-only reviewed-manifest metadata validation scaffold contract without reading manifest or market files.",
    )
    scn002_reviewed_manifest_metadata_validation_contract_parser.set_defaults(
        func=command_show_scn002_reviewed_manifest_metadata_validation_contract
    )

    scn002_reviewed_manifest_metadata_validation_prepare_parser = subparsers.add_parser(
        "prepare-scn002-reviewed-manifest-metadata-validation",
        help="Write non-trading SCN-002 reviewed-manifest metadata validation rows from reviewed binding artifacts only.",
    )
    scn002_reviewed_manifest_metadata_validation_prepare_parser.add_argument("--binding", type=Path, required=True)
    scn002_reviewed_manifest_metadata_validation_prepare_parser.add_argument(
        "--source-binding-implementation-review-id",
        required=True,
    )
    scn002_reviewed_manifest_metadata_validation_prepare_parser.add_argument("--out", type=Path, required=True)
    scn002_reviewed_manifest_metadata_validation_prepare_parser.set_defaults(
        func=command_prepare_scn002_reviewed_manifest_metadata_validation
    )

    scn002_reviewed_manifest_metadata_validation_validate_parser = subparsers.add_parser(
        "validate-scn002-reviewed-manifest-metadata-validation",
        help="Validate SCN-002 reviewed-manifest metadata validation rows and reject manifest/market/outcome/PnL fields.",
    )
    scn002_reviewed_manifest_metadata_validation_validate_parser.add_argument(
        "--metadata-validation",
        type=Path,
        required=True,
    )
    scn002_reviewed_manifest_metadata_validation_validate_parser.set_defaults(
        func=command_validate_scn002_reviewed_manifest_metadata_validation
    )

    scn002_reviewed_manifest_metadata_validation_scan_parser = subparsers.add_parser(
        "scan-scn002-reviewed-manifest-metadata-validation-capabilities",
        help="Scan SCN-002 metadata validation scaffold code for prohibited data, broker, runtime, label computation, and PnL capabilities.",
    )
    scn002_reviewed_manifest_metadata_validation_scan_parser.add_argument(
        "--path",
        type=Path,
        default=Path(__file__).resolve().parent / "scenarios" / "scn002_false_breakout_outcome_windows.py",
    )
    scn002_reviewed_manifest_metadata_validation_scan_parser.set_defaults(
        func=command_scan_scn002_reviewed_manifest_metadata_validation_capabilities
    )

    verify_public_seed_checksum_parser = subparsers.add_parser(
        "verify-public-seed-checksum",
        help="Blocked placeholder; real seed checksum byte verification requires a later review gate.",
    )
    verify_public_seed_checksum_parser.add_argument("--manifest", action="append", default=[])
    verify_public_seed_checksum_parser.add_argument("--out", type=Path, default=None)
    verify_public_seed_checksum_parser.set_defaults(func=command_blocked)

    public_seed_checksum_status_parser = subparsers.add_parser(
        "public-seed-checksum-status",
        help="Blocked placeholder; requires generated checksum verification outputs from a later review gate.",
    )
    public_seed_checksum_status_parser.add_argument("--out", type=Path, default=None)
    public_seed_checksum_status_parser.set_defaults(func=command_blocked)

    real_manifest_metadata_status_parser = subparsers.add_parser(
        "real-manifest-metadata-status",
        help="Blocked placeholder; requires a later review gate.",
    )
    real_manifest_metadata_status_parser.add_argument("--out", type=Path, default=None)
    real_manifest_metadata_status_parser.set_defaults(func=command_blocked)

    real_manifest_metadata_scan_parser = subparsers.add_parser(
        "scan-real-manifest-metadata-capabilities",
        help="Blocked placeholder; requires a later review gate.",
    )
    real_manifest_metadata_scan_parser.set_defaults(func=command_blocked)

    for name in [
        "inventory-manifests",
        "manifest-inventory-status",
        "scan-manifest-inventory-capabilities",
        "build-split-manifest",
        "observe-offline",
    ]:
        blocked_parser = subparsers.add_parser(name, help="Blocked placeholder; requires a later review gate.")
        blocked_parser.set_defaults(func=command_blocked)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
