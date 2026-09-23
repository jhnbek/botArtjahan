"""Tail-based BSUs, structural context and causal confirmation regressions."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'knowledge_bot'))
from level_discovery import (Bar, DiscoveryParams, inflection_anchors, discover_levels,
                             apply_confirmed_inflections, historical_mirror_confirmation,
                             inflection_context, bar_time, atr_at, annotate_inflection_lifecycle)


def fixture(mirror=False):
    closes = [100+i for i in range(31)] + [128,126,124,122,120,118,116,114]
    if mirror:
        closes = [250-c for c in closes]
    return [Bar(i*86400000, c-.1, c+.2, c-.2, c, 10) for i,c in enumerate(closes)]


class InflectionTests(unittest.TestCase):
    def test_mirror_requires_opposite_touch_and_intervening_cross(self):
        bars = [Bar(i*86400000,90,95,85,90,1) for i in range(40)]
        bars[5] = Bar(5*86400000,102,105,100,103,1)
        bars[35] = Bar(35*86400000,99,100,97,99,1)
        params = DiscoveryParams()
        self.assertEqual(historical_mirror_confirmation(bars,35,'H',2,params)['index'],5)
        bars[5].close = 99
        self.assertEqual(historical_mirror_confirmation(bars,35,'H',2,params),{})
        bars[5].close = 103
        for bar in bars[6:35]:bar.close=101
        self.assertEqual(historical_mirror_confirmation(bars,35,'H',2,params),{})

    def test_manual_bsu_is_explicit_and_requires_matching_date_and_price(self):
        bars = fixture()
        review = {'price':bars[30].high, 'human_bsu_date':{'year':1970,'month':1,'day':31}}
        result = apply_confirmed_inflections(bars, [], [review])
        self.assertEqual(len(result),1)
        self.assertEqual(result[0].source,'human_confirmed_inflection')
        self.assertFalse(result[0].inflection_check['automatic_detection'])
        self.assertEqual(apply_confirmed_inflections(bars,[],[{**review,'price':999}]),[])
        self.assertEqual(apply_confirmed_inflections(bars[:20],[],[review]),[])
        self.assertEqual(apply_confirmed_inflections(bars,[],[{**review,'human_bsu_date':{'day':31,'month':1}}]),[])

    def test_high_and_low_reversals_have_closed_bar_confirmation(self):
        for mirror,kind in [(False,'H'),(True,'L')]:
            bars=fixture(mirror)
            anchors=inflection_anchors(bars,DiscoveryParams())
            self.assertIn((30,kind),anchors)
            self.assertGreaterEqual(anchors[(30,kind)]['confirmation_index'],33)

    def test_unconfirmed_future_not_used(self):
        self.assertNotIn((30,'H'),inflection_anchors(fixture()[:33],DiscoveryParams()))

    def test_false_breakout_wick_does_not_replace_previous_bsu(self):
        bars=fixture(); bars[31].high=134
        anchors=inflection_anchors(bars,DiscoveryParams())
        self.assertIn((30,'H'),anchors)
        self.assertNotIn((31,'H'),anchors)
        self.assertEqual(anchors[(30,'H')]['price'],bars[30].high)

    def test_tail_must_extend_prior_range(self):
        bars=fixture();bars[27].high=131
        self.assertNotIn((30,'H'),inflection_anchors(bars,DiscoveryParams()))

    def test_next_close_beyond_bsu_close_but_inside_tail_is_allowed(self):
        for mirror,kind in [(False,'H'),(True,'L')]:
            bars=fixture(mirror)
            if mirror:
                bars[30].low=116; bars[31].low=115; bars[31].close=118
            else:
                bars[30].high=134; bars[31].high=135; bars[31].close=132
            anchors=inflection_anchors(bars,DiscoveryParams())
            self.assertIn((30,kind),anchors)
            self.assertNotIn((31,kind),anchors)

    def test_close_beyond_bsu_tail_cancels_candidate(self):
        for mirror,kind in [(False,'H'),(True,'L')]:
            bars=fixture(mirror)
            if mirror:
                bars[31].low=bars[30].low-2; bars[31].close=bars[30].low-1
            else:
                bars[31].high=bars[30].high+2; bars[31].close=bars[30].high+1
            self.assertNotIn((30,kind),inflection_anchors(bars,DiscoveryParams()))

    def test_wick_only_reversal_is_not_confirmation(self):
        bars=fixture()[:31]
        bars += [Bar(i*86400000,129,130,110,129,10) for i in range(31,39)]
        self.assertNotIn((30,'H'),inflection_anchors(bars,DiscoveryParams()))

    def test_discovery_keeps_exact_anchor_and_explains_confirmation(self):
        bars=fixture()
        levels=discover_levels(bars,DiscoveryParams(nearest_window_atr=float('inf')))
        level=next(l for l in levels if 'inflection' in l.basis_tags)
        self.assertEqual(level.bsu_index,30)
        self.assertEqual(level.price,bars[30].high)
        self.assertIn('confirmation_time',level.inflection_check)


class ReviewedContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        payload = json.loads((Path(__file__).parent/'fixtures/inflection_review_btc_1d.json').read_text(encoding='utf-8'))
        cls.bars = [Bar(*row) for row in payload['bars']]
        cls.params = DiscoveryParams(nearest_window_atr=float('inf'))
        cls.anchors = inflection_anchors(cls.bars, cls.params)
        cls.levels = discover_levels(cls.bars, cls.params)
        cls.indices = {bar_time(b)[:10]: i for i,b in enumerate(cls.bars)}

    def test_reviewed_local_waves_and_pullback_are_not_inflections(self):
        for date,kind in [('2026-03-29','L'),('2025-12-09','H'),('2025-08-29','L'),
                          ('2025-08-02','L'),('2025-09-17','H')]:
            with self.subTest(date=date):
                i=self.indices[date]
                self.assertNotIn((i,kind), self.anchors)
                context=inflection_context(self.bars,i,kind,atr_at(self.bars,i,14),self.params)
                self.assertEqual(context['reason'],'local_move_in_broader_range')
        # Rolling the old low out of a fixed window must not resurrect the same pullback.
        self.assertNotIn((self.indices['2025-09-01'],'L'),self.anchors)

    def test_approved_bsUs_survive_context_and_price_clustering(self):
        expected={'2026-06-25':58042.6, '2026-09-15':74919.6, '2025-08-13':123742.2,
                  '2026-01-14':97963.2, '2025-10-05':125849.7}
        actual={l.bsu_time[:10]:l for l in self.levels if 'inflection' in l.basis_tags}
        for date,price in expected.items():
            with self.subTest(date=date):
                self.assertIn(date,actual)
                self.assertAlmostEqual(actual[date].price,price)

    def test_boundary_exception_requires_prior_reversal_and_opposite_confirmation(self):
        anchor=self.anchors[(self.indices['2026-09-15'],'L')]
        self.assertEqual(anchor['context']['reason'],'confirmed_historical_boundary')
        boundary=anchor['context']['boundary_confirmation']
        self.assertEqual(boundary['original']['time'][:10],'2026-03-16')
        self.assertEqual(boundary['opposite_touch']['time'][:10],'2026-04-29')
        self.assertLess(boundary['opposite_touch']['confirmation_index'],self.indices['2026-09-15'])

    def test_secondary_limit_preserves_bsu_and_june24_confirmation(self):
        level=next(l for l in self.levels if l.bsu_time[:10]=='2026-06-05')
        self.assertNotIn('inflection',level.basis_tags)
        self.assertIn('limit_level',level.basis_tags)
        self.assertAlmostEqual(level.price,59081.4)
        check=level.inflection_check
        self.assertTrue(check['retained_secondary'])
        self.assertIn('2026-06-24',[x['time'][:10] for x in check['limit_confirmations']])
        self.assertEqual(check['superseded_by']['bsu_time'][:10],'2026-06-25')
        self.assertEqual(check['superseded_by']['close_cross_time'][:10],'2026-06-30')
        self.assertEqual(check['superseded_by']['effective_time'][:10],'2026-07-05')
        primary=self.anchors[(self.indices['2026-06-25'],'L')]
        touch=next(t for t in primary['limit_confirmations'] if t['time'][:10]=='2026-06-30')
        self.assertAlmostEqual(touch['gap'],114.1)

    def test_supersession_waits_for_both_cross_and_confirmed_replacement(self):
        key=(self.indices['2026-06-05'],'L')
        for date in ['2026-06-29','2026-06-30','2026-07-04']:
            with self.subTest(date=date):
                prefix=inflection_anchors(self.bars[:self.indices[date]+1],self.params)
                self.assertEqual(prefix[key]['status'],'confirmed')
        after=inflection_anchors(self.bars[:self.indices['2026-07-05']+1],self.params)
        self.assertEqual(after[key]['status'],'superseded')

    def test_context_never_uses_bars_after_bsu(self):
        i=self.indices['2026-09-15']; atr=atr_at(self.bars,i,14)
        self.assertEqual(inflection_context(self.bars,i,'L',atr,self.params),
                         inflection_context(self.bars[:i+1],i,'L',atr,self.params))

    def test_manual_confirmation_preserves_independently_detected_evidence(self):
        result=apply_confirmed_inflections(self.bars,self.levels,[{
            'price':58042.74,'human_bsu_date':{'year':2026,'month':6,'day':25}}])
        level=next(l for l in result if l.source=='human_confirmed_inflection')
        self.assertTrue(level.inflection_check['automatic_detection'])
        self.assertIn('limit_level',level.basis_tags)
        self.assertEqual(level.inflection_check['limit_confirmations'][0]['time'][:10],'2026-06-30')

    def test_prices_and_long_short_orientation_are_not_hardcoded(self):
        for scale,offset in [(0.01,5.0),(-1.0,300000.0)]:
            transformed=[Bar(b.open_time,offset+scale*b.open,
                             offset+scale*(b.high if scale>0 else b.low),
                             offset+scale*(b.low if scale>0 else b.high),
                             offset+scale*b.close,b.volume) for b in self.bars]
            actual=inflection_anchors(transformed,self.params)
            expected={(i,kind if scale>0 else 'L' if kind=='H' else 'H'):a
                      for (i,kind),a in self.anchors.items()}
            self.assertEqual(set(actual),set(expected))
            for key,a in expected.items():
                self.assertEqual(actual[key]['status'],a['status'])
                self.assertEqual(actual[key]['confirmation_index'],a['confirmation_index'])
                self.assertAlmostEqual(actual[key]['price'],offset+scale*a['price'])

    def test_wick_break_does_not_supersede_even_with_a_later_anchor(self):
        bars=[Bar(i*86400000,95,102,90,95,1) for i in range(12)]
        for b in bars[5:]: b.high=105  # higher wicks, closes still below old high
        anchors={(1,'H'):{'price':100,'atr_at_bsu':5,'confirmation_index':4,'status':'confirmed'},
                 (5,'H'):{'price':103,'atr_at_bsu':5,'confirmation_index':8,'status':'confirmed'}}
        annotate_inflection_lifecycle(bars,anchors,DiscoveryParams())
        self.assertEqual(anchors[(1,'H')]['status'],'confirmed')
