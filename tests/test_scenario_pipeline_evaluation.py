"""Held-out pipeline metrics must include upstream direction errors."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from knowledge_bot import evaluate_scenario_pipeline as evaluator
from knowledge_bot.scenario_direction_model import MODEL_KIND as DIRECTION_KIND, shared_corpus_splits
from knowledge_bot.scenario_image_features import FEATURE_NAMES
from knowledge_bot.scenario_model import READINESS_MODEL_KIND, features_for_entry, score_features
from knowledge_bot.scenario_prerequisites import FEATURE_SCHEMA_VERSION, PREREQUISITE_FEATURE_NAMES


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class PipelineEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.collection = Path(self.temporary.name)
        self.training = self.collection / "training"
        self.training.mkdir()
        (self.collection / "visual_analysis").mkdir()
        self.rows = [{"scenario_id": i + 1, "instrument": f"COIN_{i // 2}", "direction": "long"} for i in range(12)]
        self.splits = shared_corpus_splits(self.rows)
        self.test_ids = sorted(sid for sid, split in self.splits.items() if split == "test")
        self.fit_ids = sorted(sid for sid, split in self.splits.items() if split != "test")
        self.bars = [{"x": i, "open": 100. + i, "high": 102. + i, "low": 99. + i, "close": 101. + i}
                     for i in range(18)]
        self.entry_cases = [{
            "scenario_id": sid, "group": self.rows[sid - 1]["instrument"], "direction": "long", "split": "test",
            "offsets": [1, 0], "anchor_x": 17, "last_feature_bar_x": 17, "daily_close_bar_x": 15,
            "decision_timing": "after_bar_close", "entry_state_resolution": "closed_hourly_bar",
            # Deliberately wrong stored feature values: prediction must not read them.
            "states": [dict.fromkeys(FEATURE_NAMES, 9999.) for _ in range(2)],
        } for sid in self.test_ids]
        self.daily_cases = [{
            "scenario_id": sid, "group": self.rows[sid - 1]["instrument"], "direction": "long", "split": "test",
            "features": dict.fromkeys(PREREQUISITE_FEATURE_NAMES, 0.),
        } for sid in self.test_ids]
        self.entry_audit = {"records": [
            {"scenario_id": sid, "timeframe": "1H", "bars": self.bars, "level": 108.} for sid in self.test_ids
        ]}
        self.write_jsonl(self.collection / "visual_analysis/scenario_analysis.jsonl", self.rows)
        self.write_jsonl(self.training / "entry_training_cases.jsonl", self.entry_cases)
        self.write_json(self.training / "reviewed_entry_extraction.json", self.entry_audit)
        self.write_json(self.training / "image_extraction_audit.json", {"records": []})
        direction_size, entry_size = len(PREREQUISITE_FEATURE_NAMES), len(FEATURE_NAMES)
        self.direction_model = {
            "schema_version": 1, "model_kind": DIRECTION_KIND, "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "feature_names": list(PREREQUISITE_FEATURE_NAMES), "classes": ["short", "long"],
            "mean": [0.] * direction_size, "scale": [1.] * direction_size,
            "weights": [0.] * direction_size, "bias": 1.,
            "research_only": True, "automatic_order_execution_allowed": False,
            "fit_partitions": ["train", "validation"],
            "splits": {split: {"scenario_ids": sorted(sid for sid, value in self.splits.items() if value == split)}
                       for split in ("train", "validation", "test")},
            "provenance": {
                "author_annotation_sha256": digest(self.collection / "visual_analysis/scenario_analysis.jsonl"),
                "original_extraction_audit_sha256": digest(self.training / "image_extraction_audit.json"),
            },
        }
        self.entry_model = {
            "schema_version": 1, "model_kind": READINESS_MODEL_KIND, "feature_names": list(FEATURE_NAMES),
            "mean": [0.] * entry_size, "scale": [1.] * entry_size,
            "weights": [1.] + [0.] * (entry_size - 1), "bias": 0., "threshold": None,
            "training": {
                "fitted_scenario_ids": self.fit_ids, "test_scenario_ids": self.test_ids,
                "source_annotations_sha256": digest(self.collection / "visual_analysis/scenario_analysis.jsonl"),
                "image_audit_sha256": digest(self.training / "reviewed_entry_extraction.json"),
            },
        }
        self.save_models()
        self.daily_patch = patch.object(evaluator, "build_direction_cases", return_value=(self.daily_cases, []))
        self.daily_patch.start()
        self.addCleanup(self.daily_patch.stop)
        self.entry_patch = patch.object(evaluator, "build_cases", return_value=(self.entry_cases, []))
        self.entry_patch.start()
        self.addCleanup(self.entry_patch.stop)

    @staticmethod
    def write_json(path, data):
        path.write_text(json.dumps(data, allow_nan=False), encoding="utf-8")

    @staticmethod
    def write_jsonl(path, data):
        path.write_text("".join(json.dumps(row, allow_nan=False) + "\n" for row in data), encoding="utf-8")

    def save_models(self):
        self.write_json(self.training / "direction_model.json", self.direction_model)
        self.write_json(self.training / "scenario_model.json", self.entry_model)

    def test_predicted_direction_recanonicalizes_raw_h1_and_changes_combined_result(self):
        correct = evaluator.evaluate_pipeline(self.collection)
        self.assertEqual(correct["correct_direction_and_entry"], 2)
        self.assertEqual(correct["number_of_cases"], 2)
        self.direction_model["bias"] = -1.
        self.save_models()
        incorrect = evaluator.evaluate_pipeline(self.collection)
        self.assertEqual(incorrect["correct_direction_and_entry"], 0)
        self.assertEqual(incorrect["direction_correct"], 0)
        expected = score_features(features_for_entry(self.bars, 108., "short"), self.entry_model)
        for case in incorrect["details"]:
            self.assertEqual(case["predicted_direction"], "short")
            self.assertAlmostEqual(case["selected_state_raw_entry_score"], expected)
            self.assertLess(case["selected_state_raw_entry_score"], 0.)
        self.assertFalse(incorrect["models_refitted"])
        self.assertFalse(incorrect["parameters_or_thresholds_tuned"])

    def test_entry_pattern_alone_cannot_hide_wrong_direction(self):
        self.direction_model["bias"] = -1.
        self.entry_model["bias"] = 100.
        self.save_models()
        result = evaluator.evaluate_pipeline(self.collection)
        self.assertEqual(result["selected_states_predicted_entry"], 2)
        self.assertEqual(result["direction_correct"], 0)
        self.assertEqual(result["correct_direction_and_entry"], 0)
        self.assertEqual(result["waiting_states"]["predicted_entry"], 2)
        self.assertEqual(result["scenarios_with_wait_states"], 2)

    def test_same_instrument_in_fit_is_rejected_even_with_different_scenario_id(self):
        kept, leaked = self.test_ids
        self.entry_model["training"]["test_scenario_ids"] = [kept]
        self.entry_model["training"]["fitted_scenario_ids"] += [leaked]
        self.save_models()
        with self.assertRaisesRegex(ValueError, "instrument was fitted"):
            evaluator.evaluate_pipeline(self.collection)
        self.assertFalse((self.training / "pipeline_evaluation.json").exists())

    def test_changed_source_provenance_aborts_evaluation(self):
        self.write_json(self.training / "reviewed_entry_extraction.json", {**self.entry_audit, "changed": True})
        with self.assertRaisesRegex(ValueError, "provenance"):
            evaluator.evaluate_pipeline(self.collection)

    def test_changed_stored_offsets_cannot_move_the_author_endpoint(self):
        changed = deepcopy(self.entry_cases)
        changed[0]["offsets"] = [2, 1, 0]
        self.write_jsonl(self.training / "entry_training_cases.jsonl", changed)
        with self.assertRaisesRegex(ValueError, "offsets differs"):
            evaluator.evaluate_pipeline(self.collection)

    def test_only_common_test_cases_are_evaluated_and_report_is_serializable(self):
        self.entry_model["training"]["test_scenario_ids"] = self.test_ids[:1]
        self.save_models()
        result = evaluator.evaluate_pipeline(self.collection)
        self.assertEqual(result["scenario_ids"], self.test_ids[:1])
        self.assertEqual(result["waiting_states"]["total"], 1)
        saved = json.loads((self.training / "pipeline_evaluation.json").read_text(encoding="utf-8"))
        self.assertEqual(saved, result)
        self.assertFalse(result["profitability_evaluated"])
        self.assertFalse(result["continuous_market_entry_detection_evaluated"])
        self.assertEqual(result["action"], "no_order")

    def test_empty_intersection_has_no_invented_accuracy(self):
        self.entry_model["training"]["test_scenario_ids"] = []
        self.save_models()
        result = evaluator.evaluate_pipeline(self.collection)
        self.assertEqual(result["number_of_cases"], 0)
        self.assertIsNone(result["correct_direction_and_entry_fraction"])

    def test_pairwise_ranker_cannot_be_evaluated_as_entry_wait_classifier(self):
        self.entry_model["model_kind"] = "entry_pairwise_ranker"
        self.save_models()
        with self.assertRaisesRegex(ValueError, "requires the readiness model"):
            evaluator.evaluate_pipeline(self.collection)


if __name__ == "__main__":
    unittest.main()
