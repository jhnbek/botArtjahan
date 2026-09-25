"""Body size remains evidence below the paranormal threshold and above 2.5 ATR."""

from dataclasses import replace
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'knowledge_bot'))

from detector_prototype import is_paranormal_body
from level_discovery import Bar, DiscoveryParams, Level
from level_evidence_strength import (body_strength_evidence, origin_evidence,
                                     progressive_body_strength)
from level_selection import ordinary_departures


def bars_from_rows(rows):
    return [Bar(i * 86400000, *row, 1.0) for i, row in enumerate(rows)]


def contact(index, price=100.0, atr=1.0, kind='L', known=None):
    return {'index': index, 'indices': [index], 'price': price, 'kind': kind,
            'atr': atr, 'role': 'touch', 'confirms_level': True,
            'entry_context_only': False, 'known_index': index if known is None else known}


def context(index, arrival=0.0, known=None):
    return {'basis': 'clean_hold', 'arrival_atr': arrival,
            'known_index': index if known is None else known, 'formation_chop': []}


def assess(bars, events, contexts, price=100.0):
    params = DiscoveryParams()
    profile = {'events': events}
    level = Level(price, 0, '0', 'support', structure=profile)
    episodes = ordinary_departures(bars, price, events, params, contexts=contexts)
    return origin_evidence(bars, level, profile, contexts, episodes, params)


