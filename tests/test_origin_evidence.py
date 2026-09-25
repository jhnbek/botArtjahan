"""Price origins need their own evidence; compact groups cannot borrow outliers."""

from dataclasses import replace
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'knowledge_bot'))

from level_discovery import Bar, DiscoveryParams, Level
from level_evidence_strength import dense_stopping_group, origin_evidence
from level_selection import ordinary_departures, stopping_contexts


def bars_from_rows(rows):
    return [Bar(i * 86400000, *row, 1.0) for i, row in enumerate(rows)]


def contact(index, price=100.0, kind='L', atr=1.0, known=None):
    return {'index': index, 'indices': [index], 'price': price, 'kind': kind,
            'atr': atr, 'role': 'touch', 'confirms_level': True,
            'entry_context_only': False, 'known_index': index if known is None else known}


def stopping(index, basis='clean_hold', known=None, arrival=0.0):
    return {'basis': basis, 'known_index': index if known is None else known,
            'arrival_atr': arrival, 'formation_chop': []}


def evidence_for(bars, events, contexts, bsu=0, price=100.0, profile_extra=None, chop_runs=()):
    profile = {'events': events, **(profile_extra or {})}
    level = Level(price, bsu, str(bsu), 'support', structure=profile)
    params = DiscoveryParams()
    episodes = ordinary_departures(bars, price, events, params, contexts=contexts)
    return origin_evidence(bars, level, profile, contexts, episodes, params, chop_runs=chop_runs)


class DenseStoppingGroupTests(unittest.TestCase):
    def test_chained_nearby_prices_do_not_become_one_dense_group(self):
        events = [contact(i, p) for i, p in enumerate((100.0, 100.015, 100.030, 100.045))]
        contexts = {(i, 'L'): stopping(i) for i in range(4)}
        group = dense_stopping_group(events, contexts, 100.0)
        self.assertEqual(group['indices'], [0, 1])
        self.assertLessEqual(group['price_span'], group['tolerance'])
        self.assertAlmostEqual(group['density'], 2 / 1.75)

    def test_tight_group_far_from_quoted_price_cannot_validate_that_price(self):
        events = [contact(0, 100.10), contact(1, 100.11)]
        contexts = {(i, 'L'): stopping(i) for i in range(2)}
        self.assertIsNone(dense_stopping_group(events, contexts, 100.0))

    def test_incompatible_low_atr_contact_does_not_hide_a_valid_subset(self):
        events = [contact(0, 100.0), contact(1, 100.005, atr=0.1),
                  contact(2, 100.015)]
        contexts = {(i, 'L'): stopping(i) for i in range(3)}
        group = dense_stopping_group(events, contexts, 100.0)
        self.assertEqual(group['indices'], [0, 2])
        self.assertAlmostEqual(group['price_span'], 0.015)
        self.assertAlmostEqual(group['tolerance'], 0.02)

    def test_one_candle_cannot_count_as_two_independent_contacts(self):
        events = [contact(0, kind='L'), contact(0, kind='H')]
        contexts = {(0, kind): stopping(0) for kind in ('L', 'H')}
        self.assertIsNone(dense_stopping_group(events, contexts, 100.0))

    def test_group_is_known_only_when_its_slowest_contact_is_confirmed(self):
        events = [contact(0), contact(1, 100.005, known=4)]
        contexts = {(0, 'L'): stopping(0), (1, 'L'): stopping(1, known=4)}
        group = dense_stopping_group(events, contexts, 100.0)
        self.assertEqual(group['known_index'], 4)


