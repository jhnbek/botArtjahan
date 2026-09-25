"""Price choice follows repeated actual contacts, chronology and closed OHLC.

The synthetic examples are independent of the reviewed BTC prices.  A nearby
inflection, round number or false-breakout return cannot replace the price
supported by the greatest number of distinct exact-contact bars.
"""
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'knowledge_bot'))
from level_discovery import Bar, DiscoveryParams, discover_levels
from level_structure import discover_strong_levels, level_profile, clean_limit_group, StructureParams


DAY_MS = 86_400_000
START_MS = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)


def append_bar(bars, opening, high, low, close):
    index = len(bars)
    bars.append(Bar(START_MS + index * DAY_MS, opening, high, low, close, 1.0))
    return index


def departure(bars):
    append_bar(bars, 7465, 7468, 7245, 7250)
    for _ in range(3):
        append_bar(bars, 7250, 7260, 7240, 7250)


def contact_history(prices):
    bars = []
    for _ in range(20):
        append_bar(bars, 7430, 7450, 7410, 7430)
    contacts = []
    for price in prices:
        contacts.append(append_bar(bars, 7470, price, 7460, 7465))
        departure(bars)
    return bars, contacts


class PriceConsensusTests(unittest.TestCase):
    def candidate(self, profiles, expected, radius=2):
        nearby = [p for p in profiles if abs(p['price'] - expected) <= radius]
        self.assertEqual([p['price'] for p in nearby], [expected])
        return nearby[0]

    def test_clean_limit_group_requires_three_contacts_and_adjacent_pair(self):
        def event(i,price,kind='L'):
            return {'index':i,'price':price,'kind':kind,'atr':100,'known_index':i,
                    'reaction_confirmed_index':i+2 if i==20 else None}
        contacts=[event(20,1000),event(40,1000.2),event(41,1000.6)]
        p=StructureParams()
        self.assertIsNone(clean_limit_group(contacts[:2],1000,p))
        group=clean_limit_group(contacts,1000,p)
        self.assertEqual(group['indices'],[20,40,41])
        self.assertEqual(group['limit_pairs'],[{'indices':[40,41]}])
        self.assertEqual(group['known_index'],41)
        self.assertIsNone(clean_limit_group(contacts,1000.2,p))
        self.assertIsNone(clean_limit_group([event(20,1000),event(40,1000.2),event(42,1000.6)],1000,p))
        self.assertIsNone(clean_limit_group([event(20,1000),event(40,1000.2),event(41,1002)],1000,p))
        inverse=[dict(e,price=2000-e['price'],kind='H') for e in contacts]
        self.assertEqual(clean_limit_group(inverse,1000,p)['indices'],[20,40,41])
        for scale in (.01,100):
            scaled=[dict(e,price=e['price']*scale,atr=e['atr']*scale) for e in contacts]
            self.assertEqual(clean_limit_group(scaled,1000*scale,p)['indices'],[20,40,41])

    def test_six_exact_contacts_win_over_four_earlier_contacts(self):
        bars, contacts = contact_history([7517] * 4 + [7518] * 6)
        selected = self.candidate(discover_strong_levels(bars), 7518)
        self.assertEqual(selected['bsu_index'], contacts[4])
        self.assertEqual(selected['kind'], 'H')
        actual_wicks = {b.high for b in bars} | {b.low for b in bars}
        self.assertIn(selected['price'], actual_wicks)

    def test_preferred_inflection_cannot_override_contact_majority(self):
        bars, contacts = contact_history([7517] * 4 + [7518] * 6)
        selected = self.candidate(discover_strong_levels(
            bars, preferred=[(7517, contacts[3], 'H')]), 7518)
        self.assertEqual(selected['bsu_index'], contacts[4])

    def test_tie_keeps_the_earliest_actual_contact(self):
        bars, contacts = contact_history([7517.9, 7518, 7518, 7517.9])
        first = level_profile(bars,7517.9,contacts[0],'H')
        second = level_profile(bars,7518,contacts[1],'H')
        self.assertEqual(first['strength_score'],second['strength_score'])
        selected = self.candidate(discover_strong_levels(
            bars, preferred=[(7518, contacts[2], 'H')]), 7517.9)
        self.assertEqual(selected['bsu_index'], contacts[0])

    def test_equal_votes_prefer_stronger_confirmed_structure(self):
        bars, contacts = contact_history([7517, 7518, 7518, 7517])
        first = level_profile(bars,7517,contacts[0],'H')
        second = level_profile(bars,7518,contacts[1],'H')
        self.assertEqual(first['exact_price_contact_count'],second['exact_price_contact_count'])
        self.assertGreater(second['strength_score'],first['strength_score'])
        selected = self.candidate(discover_strong_levels(bars),7518)
        self.assertEqual(selected['bsu_index'],contacts[1])

    def test_later_preferred_anchor_does_not_move_bsu_forward(self):
        bars, contacts = contact_history([7518] * 4)
        selected = self.candidate(discover_strong_levels(
            bars, preferred=[(7518, contacts[-1], 'H')]), 7518)
        self.assertEqual(selected['bsu_index'], contacts[0])

    def test_round_price_cannot_outvote_more_actual_nonround_contacts(self):
        bars, contacts = contact_history([7500] * 4 + [7501] * 6)
        selected = self.candidate(discover_strong_levels(bars), 7501)
        self.assertEqual(selected['bsu_index'], contacts[4])

    def test_one_flat_bar_cannot_cast_both_high_and_low_votes(self):
        bars, contacts = contact_history([7518, 7517, 7517, 7517])
        append_bar(bars, 7518, 7518, 7518, 7518)
        departure(bars)
        selected = self.candidate(discover_strong_levels(bars), 7517)
        self.assertEqual(selected['bsu_index'], contacts[1])

    def test_two_bar_false_breakout_returns_cannot_outvote_clean_contacts(self):
        bars, contacts = contact_history([7517] * 4 + [7518] * 2)
        returns = set()
        for number in range(5):
            append_bar(bars, 7470, 7530 + number, 7460, 7520)
            returns.add(append_bar(bars, 7510, 7518, 7460, 7465))
            departure(bars)
        selected = self.candidate(discover_strong_levels(bars), 7517)
        confirmations = {
            e['index'] for e in selected['events'] if e['confirms_level']
        }
        self.assertFalse(confirmations & returns)
        self.assertEqual(selected['bsu_index'], contacts[0])

    def test_first_raw_wick_on_false_breakout_return_cannot_be_bsu(self):
        bars, _ = contact_history([])
        append_bar(bars, 7470, 7530, 7460, 7520)
        return_index = append_bar(bars, 7510, 7518, 7460, 7465)
        departure(bars)
        clean_contacts = []
        for _ in range(3):
            clean_contacts.append(append_bar(bars, 7470, 7518, 7460, 7465))
            departure(bars)

        selected = self.candidate(discover_strong_levels(bars), 7518)
        self.assertEqual(selected['bsu_index'], clean_contacts[0])
        self.assertEqual(selected['exact_price_indices'], clean_contacts)
        self.assertEqual(selected['exact_price_contact_count'], 3)
        confirmations = {e['index'] for e in selected['events'] if e['confirms_level']}
        self.assertNotIn(return_index, confirmations)
        self.assertTrue(any(e['role'] == 'false_breakout_two_bar'
                            and return_index in e['indices']
                            for e in selected['events']))

    def test_support_prices_use_the_same_majority_rule(self):
        resistance, contacts = contact_history([7517] * 4 + [7518] * 6)
        bars = [Bar(b.open_time, 15000 - b.open, 15000 - b.low,
                    15000 - b.high, 15000 - b.close, b.volume)
                for b in resistance]
        selected = self.candidate(discover_strong_levels(bars), 7482)
        self.assertEqual(selected['kind'], 'L')
        self.assertEqual(selected['bsu_index'], contacts[4])


class ReviewedPriceConsensusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        snapshot = ROOT / '_knowledge_base/manual_reviews/strong_levels_review_20260923/candles.json'
        rows = json.loads(snapshot.read_text(encoding='utf-8'))['bars']
        # Fixed retrospective review date: forming Sep 23 is excluded, as are
        # all candles older than the user's 18-calendar-month review window.
        cls.bars = [Bar(int(r['open_time_ms']),
                        *[float(r[k]) for k in ('open', 'high', 'low', 'close', 'volume')])
                    for r in rows if '2025-03-23' <= r['open_time'][:10] < '2026-09-23']
        cls.levels = discover_levels(cls.bars, DiscoveryParams(nearest_window_atr=float('inf')))

    def test_repeated_74900_is_selected_with_first_march_anchor(self):
        selected = [level for level in self.levels
                    if abs(level.price - 74900) <= 20 and level.structure]
        self.assertEqual([level.price for level in selected], [74900])
        level = selected[0]
        self.assertEqual(level.bsu_time[:10], '2026-03-16')
        self.assertEqual(level.structure['bsu_time'][:10], '2026-03-16')
        exact = {(e['time'][:10], e['kind']) for e in level.structure['events']
                 if e['confirms_level'] and e['price'] == 74900}
        self.assertIn(('2026-03-16', 'H'), exact)
        self.assertIn(('2026-04-29', 'L'), exact)
        later = [e for e in level.structure['events']
                 if e['time'][:10] == '2026-09-15' and e['kind'] == 'L']
        self.assertEqual(len(later), 1)
        self.assertEqual(later[0]['price'], 74919.6)
        self.assertTrue(later[0]['confirms_level'])
        self.assertIn('mirror_level', level.basis_tags)


if __name__ == '__main__':
    unittest.main()
