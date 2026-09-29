"""Counterexamples for causal level prerequisites and price reflection."""
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "knowledge_bot"))
from scenario_prerequisites import (PREREQUISITE_FEATURE_NAMES, analyze_prerequisites,
                                    prerequisite_features_from_ohlc)


def bar(o=103., h=104., l=102., c=103., **metadata):
    return {"open": o, "high": h, "low": l, "close": c, **metadata}


def reflect(bars, pivot=100.):
    return [bar(2*pivot-b["open"], 2*pivot-b["low"], 2*pivot-b["high"], 2*pivot-b["close"])
            for b in bars]


class PrerequisiteTests(unittest.TestCase):
    def test_single_lower_sweep_requires_closed_return_from_original_side(self):
        history = [bar() for _ in range(20)]
        result = analyze_prerequisites(history + [bar(101, 102, 97, 101.5)], 100)
        self.assertEqual(result["features"]["long_false_breakout_one_bar"], 1)
        self.assertEqual(result["features"]["short_false_breakout_one_bar"], 0)
        lp = next(e for e in result["evidence"]["long"] if e["code"] == "false_breakout")
        self.assertFalse(lp["confirms_level"])
        self.assertEqual(lp["known_index"], 20)
        for candidate in (bar(101, 102, 97, 99), bar(99, 102, 97, 101), bar(100, 102, 97, 101)):
            self.assertEqual(prerequisite_features_from_ohlc(history + [candidate], 100)["long_false_breakout_one_bar"], 0)

    def test_two_bar_return_only_known_after_second_close(self):
        before = [bar() for _ in range(20)] + [bar(102, 103, 97, 98)]
        after = before + [bar(98, 103, 97, 102)]
        self.assertEqual(prerequisite_features_from_ohlc(before, 100)["long_false_breakout_two_bar"], 0)
        result = analyze_prerequisites(after, 100)
        self.assertEqual(result["features"]["long_false_breakout_two_bar"], 1)
        event = next(e for e in result["evidence"]["long"] if e["code"] == "false_breakout_two_bar")
        self.assertEqual(event["bar_indices"], [20, 21])
        self.assertEqual(event["known_index"], 21)

    def test_adjacent_wick_returns_are_chop_not_multiple_lp1(self):
        before = [bar() for _ in range(20)] + [bar(101, 102, 97, 101)]
        after = before + [bar(101, 102, 98, 101)]
        a, b = (prerequisite_features_from_ohlc(rows, 100) for rows in (before, after))
        self.assertEqual(a["long_false_breakout_one_bar"], 1)
        self.assertEqual(b["long_false_breakout_one_bar"], 0)
        self.assertEqual(b["current_chop"], 1)
        self.assertEqual(prerequisite_features_from_ohlc(after, 100, decision_index=20), a)

    def test_compression_requires_approach_side_rising_lows_and_closes(self):
        last = [bar(95+i, 99.8, 94+i, 96+i) for i in range(4)]
        rows = [bar(94, 96, 93, 95) for _ in range(16)] + last
        result = analyze_prerequisites(rows, 100)
        codes = [e["code"] for e in result["evidence"]["long"]]
        self.assertIn("compression_toward_level", codes)
        self.assertNotIn("compression_toward_level", [e["code"] for e in result["evidence"]["short"]])
        self.assertGreater(result["features"]["long_approach_progress_4_tr"], 0)
        shifted = [{key: value+10 for key, value in row.items()} for row in rows]
        self.assertNotIn("compression_toward_level", [e["code"] for e in analyze_prerequisites(shifted, 100)["evidence"]["long"]])
        low_break = rows[:-2] + [bar(97, 99.8, 94, 98), rows[-1]]
        self.assertNotIn("compression_toward_level", [e["code"] for e in analyze_prerequisites(low_break, 100)["evidence"]["long"]])

    def test_all_side_features_swap_under_price_reflection(self):
        rows = [bar(102+i*.1, 104+i*.1, 101+i*.1, 103+i*.1) for i in range(20)]
        rows += [bar(101, 104, 97, 103)]
        original = prerequisite_features_from_ohlc(rows, 100)
        inverse = prerequisite_features_from_ohlc(reflect(rows), 100)
        for name, value in original.items():
            swapped = name.replace("long_", "short_", 1) if name.startswith("long_") else name.replace("short_", "long_", 1)
            self.assertAlmostEqual(value, inverse[swapped], places=10, msg=name)
        self.assertEqual(tuple(original), PREREQUISITE_FEATURE_NAMES)
        self.assertEqual(len(original), 24)

    def test_adding_future_candles_and_labels_cannot_change_prefix(self):
        rows = [bar() for _ in range(20)] + [bar(101, 102, 97, 101)]
        original = analyze_prerequisites(rows, 100)
        with_metadata = [dict(row, direction="short", outcome="loss", scenario_id=12345) for row in rows]
        future = [{"open": math.nan, "close": "future", "high": -9, "low": 800}]
        self.assertEqual(original, analyze_prerequisites(with_metadata + future, 100, decision_index=20))

    def test_scale_excludes_decision_bar_and_units_are_invariant(self):
        rows = [bar() for _ in range(20)] + [bar(101, 102, 97, 101)]
        result = analyze_prerequisites(rows, 100)
        enormous = rows[:-1] + [bar(101, 1000, -500, 101)]
        self.assertEqual(result["scale_prior_tr"], analyze_prerequisites(enormous, 100)["scale_prior_tr"])
        transformed = [{key: value*3-1000 for key, value in row.items()} for row in rows]
        units = prerequisite_features_from_ohlc(transformed, -700)
        for key, value in result["features"].items():
            self.assertAlmostEqual(value, units[key], places=10)

    def test_micro_penetration_not_false_breakout(self):
        rows = [bar() for _ in range(20)] + [bar(101, 102, 99.999, 101)]
        self.assertEqual(prerequisite_features_from_ohlc(rows, 100)["long_false_breakout_one_bar"], 0)

    def test_invalid_inputs_and_zero_history_range_fail_closed(self):
        cases = ([bar()]*15, [bar(100, 100, 100, 100)]*16,
                 [bar()]*15+[bar(102, 101, 99, 100)], [bar()]*15+[bar(c=math.nan)])
        for rows in cases:
            with self.subTest(rows=rows[-1]), self.assertRaises(ValueError):
                prerequisite_features_from_ohlc(rows, 100)
        for index in (-1, 16, True, 14.5):
            with self.subTest(index=index), self.assertRaises(ValueError):
                prerequisite_features_from_ohlc([bar()]*16, 100, decision_index=index)


if __name__ == "__main__":
    unittest.main()
