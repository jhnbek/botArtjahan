"""Fit separate research models from causally prepared Bybit scenario examples.

The original pixel-based models are preserved. Train/validation/test groups are
fixed by preparation, never reshuffled to improve scores. Test cases never fit
weights, normalization, regularization, or a threshold; historical test results
were already observed in the earlier experiment, so this is not a fresh test.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path

from .align_scenario_market import COLLECTION, digest, read_jsonl
from .prepare_scenario_training import load_reviews
from .scenario_direction_model import (MODEL_KIND as DIRECTION_KIND,
                                       evaluate_direction, fit_direction_classifier,
                                       validate_direction_model)
from .scenario_image_features import FEATURE_NAMES, FEATURE_SCHEMA_VERSION as ENTRY_SCHEMA
from .scenario_model import validate_model
from .scenario_prerequisites import (PREREQUISITE_FEATURE_NAMES,
                                     FEATURE_SCHEMA_VERSION as DIRECTION_SCHEMA)
from .train_scenario_entry_model import fit_entry_readiness, evaluate_readiness

MODULE = Path(__file__).resolve().parent
SPLITS = ("train", "validation", "test")
MANDATORY_SKIPPED = {54, 85}
DAY, HOUR = 86_400_000, 3_600_000
FEATURE_CODE = {"scenario_prerequisites.py", "scenario_image_features.py",
                "scenario_model.py", "scenario_training_rules.py", "scenario_author_entry.py", "scenario_hourly_atr.py"}
REQUIRED_SOURCES = {"visual_analysis/scenario_analysis.jsonl",
                    "training/market_daily_alignment.jsonl",
                    "training/market_hourly_alignment.jsonl",
                    "training/market_daily_anchor_reviews.jsonl",
                    "training/image_extraction_audit.json",
                    "training/direction_training_ledger.jsonl"}
LIMITATIONS = [
    "Selected author demonstrations include stop-outs; labels are not all profitable trades.",
    "Earlier verified waiting states are not losing-trade labels.",
    "Intrabar demonstrations may use a pre-hour proxy; exact execution prices are not learned.",
    "Caption-derived OR branches are valid examples, not proof of the author's earliest chosen entry.",
    "The author's blue levels are supplied; autonomous level discovery is not trained.",
    "Instrument groups were fixed before eligibility, but their earlier test results were already observed.",
    "Metrics measure author-label agreement, not profit, win rate, or continuous-market detection.",
    "Models do not learn stop or take-profit regression and never authorize orders.",
    "H1 ATR-derived labels use the documented project Pine-excerpt convention; individual execution hours were not confirmed by the author.",
]


def _contained(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("provenance_path_outside_source_root")
    return path


def _check_hashes(root: Path, hashes: dict, required: set[str] = frozenset()) -> None:
    if not isinstance(hashes, dict) or not required.issubset(hashes):
        raise ValueError("missing_required_provenance_hashes")
    for relative, expected in hashes.items():
        if digest(_contained(root, relative)) != expected:
            raise ValueError(f"prepared_source_changed: {relative}")


def _features(values: dict, names: list[str], kind: str) -> None:
    if not isinstance(values, dict) or set(values) != set(names):
        raise ValueError(f"invalid_{kind}_feature_schema")
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
           for value in values.values()):
        raise ValueError(f"invalid_{kind}_feature_values")


def validate_cases(cases: list[dict], preparation: dict) -> None:
    """Validate source coverage, disjoint frozen groups, and temporal boundaries."""
    skipped = set(preparation.get("user_skipped", []))
    if not MANDATORY_SKIPPED.issubset(skipped):
        raise ValueError("stale_user_contract_must_skip_54_and_85")
    if preparation.get("automatic_order_execution_allowed") is not False:
        raise ValueError("prepared_data_must_not_authorize_orders")
    target = preparation.get("target_pairs")
    if target != 409 - len(skipped) or preparation.get("eligible_pairs") != len(cases):
        raise ValueError("prepared_coverage_count_mismatch")
    seen, group_splits = set(), {}
    for case in cases:
        sid = case.get("scenario_id")
        if isinstance(sid, bool) or not isinstance(sid, int) or not 1 <= sid <= 409 or sid in seen or sid in skipped:
            raise ValueError("duplicate_invalid_or_user_skipped_scenario")
        seen.add(sid)
        group, split = case.get("group"), case.get("split")
        if not isinstance(group, str) or not group.strip() or group != group.strip().upper() or split not in SPLITS:
            raise ValueError("invalid_frozen_instrument_group_or_partition")
        if group in group_splits and group_splits[group] != split:
            raise ValueError("instrument_group_leaked_across_partitions")
        group_splits[group] = split
        if case.get("direction") not in {"long", "short"}:
            raise ValueError("unreviewed_direction_label")
        _features(case.get("direction_features"), list(PREREQUISITE_FEATURE_NAMES), "direction")
        states, times, offsets = case.get("states"), case.get("state_close_times_ms"), case.get("offsets")
        if not states or not isinstance(times, list) or not isinstance(offsets, list) or len(states) != len(times) or len(times) != len(offsets):
            raise ValueError("invalid_entry_state_episode")
        for state in states:
            _features(state, list(FEATURE_NAMES), "entry")
        decision = case.get("decision_time_ms")
        signal = case.get("signal_d1_open_time_ms")
        latest = case.get("latest_closed_d1_time_ms")
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0
               for value in [decision, signal, latest, *times]):
            raise ValueError("invalid_market_time_boundary")
        if (signal % DAY or latest % DAY or any(t % HOUR for t in times)
                or any(a >= b for a, b in zip(times, times[1:]))
                or times[-1] != decision or offsets != [(decision-t)//HOUR for t in times]):
            raise ValueError("invalid_ordered_market_state_boundaries")
        if any(t < signal + DAY or t < latest for t in times):
            raise ValueError("entry_state_precedes_confirmed_daily_signal")
        if case.get("entry_resolution") not in {"closed_hourly_bar", "pre_hour_proxy_not_exact_intrabar_entry"}:
            raise ValueError("unknown_entry_time_resolution")
        if case.get("earlier_states_are_failed_trades") is not False:
            raise ValueError("waits_must_not_be_losing_trade_labels")


def load_prepared_cases(collection: Path = COLLECTION) -> tuple[list[dict], dict, dict]:
    """Reject stale vectors, annotations, reviews, images, markets, or code."""
    collection = Path(collection)
    folder = collection / "training/market_v2"
    report_path, cases_path = folder / "preparation_report.json", folder / "cases.jsonl"
    preparation = json.loads(report_path.read_text(encoding="utf-8"))
    if preparation.get("cases_sha256") != digest(cases_path):
        raise ValueError("prepared_cases_changed")
    if preparation.get("preparation_sha256") != digest(MODULE / "prepare_market_training.py"):
        raise ValueError("preparation_code_changed_rebuild_required")
    _check_hashes(collection, preparation.get("source_sha256"), REQUIRED_SOURCES)
    _check_hashes(MODULE, preparation.get("feature_code_sha256"), FEATURE_CODE)
    _, actual_reviews = load_reviews(collection)
    if preparation.get("review_sources_sha256") != actual_reviews:
        raise ValueError("prepared_anchor_reviews_changed")
    cases = read_jsonl(cases_path)
    validate_cases(cases, preparation)
    checked = {}
    for case in cases:
        expected = dict(case.get("source_images_sha256", {}))
        for timeframe in ("daily", "hourly"):
            expected[case[f"source_{timeframe}_cache"]] = case[f"source_{timeframe}_cache_sha256"]
        if not {f'images/1D_{case["scenario_id"]}.jpg', f'images/1H_{case["scenario_id"]}.jpg'}.issubset(expected):
            raise ValueError("missing_prepared_original_image_hashes")
        for relative, checksum in expected.items():
            if relative not in checked:
                checked[relative] = digest(_contained(collection, relative))
            if checked[relative] != checksum:
                raise ValueError(f"prepared_case_source_changed: {relative}")
    provenance = dict(preparation_report_sha256=digest(report_path),
                      cases_sha256=digest(cases_path),
                      source_sha256=preparation["source_sha256"],
                      review_sources_sha256=actual_reviews,
                      preparation_sha256=preparation["preparation_sha256"],
                      feature_code_sha256=preparation["feature_code_sha256"],
                      training_code_sha256=digest(Path(__file__)),
                      fitting_helper_sha256={name: digest(MODULE / name) for name in
                                             ("scenario_direction_model.py", "train_scenario_entry_model.py")})
    return cases, preparation, provenance


def _partitions(cases: list[dict]) -> dict[str, list[dict]]:
    return {name: [case for case in cases if case["split"] == name] for name in SPLITS}


def _split_report(partitions: dict) -> dict:
    return {name: dict(scenario_ids=sorted(case["scenario_id"] for case in rows),
                       groups=sorted({case["group"] for case in rows}), scenarios=len(rows),
                       author_directions=dict(Counter(case["direction"] for case in rows)),
                       entry_label_sources=dict(Counter(case.get("entry_label_source", "reviewed_author_entry_anchor") for case in rows)),
                       waits=sum(len(case.get("states", []))-1 for case in rows))
            for name, rows in partitions.items()}


def fit_market_models(cases: list[dict], preparation: dict, provenance: dict | None = None) -> tuple[dict, dict, dict]:
    """Pure fitting operation; caller writes artifacts only after both validate."""
    validate_cases(cases, preparation)
    provenance = provenance or {}
    entry_parts = _partitions(cases)
    direction_cases = [dict(case, features=case["direction_features"]) for case in cases
                       if case.get("direction_daily_only_eligible") is True]
    direction_parts = _partitions(direction_cases)
    if any(not entry_parts[s] or not direction_parts[s] for s in SPLITS):
        raise ValueError("every_fixed_partition_needs_entry_and_direction_observations")
    if len(entry_parts["train"]) < 5 or not any(len(c["states"]) > 1 for c in entry_parts["train"]):
        raise ValueError("entry_train_requires_five_cases_and_verified_waits")
    if len(direction_parts["train"]) < 4 or {c["direction"] for c in direction_parts["train"]} != {"long", "short"}:
        raise ValueError("direction_train_requires_four_cases_and_both_sides")

    # Each fallback is chosen from validation availability before any test score.
    d_two_classes = {c["direction"] for c in direction_parts["validation"]} == {"long", "short"}
    d_grid = (.01, .1, 1., 10.) if d_two_classes else (.1,)
    d_selection = "validation_balanced_accuracy_then_log_loss" if d_two_classes else "fixed_default_no_two_class_validation"
    majority = Counter(c["direction"] for c in direction_parts["train"]).most_common(1)[0][0]
    d_candidates = []
    for regularization in d_grid:
        fitted = fit_direction_classifier(direction_parts["train"], regularization)
        metrics = evaluate_direction(direction_parts["validation"], fitted, baseline_direction=majority)
        d_candidates.append(dict(regularization=regularization, validation=metrics))
    d_selected = min(d_candidates, key=lambda row: (-(row["validation"]["balanced_accuracy"] or 0.),
                                                   row["validation"]["log_loss"], -row["regularization"]))
    d_fit_cases = direction_parts["train"] + direction_parts["validation"]
    d_fitted = fit_direction_classifier(d_fit_cases, d_selected["regularization"])

    e_has_waits = any(len(c["states"]) > 1 for c in entry_parts["validation"])
    e_grid = (.01, .1, 1.) if e_has_waits else (.1,)
    e_selection = "validation_balanced_accuracy" if e_has_waits else "fixed_default_no_two_class_validation"
    names = list(FEATURE_NAMES)
    e_candidates = []
    for regularization in e_grid:
        fitted = fit_entry_readiness(entry_parts["train"], names, regularization)
        metrics = evaluate_readiness(entry_parts["validation"], names, fitted)
        e_candidates.append(dict(regularization=regularization, validation=metrics))
    e_selected = max(e_candidates, key=lambda row: ((row["validation"]["balanced_accuracy"] or 0.), row["regularization"]))
    e_fit_cases = entry_parts["train"] + entry_parts["validation"]
    e_fitted = fit_entry_readiness(e_fit_cases, names, e_selected["regularization"])

    # Both final fits are frozen before either held-out result is evaluated.
    majority_final = Counter(c["direction"] for c in d_fit_cases).most_common(1)[0][0]
    d_test = evaluate_direction(direction_parts["test"], d_fitted, baseline_direction=majority_final)
    e_test = evaluate_readiness(entry_parts["test"], names, e_fitted)
    timestamp = datetime.now(timezone.utc).isoformat()
    coverage = {name: preparation[name] for name in ("target_pairs", "eligible_pairs", "user_skipped")}
    common = dict(research_only=True, automatic_order_execution_allowed=False,
                  trained_at_utc=timestamp, provenance=provenance, limitations=LIMITATIONS,
                  corpus_coverage=coverage, trained_on_successful_examples_only=False,
                  stop_take_regression_trained=False, level_discovery_trained=False)
    direction = dict(schema_version=1, model_kind=DIRECTION_KIND, feature_schema_version=DIRECTION_SCHEMA,
                     feature_names=list(PREREQUISITE_FEATURE_NAMES), classes=["short", "long"],
                     **d_fitted, **common, splits=_split_report(direction_parts),
                     fitted_scenario_ids=sorted(c["scenario_id"] for c in d_fit_cases),
                     probability_meaning="uncalibrated author-long label probability, never profit probability")
    entry = dict(schema_version=1, model_kind="entry_demonstration_readiness", feature_schema_version=ENTRY_SCHEMA,
                 feature_names=names, **e_fitted, **common, input_timeframe="1h",
                 execution_mode="research_only", threshold=None, profit_probability_model=False,
                 splits=_split_report(entry_parts), fitted_scenario_ids=sorted(c["scenario_id"] for c in e_fit_cases))
    validate_direction_model(direction)
    validate_model(entry)
    test_waits = sum(len(c["states"])-1 for c in entry_parts["test"])
    warnings = ["Previously observed instrument test groups: not a new independent experiment."]
    if test_waits < 20:
        warnings.append(f"Only {test_waits} verified waiting states in entry test; class-wise estimates are fragile.")
    if min(Counter(c["direction"] for c in direction_parts["test"]).get(s, 0) for s in ("long", "short")) < 20:
        warnings.append("Fewer than20 test examples in at least one direction; report raw counts alongside accuracy.")
    if len(cases) < preparation["target_pairs"]:
        warnings.append(f"Coverage remains partial: {len(cases)}/{preparation['target_pairs']} target pairs prepared.")
    report = dict(schema_version=1, trained=True, **coverage, all_target_pairs_prepared=len(cases)==preparation["target_pairs"],
                  direction=dict(eligible_scenarios=len(direction_cases), splits=_split_report(direction_parts),
                                 selection_method=d_selection, selected_regularization=d_selected["regularization"],
                                 validation_search=d_candidates, held_out_test=d_test,
                                 fitted_scenario_ids=direction["fitted_scenario_ids"]),
                  entry=dict(eligible_scenarios=len(cases), splits=_split_report(entry_parts),
                             selection_method=e_selection, selected_regularization=e_selected["regularization"],
                             validation_search=e_candidates, held_out_test=e_test,
                             fitted_scenario_ids=entry["fitted_scenario_ids"],
                             resolution_counts=dict(Counter(c["entry_resolution"] for c in cases))),
                  provenance=provenance, limitations=LIMITATIONS, warnings=warnings,
                  historical_test_was_previously_observed=True, profitability_evaluated=False,
                  continuous_market_entry_detection_evaluated=False, automatic_order_execution_allowed=False)
    return direction, entry, report


def run(collection: Path = COLLECTION) -> dict:
    collection = Path(collection)
    cases, preparation, provenance = load_prepared_cases(collection)
    direction, entry, report = fit_market_models(cases, preparation, provenance)
    folder = collection / "training/market_v2"
    # Deliberately no output override: the previous training/*.json artifacts stay intact.
    for name, artifact in (("direction_model.json", direction), ("scenario_model.json", entry)):
        (folder / name).write_text(json.dumps(artifact, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    report["model_sha256"] = {name: digest(folder / name) for name in ("direction_model.json", "scenario_model.json")}
    (folder / "training_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", type=Path, default=COLLECTION)
    args = parser.parse_args()
    result = run(args.collection)
    print(json.dumps({key: result[key] for key in ("trained", "target_pairs", "eligible_pairs", "user_skipped", "warnings")}, indent=2))
