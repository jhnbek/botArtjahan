"""Working-level spacing must select stronger evidence, not a target count."""

import itertools
import math
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "knowledge_bot"))

from level_selection import enforce_minimum_spacing, level_type_confluence
from chart_level_modes import rejected_level_prices, typed_review_levels


def level(price, index=0, side="support"):
    return SimpleNamespace(price=price, bsu_index=index, side=side)


class MinimumLevelSpacingTests(unittest.TestCase):
    def select(self, levels, scores, fraction=0.015):
        return enforce_minimum_spacing(levels, fraction, scores)

    def test_exact_minimum_distance_is_allowed_but_smaller_distance_is_not(self):
        for scale in (0.001, 1, 1000):
            with self.subTest(scale=scale):
                origin = level(100 * scale)
                boundary = level(101.5 * scale, 1)
                accepted, _ = self.select(
                    [origin, boundary], {origin.price: 3, boundary.price: 2}
                )
                self.assertEqual(len(accepted), 2)

                inside = level(101.499 * scale, 1)
                accepted, audit = self.select(
                    [origin, inside], {origin.price: 3, inside.price: 2}
                )
                self.assertEqual([item.price for item in accepted], [origin.price])
                rejection = next(row for row in audit if row["price"] == inside.price)
                self.assertEqual(rejection["decision"], "rejected")
                self.assertIn("too_close_to_stronger_level", rejection["reasons"])
                self.assertEqual(rejection["stronger_price"], origin.price)
                self.assertAlmostEqual(rejection["gap_percent"], 1.499)

    def test_gap_uses_lower_price_and_does_not_depend_on_winner_direction(self):
        lower, upper = level(100), level(101.51, 1)
        for scores in ({100: 3, 101.51: 2}, {100: 2, 101.51: 3}):
            with self.subTest(scores=scores):
                selected, _ = self.select([lower, upper], scores)
                self.assertEqual({item.price for item in selected}, {100, 101.51})

        inside = level(101.49, 1)
        selected, audit = self.select([lower, inside], {100: 2, 101.49: 3})
        self.assertEqual([item.price for item in selected], [101.49])
        rejection = next(row for row in audit if row["decision"] == "rejected")
        self.assertAlmostEqual(rejection["gap_percent"], 1.49)

    def test_stronger_newer_level_wins_over_weaker_older_level(self):
        older, newer = level(100, 0), level(101, 20)
        selected, audit = self.select([older, newer], {100: 3, 101: 7})
        self.assertEqual(selected, [newer])
        rejection = next(row for row in audit if row["price"] == older.price)
        self.assertEqual(rejection["stronger_price"], newer.price)

    def test_equal_strength_prefers_earlier_bsu(self):
        older, newer = level(101, 3), level(100, 20)
        for candidates in ([older, newer], [newer, older]):
            selected, _ = self.select(candidates, {100: 7, 101: 7})
            self.assertEqual(selected, [older])

    def test_chain_does_not_merge_distant_endpoints_through_rejected_middle(self):
        left, middle, right = level(100), level(101.4, 1), level(102.8, 2)
        selected, audit = self.select(
            [middle, right, left], {100: 9, 101.4: 8, 102.8: 7}
        )
        self.assertEqual({item.price for item in selected}, {100, 102.8})
        rejected = [row for row in audit if row["decision"] == "rejected"]
        self.assertEqual([row["price"] for row in rejected], [101.4])

    def test_selection_prefers_strength_even_when_middle_blocks_two_neighbors(self):
        candidates = [level(100), level(101.4, 1), level(102.8, 2)]
        selected, audit = self.select(candidates, {100: 3, 101.4: 8, 102.8: 7})
        self.assertEqual([item.price for item in selected], [101.4])
        self.assertEqual(sum(row["decision"] == "rejected" for row in audit), 2)

    def test_input_order_and_side_do_not_change_selected_prices(self):
        candidates = [
            level(100, 10, "mirror"),
            level(100.8, 11, "resistance"),
            level(102, 12, "support"),
            level(106, 13, "mirror"),
        ]
        scores = {100: 5, 100.8: 4, 102: 3, 106: 2}
        for permutation in itertools.permutations(candidates):
            selected, _ = self.select(list(permutation), scores)
            self.assertEqual({item.price for item in selected}, {100, 102, 106})

    def test_selection_has_no_eighteen_level_cap_and_does_not_mutate_input(self):
        candidates = [level(100 * 1.02 ** i, i) for i in range(24)]
        original = list(candidates)
        selected, audit = self.select(candidates, {item.price: 1 for item in candidates})
        self.assertEqual(len(selected), 24)
        self.assertEqual(candidates, original)
        self.assertEqual(len(audit), 24)
        self.assertTrue(all(row["decision"] == "kept" for row in audit))

    def test_rejects_invalid_prices_and_distance_parameters(self):
        for price in (0, -1, math.inf, -math.inf, math.nan):
            with self.subTest(price=price):
                with self.assertRaises(ValueError):
                    self.select([level(price)], {price: 1})
        for fraction in (-0.001, math.inf, -math.inf, math.nan):
            with self.subTest(fraction=fraction):
                with self.assertRaises(ValueError):
                    self.select([level(100)], {100: 1}, fraction=fraction)

    def test_empty_input_and_disabled_spacing_are_supported(self):
        self.assertEqual(self.select([], {}), ([], []))
        selected, _ = self.select([level(100), level(100.1)], {100: 2, 100.1: 1}, fraction=0)
        self.assertEqual(len(selected), 2)


