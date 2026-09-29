"""The learned advisory pipeline cannot bypass D1 close or fabricate risk inputs."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from knowledge_bot import scenario_entry_policy as policy


START = datetime(2025, 1, 1, tzinfo=timezone.utc)


def bars(count, duration):
    return [
        {"open_time": (START + index * duration).isoformat(),
         "open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0}
        for index in range(count)
    ]


class ScenarioEntryPolicyTests(unittest.TestCase):
    def setUp(self):
        self.daily = bars(2, timedelta(days=1))
        self.hourly = bars(50, timedelta(hours=1))
        self.cutoff = START + timedelta(days=2)
        self.direction_result = {
            "status": "research_only", "direction": "long", "author_long_probability": 0.8,
            "prerequisites": {"features": {}, "evidence": {"long": [{"code": "lp1"}], "short": []}},
            "research_only": True, "automatic_order_execution_allowed": False,
        }
        self.direction = Mock(return_value=self.direction_result)
        self.direction_loader = patch.object(policy, "_direction_module", return_value=SimpleNamespace(direction_advice_from_ohlc=self.direction))
        self.direction_loader.start()
        self.addCleanup(self.direction_loader.stop)
        self.entry_patch = patch.object(policy.scenario_model, "entry_timing_advice", return_value={
            "status": "research_only", "entry_readiness_rank": 1, "compared_states": 7, "action": "no_order",
        })
        self.entry = self.entry_patch.start()
        self.addCleanup(self.entry_patch.stop)

    def advice(self, **kwargs):
        arguments = {
            "as_of": self.cutoff,
            "direction_model_path": Path("mock-direction-model.json"),
            "entry_model_path": Path("mock-entry-model.json"),
        }
        arguments.update(kwargs)
        return policy.build_learned_entry_advice(self.daily, self.hourly, 100.0, **arguments)

    def test_open_daily_bar_blocks_both_models_without_using_older_signal(self):
        self.cutoff -= timedelta(minutes=1)
        result = self.advice(structural_stop=99.0)
        self.assertEqual(result["status"], "waiting_for_daily_close")
        self.assertIsNone(result["take_profit_targets"])
        self.direction.assert_not_called()
        self.entry.assert_not_called()

    def test_exact_daily_close_calls_models_and_computes_planning_targets(self):
        result = self.advice(structural_stop=99.0)
        self.assertEqual(result["status"], "research_only")
        self.assertEqual(result["predicted_direction"], "long")
        self.assertEqual(result["evidence"]["long"], [{"code": "lp1"}])
        self.assertEqual(result["reference_entry_price"], 101.0)
        self.assertEqual(result["take_profit_targets"]["3R"]["target"], 107.0)
        self.assertEqual(result["take_profit_targets"]["4R"]["target"], 109.0)
        self.assertEqual(result["action"], "no_order")
        self.assertFalse(result["automatic_order_execution_allowed"])
        self.assertFalse(result["readiness"]["entry_permission"])
        self.assertTrue(self.direction.call_args.kwargs["daily_close_confirmed"])
        self.assertEqual(self.direction.call_args.kwargs["model_path"], Path("mock-direction-model.json"))
        self.assertEqual(self.entry.call_args.kwargs["daily_signal_open_time"], self.daily[-1]["open_time"])
        self.assertEqual(self.entry.call_args.kwargs["model_path"], Path("mock-entry-model.json"))
        self.assertEqual(self.entry.call_args.args[2], "long")

    def test_missing_stop_is_reported_without_guessing_from_atr(self):
        result = self.advice()
        self.assertEqual(result["status"], "needs_structural_stop")
        self.assertIsNone(result["structural_stop"])
        self.assertIsNone(result["take_profit_targets"])
        self.assertEqual(result["readiness"]["entry_readiness_rank"], 1)
        self.assertFalse(result["readiness"]["entry_permission"])

    def test_unclosed_hourly_price_and_outcome_fields_never_reach_models(self):
        self.daily[-1]["future_return"] = 1000.0
        self.daily[-1]["caption"] = "long profitable entry"
        for bar in self.hourly[48:]:
            bar.update(open=800.0, high=1000.0, low=1.0, close=900.0)
        self.hourly[47]["future_return"] = -1000.0
        result = self.advice(structural_stop=99.0)
        self.assertEqual(result["reference_entry_price"], 101.0)
        daily_input = self.direction.call_args.args[0]
        hourly_input = self.entry.call_args.args[0]
        self.assertEqual(len(hourly_input), 48)
        self.assertTrue(all(set(bar) == {"open", "high", "low", "close"} for bar in daily_input))
        self.assertTrue(all(set(bar) == {"open", "high", "low", "close", "open_time"} for bar in hourly_input))

    def test_direction_abstention_never_falls_back_to_long(self):
        self.direction.return_value = {"status": "blocked", "direction": None, "reasons": ["missing_model"]}
        result = self.advice(structural_stop=99.0)
        self.assertEqual(result["status"], "abstain")
        self.assertIsNone(result["predicted_direction"])
        self.assertIsNone(result["take_profit_targets"])
        self.entry.assert_not_called()

    def test_invalid_stop_does_not_become_trade_permission(self):
        for stop in (101.0, 102.0, 0, True, float("nan"), "99"):
            with self.subTest(stop=stop):
                result = self.advice(structural_stop=stop)
                self.assertEqual(result["status"], "invalid_structural_stop")
                self.assertIsNone(result["take_profit_targets"])
                self.assertEqual(result["action"], "no_order")

    def test_short_direction_uses_adverse_structural_stop(self):
        self.direction.return_value = {**self.direction_result, "direction": "short"}
        result = self.advice(structural_stop=103.0)
        self.assertEqual(result["take_profit_targets"]["3R"]["target"], 95.0)
        self.assertEqual(result["take_profit_targets"]["4R"]["target"], 93.0)
        self.assertEqual(self.entry.call_args.args[2], "short")

    def test_unordered_or_future_daily_history_fails_before_inference(self):
        for opening in (START + timedelta(days=2), START + timedelta(days=1)):
            self.daily[0]["open_time"] = opening.isoformat()
            with self.subTest(opening=opening):
                result = self.advice(structural_stop=99.0)
                self.assertEqual(result["status"], "abstain")
                self.assertEqual(result["reason"], "invalid_closed_bar_input")
                self.direction.assert_not_called()
                self.entry.assert_not_called()

    def test_bar_objects_and_level_object_are_supported(self):
        self.daily = [SimpleNamespace(**bar) for bar in self.daily]
        self.hourly = [SimpleNamespace(**bar) for bar in self.hourly]
        result = policy.build_learned_entry_advice(
            self.daily, self.hourly, SimpleNamespace(price=100.0), as_of=self.cutoff,
            structural_stop=99.0, direction_model_path="mock-direction", entry_model_path="mock-entry",
        )
        self.assertEqual(result["status"], "research_only")
        self.assertEqual(self.direction.call_args.args[1], 100.0)

    def test_entry_model_unavailable_is_visible_even_when_geometry_exists(self):
        self.entry.return_value = {"status": "unavailable", "reason": "model_not_found"}
        result = self.advice(structural_stop=99.0)
        self.assertEqual(result["status"], "entry_timing_unavailable")
        self.assertEqual(result["reason"], "model_not_found")
        self.assertIsNotNone(result["take_profit_targets"])
        self.assertFalse(result["readiness"]["entry_permission"])

    def test_reading_inputs_does_not_mutate_user_bars(self):
        original_daily, original_hourly = deepcopy(self.daily), deepcopy(self.hourly)
        self.advice(structural_stop=99.0)
        self.assertEqual(self.daily, original_daily)
        self.assertEqual(self.hourly, original_hourly)


if __name__ == "__main__":
    unittest.main()
