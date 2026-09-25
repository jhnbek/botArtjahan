"""Behavioral checks for departure quality and independent channel ranking."""

import itertools
import json
from dataclasses import replace
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'knowledge_bot'))

from level_discovery import Bar, DiscoveryParams, Level, discover_levels
from level_selection import (ordinary_departures, select_working_levels,
                             stopping_contexts, unresolved_chopping)


def candles(closes):
    result = []
    for index, close in enumerate(closes):
        opening = closes[index-1] if index else close
        result.append(Bar(index*86400000, opening, max(opening, close)+0.1,
                          min(opening, close)-0.1, close, 1.0))
    return result


def touch(index=0, kind='L', known_index=None):
    return {'index': index, 'indices': [index], 'kind': kind, 'atr': 1.0,
            'role': 'touch', 'confirms_level': True, 'entry_context_only': False,
            'known_index': index if known_index is None else known_index}


def false_breakout(indices):
    return {'index': indices[0], 'indices': indices, 'kind': 'L', 'atr': 1.0,
            'role': 'false_breakout_two_bar' if len(indices) == 2 else 'false_breakout',
            'confirms_level': False, 'entry_context_only': True,
            'known_index': indices[-1]}


class OrdinaryDepartureQualityTests(unittest.TestCase):
    def setUp(self):
        self.params = DiscoveryParams(working_reaction_bars=5, working_min_reaction_atr=2.0,
                                      working_min_reaction_efficiency=0.55)

    def test_fast_directional_departure_is_confirmed_on_actual_close(self):
        bars = candles([100.2, 101.0, 102.4])
        self.assertEqual(ordinary_departures(bars[:2], 100, [touch()], self.params), [])
        episodes = ordinary_departures(bars, 100, [touch()], self.params)
        self.assertEqual(len(episodes), 1)
        evidence = episodes[0]['best']
        self.assertEqual(evidence['known_index'], 2)
        self.assertAlmostEqual(evidence['distance_atr'], 2.4)
        self.assertAlmostEqual(evidence['efficiency'], 1.0)

    def test_slow_drift_outside_reaction_window_cannot_earn_fast_reaction(self):
        bars = candles([100.2, 100.6, 101.0, 101.4, 101.8, 102.2])
        params = DiscoveryParams(working_reaction_bars=3)
        self.assertEqual(ordinary_departures(bars, 100, [touch()], params), [])

    def test_choppy_path_does_not_equal_a_clean_departure_of_same_net_size(self):
        choppy = candles([100.2, 101.8, 100.3, 101.9, 100.4, 102.1])
        clean = candles([100.2, 100.6, 101.0, 101.4, 101.8, 102.1])
        self.assertEqual(ordinary_departures(choppy, 100, [touch()], self.params), [])
        self.assertEqual(len(ordinary_departures(clean, 100, [touch()], self.params)), 1)

    def test_one_bar_false_breakout_cannot_supply_reaction_or_a_contact(self):
        bars = candles([100.2, 110.0, 101.0])
        bars[1] = replace(bars[1], low=99.0)
        breakout = false_breakout([1])
        self.assertEqual(ordinary_departures(bars, 100, [touch(), breakout], self.params), [])
        self.assertEqual(ordinary_departures(bars, 100, [breakout], self.params), [])
        self.assertEqual(len(ordinary_departures(bars, 100, [touch()], self.params)), 1)

    def test_both_bars_of_two_bar_breakout_are_excluded_including_large_return(self):
        bars = candles([100.2, 99.0, 110.0, 101.0])
        breakout = false_breakout([1, 2])
        self.assertEqual(ordinary_departures(bars, 100, [touch(), breakout], self.params), [])
        self.assertEqual(ordinary_departures(bars, 100, [breakout], self.params), [])

    def test_confirmed_departure_is_not_amplified_by_later_false_breakout(self):
        bars = candles([100.2, 102.5, 150.0])
        bars[2] = replace(bars[2], low=99.0)
        episodes = ordinary_departures(bars, 100, [touch(), false_breakout([2])], self.params)
        self.assertEqual(len(episodes), 1)
        self.assertAlmostEqual(episodes[0]['best']['quality'], 2.5)
        self.assertEqual(episodes[0]['best']['known_index'], 1)

    def test_close_across_level_without_a_return_pattern_ends_departure(self):
        self.assertEqual(ordinary_departures(candles([100.2, 99, 98, 105]), 100,
                                             [touch()], self.params), [])

    def test_contact_cannot_earn_reaction_before_its_own_confirmation(self):
        bars = candles([100.2, 103, 101])
        self.assertEqual(ordinary_departures(bars, 100, [touch(known_index=2)], self.params), [])

    def test_outlier_size_is_capped_so_one_departure_cannot_dominate_ranking(self):
        qualities = []
        for final in (110, 1000):
            episodes = ordinary_departures(candles([100.2, final]), 100, [touch()], self.params)
            self.assertEqual(len(episodes), 1)
            qualities.append(episodes[0]['best']['quality'])
        self.assertEqual(qualities, [4.0, 4.0])

    def test_support_and_resistance_departures_are_symmetric(self):
        upward = candles([100.2, 101, 102.4])
        downward = candles([99.8, 99, 97.6])
        support = ordinary_departures(upward, 100, [touch(kind='L')], self.params)
        resistance = ordinary_departures(downward, 100, [touch(kind='H')], self.params)
        self.assertAlmostEqual(support[0]['best']['quality'], resistance[0]['best']['quality'])
        self.assertEqual(support[0]['best']['known_index'], resistance[0]['best']['known_index'])


