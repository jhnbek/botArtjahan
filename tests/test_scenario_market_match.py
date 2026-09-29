import unittest
import numpy as np

from knowledge_bot.scenario_market_match import match_fingerprint, time_at_x, price_at_y


class MarketMatchTests(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(79)
        self.bars = []
        for i in range(90):
            o = 100 + rng.normal(0, 7)
            c = o + rng.normal(0, 4)
            self.bars.append(dict(open_time_ms=i*3600000, open=o, close=c,
                                 high=max(o,c)+rng.uniform(.5,5), low=min(o,c)-rng.uniform(.5,5)))
        self.fp = dict(origin_x=10, pitch=8, bars=[
            dict(slot=i, x=10+i*8, color='green' if b['close']>=b['open'] else 'red',
                 high=(b['high']-200)/.25, low=(b['low']-200)/.25)
            for i,b in enumerate(self.bars[21:51])])

    def test_exact_match_and_coordinate_transform(self):
        result = match_fingerprint(self.fp, self.bars)
        self.assertTrue(result['accepted'])
        self.assertEqual(result['offset'], 21)
        self.assertEqual(time_at_x(self.fp, result, 50), 26*3600000)
        self.assertAlmostEqual(price_at_y(result, 400), 100)
        self.assertFalse(result['training_eligible'])

    def test_missing_visible_stems_preserve_slots(self):
        self.fp['bars'] = [b for b in self.fp['bars'] if b['slot'] not in {3,8,17}]
        result = match_fingerprint(self.fp, self.bars)
        self.assertTrue(result['accepted'])
        self.assertEqual(result['offset'], 21)

    def test_gap_in_market_does_not_get_silently_bridged(self):
        result = match_fingerprint(self.fp, self.bars[:32]+self.bars[33:])
        self.assertFalse(result['accepted'])

    def test_duplicate_sequence_abstains(self):
        repeated = [dict(b, open_time_ms=(90+i)*3600000) for i,b in enumerate(self.bars)]
        result = match_fingerprint(self.fp, self.bars+repeated)
        self.assertFalse(result['accepted'])
        self.assertAlmostEqual(result['quality_margin'], 0)

    def test_bad_anchor_or_unconfirmed_match_rejected(self):
        result = match_fingerprint(self.fp, self.bars)
        with self.assertRaises(ValueError):
            time_at_x(self.fp, result, 14)
        with self.assertRaises(ValueError):
            price_at_y({'accepted': False}, 4)


if __name__ == '__main__':
    unittest.main()