class PriceOriginEvidenceTests(unittest.TestCase):
    def test_old_origin_cannot_borrow_new_clean_holds_after_chopping(self):
        bars = bars_from_rows([(100.02, 100.08, 100.0, 100.05),
                              (100.05, 100.1, 98.9, 99.0),
                              (99.0, 101.1, 98.9, 101.0),
                              (100.05, 100.09, 100.0, 100.07),
                              (100.05, 100.09, 100.0, 100.07)])
        events = [contact(i) for i in (0, 3, 4)]
        contexts = {(i, 'L'): stopping(i, known=4 if i > 0 else 0) for i in (0, 3, 4)}
        chop = [{'indices': [1, 2]}]
        self.assertIsNone(dense_stopping_group(events[:2], contexts, 100.0, chop_runs=chop))
        for bsu, valid in ((0, False), (3, True)):
            with self.subTest(bsu=bsu):
                # Even a stale cached clean_limit_group cannot join old and
                # newly established foundations across intervening chop.
                evidence = evidence_for(bars, events, contexts, bsu=bsu,
                    profile_extra={'clean_limit_group': {'indices': [0, 3, 4]}},
                    chop_runs=chop)
                self.assertEqual(evidence['valid'], valid)
                self.assertEqual(evidence['dense_group']['indices'], [3, 4])

    def test_small_clean_pair_can_supply_origin_without_large_bodies(self):
        bars = bars_from_rows([(100.02, 100.08, 100.0, 100.05),
                              (100.05, 100.09, 100.005, 100.07)])
        events = [contact(0), contact(1, 100.005)]
        contexts = stopping_contexts(bars, 100.0, events, DiscoveryParams())
        evidence = evidence_for(bars, events, contexts)
        self.assertTrue(evidence['valid'])
        self.assertEqual(evidence['own_reactions'], [])
        self.assertLess(evidence['bsu_body_atr'], 0.1)
        self.assertEqual(evidence['dense_group']['indices'], [0, 1])
        self.assertEqual(evidence['stopped_movement_score'], 0)

    def test_isolated_weak_origin_cannot_borrow_an_unrelated_later_reaction(self):
        bars = bars_from_rows([(100.1, 100.2, 100.0, 100.11),
                              (100.11, 100.2, 99.0, 99.1),
                              (99.1, 101.6, 99.0, 101.5),
                              (101.5, 102.6, 100.1, 102.5),
                              (102.5, 104.2, 102.4, 104.0)])
        events = [contact(0), contact(3, 100.1)]
        contexts = {(0, 'L'): stopping(0, basis=None),
                    (3, 'L'): stopping(3, basis='rejection_tail', arrival=2.0)}
        evidence = evidence_for(bars, events, contexts)
        self.assertFalse(evidence['valid'])
        self.assertEqual(evidence['own_reactions'], [])
        self.assertIsNone(evidence['dense_group'])
        self.assertEqual([m['index'] for m in evidence['stopped_movements']], [3])
        self.assertGreater(evidence['stopped_movement_score'], 0)

    def test_false_breakout_cannot_supply_origin_density_or_stopped_size(self):
        bars = bars_from_rows([(100.1, 100.2, 100.0, 100.11),
                              (100.11, 110.1, 99.0, 110.0),
                              (110.0, 110.4, 109.9, 110.3)])
        breakout = {**contact(1, 99.0), 'role': 'false_breakout',
                    'confirms_level': False, 'entry_context_only': True}
        events = [contact(0), breakout]
        contexts = {(0, 'L'): stopping(0, basis='rejection_tail')}
        evidence = evidence_for(bars, events, contexts)
        self.assertFalse(evidence['valid'])
        self.assertIsNone(evidence['dense_group'])
        self.assertEqual(evidence['own_reactions'], [])
        self.assertEqual(evidence['stopped_movements'], [])
        self.assertEqual(evidence['strength_bonus'], 0)
        rejected_origin = evidence_for(bars, events, contexts, bsu=1, price=99.0)
        self.assertFalse(rejected_origin['valid'])

    def test_strength_is_symmetric_and_invariant_to_price_scale(self):
        original = bars_from_rows([(101.0, 101.1, 100.0, 100.6),
                                   (100.6, 102.5, 100.005, 102.4),
                                   (102.4, 103.2, 102.3, 103.1)])
        expected = None
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
                    events = [contact(i, (200-p if mirror else p)*scale, kind, scale)
                              for i, p in enumerate((100.0, 100.005))]
                    params = DiscoveryParams()
                    contexts = stopping_contexts(transformed, 100*scale, events, params)
                    result = evidence_for(transformed, events, contexts, price=100*scale)
                    self.assertTrue(result['valid'])
                    self.assertTrue(result['stopped_movements'])
                    values = (result['bsu_body_atr'], result['density_score'],
                              result['stopped_movement_score'], result['strength_bonus'])
                    if expected is None:
                        expected = values
                    for actual, value in zip(values, expected):
                        self.assertAlmostEqual(actual, value)


if __name__ == '__main__':
    unittest.main()