class WholeLevelCorrectionTests(unittest.TestCase):
    chart_key = ('bybit', 'BTCUSDT', '1d')

    @staticmethod
    def rejection(price=100, **overrides):
        return {'action': 'hide_robot_level', 'price': price, 'exchange': 'bybit',
                'symbol': 'BTCUSDT', 'interval': '1d', 'review_mode': 'mirror_limit',
                **overrides}

    def test_replay_is_scoped_before_restoring_same_price_in_other_mode(self):
        records = [self.rejection(),
                   self.rejection(review_mode='paranormal'),
                   self.rejection(action='restore_robot_level', review_mode='paranormal'),
                   self.rejection(200, symbol='ETHUSDT'),
                   self.rejection(300, exchange='binance'),
                   self.rejection(400, interval='1h')]
        self.assertEqual(rejected_level_prices(records, self.chart_key, 'mirror_limit'), (100,))
        self.assertEqual(rejected_level_prices(records, self.chart_key, 'paranormal'), ())
        self.assertEqual(rejected_level_prices(records, self.chart_key, 'inflection'), ())
        records.append(self.rejection(action='restore_robot_level'))
        self.assertEqual(rejected_level_prices(records, self.chart_key, 'mirror_limit'), ())

    def test_legacy_global_restore_and_inflection_scope_follow_existing_rules(self):
        legacy = self.rejection(100, basis_tags=['inflection'])
        legacy.pop('review_mode')
        global_hide = self.rejection(200, review_mode='all_levels')
        records = [legacy, global_hide]
        self.assertEqual(rejected_level_prices(records, self.chart_key, 'mirror_limit'), (200,))
        self.assertEqual(rejected_level_prices(records, self.chart_key, 'inflection'), (100, 200))
        restore = self.rejection(200, action='restore_robot_level')
        restore.pop('review_mode')
        records.append(restore)
        self.assertEqual(rejected_level_prices(records, self.chart_key, 'mirror_limit'), ())
        self.assertEqual(rejected_level_prices(records, self.chart_key, 'inflection'), (100,))
        records.append(self.rejection(100, action='restore_robot_level', review_mode='secondary_limit'))
        self.assertEqual(rejected_level_prices(records, self.chart_key, 'inflection'), ())

    def test_manual_lines_bar_markers_and_malformed_rows_do_not_change_constraints(self):
        records = [self.rejection()]
        records.extend(self.rejection(action=action) for action in
                       ('add_manual_level', 'update_manual_level', 'remove_manual_level',
                        'clear_manual_levels', 'reject_robot_bsu', 'restore_robot_bsu'))
        records.extend([None, {}, self.rejection(None), self.rejection('invalid'),
                        self.rejection(math.nan), self.rejection(math.inf), self.rejection(-1)])
        self.assertEqual(rejected_level_prices(records, self.chart_key, 'mirror_limit'), (100,))

    def test_typed_computation_receives_constraints_before_selecting_levels(self):
        day = 86400000
        rows = [dict(open_time_ms=i*day, open=99, high=100, low=98, close=99, volume=1)
                for i in range(2)]
        with patch('chart_level_modes.discover_levels', return_value=[]) as discover:
            typed_review_levels(rows, 'mirror_limit', as_of_ms=2*day,
                                excluded_level_prices=(100, 105),
                                excluded_contacts=((day, 'L', 99),))
        params = discover.call_args.args[1]
        self.assertEqual(params.excluded_level_prices, (100, 105))
        self.assertEqual(params.excluded_contacts, ((day, 'L', 99),))
        self.assertTrue(params.working_selection)

    def test_typed_worker_forwards_only_active_same_context_and_mode_rejections(self):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        try:
            import desktop_app as ui
        except ModuleNotFoundError:
            self.skipTest('Optional desktop dependencies are unavailable')
        records = [self.rejection(), self.rejection(200, review_mode='paranormal'),
                   self.rejection(action='restore_robot_level', review_mode='paranormal'),
                   self.rejection(300, symbol='ETHUSDT')]
        for mode, expected in [('mirror_limit', (100,)), ('paranormal', (200,))]:
            with self.subTest(mode=mode):
                worker = ui.AnalyzeWorker('BTCUSDT', '1d', 500, review_mode=mode)
                errors, results = [], []
                worker.failed.connect(errors.append)
                worker.finished_ok.connect(results.append)
                with patch.object(ui, 'read_records', return_value=records), \
                     patch.object(ui, 'feed_get_ohlc', return_value={'bars': []}), \
                     patch.object(ui, 'typed_review_levels', return_value=[]) as review:
                    worker.run()
                self.assertEqual(errors, [])
                self.assertEqual(len(results), 1)
                self.assertEqual(review.call_args.kwargs['excluded_level_prices'], expected)


