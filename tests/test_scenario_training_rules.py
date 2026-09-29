"""Source portability, interpretation policy and explicit trade-price geometry."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from knowledge_bot import scenario_training_rules as rules


ROOT = Path(__file__).resolve().parents[1]


class ScenarioTrainingRulesTests(unittest.TestCase):
    def test_all_actual_sources_resolve_inside_this_checkout_with_hashes(self):
        sources = rules.source_fingerprints()
        self.assertEqual(len(sources), len(rules.SOURCE_REFERENCES))
        self.assertEqual(len({source["key"] for source in sources}), len(sources))
        for source, original in zip(sources, rules.SOURCE_REFERENCES):
            with self.subTest(key=source["key"]):
                self.assertEqual(source["original_path"], original["path"])
                self.assertIn(Path(source["path"]).parts[0], ("knowledge", "_knowledge_base", "knowledge_bot"))
                data = (ROOT / source["path"]).read_bytes()
                self.assertEqual(source["sha256"], hashlib.sha256(data).hexdigest())
                self.assertEqual(source["bytes"], len(data))

    def test_source_hashes_survive_download_to_a_renamed_repository(self):
        expected = rules.source_fingerprints()
        with tempfile.TemporaryDirectory() as temporary:
            checkout = Path(temporary) / "renamed project on another PC"
            for source in expected:
                destination = checkout / source["path"]
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes((ROOT / source["path"]).read_bytes())
            self.assertEqual(rules.source_fingerprints(checkout), expected)

    def test_missing_local_source_fails_even_when_old_sibling_copy_exists(self):
        reference = {"key": "test", "path": "botArtjahan/knowledge_bot/source.py", "kind": "current_code", "rule": "test"}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            checkout = root / "downloaded"
            checkout.mkdir()
            stale = root / reference["path"]
            stale.parent.mkdir(parents=True)
            stale.write_text("stale source", encoding="utf-8")
            with patch.object(rules, "SOURCE_REFERENCES", (reference,)):
                with self.assertRaises(FileNotFoundError):
                    rules.source_fingerprints(checkout)

    def test_contract_keeps_causal_guard_and_user_risk_rules(self):
        contract = rules.build_training_contract()
        self.assertTrue(contract["daily_close_required"])
        self.assertFalse(contract["automatic_order_execution_allowed"])
        self.assertFalse(contract["outcome_validation_available"])
        self.assertEqual(contract["risk_policy"]["take_profit_multiples"], [3.0, 4.0])
        self.assertEqual(contract["risk_policy"]["price_inputs_required"], ["entry", "structural_stop"])
        self.assertIn("separate learning target", contract["direction_role"])
        self.assertIn("accepted as correct", contract["annotation_authority"])
        self.assertIn("future chart pixels", contract["feature_cutoff"])

    def test_unresolved_interpretations_remain_excluded_until_aligned(self):
        for identifier in (30, 54, 103, 156, 160, 201, 278, 363):
            row = {"scenario_id": identifier, "direction": "long", "image_files": [f"1D_{identifier}.jpg", f"1H_{identifier}.jpg"]}
            with self.subTest(scenario=identifier):
                policy = rules.scenario_training_policy(row)
                self.assertFalse(policy["entry_ranking_eligible"])
                self.assertTrue(policy["entry_coordinates_must_be_verified"])
                self.assertTrue(policy["reasons"])
                self.assertNotIn("contradictory_direction_annotations", policy["reasons"])
        ready = rules.scenario_training_policy({"scenario_id": 1, "direction": "long", "image_files": ["1D_1.jpg", "1H_1.jpg"]})
        self.assertTrue(ready["entry_ranking_eligible"])
        self.assertTrue(ready["direction_training_eligible"])
        self.assertFalse(ready["profitability_label_available"])

    def test_missing_hourly_image_does_not_invent_an_entry(self):
        policy = rules.scenario_training_policy({"scenario_id": 85, "direction": "short", "image_files": ["1D_85.jpg"]})
        self.assertFalse(policy["entry_ranking_eligible"])
        self.assertIn("missing_hourly_image", policy["reasons"])

    def test_take_prices_reproduce_knowledge_base_worked_example(self):
        targets = rules.calculate_take_profit_targets(direction="long", entry=225.12, stop=224.95)
        self.assertAlmostEqual(targets["3R"]["target"], 225.63)
        self.assertAlmostEqual(targets["4R"]["target"], 225.80)
        for key, target in targets.items():
            with self.subTest(target=key):
                self.assertTrue(target["meets_supplied_geometry"])
                self.assertFalse(target["technical_stop_verified"])
                self.assertFalse(target["automatic_order_execution_allowed"])
                self.assertFalse(target["fees_and_slippage_included"])
                self.assertEqual(target["prices_source"], "entry_and_stop_caller_supplied_target_calculated")

    def test_short_targets_use_supplied_structural_stop_and_not_atr(self):
        targets = rules.calculate_take_profit_targets(direction="short", entry=100, stop=102, prior_atr=5)
        wider_atr = rules.calculate_take_profit_targets(direction="short", entry=100, stop=102, prior_atr=50)
        self.assertEqual(targets["3R"]["target"], 94)
        self.assertEqual(targets["4R"]["target"], 92)
        self.assertEqual(targets["3R"]["stop_atr"], 0.4)
        self.assertEqual(wider_atr["3R"]["stop_atr"], 0.04)
        self.assertEqual(targets["3R"]["target"], wider_atr["3R"]["target"])
        self.assertEqual(targets["4R"]["target"], wider_atr["4R"]["target"])

    def test_roundoff_at_exact_3r_is_accepted_but_lower_reward_is_not(self):
        exact = rules.assess_risk_geometry(direction="long", entry=225.12, stop=224.95, target=225.63)
        lower = rules.assess_risk_geometry(direction="long", entry=225.12, stop=224.95, target=225.629)
        self.assertTrue(exact["meets_supplied_geometry"])
        self.assertFalse(lower["meets_supplied_geometry"])

    def test_invalid_supplied_prices_are_rejected_before_target_calculation(self):
        for field in ("entry", "stop", "prior_atr"):
            for value in (0, -1, float("nan"), float("inf"), True, "100"):
                arguments = {"direction": "long", "entry": 100, "stop": 99, "prior_atr": 10}
                arguments[field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    rules.calculate_take_profit_targets(**arguments)
        for arguments in (
            {"direction": "flat", "entry": 100, "stop": 99},
            {"direction": "long", "entry": 100, "stop": 100},
            {"direction": "long", "entry": 100, "stop": 101},
            {"direction": "short", "entry": 100, "stop": 99},
            # 3R would remain positive, but the 4R price would equal zero.
            {"direction": "short", "entry": 100, "stop": 125},
        ):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                rules.calculate_take_profit_targets(**arguments)


if __name__ == "__main__":
    unittest.main()