class IndependentReactionAfterEntryTests(unittest.TestCase):
    def test_future_confirmation_cannot_retroactively_bridge_repeated_entries(self):
        bars = candles([100.2, 100.3, 110.0, 120.0, 120.3, 123.0])
        events = [touch(0), touch(1, known_index=4),
                  false_breakout([2]), false_breakout([3])]
        contexts = {(i, 'L'): {'basis': 'clean_hold', 'known_index': 4} for i in (0, 1)}
        # The second contact is known only after both sweeps. Its future
        # confirmation must not exempt those sweeps from the repeated-LP gate.
        self.assertEqual(ordinary_departures(bars, 100, events, DiscoveryParams(),
                                             contexts=contexts), [])

    def test_established_clean_pair_survives_repeated_entries_only_on_independent_reaction(self):
        bars = candles([100.2, 100.3, 110.0, 120.0, 120.3, 123.0])
        for i in (0, 1):
            bars[i] = replace(bars[i], low=100.0)
        for i in (2, 3):
            bars[i] = replace(bars[i], low=99.0)
        events = [touch(0), touch(1), false_breakout([2]), false_breakout([3])]
        params = DiscoveryParams()
        contexts = stopping_contexts(bars, 100, events, params)
        self.assertEqual(contexts[0, 'L']['basis'], 'clean_hold')
        self.assertEqual(contexts[0, 'L']['known_index'], 1)
        self.assertEqual(ordinary_departures(bars[:5], 100, events, params, contexts=contexts), [])
        result = ordinary_departures(bars, 100, events, params, contexts=contexts)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['best']['known_index'], 5)
        self.assertAlmostEqual(result[0]['best']['distance_atr'], 3.3)
        self.assertAlmostEqual(result[0]['best']['entry_gain_excluded_atr'], 19.7)
        # Without the preceding held pair the same repeated entries cannot
        # bridge an isolated contact to the later move.
        isolated = {(0, 'L'): {'basis': 'rejection_tail', 'known_index': 0}}
        self.assertEqual(ordinary_departures(bars, 100, [events[0], *events[2:]],
                                             params, contexts=isolated), [])

    def test_large_entry_cannot_dilute_independent_reaction_on_either_side_or_scale(self):
        original = candles([100.2, 103.0, 103.4, 105.4])
        original[1] = replace(original[1], low=99.0)
        for scale in (0.001, 1.0, 1000.0):
            for mirror in (False, True):
                with self.subTest(scale=scale, mirror=mirror):
                    transformed = []
                    for b in original:
                        o, h, l, c = b.open, b.high, b.low, b.close
                        if mirror:
                            o, h, l, c = 200-o, 200-l, 200-h, 200-c
                        transformed.append(replace(b, open=o*scale, high=h*scale,
                                                   low=l*scale, close=c*scale))
                    kind = 'H' if mirror else 'L'
                    events = [{**touch(kind=kind), 'atr': scale},
                              {**false_breakout([1]), 'kind': kind, 'atr': scale}]
                    params = DiscoveryParams()
                    self.assertEqual(ordinary_departures(transformed[:3], 100*scale,
                                                         events, params), [])
                    result = ordinary_departures(transformed, 100*scale, events, params)
                    self.assertEqual(len(result), 1)
                    evidence = result[0]['best']
                    self.assertEqual(evidence['known_index'], 3)
                    self.assertAlmostEqual(evidence['distance_atr'], 2.6)
                    self.assertAlmostEqual(evidence['entry_gain_excluded_atr'], 2.8)
                    self.assertAlmostEqual(evidence['efficiency'], 1.0)
                    self.assertAlmostEqual(evidence['quality'], 2.6)

    def test_entry_jump_without_sufficient_independent_reaction_still_fails(self):
        bars = candles([100.2, 110.0, 110.3])
        bars[1] = replace(bars[1], low=99.0)
        events = [touch(), false_breakout([1])]
        self.assertEqual(ordinary_departures(bars, 100, events, DiscoveryParams()), [])

    def test_outside_step_of_two_bar_entry_still_penalizes_efficiency(self):
        bars = candles([100.2, 99.0, 110.0, 113.4])
        events = [touch(), false_breakout([1, 2])]
        # The ordinary remainder is 2.4 ATR, but its efficiency is only 0.5:
        # the 1.2 ATR move below the old contact remains in the path.
        self.assertEqual(ordinary_departures(bars, 100, events, DiscoveryParams()), [])
        permissive = DiscoveryParams(working_min_reaction_efficiency=0.49)
        result = ordinary_departures(bars, 100, events, permissive)
        self.assertAlmostEqual(result[0]['best']['distance_atr'], 2.4)
        self.assertAlmostEqual(result[0]['best']['efficiency'], 0.5)

    def test_independent_reaction_waits_for_contact_and_inflection_confirmation(self):
        bars = candles([100.2, 103.0, 103.4, 105.4, 105.6])
        bars[1] = replace(bars[1], low=99.0)
        for contact_known, inflection_known in ((4, 3), (3, 4)):
            with self.subTest(contact_known=contact_known, inflection_known=inflection_known):
                events = [touch(known_index=contact_known), false_breakout([1])]
                contexts = {(0, 'L'): {'basis': 'confirmed_inflection',
                                       'known_index': inflection_known}}
                self.assertEqual(ordinary_departures(bars[:4], 100, events, DiscoveryParams(),
                                                     contexts=contexts), [])
                result = ordinary_departures(bars, 100, events, DiscoveryParams(),
                                             contexts=contexts)
                self.assertEqual(result[0]['best']['known_index'], 4)
                self.assertAlmostEqual(result[0]['best']['distance_atr'], 2.8)

    def test_august_inflection_survives_on_ordinary_reaction_after_entry_bar(self):
        root = Path(__file__).resolve().parents[1]
        snapshot = json.loads((root / '_knowledge_base/manual_reviews/'
                               'working_31_review_20260925/chart_snapshot.json').read_text(encoding='utf-8'))
        bars = [Bar(row['open_time_ms'], row['open'], row['high'], row['low'],
                    row['close'], row['volume']) for row in snapshot['bars']]
        levels = discover_levels(bars, DiscoveryParams(working_selection=False,
                                 nearest_window_atr=float('inf')), interval='1d',
                                 as_of_ms=snapshot['analysis_as_of_ms'])
        level = next(v for v in levels if abs(v.price-123742.2) < 1e-8)
        self.assertEqual(level.inflection_check['status'], 'confirmed')
        selected = select_working_levels(bars, [level], DiscoveryParams())
        self.assertEqual(selected, [level])
        evidence = level.selection['reaction_episodes'][0]['best']
        known = bars[evidence['known_index']].open_time
        self.assertGreaterEqual(known, 1755561600000)  # 19 August 2025 UTC
        self.assertGreater(evidence['distance_atr'], 2.0)
        self.assertGreater(evidence['entry_gain_excluded_atr'], 1.8)
        self.assertNotIn('mirror_level', level.basis_tags)


