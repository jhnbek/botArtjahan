import sys
import json
import unittest
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "knowledge_bot"))
from level_history import DAY_MS, daily_level_history, subtract_calendar_months
from scn002_strict_kb_backtest import Bar


def stamp(value):
    return int(datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp() * 1000)


def row(value):
    return {"open_time_ms": stamp(value), "payload": object()}


class DailyLevelHistoryTests(unittest.TestCase):
    def test_calendar_months_clamp_month_end_and_leap_day(self):
        for source, target in [("2026-08-31", "2025-02-28"),
                               ("2025-08-31", "2024-02-29"),
                               ("2026-09-23", "2025-03-23")]:
            with self.subTest(source=source):
                actual = subtract_calendar_months(datetime.fromisoformat(source).date(), 18)
                self.assertEqual(actual.isoformat(), target)

    def test_cutoff_inclusive_and_original_entries_preserved(self):
        bars = [row(day) for day in ("2025-03-22", "2025-03-23", "2026-09-23")]
        retained, metadata = daily_level_history(bars, interval="1d")
        self.assertEqual(retained, bars[1:])
        self.assertIs(retained[0], bars[1])
        self.assertEqual(metadata["cutoff_date"], "2025-03-23")
        self.assertEqual(metadata["start_index"], 1)
        self.assertEqual(metadata["end_index"], 3)
        self.assertEqual(metadata["excluded_before"], 1)
        self.assertFalse(metadata["older_bars_used_for_atr"])
        self.assertEqual(len(bars), 3)

    def test_as_of_is_explicit_and_excludes_future_opens(self):
        bars = [row(day) for day in ("2025-03-22", "2025-03-23", "2026-09-23",
                                     "2026-09-24", "2027-09-24")]
        retained, metadata = daily_level_history(
            bars, interval="1d", as_of_ms=stamp("2026-09-23T12:00:00"))
        self.assertEqual(retained, bars[1:3])
        self.assertEqual(metadata["cutoff_date"], "2025-03-23")
        self.assertEqual(metadata["excluded_after"], 2)
        self.assertEqual(metadata["reference_source"], "explicit_as_of")

    def test_retrospective_reference_uses_supplied_history_not_today(self):
        bars = [row(day) for day in ("2000-01-01", "2000-01-02", "2002-09-30")]
        retained, metadata = daily_level_history(bars, interval="1d")
        self.assertEqual(retained, [bars[-1]])
        self.assertEqual(metadata["as_of_date"], "2002-09-30")
        self.assertEqual(metadata["cutoff_date"], "2001-03-30")
        self.assertEqual(metadata["reference_source"], "last_supplied_bar")

    def test_bar_objects_infer_daily_cadence_with_weekend_gaps(self):
        bars = [Bar(stamp(day), 100, 101, 99, 100, 1) for day in
                ("2024-03-01", "2024-03-04", "2024-03-05", "2026-09-23")]
        retained, metadata = daily_level_history(bars)
        self.assertTrue(metadata["is_daily"])
        self.assertTrue(metadata["applied"])
        self.assertEqual(retained, bars[-1:])

    def test_intraday_and_weekly_histories_remain_unrestricted(self):
        for step in (3_600_000, 4 * 3_600_000, 2 * DAY_MS, 7 * DAY_MS):
            bars = [{"open_time_ms": i * step} for i in range(1000)]
            with self.subTest(step=step):
                retained, metadata = daily_level_history(bars, as_of_ms=step)
                self.assertEqual(retained, bars)
                self.assertFalse(metadata["applied"])

    def test_explicit_interval_overrides_inference(self):
        bars = [{"open_time_ms": i * DAY_MS} for i in range(1000)]
        retained, metadata = daily_level_history(bars, interval="4h", as_of_ms=DAY_MS)
        self.assertEqual(retained, bars)
        self.assertFalse(metadata["is_daily"])

    def test_empty_single_and_all_expired_histories(self):
        retained, metadata = daily_level_history([], interval="1d")
        self.assertEqual(retained, [])
        self.assertFalse(metadata["applied"])
        bars = [row("2020-01-01")]
        self.assertFalse(daily_level_history(bars)[1]["applied"])
        self.assertEqual(daily_level_history(bars, interval="1d")[0], bars)
        retained, metadata = daily_level_history(
            bars, interval="1d", as_of_ms=stamp("2026-09-23"))
        self.assertEqual(retained, [])
        self.assertEqual(metadata["start_index"], 1)
        self.assertEqual(metadata["end_index"], 1)
        self.assertEqual(metadata["retained_count"], 0)

    def test_all_future_bars_and_as_of_exact_open(self):
        bars = [row("2026-09-23"), row("2026-09-24")]
        retained, metadata = daily_level_history(
            bars, interval="1d", as_of_ms=stamp("2020-01-01"))
        self.assertEqual(retained, [])
        self.assertEqual(metadata["excluded_after"], 2)
        retained, _ = daily_level_history(
            bars, interval="1d", as_of_ms=stamp("2026-09-23"))
        self.assertEqual(retained, bars[:1])

    def test_dict_open_time_alias_and_invalid_input(self):
        bars = [{"open_time": i * DAY_MS} for i in range(1000)]
        self.assertTrue(daily_level_history(bars)[1]["applied"])
        with self.assertRaisesRegex(ValueError, "oldest to newest"):
            daily_level_history(list(reversed(bars)), interval="1d")
        for invalid in (-1, 1.5, True):
            with self.subTest(months=invalid), self.assertRaises(ValueError):
                daily_level_history(bars, months=invalid)


