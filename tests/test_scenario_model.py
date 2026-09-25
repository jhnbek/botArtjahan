"""Learned timing scores cannot use future bars or override rule-based entries."""
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "knowledge_bot"))

import scenario_model as model
from scenario_image_features import FEATURE_NAMES, features_from_ohlc


def artifact():
    size = len(FEATURE_NAMES)
    return dict(schema_version=1, model_kind="entry_pairwise_ranker",
                feature_names=list(FEATURE_NAMES), mean=[1.] * size,
                scale=[2.] * size, weights=[2.] + [0.] * (size-1), bias=.25,
                threshold=None)


def bars(count=23):
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    return [dict(open_time=(start+timedelta(hours=i)).isoformat(),
                 open=100+i*.2, high=101+i*.2, low=99+i*.2, close=100.4+i*.2)
            for i in range(count)]


class ScenarioModelTests(unittest.TestCase):
    def test_raw_ranking_math_and_not_probability(self):
        features = dict.fromkeys(FEATURE_NAMES, 0.)
        features[FEATURE_NAMES[0]] = 5.
        self.assertEqual(model.score_features(features, artifact()), 4.25)

    def test_unknown_missing_or_nonfinite_features_rejected(self):
        features = dict.fromkeys(FEATURE_NAMES, 0.)
        for value in (float("nan"), float("inf"), True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                model.score_features({**features, FEATURE_NAMES[0]: value}, artifact())
        with self.assertRaises(ValueError):
            model.score_features({**features, "future_return": 1}, artifact())
        with self.assertRaises(ValueError):
            model.score_features({}, artifact())

    def test_malformed_artifacts_rejected(self):
        cases = [dict(schema_version=2), dict(model_kind="direction_classifier"),
                 dict(feature_names=["future_return"]), dict(weights=[]),
                 dict(scale=[0.] * len(FEATURE_NAMES)), dict(bias=float("nan")),
                 dict(threshold=.65), dict(classes=["short", "long"])]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                model.validate_model({**artifact(), **changes})

    def test_missing_artifact_and_malformed_file_abstain(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/"model.json"
            result = model.entry_timing_advice([], 100., "long", model_path=path)
            self.assertEqual(result["status"], "unavailable")
            self.assertEqual(result["action"], "no_order")
            path.write_text('{"schema_version":', encoding="utf-8")
            result = model.entry_timing_advice(bars(), 100., "long", model_path=path)
            self.assertEqual(result["status"], "abstain")
            self.assertFalse(result["evaluated_profit"])

    def test_short_reflection_swaps_high_low_and_matches_long_features(self):
        original = bars()
        reflected = [dict(open=-b["open"], high=-b["low"], low=-b["high"], close=-b["close"])
                     for b in original]
        self.assertEqual(model.features_for_entry(reflected, -102., "short"),
                         features_from_ohlc(original, 102.))
        self.assertEqual(model.features_for_entry(original, 102., "long"),
                         features_from_ohlc(original, 102.))
        with self.assertRaises(ValueError):
            model.features_for_entry(original[:15], 102., "long")

    def test_unclosed_and_future_bars_cannot_change_score(self):
        history = bars()
        cutoff = datetime(2025, 1, 1, 21, 30, tzinfo=timezone.utc)
        altered = [dict(b) for b in history]
        for bar in altered[21:]:
            bar.update(open=9000., high=10000., low=1., close=9999.)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/"model.json"
            path.write_text(json.dumps(artifact()), encoding="utf-8")
            a = model.entry_timing_advice(history, 104., "long", as_of=cutoff, model_path=path)
            b = model.entry_timing_advice(altered, 104., "long", as_of=cutoff, model_path=path)
        self.assertEqual(a, b)
        self.assertEqual(a["status"], "research_only")
        self.assertEqual(a["closed_bars_used"], 21)
        self.assertEqual(a["decision_available_at"], "2025-01-01T21:00:00+00:00")
        self.assertLessEqual(a["compared_states"], 7)
        self.assertFalse(a["score_is_probability"])

    def test_timestamp_order_and_non_hourly_inputs_fail_closed(self):
        history = bars()
        with self.assertRaises(ValueError):
            model.closed_hourly_bars([history[1], history[0]], "2025-02-01T00:00:00Z")
        with self.assertRaises(ValueError):
            model.closed_hourly_bars([{**history[0], "open_time": "2025-01-01T00:00:00"}],
                                     "2025-02-01T00:00:00Z")
        result = model.entry_timing_advice(history, 102., "long", interval="1d")
        self.assertEqual(result["reason"], "model_trained_for_h1_only")

    def test_advice_is_an_added_field_never_a_new_entry_or_risk_decision(self):
        import entry_context as entries
        from level_discovery import Level
        from scn002_strict_kb_backtest import Bar
        history = [Bar(int(datetime.fromisoformat(b["open_time"]).timestamp()*1000),
                       b["open"], b["high"], b["low"], b["close"], 1.) for b in bars()]
        level = Level(102., 0, "2025-01-01T00:00:00Z", "support")
        candidate = dict(model="existing_entry", status="trigger", entry_price=104.,
                         stop_price=103., target_price=110., manual_review=[])
        approach = dict(status="setup", atr=2., nearest_level=dict(price=102.))
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(entries, "build_approach_context", return_value=approach), \
             patch.object(entries, "nearest_working_level", return_value=level), \
             patch.object(entries, "scenario_from_approach", return_value=dict(direction="long", valid=True, family="breakout")), \
             patch.object(entries, "build_fixation_candidate", return_value=candidate), \
             patch.object(entries, "build_bsu_bpu_candidate", return_value=candidate), \
             patch.object(entries, "build_primary_impulse_candidate", return_value=candidate):
            path = Path(temp)/"model.json"
            with patch.object(entries, "SCENARIO_MODEL_PATH", path):
                before = entries.build_entry_context("TEST", "1d", "1h", history, history,
                                                     [level], "auto", entries.EntryParams())
                path.write_text(json.dumps(artifact()), encoding="utf-8")
                after = entries.build_entry_context("TEST", "1d", "1h", history, history,
                                                    [level], "auto", entries.EntryParams())
        advice = after.pop("scenario_model")
        self.assertEqual(before, after)
        self.assertEqual(advice["status"], "research_only")
        self.assertFalse(advice["changes_entry_or_risk_rules"])


if __name__ == "__main__":
    unittest.main()