class EntryPatternAndChopTests(unittest.TestCase):
    def test_exact_two_bar_false_breakout_does_not_invalidate_working_level(self):
        profile = {'currently_chopped': True, 'events': [false_breakout([10, 11])],
                   'chopping_runs': [{'indices': [10, 11]}]}
        self.assertFalse(unresolved_chopping(profile, 0))
        self.assertFalse(profile['events'][0]['confirms_level'])

    def test_two_bar_pattern_inside_longer_crossing_run_does_not_exempt_run(self):
        profile = {'currently_chopped': True, 'events': [false_breakout([10, 11])],
                   'chopping_runs': [{'indices': [10, 11, 12]}]}
        self.assertTrue(unresolved_chopping(profile, 0))

    def test_separate_recent_chop_is_not_cleared_by_two_bar_entry_pattern(self):
        profile = {'currently_chopped': True, 'events': [false_breakout([10, 11])],
                   'chopping_runs': [{'indices': [2, 3]}, {'indices': [10, 11]}]}
        self.assertTrue(unresolved_chopping(profile, 0))
        self.assertFalse(unresolved_chopping(profile, 4))
        profile['chopping_runs'].append({'indices': [13, 14]})
        self.assertTrue(unresolved_chopping(profile, 4))

    def test_recovery_needs_a_later_ordinary_contact_with_confirmed_reaction(self):
        profile = {'currently_chopped': True, 'events': [false_breakout([13, 14])],
                   'chopping_runs': [{'indices': [10, 11]}]}
        self.assertTrue(unresolved_chopping(profile, 0))
        profile['events'].append({**touch(9), 'reaction_confirmed_index': 12})
        self.assertTrue(unresolved_chopping(profile, 0))
        profile['events'].append({**touch(16), 'reaction_confirmed_index': None})
        self.assertTrue(unresolved_chopping(profile, 0))
        profile['events'][-1]['reaction_confirmed_index'] = 17
        self.assertFalse(unresolved_chopping(profile, 0))