class DailyChartHistoryTests(unittest.TestCase):
    @staticmethod
    def rows():
        return [{"open_time_ms": value, "open": 100, "high": 101,
                 "low": 99, "close": 100, "volume": 1}
                for value in range(stamp("2024-09-23"), stamp("2026-09-25"), DAY_MS)]

    def test_manual_inflection_cannot_revive_expired_bsu_and_indices_stay_original(self):
        import desktop_app as ui
        rows = self.rows()
        reviews = [{"price": 99, "human_bsu_date": {"year": 2025, "month": 3, "day": 22}},
                   {"price": 101, "human_bsu_date": {"year": 2025, "month": 4, "day": 1}}]
        with patch.object(ui, "discover_levels", return_value=[]) as discover:
            reports = ui.inflection_review_levels(
                rows, reviews, interval="1d", as_of_ms=stamp("2026-09-23T12:00:00"))
        self.assertEqual([report["price"] for report in reports], [101])
        report = reports[0]
        self.assertEqual(rows[report["bsu"]["index"]]["open_time_ms"], stamp("2025-04-01"))
        self.assertEqual(report["history_window"]["cutoff_date"], "2025-03-23")
        retained_bars = discover.call_args.args[0]
        self.assertEqual(retained_bars[0].open_time, stamp("2025-03-23"))
        self.assertEqual(retained_bars[-1].open_time, stamp("2026-09-22"))

    def test_worker_trims_chart_but_keeps_current_forming_day(self):
        import desktop_app as ui
        worker = ui.AnalyzeWorker("BTCUSDT", "1d", 1000, review_mode="mirror_limit")
        results, errors = [], []
        worker.finished_ok.connect(results.append)
        worker.failed.connect(errors.append)
        with patch.object(ui, "feed_get_ohlc", return_value={"bars": self.rows()}), \
             patch.object(ui, "typed_review_levels", return_value=[]) as review, \
             patch.object(ui, "datetime") as clock:
            clock.now.return_value = datetime.fromtimestamp(
                stamp("2026-09-23T12:00:00") / 1000, tz=timezone.utc)
            worker.run()
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertEqual(result["bars"][0]["open_time_ms"], stamp("2025-03-23"))
        self.assertEqual(result["bars"][-1]["open_time_ms"], stamp("2026-09-23"))
        self.assertEqual(review.call_args.kwargs["interval"], "1d")
        self.assertEqual(review.call_args.args[0], result["bars"])

    def test_real_discovery_matches_explicit_window_and_rebases_all_evidence_dates(self):
        from chart_level_modes import closed_candle_rows
        from level_discovery import DiscoveryParams, discover_levels
        fixture = (Path(__file__).resolve().parents[1] / "_knowledge_base" / "manual_reviews"
                   / "strong_levels_review_20260923" / "candles.json")
        snapshot = json.loads(fixture.read_text(encoding="utf-8"))
        as_of = stamp("2026-09-23T06:00:00")
        cutoff = stamp("2025-03-23")
        rows = closed_candle_rows(snapshot["bars"], as_of_ms=as_of)
        full_bars = [Bar(int(bar["open_time_ms"]), *[float(bar[key]) for key in
                     ("open", "high", "low", "close", "volume")]) for bar in rows]
        # Build the baseline independently of the production window helper.
        recent_bars = [bar for bar in full_bars if bar.open_time >= cutoff]
        self.assertEqual((len(full_bars), len(recent_bars)), (701, 549))
        self.assertEqual(recent_bars[0].open_time, cutoff)
        params = DiscoveryParams(nearest_window_atr=float("inf"))
        full_levels = discover_levels(full_bars, params, interval="1d", as_of_ms=as_of)
        recent_levels = discover_levels(recent_bars, params, interval="1d", as_of_ms=as_of)
        self.assertGreater(len(full_levels), 10)
        self.assertEqual(len(full_levels), len(recent_levels))
        checked = {"bsu": 0, "touches": 0, "events": 0, "channels": 0}

        def assert_same_evidence(full, recent, path=()):
            key = path[-1] if path else ""
            if key == "history_window":
                return
            if isinstance(full, dict):
                self.assertEqual(full.keys(), recent.keys(), path)
                for name in full:
                    assert_same_evidence(full[name], recent[name], (*path, name))
                return
            if isinstance(full, list):
                self.assertEqual(len(full), len(recent), path)
                index_list = (key == "indices" or key.endswith("_indices")
                              or key in ("mirror_pair", "recent_close_switches", "lifetime_close_switches"))
                for first, second in zip(full, recent):
                    if index_list and isinstance(first, int):
                        assert_same_evidence(first, second, (*path, "index"))
                    else:
                        assert_same_evidence(first, second, path)
                return
            if isinstance(full, int) and (key == "index" or key.endswith("_index")):
                self.assertGreaterEqual(full, 152, path)
                self.assertLess(full, len(full_bars), path)
                self.assertGreaterEqual(recent, 0, path)
                self.assertLess(recent, len(recent_bars), path)
                self.assertEqual(full_bars[full].open_time, recent_bars[recent].open_time, path)
                self.assertGreaterEqual(full_bars[full].open_time, cutoff, path)
                if key == "bsu_index":
                    checked["bsu"] += 1
                if "touch_indices" in path:
                    checked["touches"] += 1
                if "events" in path or "entry_events" in path:
                    checked["events"] += 1
                if "channels" in path:
                    checked["channels"] += 1
                return
            self.assertEqual(full, recent, path)

        for full, recent in zip(full_levels, recent_levels):
            with self.subTest(price=full.price):
                self.assertEqual(full.history_window["start_index"], 152)
                self.assertEqual(recent.history_window["start_index"], 0)
                assert_same_evidence(asdict(full), asdict(recent))
        for category, count in checked.items():
            self.assertGreater(count, 0, f"Real fixture must exercise {category} coordinates")


if __name__ == "__main__":
    unittest.main()
