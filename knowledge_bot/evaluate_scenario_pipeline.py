"""Evaluate the direction -> entry pipeline on its shared held-out scenarios.

No model is fitted, parameter selected or threshold tuned here. H1 features
are reconstructed using the predicted direction, not the author's direction.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from .scenario_direction_model import build_direction_cases, load_direction_model, shared_corpus_splits
from .scenario_model import READINESS_MODEL_KIND, features_for_entry, load_model, score_features
from .train_scenario_entry_model import build_cases


DEFAULT_COLLECTION = Path(__file__).resolve().parents[1] / "_knowledge_base/manual_reviews/scenarios_dzhahan_20260925"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _index(records: list[dict], label: str) -> dict[int, dict]:
    result = {}
    for record in records:
        sid = record["scenario_id"]
        if isinstance(sid, bool) or not isinstance(sid, int) or sid in result:
            raise ValueError(f"Invalid or duplicate scenario in {label}")
        result[sid] = record
    return result


def _ids(values: Any, label: str) -> set[int]:
    if (not isinstance(values, list) or any(isinstance(value, bool) or not isinstance(value, int) for value in values)
            or len(set(values)) != len(values)):
        raise ValueError(f"Invalid scenario IDs in {label}")
    return set(values)


def _direction_score(case: dict, model: dict) -> tuple[str, float, float]:
    features = case["features"]
    if set(features) != set(model["feature_names"]):
        raise ValueError("Direction evaluation feature schema differs from fitted schema")
    values = [features[name] for name in model["feature_names"]]
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in values):
        raise ValueError("Invalid direction evaluation features")
    logit = model["bias"] + math.fsum(
        ((value - mean) / scale) * weight
        for value, mean, scale, weight in zip(values, model["mean"], model["scale"], model["weights"])
    )
    if not math.isfinite(logit):
        raise ValueError("Non-finite direction evaluation score")
    probability = 1 / (1 + math.exp(-max(-60., min(60., logit))))
    return "long" if probability >= .5 else "short", logit, probability


def evaluate_pipeline(collection: str | Path = DEFAULT_COLLECTION) -> dict:
    """Write pipeline_evaluation.json using only common, untouched test groups."""
    collection = Path(collection)
    training = collection / "training"
    paths = {
        "direction_model": training / "direction_model.json",
        "entry_model": training / "scenario_model.json",
        "annotations": collection / "visual_analysis/scenario_analysis.jsonl",
        "reviewed_entry_extraction": training / "reviewed_entry_extraction.json",
        "original_daily_extraction": training / "image_extraction_audit.json",
        "entry_training_cases": training / "entry_training_cases.jsonl",
    }
    direction_model = load_direction_model(paths["direction_model"])
    entry_model = load_model(paths["entry_model"])
    if entry_model["model_kind"] != READINESS_MODEL_KIND:
        raise ValueError("Pipeline entry/wait evaluation requires the readiness model")
    hashes = {name: _digest(path) for name, path in paths.items()}
    expected_sources = (
        (direction_model.get("provenance", {}).get("author_annotation_sha256"), hashes["annotations"]),
        (direction_model.get("provenance", {}).get("original_extraction_audit_sha256"), hashes["original_daily_extraction"]),
        (entry_model.get("training", {}).get("source_annotations_sha256"), hashes["annotations"]),
        (entry_model.get("training", {}).get("image_audit_sha256"), hashes["reviewed_entry_extraction"]),
    )
    if any(expected != actual for expected, actual in expected_sources):
        raise ValueError("Model provenance differs from evaluation annotations or extraction audits")

    rows = _jsonl(paths["annotations"])
    annotations = _index(rows, "annotations")
    fixed_splits = shared_corpus_splits(rows)
    groups = {sid: (row.get("instrument") or "").strip().upper() for sid, row in annotations.items()}
    daily_audit = json.loads(paths["original_daily_extraction"].read_text(encoding="utf-8"))
    entry_audit = json.loads(paths["reviewed_entry_extraction"].read_text(encoding="utf-8"))
    daily_cases, _ = build_direction_cases(collection, daily_audit, rows)
    reconstructed_cases, _ = build_cases(collection, entry_audit)
    daily = _index(daily_cases, "D1 cases")
    reconstructed = _index(reconstructed_cases, "reconstructed H1 cases")
    stored = _index(_jsonl(paths["entry_training_cases"]), "stored H1 cases")
    hourly = _index([record for record in entry_audit["records"] if record.get("timeframe", "").upper() == "1H"], "H1 extraction")

    direction_splits = direction_model.get("splits", {})
    fit_partitions = direction_model.get("fit_partitions")
    if not isinstance(fit_partitions, list) or set(fit_partitions) != {"train", "validation"}:
        raise ValueError("Direction fit partitions must identify train and validation only")
    direction_test = _ids(direction_splits.get("test", {}).get("scenario_ids"), "direction test")
    direction_fit = set().union(*(_ids(direction_splits.get(name, {}).get("scenario_ids"), f"direction {name}")
                                 for name in fit_partitions))
    entry_test = _ids(entry_model.get("training", {}).get("test_scenario_ids"), "entry test")
    entry_fit = _ids(entry_model.get("training", {}).get("fitted_scenario_ids"), "entry fit")
    if not (direction_fit | direction_test | entry_fit | entry_test) <= annotations.keys():
        raise ValueError("Model split IDs are absent from the annotated corpus")
    if direction_test & direction_fit or entry_test & entry_fit:
        raise ValueError("A model's fitted and test IDs overlap")
    common = sorted(direction_test & entry_test & daily.keys() & stored.keys() & reconstructed.keys())
    fitted_groups = {groups[sid] for sid in direction_fit | entry_fit}
    for sid in common:
        if fixed_splits.get(sid) != "test" or groups[sid] in fitted_groups:
            raise ValueError(f"Pipeline test instrument was fitted or violates fixed split: #{sid}")
        if daily[sid].get("split") != "test" or stored[sid].get("split") != "test":
            raise ValueError(f"Case split disagrees with model test partition: #{sid}")
        for field in ("group", "direction", "offsets", "anchor_x", "last_feature_bar_x", "daily_close_bar_x", "decision_timing"):
            if stored[sid].get(field) != reconstructed[sid].get(field):
                raise ValueError(f"Stored H1 {field} differs from reviewed reconstruction: #{sid}")
        if stored[sid]["group"] != groups[sid] or daily[sid]["group"] != groups[sid]:
            raise ValueError(f"Instrument identity differs across pipeline inputs: #{sid}")
        if daily[sid]["direction"] != annotations[sid]["direction"] or stored[sid]["direction"] != annotations[sid]["direction"]:
            raise ValueError(f"Author direction differs across pipeline inputs: #{sid}")

    details = []
    waiting = {"total": 0, "predicted_wait": 0, "predicted_entry": 0, "correct_direction_and_wait": 0}
    for sid in common:
        predicted, daily_logit, probability = _direction_score(daily[sid], direction_model)
        author_direction = annotations[sid]["direction"]
        correct_direction = predicted == author_direction
        extraction = hourly[sid]
        bars = extraction.get("bars", extraction.get("decoded_bars"))
        level = extraction.get("level", extraction.get("selected_level_pixel_price"))
        states = []
        for index, offset in enumerate(stored[sid]["offsets"]):
            prefix = bars[:-offset] if offset else bars
            # Recomputing is essential: stored features are canonicalized using
            # the AUTHOR direction, which would hide upstream prediction errors.
            features = features_for_entry(prefix, level, predicted)
            score = score_features(features, entry_model)
            predicted_entry = score >= 0
            selected = index == len(stored[sid]["offsets"]) - 1
            states.append({"offset_before_author_entry": offset,
                           "author_label": "entry" if selected else "wait",
                           "raw_entry_score": score,
                           "demonstrated_entry_pattern": predicted_entry})
            if not selected:
                waiting["total"] += 1
                waiting["predicted_entry" if predicted_entry else "predicted_wait"] += 1
                waiting["correct_direction_and_wait"] += int(correct_direction and not predicted_entry)
        selected_entry = states[-1]["demonstrated_entry_pattern"]
        details.append({
            "scenario_id": sid, "instrument_group": groups[sid],
            "author_direction": author_direction, "predicted_direction": predicted,
            "direction_correct": correct_direction,
            "raw_daily_direction_score": daily_logit, "author_long_probability": probability,
            "selected_state_raw_entry_score": states[-1]["raw_entry_score"],
            "selected_state_demonstrated_entry_pattern": selected_entry,
            "correct_direction_and_entry": correct_direction and selected_entry,
            "entry_state_resolution": reconstructed[sid].get("entry_state_resolution"),
            "states": states,
        })
    combined = sum(case["correct_direction_and_entry"] for case in details)
    report = {
        "schema_version": 1,
        "evaluated_at_utc": datetime.now(timezone.utc).isoformat(),
        "research_only": True, "action": "no_order", "automatic_order_execution_allowed": False,
        "models_refitted": False, "parameters_or_thresholds_tuned": False,
        "number_of_cases": len(details), "scenario_ids": common,
        "instrument_groups": sorted({groups[sid] for sid in common}),
        "direction_correct": sum(case["direction_correct"] for case in details),
        "selected_states_predicted_entry": sum(case["selected_state_demonstrated_entry_pattern"] for case in details),
        "correct_direction_and_entry": combined,
        "correct_direction_and_entry_fraction": combined / len(details) if details else None,
        "waiting_states": waiting,
        "scenarios_with_wait_states": sum(len(case["states"]) > 1 for case in details),
        "fit_group_overlap": False,
        "source_sha256": hashes,
        "metric_scope": "author direction and entry-pattern agreement on the intersection of both fixed instrument-held-out test sets",
        "waiting_label_meaning": "earlier author-unselected states after D1 close, not losing trades",
        "profitability_evaluated": False, "continuous_market_entry_detection_evaluated": False,
        "limitations": [
            "The shared held-out intersection is small and does not represent all 409 scenarios.",
            "Entry endpoints and price levels were preselected from author examples.",
            "Pixel OHLC and daily-close alignment are approximate readings of annotations.",
            "Intrabar examples use a closed-hour proxy before their actual intrabar trigger.",
            "Several wait observations may belong to the same scenario and are not independent trades.",
            "Combined agreement is not win rate, profitability, or permission to enter a trade.",
        ],
        "details": details,
    }
    (training / "pipeline_evaluation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8",
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", type=Path, default=DEFAULT_COLLECTION)
    arguments = parser.parse_args()
    result = evaluate_pipeline(arguments.collection)
    print(json.dumps({key: value for key, value in result.items() if key != "details"}, ensure_ascii=False, indent=2))
