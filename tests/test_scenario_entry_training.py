"""Regression checks for fitted weights, independent splits and causal features."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "knowledge_bot"))

import train_scenario_entry_model as trainer
from scenario_image_features import FEATURE_NAMES, features_from_ohlc
from scenario_model import features_for_entry, validate_model


def synthetic_cases():
    """Entry preference is encoded in geometry; IDs/groups are split metadata."""
    cases = []
    for group_number in range(10):
        for episode in range(3):
            states = []
            for state_number in range(4):
                values = dict.fromkeys(FEATURE_NAMES, float(group_number))
                values[FEATURE_NAMES[0]] = state_number + .05 * episode
                values[FEATURE_NAMES[1]] = .1 * episode
                states.append(values)
            cases.append(dict(scenario_id=group_number*3+episode+1,
                              group=f"COIN_{group_number}", states=states,
                              offsets=[3, 2, 1, 0]))
    return cases


class ScenarioEntryTrainingTests(unittest.TestCase):
    def test_real_fit_reduces_pairwise_objective_and_learns_nonzero_weights(self):
        cases = synthetic_cases()
        fitted = trainer.fit_ranker(cases, list(FEATURE_NAMES), .1)
        history = fitted["optimization"]["objective_history"]
        self.assertGreater(len(history), 1)
        self.assertLess(history[-1], history[0] - .1)
        self.assertTrue(all(after <= before + 1e-10 for before, after in zip(history, history[1:])))
        self.assertGreater(fitted["weights"][0], 0.)
        self.assertTrue(np.isfinite(fitted["weights"]).all())
        evaluation = trainer.evaluate(cases, list(FEATURE_NAMES), fitted)
        self.assertEqual(evaluation["strict_top1_fraction"], 1.)
        self.assertEqual(evaluation["pairwise_preference_agreement"], 1.)

    def test_tied_scores_are_not_reported_as_correct_top1(self):
        fitted = dict(mean=[0.] * len(FEATURE_NAMES), scale=[1.] * len(FEATURE_NAMES),
                      weights=[0.] * len(FEATURE_NAMES), bias=0.)
        result = trainer.evaluate(synthetic_cases(), list(FEATURE_NAMES), fitted)
        self.assertEqual(result["pairwise_preference_agreement"], .5)
        self.assertEqual(result["strict_top1_fraction"], 0.)
        self.assertTrue(all(row["tied_earlier_states"] == 3 for row in result["details"]))

    def test_instrument_and_duplicate_groups_never_cross_splits(self):
        cases = synthetic_cases()
        cases.extend([dict(scenario_id=281, group="AVAX"),
                      dict(scenario_id=284, group="AVAX")])
        splits = trainer.assign_splits(cases)
        self.assertEqual(splits[281], splits[284])
        for group in {case["group"] for case in cases}:
            self.assertEqual(len({splits[case["scenario_id"]] for case in cases if case["group"] == group}), 1)
        self.assertEqual(splits, trainer.assign_splits(list(reversed(cases))))
        self.assertEqual(set(splits.values()), {"train", "validation", "test"})

    def test_small_number_of_independent_instruments_is_rejected(self):
        with self.assertRaises(ValueError):
            trainer.assign_splits([dict(scenario_id=i, group="ONE") for i in range(30)])

    def test_validation_only_selects_hyperparameter_and_test_never_enters_fit(self):
        cases = synthetic_cases()
        assigned = trainer.assign_splits(cases)
        partitions = {split: {case["scenario_id"] for case in cases
                              if assigned[case["scenario_id"]] == split}
                      for split in ("train", "validation", "test")}
        fits = []
        fit = trainer.fit_ranker

        def observe_fit(inputs, names, regularization):
            fits.append({case["scenario_id"] for case in inputs})
            return fit(inputs, names, regularization)

        ledger = [dict(scenario_id=case["scenario_id"], status="eligible", reason="synthetic") for case in cases]
        with tempfile.TemporaryDirectory() as temp:
            collection = Path(temp)
            (collection/"training").mkdir()
            (collection/"visual_analysis").mkdir()
            (collection/"training/image_extraction_audit.json").write_text('{"records": []}', encoding="utf-8")
            (collection/"visual_analysis/scenario_analysis.jsonl").write_text('{}\n', encoding="utf-8")
            with patch.object(trainer, "build_cases", return_value=(deepcopy(cases), ledger)), \
                 patch.object(trainer, "build_training_contract", return_value={}), \
                 patch.object(trainer, "fit_ranker", side_effect=observe_fit):
                report = trainer.run(collection)
            artifact = json.loads((collection/"training/scenario_model.json").read_text(encoding="utf-8"))
        self.assertEqual(len(fits), 4)
        self.assertTrue(all(ids == partitions["train"] for ids in fits[:3]))
        self.assertEqual(fits[-1], partitions["train"] | partitions["validation"])
        self.assertTrue(all(not ids & partitions["test"] for ids in fits))
        self.assertEqual(set(artifact["training"]["test_scenario_ids"]), partitions["test"])
        self.assertEqual(set(artifact["training"]["fitted_scenario_ids"]), fits[-1])
        expected_mean = np.asarray([[state[name] for name in FEATURE_NAMES]
                                    for case in cases if case["scenario_id"] in fits[-1]
                                    for state in case["states"]]).mean(axis=0)
        np.testing.assert_allclose(artifact["mean"], expected_mean)
        self.assertEqual(artifact["feature_names"], list(FEATURE_NAMES))
        validate_model(artifact)
        self.assertFalse(artifact["metrics"]["profitability_evaluated"])
        self.assertFalse(artifact["metrics"]["live_entry_detection_evaluated"])
        self.assertEqual(report["baselines"]["most_recent_state_on_these_preselected_windows"], 1.)

    def test_shared_geometry_features_are_affine_invariant_and_ignore_outcome_fields(self):
        bars = [dict(open=100.+i*.2, high=101.+i*.2, low=99.+i*.2, close=100.5+i*.2)
                for i in range(25)]
        base = features_from_ohlc(bars, 104.)
        shifted = [{key: value*1.7+500 for key, value in bar.items()} for bar in bars]
        scaled = features_from_ohlc(shifted, 104.*1.7+500)
        np.testing.assert_allclose(list(base.values()), list(scaled.values()), rtol=1e-12, atol=1e-12)
        decorated = [{**bar, "future_return": 1000., "author_entry": True,
                      "scenario_id": 1, "direction": "short"} for bar in bars]
        self.assertEqual(features_for_entry(decorated, 104., "long"), base)


if __name__ == "__main__":
    unittest.main()
