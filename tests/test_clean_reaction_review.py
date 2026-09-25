"""Local stopping evidence must precede credit for a later price departure."""

from dataclasses import replace
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'knowledge_bot'))

from level_discovery import Bar, DiscoveryParams, Level
from level_selection import (interaction_chopping, ordinary_departures,
                             select_working_levels, stopping_contexts)


def bars_from_rows(rows):
    return [Bar(i * 86400000, *row, 1.0) for i, row in enumerate(rows)]


def contact(index, kind='L', atr=1.0, known=None):
    return {'index': index, 'indices': [index], 'kind': kind, 'atr': atr,
            'role': 'touch', 'confirms_level': True, 'entry_context_only': False,
            'known_index': index if known is None else known}


def entry(indices, kind='L'):
    return {'index': indices[0], 'indices': indices, 'kind': kind, 'atr': 1.0,
            'role': 'false_breakout_two_bar' if len(indices) == 2 else 'false_breakout',
            'confirms_level': False, 'entry_context_only': True,
            'known_index': indices[-1]}


class LocalStoppingEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.params = DiscoveryParams()

    def test_downward_continuation_near_open_is_not_a_resistance_stop(self):
        bars = bars_from_rows([(105, 105.1, 102.5, 103),
                              (103, 103.1, 100.2, 100.3),
                              (99.98, 100, 97, 97.2),
                              (97.2, 97.3, 94, 94.2)])
        events = [contact(2, 'H')]
        contexts = stopping_contexts(bars, 100, events, self.params)
        self.assertIsNone(contexts[2, 'H']['basis'])
        self.assertEqual(ordinary_departures(bars, 100, events, self.params,
                                             contexts=contexts), [])

    def test_visible_tail_rejection_supplies_stop_even_without_three_prior_bars(self):
        bars = bars_from_rows([(101, 101.1, 100, 100.7),
                              (100.7, 102.5, 100.6, 102.4)])
        events = [contact(0)]
        contexts = stopping_contexts(bars, 100, events, self.params)
        self.assertEqual(contexts[0, 'L']['basis'], 'rejection_tail')
        self.assertEqual(len(ordinary_departures(bars, 100, events, self.params,
                                                 contexts=contexts)), 1)

    def test_incoming_move_can_stop_on_a_small_tail_without_being_continuation(self):
        bars = bars_from_rows([(103, 103.1, 102.9, 103),
                              (103, 103.1, 100.4, 100.5),
                              (100.05, 102.4, 100, 102.3)])
        contexts = stopping_contexts(bars, 100, [contact(2)], self.params)
        self.assertEqual(contexts[2, 'L']['basis'], 'incoming_stop')

    def test_clean_adjacent_holds_do_not_require_big_tails_or_immediate_departure(self):
        bars = bars_from_rows([(100.05, 100.3, 100, 100.2),
                              (100.05, 100.3, 100, 100.2),
                              (100.2, 100.4, 100.1, 100.3),
                              (100.3, 100.5, 100.2, 100.4),
                              (100.4, 101.3, 100.3, 101.2),
                              (101.2, 102.5, 101.1, 102.4)])
        events = [contact(0), contact(1)]
        contexts = stopping_contexts(bars, 100, events, self.params)
        self.assertEqual(contexts[0, 'L']['basis'], 'clean_hold')
        self.assertEqual(contexts[0, 'L']['known_index'], 1)
        self.assertTrue(ordinary_departures(bars, 100, events, self.params,
                                            contexts=contexts))

    def test_false_breakout_between_contacts_is_not_a_clean_held_series(self):
        bars = bars_from_rows([(100.05, 100.3, 100, 100.2),
                              (100.2, 100.4, 99.5, 100.1),
                              (100.05, 100.3, 100, 100.2)])
        events = [contact(0), entry([1]), contact(2)]
        contexts = stopping_contexts(bars, 100, events, self.params)
        self.assertFalse(any(c['basis'] == 'clean_hold' for c in contexts.values()))

    def test_clean_hold_waits_until_supporting_contact_is_actually_known(self):
        # A near touch at index 1 becomes admissible only at index 4. It must
        # not retroactively establish an adjacent series at index 1.
        bars = bars_from_rows([(100.05, 100.3, 100, 100.2),
                              (100.05, 100.3, 100, 100.2),
                              (100.2, 103.1, 100.1, 103),
                              (103, 103.1, 100.1, 100.2),
                              (100.2, 100.4, 100.1, 100.3)])
        events = [contact(0), contact(1, known=4)]
        contexts = stopping_contexts(bars, 100, events, self.params)
        self.assertGreaterEqual(contexts[0, 'L']['known_index'], 4)
        self.assertEqual(ordinary_departures(bars, 100, events, self.params,
                                             contexts=contexts), [])

    def test_stop_and_departure_are_symmetric_across_sides_and_price_scales(self):
        original = bars_from_rows([(101, 101.1, 100, 100.7),
                                   (100.7, 102.5, 100.6, 102.4)])
        qualities = []
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
                    events = [contact(0, kind, scale)]
                    contexts = stopping_contexts(transformed, 100*scale, events, self.params)
                    episodes = ordinary_departures(transformed, 100*scale, events,
                                                     self.params, contexts=contexts)
                    self.assertEqual(contexts[0, kind]['basis'], 'rejection_tail')
                    self.assertEqual(len(episodes), 1)
                    qualities.append(episodes[0]['best']['quality'])
        for quality in qualities:
            self.assertAlmostEqual(quality, qualities[0])


