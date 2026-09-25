"""Distance regressions from the twenty explicit rejections, without UI filters."""
import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'knowledge_bot'))
from level_discovery import Bar, DiscoveryParams, annotate_inflection_lifecycle, discover_levels
from level_structure import StructureParams, level_events, level_profile, confirming_contact
from chart_level_modes import evidence_markers, rejected_contact_constraints
from level_discovery import Level


class RejectedDistanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=ROOT/'_knowledge_base/manual_reviews'
        rows=json.loads((root/'strong_levels_review_20260923/candles.json').read_text(encoding='utf-8'))['bars'][:-1]
        cls.bars=[Bar(int(r['open_time_ms']),*[float(r[k]) for k in ('open','high','low','close','volume')]) for r in rows]
        cls.by_time={b.open_time:i for i,b in enumerate(cls.bars)}
        cls.rejected=json.loads((root/'price_consensus_20260923/changes.json').read_text(encoding='utf-8'))['active_bsu_rejections']

    def test_all_twenty_are_excluded_from_events_strength_and_chart_without_journal(self):
        self.assertEqual(len(self.rejected),20)
        for review in self.rejected:
            with self.subTest(price=review['price'],time=review['marker']['time']):
                index=self.by_time[review['bar_open_time_ms']]
                kind=review['marker']['kind']
                seed=next(i for i,b in enumerate(self.bars)
                          if review['bsu']['time'][:10] == review_time(b))
                profile=level_profile(self.bars,review['price'],seed,kind)
                matches=[e for e in profile['events'] if e['index']==index and e['kind']==kind]
                self.assertEqual(matches,[])
                self.assertNotIn(index,profile['exact_price_indices'])
                level=Level(review['price'],seed,review['bsu']['time'],'support' if kind=='L' else 'resistance',structure=profile)
                markers=evidence_markers(self.bars,level,'mirror_limit',DiscoveryParams())
                self.assertFalse(any(m['index']==index and m['kind']==kind and m['symbol']=='o' for m in markers))
                # Legacy mirror/limit display without a profile must use the same rule.
                level.structure={};level.atr=profile['atr_at_bsu']
                legacy=evidence_markers(self.bars,level,'mirror_limit',DiscoveryParams())
                self.assertFalse(any(m['index']==index and m['kind']==kind and m['symbol']=='o' for m in legacy))

    def test_inflection_confirmation_path_uses_same_distance_gate(self):
        for review in self.rejected:
            index=self.by_time[review['bar_open_time_ms']]
            if index<17:continue
            kind=review['marker']['kind']
            anchor={'price':review['price'],'atr_at_bsu':review['marker']['evidence'][0]['atr'],
                    'confirmation_index':index-1,'status':'confirmed'}
            anchors={(index-1,kind):anchor}
            annotate_inflection_lifecycle(self.bars,anchors,DiscoveryParams())
            self.assertNotIn(index,[e['index'] for e in anchor['limit_confirmations']])

    def test_approved_small_misses_and_small_penetration_remain(self):
        for price,date in [(58042.6,'2026-06-30'),(59081.4,'2026-06-24'),
                           (74900,'2026-03-16'),(74900,'2026-04-29'),
                           (74900,'2026-09-15'),(74900,'2026-09-16'),
                           (81787,'2026-05-07'),(81787,'2026-05-12')]:
            with self.subTest(price=price,date=date):
                events=[e for e in level_events(self.bars,price) if e['time'].startswith(date) and e['confirms_level']]
                self.assertTrue(events)

    def test_wider_near_touch_requires_closed_reaction_no_future_leak(self):
        index=next(i for i,b in enumerate(self.bars) if review_time(b)=='2026-06-30')
        self.assertIsNone(confirming_contact(self.bars[:index+1],index,58042.6,'L'))
        final=confirming_contact(self.bars,index,58042.6,'L')
        known=final['known_index']
        self.assertGreater(known,index)
        self.assertIsNone(confirming_contact(self.bars[:known],index,58042.6,'L'))
        self.assertIsNotNone(confirming_contact(self.bars[:known+1],index,58042.6,'L'))

    def test_distance_gate_scales_to_other_price_units(self):
        for review in self.rejected:
            index=self.by_time[review['bar_open_time_ms']]
            for scale in (0.001,100):
                bars=[replace(b,open=b.open*scale,high=b.high*scale,low=b.low*scale,close=b.close*scale) for b in self.bars]
                self.assertIsNone(confirming_contact(bars,index,review['price']*scale,review['marker']['kind']))

    def test_new_discovery_does_not_reintroduce_rejected_markers_by_moving_level(self):
        from datetime import datetime
        instant=int(datetime.fromisoformat('2026-09-23T06:00:00+00:00').timestamp()*1000)
        exclusions=rejected_contact_constraints(self.rejected,('bybit','BTCUSDT','1d'))
        params=DiscoveryParams(nearest_window_atr=float('inf'),excluded_contacts=exclusions)
        levels=discover_levels(self.bars,params,as_of_ms=instant,interval='1d')
        selected=[lv for lv in levels if lv.structure.get('strong') or 'inflection' in lv.basis_tags]
        self.assertTrue(selected)
        for review in self.rejected:
            with self.subTest(price=review['price'],time=review['marker']['time']):
                nearest=min(selected,key=lambda lv:abs(lv.price-review['price']))
                markers=evidence_markers(self.bars,nearest,'mirror_limit',params)
                self.assertFalse(any(self.bars[m['index']].open_time==review['bar_open_time_ms'] and
                                     m['kind']==review['marker']['kind'] and m['symbol']=='o' for m in markers))