class ProgressiveBodyStrengthTests(unittest.TestCase):
    def test_strength_grows_below_threshold_and_above_former_cap(self):
        ratios = [0.0, 0.1, 0.5, 1.0, 1.35, 1.6, 2.5, 3.0, 4.0, 10.0]
        weights = [progressive_body_strength(ratio) for ratio in ratios]
        self.assertEqual(weights[0], 0)
        self.assertAlmostEqual(progressive_body_strength(1.0), 1.0)
        self.assertTrue(all(a < b for a, b in zip(weights, weights[1:])))
        self.assertTrue(all(weight < 2.5 for weight in weights))

    def test_continuous_strength_does_not_change_paranormal_boundary(self):
        self.assertFalse(is_paranormal_body(100.0, 101.35, 1.0))
        self.assertFalse(is_paranormal_body(100.0, 101.59999, 1.0))
        self.assertTrue(is_paranormal_body(100.0, 101.6, 1.0))
        below = progressive_body_strength(1.6 - 1e-8)
        at = progressive_body_strength(1.6)
        above = progressive_body_strength(1.6 + 1e-8)
        self.assertLess(below, at)
        self.assertLess(at, above)
        self.assertLess(above - below, 1e-7)

    def test_invalid_atr_ratios_cannot_inflate_strength(self):
        for ratio in (-1.0, math.inf, -math.inf, math.nan):
            with self.subTest(ratio=ratio), self.assertRaises(ValueError):
                progressive_body_strength(ratio)

    def test_body_changes_strength_even_when_arrival_is_larger(self):
        results = []
        for body in (0.2, 1.0, 1.35, 1.6, 2.4):
            # Identical tails, closes and subsequent reaction: only the body changes.
            bars = bars_from_rows([(102.5-body, 102.6, 100.0, 102.5),
                                   (102.5, 103.1, 102.4, 103.0)])
            result = assess(bars, [contact(0)], {(0, 'L'): context(0, arrival=4.0)})
            self.assertTrue(result['valid'])
            self.assertEqual(result['stopped_movement_score'], 2.5)
            self.assertAlmostEqual(result['body_strength_evidence'][0]['body_atr'], body)
            results.append(result['strength_bonus'])
        self.assertTrue(all(a < b for a, b in zip(results, results[1:])))

    def test_large_bodies_do_not_flatten_at_old_cap(self):
        results = []
        for body in (3.0, 4.0):
            bars = bars_from_rows([(106.0-body, 106.1, 100.0, 106.0)])
            result = assess(bars, [contact(0)], {(0, 'L'): context(0)})
            self.assertTrue(result['valid'])
            results.append(result['strength_bonus'])
        self.assertGreater(results[1], results[0])

    def test_dense_member_earns_body_weight_without_being_best_departure(self):
        bars = bars_from_rows([(100.2, 100.5, 100.0, 100.4),
                               (100.2, 101.6, 100.005, 101.55),
                               (101.55, 101.7, 101.4, 101.5),
                               (101.5, 101.7, 101.4, 101.6)])
        events = [contact(0), contact(1, price=100.005, known=3)]
        contexts = {(0, 'L'): context(0, known=3), (1, 'L'): context(1, known=3)}
        result = assess(bars, events, contexts)
        self.assertTrue(result['valid'])
        self.assertEqual(result['stopped_movements'], [])
        member = next(e for e in result['body_strength_evidence'] if e['index'] == 1)
        self.assertAlmostEqual(member['body_atr'], 1.35)
        self.assertGreater(member['weight'], progressive_body_strength(1.0))
        self.assertEqual(member['known_index'], 3)
        self.assertIn('dense_group', member['sources'])

    def test_group_weight_waits_for_confirming_bar_to_exist(self):
        bars = bars_from_rows([(100.2, 101.6, 100.0, 101.55),
                               (101.55, 101.7, 101.4, 101.5)])
        events = [contact(0)]
        contexts = {(0, 'L'): context(0)}
        self.assertEqual(body_strength_evidence(bars, events, events, contexts,
                                                [(0, 'dense_group', 2)]), [])
        confirmed = bars + bars_from_rows([(101.5, 101.7, 101.4, 101.6)])
        evidence = body_strength_evidence(confirmed, events, events, contexts,
                                          [(0, 'dense_group', 2)])
        self.assertEqual([e['index'] for e in evidence], [0])
        self.assertEqual(evidence[0]['known_index'], 2)

    def test_small_clean_pair_is_still_valid(self):
        bars = bars_from_rows([(100.02, 100.08, 100.0, 100.05),
                               (100.05, 100.09, 100.005, 100.07)])
        events = [contact(0), contact(1, price=100.005)]
        result = assess(bars, events, {(0, 'L'): context(0, known=1),
                                      (1, 'L'): context(1)})
        self.assertTrue(result['valid'])
        self.assertEqual(result['stopped_movements'], [])
        self.assertEqual(len(result['body_strength_evidence']), 2)
        self.assertGreater(result['body_strength_score'], 0)
        self.assertGreater(result['density_score'], result['body_strength_score'])

    def test_false_breakout_bar_cannot_borrow_an_ordinary_source(self):
        bars = bars_from_rows([(100.1, 100.2, 100.0, 100.11),
                               (100.11, 120.1, 99.0, 120.0),
                               (120.0, 120.4, 119.9, 120.3)])
        ordinary = [contact(0), contact(1), contact(2)]
        # Even a conflicting ordinary record for either member of an LP pair
        # must not credit the entry candles as strong level confirmations.
        lp = {**contact(1), 'indices': [1, 2], 'role': 'false_breakout_two_bar',
              'entry_context_only': True, 'confirms_level': False}
        evidence = body_strength_evidence(
            bars, ordinary, ordinary + [lp],
            {(i, 'L'): context(i) for i in range(3)},
            [(i, 'dense_group', 2) for i in range(3)])
        self.assertEqual([e['index'] for e in evidence], [0])

    def test_duplicate_sources_are_one_bar_and_only_two_largest_count(self):
        bars = bars_from_rows([(100, 104, 100, 100+body) for body in (0.2, 1.35, 3)])
        events = [contact(i) for i in range(3)]
        evidence = body_strength_evidence(
            bars, events, events, {(i, 'L'): context(i) for i in range(3)},
            [(0, 'price_origin', 1), (0, 'ordinary_reaction', 2),
             (0, 'dense_group', 2), (1, 'dense_group', 2), (2, 'dense_group', 2)])
        self.assertEqual(len(evidence), 3)
        self.assertEqual([e['index'] for e in evidence if e['contributes_to_score']], [1, 2])
        self.assertEqual(len(evidence[0]['sources']), 3)
        self.assertEqual(evidence[0]['known_index'], 1)

    def test_body_bonus_is_symmetric_and_price_scale_independent(self):
        original = bars_from_rows([(101.15, 102.6, 100.0, 102.5),
                                   (102.5, 103.1, 102.4, 103.0)])
        expected = None
        for scale in (0.001, 1.0, 1000.0):
            for mirror in (False, True):
                with self.subTest(scale=scale, mirror=mirror):
                    transformed = []
                    for bar in original:
                        o, h, l, c = bar.open, bar.high, bar.low, bar.close
                        if mirror:
                            o, h, l, c = 200-o, 200-l, 200-h, 200-c
                        transformed.append(replace(bar, open=o*scale, high=h*scale,
                                                   low=l*scale, close=c*scale))
                    kind = 'H' if mirror else 'L'
                    result = assess(transformed, [contact(0, 100*scale, scale, kind)],
                                    {(0, kind): context(0, arrival=4.0)}, price=100*scale)
                    values = (result['body_strength_score'], result['strength_bonus'])
                    if expected is None:
                        expected = values
                    for actual, value in zip(values, expected):
                        self.assertAlmostEqual(actual, value)


if __name__ == '__main__':
    unittest.main()
