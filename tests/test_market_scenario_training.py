"""Market model fitting must preserve fixed partitions and causal provenance."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from knowledge_bot import train_market_scenario_models as trainer


def fixtures(*, validation_waits=True, validation_two_sides=True):
    cases = []
    for split, identifiers in (("train", range(1, 7)), ("validation", range(20, 22)), ("test", range(40, 42))):
        for sid in identifiers:
            side = "long" if sid % 2 else "short"
            if split == "validation" and not validation_two_sides:
                side = "long"
            delta = 1. if side == "long" else -1.
            features = {name: delta * (index+1) / 30 + (sid % 3)*.03
                        for index, name in enumerate(trainer.PREREQUISITE_FEATURE_NAMES)}
            wait = {name: -(index+1)/50 + (sid % 3)*.02 for index, name in enumerate(trainer.FEATURE_NAMES)}
            enter = {name: (index+1)/50 + (sid % 3)*.02 for index, name in enumerate(trainer.FEATURE_NAMES)}
            decision = 24 * trainer.DAY + 3 * trainer.HOUR
            states = [wait, enter] if split != "validation" or validation_waits else [enter]
            times = [decision-trainer.HOUR, decision] if len(states) == 2 else [decision]
            cases.append(dict(scenario_id=sid, group=split.upper(), split=split, direction=side,
                              direction_features=features, states=states,
                              state_close_times_ms=times, offsets=[(decision-t)//trainer.HOUR for t in times],
                              decision_time_ms=decision, signal_d1_open_time_ms=23*trainer.DAY,
                              latest_closed_d1_time_ms=24*trainer.DAY,
                              entry_resolution="closed_hourly_bar", earlier_states_are_failed_trades=False,
                              direction_daily_only_eligible=True))
    preparation = dict(target_pairs=407, eligible_pairs=len(cases), user_skipped=[54, 85],
                       automatic_order_execution_allowed=False)
    return cases, preparation


class MarketTrainingTests(unittest.TestCase):
    def test_test_cases_never_fit_or_select_and_evaluate_once_per_target(self):
        cases, preparation = fixtures()
        direction_fits, entry_fits, test_evaluations = [], [], []
        real_d_fit, real_e_fit = trainer.fit_direction_classifier, trainer.fit_entry_readiness
        real_d_eval, real_e_eval = trainer.evaluate_direction, trainer.evaluate_readiness

        def d_fit(rows, regularization):
            direction_fits.append({r["scenario_id"] for r in rows})
            return real_d_fit(rows, regularization)

        def e_fit(rows, names, regularization):
            entry_fits.append({r["scenario_id"] for r in rows})
            return real_e_fit(rows, names, regularization)

        def d_eval(rows, model, **kwargs):
            if rows[0]["split"] == "test": test_evaluations.append("direction")
            return real_d_eval(rows, model, **kwargs)

        def e_eval(rows, names, model):
            if rows[0]["split"] == "test": test_evaluations.append("entry")
            return real_e_eval(rows, names, model)

        with patch.object(trainer, "fit_direction_classifier", side_effect=d_fit), \
             patch.object(trainer, "fit_entry_readiness", side_effect=e_fit), \
             patch.object(trainer, "evaluate_direction", side_effect=d_eval), \
             patch.object(trainer, "evaluate_readiness", side_effect=e_eval):
            direction, entry, report = trainer.fit_market_models(cases, preparation)
        held_out = {40, 41}
        self.assertTrue(all(not held_out.intersection(ids) for ids in direction_fits+entry_fits))
        self.assertEqual(test_evaluations, ["direction", "entry"])
        self.assertEqual(direction_fits[-1], set(range(1, 7)) | {20, 21})
        self.assertEqual(entry_fits[-1], direction_fits[-1])
        self.assertEqual(trainer.validate_direction_model(direction)["model_kind"], trainer.DIRECTION_KIND)
        self.assertEqual(trainer.validate_model(entry)["model_kind"], "entry_demonstration_readiness")
        self.assertFalse(entry["trained_on_successful_examples_only"])
        self.assertFalse(report["all_target_pairs_prepared"])
        self.assertFalse(report["automatic_order_execution_allowed"])

    def test_arbitrary_test_features_cannot_change_weights_or_normalization(self):
        cases, preparation = fixtures()
        first_d, first_e, _ = trainer.fit_market_models(cases, preparation)
        altered = deepcopy(cases)
        for case in altered:
            if case["split"] != "test": continue
            case["direction_features"] = {name: 1e9 for name in trainer.PREREQUISITE_FEATURE_NAMES}
            case["states"] = [{name: -1e9 for name in trainer.FEATURE_NAMES} for _ in case["states"]]
            case["direction"] = "short"
        second_d, second_e, _ = trainer.fit_market_models(altered, preparation)
        for key in ("weights", "mean", "scale", "bias", "regularization"):
            self.assertEqual(first_d[key], second_d[key])
            self.assertEqual(first_e[key], second_e[key])

    def test_single_class_validation_uses_fixed_default(self):
        cases, preparation = fixtures(validation_waits=False, validation_two_sides=False)
        _, _, report = trainer.fit_market_models(cases, preparation)
        for kind in ("entry", "direction"):
            self.assertEqual(report[kind]["selected_regularization"], .1)
            self.assertEqual(len(report[kind]["validation_search"]), 1)
            self.assertEqual(report[kind]["selection_method"], "fixed_default_no_two_class_validation")

    def test_group_leakage_and_skipped_author_case_fail_before_fitting(self):
        cases, preparation = fixtures()
        cases[-1]["group"] = "TRAIN"
        with patch.object(trainer, "fit_direction_classifier") as fit:
            with self.assertRaisesRegex(ValueError, "leaked"):
                trainer.fit_market_models(cases, preparation)
            fit.assert_not_called()
        cases, preparation = fixtures()
        cases[0]["scenario_id"] = 54
        with self.assertRaisesRegex(ValueError, "skipped"):
            trainer.validate_cases(cases, preparation)
        preparation["user_skipped"] = [85]
        with self.assertRaisesRegex(ValueError, "stale_user_contract"):
            trainer.validate_cases(cases, preparation)

    def test_states_must_follow_actual_closed_signal_not_just_an_older_daily_bar(self):
        cases, preparation = fixtures()
        cases[0]["signal_d1_open_time_ms"] = 24 * trainer.DAY
        with self.assertRaisesRegex(ValueError, "precedes_confirmed"):
            trainer.validate_cases(cases, preparation)
        cases, preparation = fixtures()
        cases[0]["states"][0][trainer.FEATURE_NAMES[0]] = float("inf")
        with self.assertRaisesRegex(ValueError, "feature_values"):
            trainer.validate_cases(cases, preparation)

    def write_prepared(self, root):
        cases, preparation = fixtures()
        folder = root / "training/market_v2"
        folder.mkdir(parents=True)
        for source in trainer.REQUIRED_SOURCES:
            path = root / source
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture", encoding="utf-8")
        for case in cases:
            sid = case["scenario_id"]
            case["source_images_sha256"] = {}
            for timeframe in ("1D", "1H"):
                path = root / f"images/{timeframe}_{sid}.jpg"
                path.parent.mkdir(exist_ok=True)
                path.write_bytes(b"synthetic source; not a market image")
                case["source_images_sha256"][path.relative_to(root).as_posix()] = trainer.digest(path)
            for timeframe in ("daily", "hourly"):
                relative = f"training/{timeframe}_cache.json"
                path = root / relative
                path.write_text("synthetic market source", encoding="utf-8")
                case[f"source_{timeframe}_cache"] = relative
                case[f"source_{timeframe}_cache_sha256"] = trainer.digest(path)
        cases_path = folder / "cases.jsonl"
        cases_path.write_text("".join(json.dumps(c)+"\n" for c in cases), encoding="utf-8")
        preparation.update(cases_sha256=trainer.digest(cases_path),
                           preparation_sha256=trainer.digest(trainer.MODULE / "prepare_market_training.py"),
                           source_sha256={name: trainer.digest(root / name) for name in trainer.REQUIRED_SOURCES},
                           feature_code_sha256={name: trainer.digest(trainer.MODULE / name) for name in trainer.FEATURE_CODE},
                           review_sources_sha256={"review_fixture": "stable"})
        (folder / "preparation_report.json").write_text(json.dumps(preparation), encoding="utf-8")
        return folder

    def test_changed_sources_or_review_set_invalidate_preparation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_prepared(root)
            with patch.object(trainer, "load_reviews", return_value=({}, {"review_fixture": "stable"})):
                cases, _, _ = trainer.load_prepared_cases(root)
                self.assertEqual(len(cases), 10)
                (root / "images/1H_1.jpg").write_bytes(b"changed source")
                with self.assertRaisesRegex(ValueError, "case_source_changed"):
                    trainer.load_prepared_cases(root)
            with patch.object(trainer, "load_reviews", return_value=({}, {"new_review": "changed"})):
                with self.assertRaisesRegex(ValueError, "anchor_reviews_changed"):
                    trainer.load_prepared_cases(root)

    def test_changed_vectors_and_unbound_feature_code_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = self.write_prepared(root)
            path = folder / "preparation_report.json"
            preparation = json.loads(path.read_text(encoding="utf-8"))
            preparation["feature_code_sha256"].pop("scenario_model.py")
            path.write_text(json.dumps(preparation), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "required_provenance"):
                trainer.load_prepared_cases(root)
            (folder / "cases.jsonl").write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "prepared_cases_changed"):
                trainer.load_prepared_cases(root)


if __name__ == "__main__":
    unittest.main()