def review_time(bar):
    from datetime import datetime,timezone
    return datetime.fromtimestamp(bar.open_time/1000,timezone.utc).date().isoformat()


class SyntheticDistanceTests(unittest.TestCase):
    def bars(self, high=100.05, opening=99.6):
        return [Bar(0,opening,high,99.5,99.6,1),Bar(86400000,99.6,99.65,96,96.5,1)]

    def test_large_atr_cannot_make_distant_price_a_touch(self):
        bars=self.bars(high=99.8,opening=99.7)
        self.assertIsNone(confirming_contact(bars,0,100,'H',atrs=[10,10]))

    def test_small_rounding_penetration_is_kept_and_real_false_breakout_is_entry_only(self):
        for high,role in [(100.005,'touch'),(100.05,'false_breakout')]:
            events=level_events(self.bars(high=high),100,atrs=[1,1])
            event=next(e for e in events if e['index']==0 and e['kind']=='H')
            self.assertEqual(event['role'],role)
            self.assertEqual(event['confirms_level'],role=='touch')

    def test_long_tail_far_outside_contact_zone_never_supplies_confirmation(self):
        bars=self.bars(high=99.8,opening=98)
        bars[0]=replace(bars[0],low=97.5,close=98)
        self.assertIsNone(confirming_contact(bars,0,100,'H',atrs=[2,2]))

    def test_explicit_rejection_survives_small_level_move_but_not_other_context_or_restore(self):
        record={'action':'reject_robot_bsu','exchange':'bybit','symbol':'BTCUSDT','interval':'1d',
                'price':100,'bar_open_time_ms':0,'marker':{'kind':'H'},'recorded_at':'rejection'}
        key=('bybit','BTCUSDT','1d')
        constraints=rejected_contact_constraints([record],key)
        self.assertEqual(len(constraints),1)
        bars=self.bars(high=99.98)
        base=level_profile(bars,99.98,0,'H',atrs=[1,1])
        excluded=level_profile(bars,99.98,0,'H',StructureParams(excluded_contacts=constraints),atrs=[1,1])
        self.assertGreater(base['contact_count'],excluded['contact_count'])
        self.assertGreater(base['strength_score'],excluded['strength_score'])
        self.assertIsNone(confirming_contact(bars,0,99.98,'H',StructureParams(excluded_contacts=constraints),[1,1]))
        for other in [('binance','BTCUSDT','1d'),('bybit','ETHUSDT','1d'),('bybit','BTCUSDT','1h')]:
            self.assertEqual(rejected_contact_constraints([record],other),())
        restored={**record,'action':'restore_robot_bsu','rejected_recorded_at':'rejection'}
        self.assertEqual(rejected_contact_constraints([record,restored],key),())
        self.assertIsNotNone(confirming_contact(bars,0,99.98,'H',StructureParams(),[1,1]))
        # A different price region on the same bar retains its own evidence.
        bars[0]=replace(bars[0],open=109.6,high=110,low=109.5,close=109.6)
        self.assertIsNotNone(confirming_contact(bars,0,110,'H',StructureParams(excluded_contacts=constraints),[1,1]))


if __name__=='__main__':unittest.main()
