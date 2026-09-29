"""Historical pagination, provenance, and causal availability checks."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
import urllib.error

from knowledge_bot.scenario_bybit_history import BybitHistoryClient, HistoryError

HOUR = 3_600_000


def row(hour, close="101"):
    return [str(hour * HOUR), "100", "102", "99", close, "12", "1200"]


def payload(rows, snapshot=20 * HOUR):
    return dict(retCode=0, retMsg="OK", time=snapshot,
                result=dict(category="linear", symbol="BTCUSDT", list=rows))


class BybitHistoryTests(unittest.TestCase):
    def client(self, transport, **kwargs):
        return BybitHistoryClient(cache_dir=None, transport=transport,
                                  min_request_interval=0, sleep=lambda _: None, **kwargs)

    def test_backward_pagination_exact_range_and_closed_timestamps(self):
        requests = []
        all_rows = [row(hour) for hour in range(8)]

        def transport(params):
            requests.append(dict(params))
            selected = [r for r in reversed(all_rows) if params["start"] <= int(r[0]) <= params["end"]]
            return payload(selected[:params["limit"]])

        result = self.client(transport).fetch_range("BTCUSDT", "1h", HOUR + 1, 7 * HOUR, page_limit=2)
        self.assertEqual([b["open_time_ms"] for b in result["bars"]], [h * HOUR for h in range(2, 7)])
        self.assertEqual([p["end"] for p in requests], [7 * HOUR - 1, 5 * HOUR - 1, 3 * HOUR - 1, 2 * HOUR - 1])
        self.assertTrue(result["coverage_complete"])
        self.assertEqual(result["expected_closed_bar_count"], 5)
        self.assertEqual(result["bars"][0]["open_time"], "1970-01-01T02:00:00Z")
        self.assertEqual(result["bars"][0]["close_time"], "1970-01-01T03:00:00Z")
        self.assertTrue(all(b["closed"] for b in result["bars"]))

    def test_historical_as_of_never_returns_later_final_candle(self):
        calls = iter([payload([row(3), row(2), row(1)])])
        result = self.client(lambda _: next(calls)).fetch_range(
            "BTCUSDT", "1h", HOUR, 4 * HOUR, as_of_ms=3 * HOUR + HOUR // 2)
        self.assertEqual([b["open_time_ms"] for b in result["bars"]], [HOUR, 2 * HOUR])
        self.assertEqual(result["excluded_unclosed_count"], 1)
        self.assertTrue(result["coverage_complete"])

    def test_exchange_clock_bounds_closure_even_if_requested_as_of_is_future(self):
        result = self.client(lambda _: payload([row(1), row(0)], snapshot=HOUR + 100)).fetch_range(
            "BTCUSDT", "1h", 0, 2 * HOUR, as_of_ms=8 * HOUR)
        self.assertEqual(len(result["bars"]), 1)
        self.assertEqual(result["as_of_ms"], HOUR + 100)
        self.assertEqual(result["bars"][0]["close_time_ms"], HOUR)

    def test_gaps_are_preserved_including_absent_listing_history(self):
        responses = iter([payload([row(4), row(2)]), payload([])])
        result = self.client(lambda _: next(responses)).fetch_range("BTCUSDT", "1h", HOUR, 6 * HOUR)
        self.assertFalse(result["coverage_complete"])
        self.assertEqual(result["expected_closed_bar_count"], 5)
        self.assertEqual([r["start_ms"] for r in result["missing_ranges"]], [HOUR, 3 * HOUR, 5 * HOUR])
        self.assertEqual(sum(r["missing_bars"] for r in result["missing_ranges"]), 3)

    def test_cache_reuse_preserves_request_provenance_and_reapplies_as_of(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            def transport(params):
                calls.append(params)
                return payload([row(1), row(0)])
            client = BybitHistoryClient(directory, transport=transport, min_request_interval=0)
            first = client.fetch_range("BTCUSDT", "1h", 0, 2 * HOUR)
            second = client.fetch_range("BTCUSDT", "1h", 0, 2 * HOUR, as_of_ms=HOUR)
            self.assertEqual(len(calls), 1)
            self.assertTrue(second["cache_hit"])
            self.assertEqual(len(second["bars"]), 1)
            self.assertEqual(first["source_cache_sha256"], second["source_cache_sha256"])
            stored = json.loads(Path(first["cache_path"]).read_text(encoding="utf-8"))
            stored["bars"][0]["close"] = 101.5
            Path(first["cache_path"]).write_text(json.dumps(stored), encoding="utf-8")
            with self.assertRaisesRegex(HistoryError, "hash"):
                client.fetch_range("BTCUSDT", "1h", 0, 2 * HOUR)

    def test_incomplete_current_range_cache_refreshes(self):
        with tempfile.TemporaryDirectory() as directory:
            responses = iter([payload([row(1), row(0)], snapshot=HOUR + 1),
                              payload([row(1), row(0)], snapshot=2 * HOUR + 1)])
            client = BybitHistoryClient(directory, transport=lambda _: next(responses), min_request_interval=0)
            first = client.fetch_range("BTCUSDT", "1h", 0, 2 * HOUR)
            second = client.fetch_range("BTCUSDT", "1h", 0, 2 * HOUR)
            self.assertFalse(second["cache_hit"])
            self.assertEqual(len(first["bars"]), 1)
            self.assertEqual(len(second["bars"]), 2)

    def test_rate_limit_retries_and_permanent_error_does_not(self):
        pauses = []
        responses = iter([dict(retCode=10006, retMsg="Too many visits"), payload([row(0)])])
        client = BybitHistoryClient(None, transport=lambda _: next(responses),
                                    min_request_interval=0, sleep=pauses.append)
        self.assertEqual(client.fetch_range("BTCUSDT", "1h", 0, HOUR)["bar_count"], 1)
        self.assertEqual(pauses, [1.])
        with self.assertRaisesRegex(HistoryError, "10001"):
            self.client(lambda _: dict(retCode=10001, retMsg="bad symbol")).fetch_range("BTCUSDT", "1h", 0, HOUR)

    def test_http429_obeys_retry_after_and_throttle(self):
        pauses = []
        calls = []
        def transport(params):
            calls.append(params)
            if len(calls) == 1:
                raise urllib.error.HTTPError("https://api.bybit.com", 429, "rate", {"Retry-After": "3"}, None)
            return payload([row(0)])
        client = BybitHistoryClient(None, transport=transport, sleep=pauses.append, clock=lambda: 0.)
        client.fetch_range("BTCUSDT", "1h", 0, HOUR)
        self.assertEqual(pauses, [3., .25])

    def test_invalid_geometry_inconsistent_pages_and_no_progress_fail(self):
        invalid = row(0)
        invalid[2] = "99.5"
        with self.assertRaisesRegex(HistoryError, "geometry"):
            self.client(lambda _: payload([invalid])).fetch_range("BTCUSDT", "1h", 0, HOUR)
        responses = iter([payload([row(2)]), payload([row(2, "100.5"), row(1), row(0)])])
        with self.assertRaisesRegex(HistoryError, "conflicting"):
            self.client(lambda _: next(responses)).fetch_range("BTCUSDT", "1h", 0, 3 * HOUR)
        with self.assertRaisesRegex(HistoryError, "no progress"):
            self.client(lambda _: payload([row(2)])).fetch_range("BTCUSDT", "1h", 0, 3 * HOUR)

    def test_wrong_market_and_missing_snapshot_are_rejected(self):
        wrong = payload([row(0)])
        wrong["result"]["category"] = "spot"
        with self.assertRaisesRegex(HistoryError, "symbol/category"):
            self.client(lambda _: wrong).fetch_range("BTCUSDT", "1h", 0, HOUR)
        missing = deepcopy(wrong)
        missing["result"]["category"] = "linear"
        missing.pop("time")
        with self.assertRaisesRegex(HistoryError, "snapshot"):
            self.client(lambda _: missing).fetch_range("BTCUSDT", "1h", 0, HOUR)

    def test_utc_daily_and_minute_intervals(self):
        for interval, duration in (("1d", 24 * HOUR), ("1m", 60_000)):
            with self.subTest(interval=interval):
                result = self.client(lambda _: payload([row(0)], snapshot=48 * HOUR)).fetch_range(
                    "BTCUSDT", interval, 0, duration)
                self.assertEqual(result["bars"][0]["close_time_ms"], duration)


if __name__ == "__main__":
    unittest.main()