class EntryContributionTests(unittest.TestCase):
    def test_entry_jump_is_not_credited_through_later_ordinary_close(self):
        bars = bars_from_rows([(100.3, 100.4, 100, 100.3),
                              (100.3, 110.1, 99, 110),
                              (110, 110.4, 109.9, 110.3)])
        events = [contact(0), entry([1])]
        self.assertEqual(ordinary_departures(bars, 100, events, DiscoveryParams()), [])

    def test_repeated_entry_patterns_cannot_bridge_to_an_unrelated_later_move(self):
        bars = bars_from_rows([(100.3, 100.4, 100, 100.3),
                              (100.3, 100.5, 99, 100.4),
                              (100.4, 100.6, 99, 100.5),
                              (100.5, 110, 100.4, 109.9)])
        events = [contact(0), entry([1]), entry([2])]
        self.assertEqual(ordinary_departures(bars, 100, events, DiscoveryParams()), [])

    def test_single_entry_can_preserve_old_contact_without_supplying_its_reaction(self):
        bars = bars_from_rows([(100.3, 100.4, 100, 100.3),
                              (100.3, 100.5, 99.5, 100.4),
                              (100.4, 103, 100.3, 102.8)])
        events = [contact(0), entry([1])]
        contexts = stopping_contexts(bars, 100, events, DiscoveryParams())
        result = ordinary_departures(bars, 100, events, DiscoveryParams(), contexts=contexts)
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0]['best']['entry_gain_excluded_atr'], 0.1)
        self.assertAlmostEqual(result[0]['best']['distance_atr'], 2.7)
        self.assertFalse(events[1]['confirms_level'])


class FormationAndHistoricalChoppingTests(unittest.TestCase):
    def test_distant_future_candles_do_not_dilute_past_interaction_damage(self):
        rows = [(101, 101.1, 98.9, 99), (99, 101.1, 98.9, 101),
                (101, 101.1, 98.9, 99), (100.05, 100.3, 100, 100.2)]
        bars = bars_from_rows(rows)
        events = [entry([0, 1]), contact(3)]
        initial = interaction_chopping(bars, 100, events, [1]*len(bars), DiscoveryParams())
        longer = bars_from_rows(rows + [(110, 110.2, 109.8, 110.1)] * 100)
        extended = interaction_chopping(longer, 100, events, [1]*len(longer), DiscoveryParams())
        self.assertEqual(initial, extended)
        self.assertGreater(initial['fraction'], 0)

    def test_old_local_chop_disables_an_isolated_basis_even_after_many_future_bars(self):
        rows = [(101, 101.1, 98.9, 99), (99, 101.1, 98.9, 101),
                (101, 101.1, 98.9, 99), (100.8, 101, 100, 100.7)]
        bars = bars_from_rows(rows + [(110, 110.2, 109.8, 110.1)] * 100)
        events = [contact(3)]
        params = DiscoveryParams()
        chop = interaction_chopping(bars, 100, events, [1]*len(bars), params)
        contexts = stopping_contexts(bars, 100, events, params, chop['runs'])
        self.assertIsNone(contexts[3, 'L']['basis'])
        self.assertTrue(contexts[3, 'L']['formation_chop'])

    def test_new_clean_series_can_reestablish_a_basis_after_chop(self):
        bars = bars_from_rows([(101, 101.1, 98.9, 99),
                              (99, 101.1, 98.9, 101),
                              (101, 101.1, 98.9, 99),
                              (100.05, 100.3, 100, 100.2),
                              (100.05, 100.3, 100, 100.2),
                              (100.2, 102.5, 100.1, 102.4)])
        events = [contact(3), contact(4)]
        params = DiscoveryParams()
        chop = interaction_chopping(bars, 100, events, [1]*len(bars), params)
        contexts = stopping_contexts(bars, 100, events, params, chop['runs'])
        self.assertEqual(contexts[3, 'L']['basis'], 'clean_hold')
        self.assertEqual(contexts[3, 'L']['known_index'], 4)
        self.assertTrue(ordinary_departures(bars, 100, events, params, contexts=contexts))

    def test_isolated_two_bar_entry_is_not_hard_chop_but_three_crossings_are(self):
        rows = [(101, 101.1, 98.9, 99), (99, 101.1, 98.9, 101)]
        events = [entry([0, 1])]
        initial = interaction_chopping(bars_from_rows(rows), 100, events, [1, 1],
                                       DiscoveryParams())
        self.assertEqual(initial['runs'], [])
        self.assertEqual(initial['crossing_count'], 2)
        rows.append((101, 101.1, 98.9, 99))
        longer = interaction_chopping(bars_from_rows(rows), 100, events, [1, 1, 1],
                                      DiscoveryParams())
        self.assertEqual(longer['runs'], [{'indices': [0, 1, 2]}])

    def test_saved_june_support_survives_one_july_entry_without_price_override(self):
        snapshot = json.loads((ROOT / '_knowledge_base/manual_reviews/working_31_review_20260925/'
                               'chart_snapshot.json').read_text(encoding='utf-8'))
        report = next(lv for lv in snapshot['inflection_levels'] if lv['price'] == 58042.6)
        bars = [Bar(row['open_time_ms'], row['open'], row['high'], row['low'],
                    row['close'], row['volume']) for row in snapshot['bars']]
        level = Level(report['price'], report['bsu']['index'], report['bsu']['time'],
                      report['side'], structure=report['structure'],
                      inflection_check=report['inflection_check'])
        selected = select_working_levels(bars, [level], DiscoveryParams())
        self.assertEqual(selected, [level])
        entry_events = [e for e in report['structure']['events'] if e['entry_context_only']]
        self.assertEqual(len(entry_events), 1)
        self.assertFalse(entry_events[0]['confirms_level'])
        episodes = level.selection['reaction_episodes']
        self.assertTrue(episodes)
        self.assertTrue(any(ep['best']['entry_gain_excluded_atr'] > 0 for ep in episodes))


if __name__ == '__main__':
    unittest.main()