class ChannelRankingIndependenceTests(unittest.TestCase):
    def test_nested_channel_penalties_use_reaction_times_and_unmodified_strength(self):
        # Reaction contacts matter even when the nominated BSU is later than
        # the channel. Penalized boundary scores cannot make results depend
        # on candidate iteration order.
        specs = [(100, 1, 4), (200, 2, 4), (120, 3, 3), (180, 4, 3), (150, 50, 2)]
        levels = []
        for price, index, precise in specs:
            profile = {'strong': True, 'events': [touch(0), touch(1), touch(2)],
                       'precise_contact_count': precise, 'currently_chopped': False,
                       'channels': [], 'lifetime_chop_rate_per_100_bars': 0.0}
            levels.append(Level(price, index, str(index), 'support', structure=profile))
        levels[0].structure['channels'] = [dict(lower=100, upper=200, start_index=0, end_index=10)]
        levels[2].structure['channels'] = [dict(lower=120, upper=180, start_index=0, end_index=100)]
        episodes = [
            {'index': 0, 'end_index': 2, 'best': {'index': 0, 'known_index': 2, 'quality': 3.5}},
            {'index': 4, 'end_index': 6, 'best': {'index': 4, 'known_index': 6, 'quality': 3.5}},
        ]
        params = DiscoveryParams(min_level_distance_fraction=0)
        expected_scores = None
        bars = candles([150] * 101)
        with patch('level_selection.ordinary_departures', return_value=episodes), \
             patch('level_selection.atr_series', return_value=[1.0] * len(bars)), \
             patch('level_selection.origin_evidence', return_value={'valid': True, 'strength_bonus': 0.0}):
            for permutation in itertools.permutations(levels):
                audit = []
                selected = select_working_levels(bars, list(permutation), params, audit)
                scores = {row['price']: row['strength_score'] for row in audit}
                middle = next(row for row in audit if row['price'] == 150)
                self.assertEqual(len(selected), 5)
                self.assertEqual(middle.get('channel_context'),
                                 [dict(lower=100, upper=200, start_index=0, end_index=10),
                                  dict(lower=120, upper=180, start_index=0, end_index=100)])
                self.assertAlmostEqual(scores[150], (7.0 + 0.8 + 0.7) * 0.75)
                if expected_scores is None:
                    expected_scores = scores
                self.assertEqual(scores, expected_scores)


if __name__ == '__main__':
    unittest.main()
