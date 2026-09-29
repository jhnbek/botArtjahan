"""Direction learning, leakage barriers, immutable schemas and D1 closure."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "knowledge_bot"))
import scenario_direction_model as direction
from scenario_prerequisites import FEATURE_SCHEMA_VERSION, PREREQUISITE_FEATURE_NAMES


def synthetic_cases():
    cases = []
    for index in range(40):
        label = "long" if index % 2 else "short"
        features = dict.fromkeys(PREREQUISITE_FEATURE_NAMES, 0.)
        features["long_trend_8_tr"] = (1 if label == "long" else -1) + .01*(index // 2)
        features["short_trend_8_tr"] = -features["long_trend_8_tr"]
        cases.append(dict(scenario_id=index+1, group=f"COIN_{index//4}", direction=label, features=features))
    return cases


def artifact():
    return {"schema_version": 1, "model_kind": direction.MODEL_KIND,
            "feature_schema_version": FEATURE_SCHEMA_VERSION, "feature_names": list(PREREQUISITE_FEATURE_NAMES),
            "classes": ["short", "long"], "research_only": True, "automatic_order_execution_allowed": False,
            **direction.fit_direction_classifier(synthetic_cases(), .1)}


def candle(index, rising):
    close = 98 + index*.1 if rising else 102-index*.1
    return dict(open=close-.05 if rising else close+.05, high=close+.2, low=close-.2,
                close=close, x=10+index*10)


def corpus_fixture(root):
    (root / "visual_analysis").mkdir()
    (root / "training").mkdir()
    (root / "images").mkdir()
    rows, records = [], []
    for sid in range(1, 410):
        image = f"images/1D_{sid}.jpg"
        label = "long" if (sid//10) % 2 else "short"
        row = dict(scenario_id=sid, instrument=f"COIN_{sid%10}", direction=label,
                   image_files=[image, f"images/1H_{sid}.jpg"], image_sha256={})
        if sid <= 50:
            data = f"synthetic-image-{sid}".encode()
            (root / image).write_bytes(data)
            digest = hashlib.sha256(data).hexdigest()
            row["image_sha256"][image] = digest
            records.append(dict(scenario_id=sid, timeframe="1D", usable=True, source_sha256=digest,
                                bars=[candle(i, label == "long") for i in range(20)], level=100.,
                                signal_x=200, decision_convention="after_daily_annotated_bar_close"))
        rows.append(row)
    (root / "visual_analysis/scenario_analysis.jsonl").write_text(
        "".join(json.dumps(row)+"\n" for row in rows), encoding="utf-8")
    audit = {"records": records}
    (root / "training/image_extraction_audit.json").write_text(json.dumps(audit), encoding="utf-8")
    return rows, audit


class DirectionLearningTests(unittest.TestCase):
    def test_fit_learns_labels_and_reduces_objective(self):
        fitted = direction.fit_direction_classifier(synthetic_cases(), .1)
        history = fitted["optimization"]["objective_history"]
        self.assertLess(history[-1], history[0]-.1)
        self.assertTrue(all(b <= a+1e-10 for a, b in zip(history, history[1:])))
        metrics = direction.evaluate_direction(synthetic_cases(), fitted, baseline_direction="long")
        self.assertEqual(metrics["balanced_accuracy"], 1.)
        self.assertEqual(metrics["majority_baseline"]["balanced_accuracy"], .5)
        self.assertGreater(np.linalg.norm(fitted["weights"]), 0)

    def test_fit_scaler_uses_only_supplied_cases_and_metadata_cannot_change_weights(self):
        cases = synthetic_cases()[:20]
        fitted = direction.fit_direction_classifier(cases, .1)
        expected = np.asarray([[case["features"][key] for key in PREREQUISITE_FEATURE_NAMES] for case in cases]).mean(axis=0)
        np.testing.assert_allclose(fitted["mean"], expected)
        changed = [dict(case, scenario_id=900+index, group="OTHER", outcome="win", caption="long!")
                   for index, case in enumerate(cases)]
        self.assertEqual(fitted, direction.fit_direction_classifier(changed, .1))

    def test_schema_finite_scales_and_no_trade_execution_are_enforced(self):
        base = artifact()
        mutations = [dict(feature_names=sorted(PREREQUISITE_FEATURE_NAMES)), dict(classes=["long", "short"]),
                     dict(bias=float("nan")), dict(weights=[0.]), dict(research_only=False),
                     dict(automatic_order_execution_allowed=True), dict(scale=[0.]*24),
                     dict(mean=[True]*24), dict(model_kind="profit_probability")]
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                direction.validate_direction_model(dict(base, **mutation))

    def test_training_rejects_invalid_features_regularization_and_single_class(self):
        for regularization in (0, -1, True, "0.1", float("nan")):
            with self.subTest(regularization=regularization), self.assertRaises(ValueError):
                direction.fit_direction_classifier(synthetic_cases(), regularization)
        for invalid in (float("nan"), float("inf"), True, "1"):
            cases = synthetic_cases()
            cases[0]["features"]["long_trend_8_tr"] = invalid
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                direction.fit_direction_classifier(cases, .1)
        with self.assertRaises(ValueError):
            direction.fit_direction_classifier([case for case in synthetic_cases() if case["direction"] == "long"], .1)

    def test_shared_splits_use_all_groups_and_keep_each_instrument_together(self):
        rows = [dict(scenario_id=index+1, instrument=f" coin_{index%10} ") for index in range(40)]
        rows += [dict(scenario_id=100+i, instrument=value) for i, value in enumerate(("H", "Н", "", None))]
        splits = direction.shared_corpus_splits(rows)
        self.assertEqual(len(splits), 40)
        for index in range(10):
            self.assertEqual(len({splits[row["scenario_id"]] for row in rows[:40] if row["instrument"].strip().upper() == f"COIN_{index}"}), 1)
        retained = {sid: value for sid, value in splits.items() if sid % 3}
        self.assertEqual(retained, {sid: splits[sid] for sid in retained})

    def test_inference_requires_daily_confirmation_and_ignores_future_and_metadata(self):
        model = artifact()
        bars = [candle(index, True) for index in range(20)]
        with patch.object(direction, "analyze_prerequisites") as extraction:
            blocked = direction.direction_advice_from_ohlc(bars, 100., model=model)
            extraction.assert_not_called()
        self.assertEqual(blocked["status"], "blocked")
        self.assertIsNone(blocked["direction"])
        result = direction.direction_advice_from_ohlc(bars, 100., model=model, daily_close_confirmed=True)
        changed = [dict(row, direction="short", outcome="loss") for row in bars] + [{"future": True}]
        repeated = direction.direction_advice_from_ohlc(changed, 100., model=model, daily_close_confirmed=True, decision_index=19)
        self.assertEqual(result, repeated)
        self.assertEqual(result["status"], "research_only")
        self.assertIn(result["direction"], ("long", "short"))
        self.assertFalse(result["automatic_order_execution_allowed"])
        self.assertIn("not profit", result["probability_meaning"])

    def test_data_builder_rejects_image_hash_and_future_bar_corruption(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            rows, audit = corpus_fixture(root)
            cases, ledger = direction.build_direction_cases(root, audit, rows)
            self.assertGreater(len(cases), 40)
            self.assertEqual(len(ledger), 409)
            corrupted = deepcopy(audit)
            corrupted["records"][0]["source_sha256"] = "0"*64
            with self.assertRaisesRegex(ValueError, "differs from image"):
                direction.build_direction_cases(root, corrupted, rows)
            corrupted = deepcopy(audit)
            corrupted["records"][0]["bars"][3]["x"] = 999
            with self.assertRaisesRegex(ValueError, "future"):
                direction.build_direction_cases(root, corrupted, rows)

    def test_rejected_daily_review_excludes_extraction_and_hash_is_required(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            rows, audit = corpus_fixture(root)
            original, _ = direction.build_direction_cases(root, audit, rows)
            record = audit["records"][0]
            review = dict(scenario_id=1, reviewed=True, accepted=False,
                          source_sha256=record["source_sha256"],
                          source_extraction_audit_sha256=hashlib.sha256((root/"training/image_extraction_audit.json").read_bytes()).hexdigest(),
                          signal_x=record["signal_x"], level=record["level"],
                          reason_code="reading_d1_confirmation_cutoff_incomplete",
                          reason="Our cutoff omits the return bar; the author annotation is retained.")
            path = root/"training"/direction.DAILY_REVIEW_FILENAME
            path.write_text(json.dumps(review)+"\n", encoding="utf-8")
            filtered, ledger = direction.build_direction_cases(root, audit, rows)
            self.assertEqual(len(filtered), len(original)-1)
            self.assertNotIn(1, {case["scenario_id"] for case in filtered})
            self.assertEqual(ledger[0]["reason"], "reading_d1_confirmation_cutoff_incomplete")
            self.assertTrue(ledger[0]["daily_anchor_visually_reviewed"])
            report = direction.run_direction_training(root, root/"reviewed-output")
            self.assertEqual(report["provenance"]["daily_anchor_spot_review_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(report["visual_review_coverage"]["rejected_visual_reviews"], 1)
            review["source_sha256"] = "0"*64
            path.write_text(json.dumps(review)+"\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "review source hash"):
                direction.build_direction_cases(root, audit, rows)

    def test_test_set_mutation_cannot_change_regularization_or_fitted_parameters(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            rows, audit = corpus_fixture(root)
            report = direction.run_direction_training(root, root/"run1")
            first = json.loads((root/"run1/direction_model.json").read_text(encoding="utf-8"))
            test_ids = set(report["splits"]["test"]["scenario_ids"])
            for record in audit["records"]:
                if record["scenario_id"] in test_ids:
                    record["bars"] = [candle(i, False) for i in range(20)]
            (root/"training/image_extraction_audit.json").write_text(json.dumps(audit), encoding="utf-8")
            direction.run_direction_training(root, root/"run2")
            second = json.loads((root/"run2/direction_model.json").read_text(encoding="utf-8"))
            for key in ("regularization", "mean", "scale", "weights", "bias", "optimization"):
                self.assertEqual(first[key], second[key], key)
            split_groups = [set(value["instrument_groups"]) for value in report["splits"].values()]
            self.assertFalse(split_groups[0] & split_groups[1] or split_groups[0] & split_groups[2] or split_groups[1] & split_groups[2])
            self.assertEqual(report["model_sha256"], hashlib.sha256((root/"run1/direction_model.json").read_bytes()).hexdigest())
            self.assertTrue(report["limitations"])
            direction.load_direction_model(root/"run1/direction_model.json")


if __name__ == "__main__":
    unittest.main()
