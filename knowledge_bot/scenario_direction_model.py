"""Learn author long/short labels from closed D1 geometry, never result captions.

This is an offline research classifier of demonstrated direction. Its logistic
output is not a trade win rate. The original extraction audit supplies approximate
pixel OHLC at the D1 arrow; the user supplied the level. The classifier does not
establish either level provenance or live profitability. Entry timing is a
separate model which must additionally wait for confirmed D1 closure.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

try:
    from .scenario_prerequisites import (FEATURE_SCHEMA_VERSION, PREREQUISITE_FEATURE_NAMES,
                                         analyze_prerequisites, prerequisite_features_from_ohlc)
    from .scenario_training_rules import scenario_training_policy
    from .train_scenario_entry_model import assign_splits
except ImportError:
    from scenario_prerequisites import (FEATURE_SCHEMA_VERSION, PREREQUISITE_FEATURE_NAMES,
                                        analyze_prerequisites, prerequisite_features_from_ohlc)
    from scenario_training_rules import scenario_training_policy
    from train_scenario_entry_model import assign_splits


REPO = Path(__file__).resolve().parents[1]
DEFAULT_COLLECTION = REPO / "_knowledge_base/manual_reviews/scenarios_dzhahan_20260925"
MODEL_PATH = DEFAULT_COLLECTION / "training/direction_model.json"
MODEL_KIND = "author_direction_logistic"
DAILY_REVIEW_FILENAME = "daily_anchor_spot_reviews_20260927.jsonl"
REGULARIZATION_GRID = (.01, .1, 1., 10.)
LIMITATIONS = [
    "Approximate pixel OHLC and automatic D1 arrow extraction have not all been manually verified.",
    "Blue levels are supplied by the author; their causal historical availability is not independently established.",
    "Direction labels come from our reading of author annotations; exclusions mean uncertain extraction, not author error.",
    "The corpus contains selected author-described successful examples, not a representative stream of trades.",
    "Instrument-group holdout is not chronological walk-forward validation or a profit backtest.",
    "Logistic outputs describe author long/short labels and are not calibrated probabilities of profit.",
    "The model neither discovers levels nor selects numeric stops or targets.",
]


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Expected a finite numeric value")
    return float(value)


def validate_direction_model(artifact: Mapping[str, Any]) -> dict:
    if not isinstance(artifact, Mapping):
        raise ValueError("Direction model must be an object")
    if artifact.get("schema_version") != 1 or artifact.get("model_kind") != MODEL_KIND:
        raise ValueError("Unsupported direction model schema or task")
    if artifact.get("feature_schema_version") != FEATURE_SCHEMA_VERSION:
        raise ValueError("Unsupported prerequisite feature version")
    if artifact.get("feature_names") != list(PREREQUISITE_FEATURE_NAMES):
        raise ValueError("Unknown, reordered or missing prerequisite features")
    if artifact.get("classes") != ["short", "long"]:
        raise ValueError("Direction classes must be short, long in that order")
    if artifact.get("research_only") is not True or artifact.get("automatic_order_execution_allowed") is not False:
        raise ValueError("Direction model must remain research-only without order execution")
    result = dict(artifact)
    for name in ("mean", "scale", "weights"):
        values = artifact.get(name)
        if not isinstance(values, list) or len(values) != len(PREREQUISITE_FEATURE_NAMES):
            raise ValueError(f"Invalid {name} shape")
        result[name] = [_number(value) for value in values]
    if any(value <= 0 for value in result["scale"]):
        raise ValueError("Direction feature scales must be positive")
    result["bias"] = _number(artifact.get("bias"))
    return result


def load_direction_model(path: str | Path = MODEL_PATH) -> dict:
    source = Path(path)
    if source.stat().st_size > 2_000_000:
        raise ValueError("Unexpectedly large direction model")
    return validate_direction_model(json.loads(source.read_text(encoding="utf-8")))


def shared_corpus_splits(rows: list[dict]) -> dict[int, str]:
    """Freeze groups using ALL author scenarios, before eligibility filtering.

    The entry model must use this same universe, normalization and split seed;
    otherwise the same instrument could be test in one model and train in the
    other. Unknown instruments stay excluded. IDs/groups never become features.
    """
    cases = []
    for row in rows:
        group = (row.get("instrument") or "").strip().upper()
        if group not in ("", "H", "Н"):
            cases.append({"scenario_id": row["scenario_id"], "group": group})
    return assign_splits(cases)


def _daily_anchor_reviews(collection: Path, images: dict) -> dict[int, dict]:
    """Apply bounded visual reviews only to the exact images/extraction reviewed."""
    path = collection / "training" / DAILY_REVIEW_FILENAME
    if not path.exists():
        return {}
    audit_hash = _digest(collection / "training/image_extraction_audit.json")
    reviews = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        review = json.loads(line)
        sid = review["scenario_id"]
        if sid in reviews or sid not in images:
            raise ValueError(f"Duplicate or unavailable reviewed D1 #{sid}")
        if review.get("reviewed") is not True or not isinstance(review.get("accepted"), bool):
            raise ValueError(f"Invalid visual D1 review status #{sid}")
        ext = images[sid]
        actual_hash = _digest(collection / f"images/1D_{sid}.jpg")
        if (review.get("source_sha256") != actual_hash
                or actual_hash != ext.get("source_sha256", ext.get("sha256"))
                or review.get("source_extraction_audit_sha256") != audit_hash):
            raise ValueError(f"Visual D1 review source hash mismatch #{sid}")
        if (review.get("signal_x") != ext.get("signal_x")
                or review.get("level") != ext.get("level", ext.get("selected_level_pixel_price"))):
            raise ValueError(f"Visual D1 review no longer matches extraction #{sid}")
        reviews[sid] = review
    return reviews


def build_direction_cases(collection: Path, audit: dict, rows: list[dict]) -> tuple[list[dict], list[dict]]:
    if len(rows) != 409 or {row["scenario_id"] for row in rows} != set(range(1, 410)):
        raise ValueError("Direction corpus must contain all 409 unique scenario IDs")
    splits = shared_corpus_splits(rows)
    images = {}
    for record in audit.get("records", []):
        if str(record.get("timeframe", "")).upper() != "1D":
            continue
        sid = int(record["scenario_id"])
        if sid in images:
            raise ValueError(f"Duplicate D1 extraction for #{sid}")
        images[sid] = record
    reviews = _daily_anchor_reviews(collection, images)
    cases, ledger = [], []
    for row in rows:
        sid = row["scenario_id"]
        policy = scenario_training_policy(row)
        entry = {"scenario_id": sid, "instrument": row.get("instrument"),
                 "direction": row.get("direction"), "split": splits.get(sid),
                 "status": "excluded", "reason": None,
                 "daily_anchor_visually_reviewed": sid in reviews}
        ledger.append(entry)
        if sid not in splits:
            entry["reason"] = "unknown_instrument_cannot_prevent_group_leakage"
            continue
        if not policy["direction_training_eligible"] or not policy["daily_only_direction_eligible"]:
            entry["reason"] = "our_direction_or_daily_context_reading_requires_review"
            entry["policy_detail"] = policy.get("direction_exclusion_reasons", [])
            continue
        if sid in reviews and not reviews[sid]["accepted"]:
            entry["reason"] = reviews[sid].get("reason_code", "our_daily_anchor_reading_requires_review")
            entry["review_detail"] = reviews[sid].get("reason")
            continue
        ext = images.get(sid)
        if ext is None:
            entry["reason"] = "missing_daily_extraction"
            continue
        relative_image = f"images/1D_{sid}.jpg"
        image_hash = _digest(collection / relative_image)
        if image_hash != ext.get("source_sha256", ext.get("sha256")):
            raise ValueError(f"D1 extraction differs from image #{sid}")
        if image_hash != row.get("image_sha256", {}).get(relative_image):
            raise ValueError(f"D1 image differs from annotated original #{sid}")
        entry["daily_source_sha256"] = image_hash
        if ext.get("usable") is not True:
            entry.update(reason="daily_image_extraction_abstention", extraction_reason=ext.get("reason"))
            continue
        bars = ext.get("bars", ext.get("decoded_bars"))
        level = ext.get("level", ext.get("selected_level_pixel_price"))
        anchor = _number(ext.get("signal_x"))
        if ext.get("decision_convention") != "after_daily_annotated_bar_close":
            raise ValueError(f"Unknown D1 decision convention #{sid}")
        if not bars or bars[-1].get("x") != anchor:
            raise ValueError(f"D1 decision bar is absent from prefix #{sid}")
        positions = [_number(bar.get("x")) for bar in bars]
        if any(x > anchor for x in positions) or any(b <= a for a, b in zip(positions, positions[1:])):
            raise ValueError(f"Unordered or future D1 bars #{sid}")
        if len(bars) < 16:
            entry["reason"] = "insufficient_closed_daily_history"
            continue
        features = prerequisite_features_from_ohlc(bars, level)
        cases.append({"scenario_id": sid, "group": row["instrument"].strip().upper(),
                      "direction": row["direction"], "features": features, "split": splits[sid],
                      "daily_source_sha256": image_hash, "anchor_x": anchor,
                      "last_feature_bar_x": positions[-1]})
        entry.update(status="eligible", reason="closed_daily_geometry_available",
                     feature_schema_version=FEATURE_SCHEMA_VERSION)
    return cases, ledger


def _feature_matrix(cases):
    rows = []
    for case in cases:
        features = case["features"]
        if set(features) != set(PREREQUISITE_FEATURE_NAMES):
            raise ValueError("Every direction case requires the canonical feature schema")
        rows.append([_number(features[name]) for name in PREREQUISITE_FEATURE_NAMES])
    return np.asarray(rows, dtype=float)


def _sigmoid(values):
    return 1 / (1 + np.exp(-np.clip(values, -60, 60)))


def fit_direction_classifier(cases: list[dict], regularization: float) -> dict:
    """Ordinary L2 logistic fit; labels never transform the input geometry."""
    regularization = _number(regularization)
    if regularization <= 0:
        raise ValueError("Regularization must be positive")
    if len(cases) < 4 or {case["direction"] for case in cases} != {"long", "short"}:
        raise ValueError("Need at least four cases containing both author directions")
    x = _feature_matrix(cases)
    y = np.asarray([case["direction"] == "long" for case in cases], dtype=float)
    mean, scale = x.mean(axis=0), x.std(axis=0)
    scale[scale < 1e-8] = 1.
    design = np.column_stack(((x - mean) / scale, np.ones(len(x))))
    weights = np.zeros(design.shape[1])
    penalty = np.asarray([regularization] * x.shape[1] + [0.])

    def objective(values):
        logits = design @ values
        return float(np.mean(np.logaddexp(0, logits) - y * logits) + .5 * np.sum(penalty * values**2))

    history = [objective(weights)]
    for _ in range(80):
        probability = _sigmoid(design @ weights)
        gradient = design.T @ (probability-y) / len(x) + penalty * weights
        hessian = design.T @ ((probability*(1-probability))[:, None] * design) / len(x)
        hessian += np.diag(penalty + 1e-9)
        step = np.linalg.solve(hessian, gradient)
        rate = 1.
        while rate > 1e-8 and objective(weights-rate*step) > history[-1]:
            rate *= .5
        weights -= rate * step
        history.append(objective(weights))
        if np.max(abs(rate*step)) < 1e-7:
            break
    return {"mean": mean.tolist(), "scale": scale.tolist(), "weights": weights[:-1].tolist(),
            "bias": float(weights[-1]), "regularization": regularization,
            "optimization": {"iterations": len(history)-1, "objective_history": history,
                             "initial_objective": history[0], "final_objective": history[-1]}}


def evaluate_direction(cases: list[dict], fitted: dict, *, baseline_direction: str) -> dict:
    if not cases:
        raise ValueError("Cannot evaluate an empty direction partition")
    x = _feature_matrix(cases)
    logits = ((x-np.asarray(fitted["mean"]))/np.asarray(fitted["scale"])) @ np.asarray(fitted["weights"]) + fitted["bias"]
    probabilities = _sigmoid(logits)
    labels = [case["direction"] for case in cases]
    predicted = ["long" if value >= .5 else "short" for value in probabilities]
    confusion = {true: {guess: sum(a == true and b == guess for a, b in zip(labels, predicted))
                        for guess in ("short", "long")} for true in ("short", "long")}
    counts = Counter(labels)
    balanced = sum(confusion[side][side]/counts[side] for side in counts)/len(counts) if len(counts) == 2 else None
    targets = np.asarray([side == "long" for side in labels], dtype=float)
    return {
        "scenarios": len(cases), "class_counts": dict(counts),
        "accuracy": sum(a == b for a, b in zip(labels, predicted))/len(cases),
        "balanced_accuracy": balanced, "confusion_true_rows_predicted_columns": confusion,
        "log_loss": float(np.mean(np.logaddexp(0, logits)-targets*logits)),
        "majority_baseline": {"direction_chosen_from_fit_partition": baseline_direction,
                              "accuracy": counts[baseline_direction]/len(cases),
                              "balanced_accuracy": .5 if len(counts) == 2 else None},
        "details": [{"scenario_id": case["scenario_id"], "instrument_group": case["group"],
                     "author_direction": case["direction"], "predicted_direction": guess,
                     "author_long_probability": float(probability)}
                    for case, guess, probability in zip(cases, predicted, probabilities)],
        "metric_scope": "agreement with author direction; not profitability or entry accuracy",
    }


def run_direction_training(collection: str | Path = DEFAULT_COLLECTION, output: str | Path | None = None) -> dict:
    """Fit train, select L2 on validation, refit train+validation, then test once."""
    collection = Path(collection)
    source = collection / "visual_analysis/scenario_analysis.jsonl"
    audit_path = collection / "training/image_extraction_audit.json"
    rows = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    cases, ledger = build_direction_cases(collection, audit, rows)
    partitions = {name: [case for case in cases if case["split"] == name] for name in ("train", "validation", "test")}
    if any(len(partition) < 4 or {case["direction"] for case in partition} != {"long", "short"}
           for partition in partitions.values()):
        raise ValueError("Each independent direction partition needs >=4 cases and both classes")
    majority_train = Counter(case["direction"] for case in partitions["train"]).most_common(1)[0][0]
    candidates = []
    for regularization in REGULARIZATION_GRID:
        fitted = fit_direction_classifier(partitions["train"], regularization)
        metrics = evaluate_direction(partitions["validation"], fitted, baseline_direction=majority_train)
        candidates.append({"regularization": regularization, "validation": metrics})
    selected = min(candidates, key=lambda candidate: (-candidate["validation"]["balanced_accuracy"],
                                                     candidate["validation"]["log_loss"], -candidate["regularization"]))
    fitting_cases = partitions["train"] + partitions["validation"]
    fitted = fit_direction_classifier(fitting_cases, selected["regularization"])
    majority_final = Counter(case["direction"] for case in fitting_cases).most_common(1)[0][0]
    test = evaluate_direction(partitions["test"], fitted, baseline_direction=majority_final)
    split_info = {name: {"scenario_ids": sorted(case["scenario_id"] for case in partition),
                         "instrument_groups": sorted({case["group"] for case in partition}),
                         "class_counts": dict(Counter(case["direction"] for case in partition))}
                  for name, partition in partitions.items()}
    code_files = ("scenario_direction_model.py", "scenario_prerequisites.py", "scenario_image_features.py",
                  "scenario_training_rules.py", "train_scenario_entry_model.py", "level_structure.py", "detector_prototype.py")
    provenance = {"author_annotation_sha256": _digest(source), "original_extraction_audit_sha256": _digest(audit_path),
                  "code_sha256": {name: _digest(Path(__file__).with_name(name)) for name in code_files},
                  "split_universe": "all 409 annotations with known instrument, before eligibility filtering",
                  "split_seed": "entry-v1/", "split_normalization": "instrument.strip().upper(); exclude empty/H/Н"}
    review_path = collection / "training" / DAILY_REVIEW_FILENAME
    review_rows = ([json.loads(line) for line in review_path.read_text(encoding="utf-8").splitlines() if line.strip()]
                   if review_path.exists() else [])
    provenance["daily_anchor_spot_review_sha256"] = _digest(review_path) if review_path.exists() else None
    review_coverage = {
        "visually_reviewed_original_daily_images": len(review_rows),
        "accepted_visual_reviews": sum(row["accepted"] for row in review_rows),
        "rejected_visual_reviews": sum(not row["accepted"] for row in review_rows),
        "eligible_cases_with_visual_anchor_review": sum(entry["status"] == "eligible" and entry["daily_anchor_visually_reviewed"] for entry in ledger),
        "eligible_cases_without_visual_anchor_review": sum(entry["status"] == "eligible" and not entry["daily_anchor_visually_reviewed"] for entry in ledger),
        "scope": "Bounded pre-fit spot review; other eligible automatic extractions are not claimed manually verified.",
    }
    model = {"schema_version": 1, "model_kind": MODEL_KIND, "feature_schema_version": FEATURE_SCHEMA_VERSION,
             "feature_names": list(PREREQUISITE_FEATURE_NAMES), "classes": ["short", "long"], **fitted,
             "research_only": True, "automatic_order_execution_allowed": False,
             "target": "author long/short direction from closed daily OHLC at supplied level",
             "probability_meaning": "uncalibrated author-long label probability, never trade profitability",
             "trained_at_utc": datetime.now(timezone.utc).isoformat(), "splits": split_info,
             "fit_partitions": ["train", "validation"], "provenance": provenance,
             "visual_review_coverage": review_coverage, "limitations": LIMITATIONS}
    validate_direction_model(model)
    report = {"model_kind": MODEL_KIND, "source_scenarios": len(rows), "eligible_scenarios": len(cases),
              "exclusion_counts": dict(Counter(entry["reason"] for entry in ledger if entry["status"] == "excluded")),
              "splits": split_info, "regularization_candidates": candidates,
              "selection_rule": "validation balanced accuracy, then log loss; test never selects parameters",
              "selected_regularization": selected["regularization"],
              "selected_validation_before_refit": selected["validation"], "held_out_test_after_refit": test,
              "validation_is_not_independent_after_model_selection": True,
              "research_only": True, "automatic_order_execution_allowed": False,
              "provenance": provenance, "visual_review_coverage": review_coverage, "limitations": LIMITATIONS}
    target = Path(output) if output is not None else collection / "training"
    target.mkdir(parents=True, exist_ok=True)
    (target / "direction_training_ledger.jsonl").write_text(
        "".join(json.dumps(entry, ensure_ascii=False, allow_nan=False)+"\n" for entry in ledger), encoding="utf-8")
    model["provenance"]["ledger_sha256"] = _digest(target / "direction_training_ledger.jsonl")
    (target / "direction_model.json").write_text(json.dumps(model, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    report["model_sha256"] = _digest(target / "direction_model.json")
    (target / "direction_training_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    return report


def direction_advice_from_ohlc(
    bars: Sequence[Mapping[str, float]], level: float, *, model_path: str | Path | None = None,
    model: Mapping[str, Any] | None = None, decision_index: int | None = None,
    daily_close_confirmed: bool = False,
) -> dict:
    """Research direction, gated by the caller's actual daily-close check."""
    result = {"status": "blocked", "direction": None, "author_long_probability": None,
              "prerequisites": None, "research_only": True,
              "automatic_order_execution_allowed": False, "reasons": []}
    if daily_close_confirmed is not True:
        result["reasons"] = ["daily_close_not_confirmed"]
        return result
    try:
        fitted = validate_direction_model(model) if model is not None else load_direction_model(model_path or MODEL_PATH)
        prerequisites = analyze_prerequisites(bars, level, decision_index=decision_index)
        vector = np.asarray([prerequisites["features"][name] for name in fitted["feature_names"]])
        logit = float(((vector-np.asarray(fitted["mean"]))/np.asarray(fitted["scale"])) @ np.asarray(fitted["weights"]) + fitted["bias"])
        if not math.isfinite(logit):
            raise ValueError("Non-finite direction score")
        probability = float(_sigmoid(logit))
        result.update(status="research_only", direction="long" if probability >= .5 else "short",
                      author_long_probability=probability, prerequisites=prerequisites,
                      probability_meaning="uncalibrated author-long label probability, not profit probability")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result["reasons"] = ["invalid_or_missing_direction_model_or_closed_ohlc"]
        result["detail"] = str(exc)
    return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("collection", type=Path, nargs="?", default=DEFAULT_COLLECTION)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    print(json.dumps(run_direction_training(args.collection, args.output), ensure_ascii=False, indent=2))