class LevelTypeConfluenceTests(unittest.TestCase):
    def fixture(self):
        from level_discovery import Bar, Level
        bars = [Bar(0, 98, 100, 97, 99.7, 1),
                Bar(1, 99, 100, 98, 99.5, 1),
                Bar(2, 101, 102, 100, 101.5, 1)]
        candidate = Level(100, 0, '0', 'mirror',
            basis_tags=['limit_level', 'mirror_level', 'paranormal_bar', 'inflection'],
            inflection_check={'status': 'confirmed'})
        events = [{'index': i, 'kind': kind, 'atr': 1, 'confirms_level': True}
                  for i, kind in enumerate(('H', 'H', 'L'))]
        return bars, candidate, {'events': events, 'mirror_pair': [0, 2]}

    def test_more_confirmed_types_strengthen_same_price(self):
        bars, candidate, profile = self.fixture()
        all_tags = candidate.basis_tags[:]
        for count in range(1, 5):
            candidate.basis_tags = all_tags[:count]
            result = level_type_confluence(bars, candidate, profile)
            self.assertEqual(result['type_count'], count)
            self.assertEqual(result['strength_bonus'], 0.5 * (count-1))
            self.assertEqual(candidate.price, 100)

    def test_limit_aliases_and_duplicate_tags_do_not_add_types(self):
        bars, candidate, profile = self.fixture()
        candidate.basis_tags = ['limit_level', 'two_bar_limit', 'limit_level', 'round_number']
        self.assertEqual(level_type_confluence(bars, candidate, profile),
                         {'types': ['limit_level'], 'type_count': 1, 'strength_bonus': 0})

    def test_false_breakout_cannot_supply_paranormal_or_inflection_evidence(self):
        bars, candidate, profile = self.fixture()
        profile['events'].append({'index': 0, 'indices': [0], 'entry_context_only': True})
        result = level_type_confluence(bars, candidate, profile)
        self.assertEqual(result['types'], [])
        self.assertEqual(result['strength_bonus'], 0)

    def test_tags_without_confirmation_do_not_supply_bonus(self):
        bars, candidate, profile = self.fixture()
        profile['events'] = []
        self.assertEqual(level_type_confluence(bars, candidate, profile)['strength_bonus'], 0)

    def test_small_body_and_pending_inflection_are_not_confirmed_types(self):
        from dataclasses import replace
        bars, candidate, profile = self.fixture()
        bars[0] = replace(bars[0], open=98.5)  # body 1.2 ATR, not paranormal
        candidate.inflection_check = {'status': 'pending'}
        self.assertEqual(level_type_confluence(bars, candidate, profile)['types'],
                         ['limit_level', 'mirror_level'])


if __name__ == "__main__":
    unittest.main()
