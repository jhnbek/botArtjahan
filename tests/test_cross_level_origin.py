"""A false breakout of a prior origin cannot vote for its own wick price."""
import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'knowledge_bot'))
from level_discovery import (Bar, DiscoveryParams, bar_time, daily_level_history,
                             discover_levels, historical_mirror_confirmation,
                             inflection_anchors, inflection_context,
                             structure_params)
from level_origin_context import pending_reversal_origins, prior_origin_false_breakouts
from level_structure import StructureParams, atr_series, level_events, level_profile, reaction_after

DAY = 86400000


def candle(index, opening, high, low, close):
    return Bar(index*DAY, opening, high, low, close, 1)


def origin(kind='L', **changes):
    return {'index': 0, 'time': '1970-01-01T00:00:00+00:00', 'kind': kind,
            'price': 100, 'atr': 10, 'known_index': 0, 'status': 'pending',
            'expires_index': 20, **changes}


class CrossLevelOriginTests(unittest.TestCase):
    def context(self, bars, origins):
        atrs = [10.0]*len(bars)
        p = StructureParams(atr_period=1)
        constraints, events = prior_origin_false_breakouts(bars, origins, p, atrs)
        return replace(p, automatic_origin_exclusions=constraints,
                       automatic_origin_events=tuple(events)), atrs, events

    def test_one_bar_symmetry_and_only_affected_wick_excluded(self):
        base = [candle(0, 110, 120, 100, 105), candle(1, 105, 115, 95, 110)]
        for kind in ('L', 'H'):
            with self.subTest(kind=kind):
                bars = base if kind == 'L' else [
                    candle(i, 200-b.open, 200-b.low, 200-b.high, 200-b.close)
                    for i, b in enumerate(base)]
                p, atrs, evidence = self.context(bars, [origin(kind)])
                swept_price = bars[1].low if kind == 'L' else bars[1].high
                own = [e for e in level_events(bars, swept_price, p, atrs)
                       if e['index'] == 1 and e['kind'] == kind]
                self.assertEqual(len(own), 1)
                self.assertEqual(own[0]['role'], 'false_breakout')
                self.assertFalse(own[0]['confirms_level'])
                self.assertTrue(own[0]['entry_context_only'])
                self.assertEqual(own[0]['reaction_atr'], 0)
                self.assertEqual(own[0]['protected_origin_price'], 100)
                self.assertEqual(own[0]['origin_status'], 'pending')
                opposite = 'H' if kind == 'L' else 'L'
                other_price = bars[1].high if opposite == 'H' else bars[1].low
                self.assertTrue(any(e['index'] == 1 and e['kind'] == opposite
                                    and e['confirms_level']
                                    for e in level_events(bars, other_price, p, atrs)))
                self.assertEqual(p.excluded_contacts, ())

    def test_two_bar_context_appears_only_after_return_close(self):
        bars = [candle(0, 110, 120, 100, 105), candle(1, 105, 107, 90, 95),
                candle(2, 95, 113, 94, 110)]
        _, _, unfinished = self.context(bars[:2], [origin()])
        self.assertEqual(unfinished, [])
        p, atrs, finished = self.context(bars, [origin()])
        self.assertEqual(finished[0]['indices'], [1, 2])
        self.assertEqual(finished[0]['known_index'], 2)
        self.assertEqual(finished[0]['role'], 'false_breakout_two_bar')
        own = [e for e in level_events(bars, 94, p, atrs)
               if e.get('context_source') == 'prior_origin']
        self.assertEqual(own[0]['indices'], [1, 2])
        self.assertFalse(any(e['index'] in (1, 2) and e['kind'] == 'L'
                             and e['confirms_level']
                             for e in level_events(bars, 94, p, atrs)))
        continuation = bars[:2]+[candle(2, 95, 99, 91, 94)]
        self.assertEqual(self.context(continuation, [origin()])[2], [])

    def test_luft_expiry_and_later_independent_contacts(self):
        bars = [candle(0, 110, 120, 100, 105), candle(1, 105, 115, 99.95, 110)]
        self.assertEqual(self.context(bars, [origin()])[2], [])
        self.assertEqual(self.context(bars, [origin(expires_index=0)])[2], [])
        bars[1] = candle(1, 105, 115, 95, 110)
        bars += [candle(2, 97, 101, 95, 99), candle(3, 99, 102, 95, 101)]
        p, atrs, _ = self.context(bars, [origin()])
        own = level_events(bars, 95, p, atrs)
        self.assertEqual([e['index'] for e in own if e['confirms_level'] and e['kind'] == 'L'], [2, 3])

    def test_pending_origin_uses_past_move_without_future_reversal(self):
        bars = [candle(0, 145, 150, 135, 140), candle(1, 140, 143, 125, 130),
                candle(2, 130, 133, 115, 120), candle(3, 120, 123, 100, 105)]
        p = DiscoveryParams(atr_period=1, paranormal_lookback=3)
        origins = pending_reversal_origins(bars, p, structure_params(p),
            lambda *args: {'eligible': True}, lambda *args: {}, atrs=[10]*len(bars))
        candidate = next(v for v in origins if v['index'] == 3 and v['kind'] == 'L')
        self.assertEqual(candidate['known_index'], 3)
        self.assertEqual(candidate['status'], 'pending')
        self.assertNotIn('confirmation_index', candidate)

    def test_immediate_outside_close_continues_unestablished_origin(self):
        bars = [candle(0, 110, 120, 100, 105), candle(1, 105, 107, 90, 95),
                candle(2, 95, 113, 94, 110)]
        self.assertEqual(self.context(bars, [origin(two_bar_earliest_index=4)])[2], [])

    def test_contextual_lp_cannot_supply_departure_for_an_earlier_contact(self):
        bars = [candle(0, 110, 120, 100, 105), candle(1, 99, 103, 95, 102),
                candle(2, 105, 132, 95, 130)]
        p, atrs, events = self.context(bars, [origin()])
        self.assertTrue(any(e['index'] == 2 for e in events))
        reaction, confirmed = reaction_after(bars, 1, 95, 'L', 10, p, atrs)
        self.assertLess(reaction, 1.5)
        self.assertIsNone(confirmed)


class HistoricalCrossLevelOriginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        snapshot = json.loads((ROOT/'_knowledge_base/manual_reviews/clean_reaction_selection_20260925/historical/snapshot.json').read_text(encoding='utf-8'))
        full = [Bar(int(row['open_time_ms']), *[float(row[key]) for key in
                ('open', 'high', 'low', 'close', 'volume')]) for row in snapshot['bars']]
        cls.as_of = int(datetime.fromisoformat('2026-09-23T06:00:00+00:00').timestamp()*1000)
        cls.bars, _ = daily_level_history(full, interval='1d', as_of_ms=cls.as_of)

    def make_context(self, bars):
        p = DiscoveryParams()
        atrs = atr_series(bars, p.atr_period)
        anchors = inflection_anchors(bars, p)
        origins = pending_reversal_origins(bars, p, structure_params(p),
            lambda i, kind, atr: inflection_context(bars, i, kind, atr, p),
            lambda i, kind, atr: historical_mirror_confirmation(bars, i, kind, atr, p),
            anchors, atrs)
        constraints, events = prior_origin_false_breakouts(bars, origins, structure_params(p), atrs)
        params = replace(structure_params(p), automatic_origin_exclusions=constraints,
                         automatic_origin_events=tuple(events))
        return params, atrs, events, anchors

    def test_february_sweep_cannot_strengthen_june_small_bar(self):
        p, atrs, events, anchors = self.make_context(self.bars)
        feb5 = next(i for i, b in enumerate(self.bars) if bar_time(b).startswith('2026-02-05'))
        self.assertNotIn((feb5, 'L'), anchors)
        feb6 = next(e for e in events if e['time'].startswith('2026-02-06') and e['kind'] == 'L')
        self.assertEqual(feb6['origin_price'], 62250)
        self.assertEqual(feb6['origin_status'], 'pending')
        june27 = next(i for i, b in enumerate(self.bars) if bar_time(b).startswith('2026-06-27'))
        profile = level_profile(self.bars, 59807.5, june27, 'L', p, atrs)
        self.assertFalse(profile['strong'])
        self.assertEqual(profile['contact_count'], 1)
        false_event = next(e for e in profile['events'] if e['time'].startswith('2026-02-06') and e['kind'] == 'L')
        self.assertFalse(false_event['confirms_level'])
        self.assertEqual(false_event['reaction_atr'], 0)

    def test_confirmed_replacement_preserved_without_borrowing_future(self):
        p, _, events, _ = self.make_context(self.bars)
        july1 = next(e for e in events if e['time'].startswith('2026-07-01') and e['kind'] == 'L')
        self.assertEqual(july1['origin_price'], 58042.6)
        self.assertTrue(july1['known_time'].startswith('2026-07-05'))
        self.assertFalse(any(e['time'].startswith('2026-06-25') for e in events))
        self.assertFalse(any(e['time'].startswith('2026-06-30') for e in events))
        end = next(i for i, b in enumerate(self.bars) if bar_time(b).startswith('2026-07-02'))+1
        _, _, earlier, _ = self.make_context(self.bars[:end])
        self.assertTrue(any(e['time'].startswith('2026-06-25')
                            and e['origin_price'] == 59081.4 for e in earlier))
        self.assertTrue(all(e['known_index'] < end for e in earlier))
        levels = discover_levels(self.bars, DiscoveryParams(working_selection=False,
                                 nearest_window_atr=float('inf')), interval='1d', as_of_ms=self.as_of)
        june25 = next(level for level in levels if level.price == 58042.6)
        self.assertEqual(june25.inflection_check['status'], 'confirmed')
        self.assertFalse(any(level.price == 57755.8 for level in levels))
        self.assertFalse(any(level.price == 59807.5 for level in levels))
        self.assertTrue(any(level.price == 94000 for level in levels))
        for price in (111968.0, 125849.7):
            with self.subTest(accepted_price=price):
                accepted = next(level for level in levels if level.price == price)
                self.assertEqual(accepted.inflection_check['status'], 'confirmed')


if __name__ == '__main__':
    unittest.main()
