"""Independent OHLC regressions for structural contacts and channel evidence."""
import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'knowledge_bot'))
from level_discovery import Bar, _two_bar_rejection
from level_structure import (StructureParams, atr_series, level_events, level_profile,
                             attach_channels, reaction_after, time_of, chopping_runs, discover_strong_levels, reinforced_round_origin)


def candle(index, opening=96.0, high=97.0, low=95.0, close=96.0):
    return Bar(index*86400000, opening, high, low, close, 1.0)


def quiet_bars(count=20):
    return [candle(i) for i in range(count)]


class StructuralEventTests(unittest.TestCase):
    def test_adjacent_wick_returns_are_chop_but_body_return_pair_is_false_breakout(self):
        p = StructureParams()
        for direction in (1, -1):
            def b(i, o, h, l, c):
                if direction == -1:
                    o, h, l, c = 200-o, 200-l, 200-h, 200-c
                return candle(i, opening=o, high=h, low=l, close=c)
            bars = [b(0, 98, 103, 97, 99), b(1, 99, 104, 97, 98)]
            events = level_events(bars, 100, p, [2, 2])
            self.assertFalse(any(e['role'].startswith('false_breakout') for e in events))
            self.assertTrue(any(r['indices'] == [0, 1] for r in chopping_runs(bars,100,p,[2,2])))
            single = level_events(bars[:1],100,p,[2])
            self.assertTrue(any(e['role'] == 'false_breakout' for e in single))
            pair = [b(0,98,104,97,103), b(1,103,105,97,98)]
            events = level_events(pair,100,p,[2,2])
            self.assertEqual([e['indices'] for e in events if e['role'] == 'false_breakout_two_bar'], [[0,1]])

    def test_round_origin_needs_paranormal_and_ordered_reacting_confirmations(self):
        bars=[candle(i,opening=980,high=990,low=970,close=980) for i in range(30)]
        bars[20]=candle(20,opening=745,high=1000,low=740,close=995)
        def event(i,kind,value,react=None):
            return {'index':i,'kind':kind,'price':value,'known_index':i,
                    'reaction_confirmed_index':react,'reaction_atr':2 if react else 0}
        contacts=[event(20,'H',1000),event(22,'H',999.8),event(25,'L',1000.2,27),event(26,'L',1000.3)]
        p=StructureParams();atrs=[100.0]*30
        result=reinforced_round_origin(bars,1000,20,'H',contacts,p,atrs)
        self.assertEqual(result['indices'],[20,22,25,26])
        self.assertEqual(result['known_index'],27)
        self.assertIsNone(reinforced_round_origin(bars,1000,20,'H',contacts[:3],p,atrs))
        self.assertIsNone(reinforced_round_origin(bars,1000,20,'H',[dict(e,reaction_confirmed_index=None) for e in contacts],p,atrs))
        ordinary=list(bars);ordinary[20]=candle(20,opening=995,high=1000,low=990,close=999)
        self.assertIsNone(reinforced_round_origin(ordinary,1000,20,'H',contacts,p,atrs))
        self.assertIsNone(reinforced_round_origin(bars,1001,20,'H',contacts,p,atrs))
        mirrored=[replace(b,open=2000-b.open,high=2000-b.low,low=2000-b.high,close=2000-b.close) for b in bars]
        inverse=[dict(e,kind='L' if e['kind']=='H' else 'H',price=2000-e['price']) for e in contacts]
        self.assertEqual(reinforced_round_origin(mirrored,1000,20,'L',inverse,p,atrs)['indices'],result['indices'])

    def test_chop_requires_consecutive_body_crossings(self):
        cross=lambda i: candle(i,98,103,97,102) if i%2==0 else candle(i,102,103,97,98)
        held=lambda i: candle(i,102,103,101,102)
        bars=[cross(0),held(1),cross(2),held(3)]
        self.assertEqual(chopping_runs(bars,100,atrs=[1.0]*4),[])
        bars=[cross(0),cross(1),held(2),cross(3),cross(4),cross(5)]
        self.assertEqual(chopping_runs(bars,100,atrs=[1.0]*6),
                         [{'indices':[0,1]},{'indices':[3,4,5]}])
        self.assertEqual(chopping_runs(bars[:1],100,atrs=[1.0]),[])

    def test_chop_excludes_wicks_gaps_and_noise(self):
        bars=[candle(0,102,103,97,101),candle(1,98,103,97,99),
              candle(2,99.99,103,97,100.01),candle(3,100.01,103,97,99.99)]
        self.assertEqual(chopping_runs(bars,100,atrs=[1.0]*4),[])

    def test_chop_rate_reduces_strength_without_adding_confirmation(self):
        bars=quiet_bars()
        bars += [candle(20,98,100,97,98),candle(21,98,100,97,98)]
        clean=bars+[candle(22,98,99,97,98),candle(23,102,103,101,102)]
        chopped=bars+[candle(22,98,103,97,102),candle(23,102,103,97,98)]
        first=level_profile(clean,100,20,'H',atrs=[1.0]*24)
        second=level_profile(chopped,100,20,'H',atrs=[1.0]*24)
        self.assertEqual(first['contact_count'],second['contact_count'])
        self.assertGreater(first['strength_score'],second['strength_score'])
        self.assertTrue(second['currently_chopped'])

    def test_user_two_bar_example_and_inverse_require_next_closed_return(self):
        example=[Bar(0,1050,1060,940,950,1),Bar(86400000,950,1070,940,1060,1)]
        inverse=[Bar(b.open_time,2000-b.open,2000-b.low,2000-b.high,2000-b.close,1) for b in example]
        for bars,kind,upper in [(example,'L',False),(inverse,'H',True)]:
            with self.subTest(kind=kind):
                self.assertEqual(level_events(bars[:1],1000,atrs=[100.0]),[])
                self.assertEqual(_two_bar_rejection(bars[:1],0,1000,upper,1),{})
                events=level_events(bars,1000,atrs=[100.0,100.0])
                self.assertEqual(len(events),1)
                event=events[0]
                self.assertEqual((event['role'],event['kind'],event['indices']),
                                 ('false_breakout_two_bar',kind,[0,1]))
                self.assertEqual(event['known_index'],1)
                self.assertTrue(event['entry_context_only'])
                self.assertFalse(event['confirms_level'])
                self.assertIsNone(event['reaction_confirmed_index'])
                self.assertEqual(_two_bar_rejection(bars,0,1000,upper,2)['return_index'],1)
                profile=level_profile(bars,1000,0,kind,atrs=[100.0,100.0])
                self.assertEqual(profile['contact_count'],0)
                self.assertEqual(profile['strong_reaction_count'],0)
                self.assertFalse(profile['strong'])

    def test_two_bar_return_must_close_strictly_back_on_the_next_bar(self):
        first=Bar(0,1050,1060,940,950,1)
        for close in (990,1000):
            bars=[first,Bar(86400000,950,1070,940,close,1),
                  Bar(172800000,close,1070,940,1060,1)]
            self.assertEqual(_two_bar_rejection(bars,0,1000,False,3),{})
            self.assertFalse(any(e['index']==0 and e['role']=='false_breakout_two_bar'
                                 for e in level_events(bars,1000,atrs=[100.0]*3)))
        already_outside=[replace(first,open=980),Bar(86400000,950,1070,940,1060,1)]
        self.assertEqual(_two_bar_rejection(already_outside,0,1000,False,2),{})

    def test_small_wick_luft_and_close_beyond_level_have_different_roles(self):
        held=[Bar(0,1005,1006,999.5,1002,1)]
        self.assertEqual(level_events(held,1000,atrs=[100.0])[0]['role'],'touch')
        broken=[replace(held[0],close=999.7),Bar(86400000,999.7,1006,999.5,1002,1)]
        events=level_events(broken,1000,atrs=[100.0,100.0])
        self.assertEqual(events[0]['role'],'false_breakout_two_bar')
        self.assertFalse(events[0]['confirms_level'])

    def test_user_one_bar_false_breakout_example_and_inverse(self):
        support=Bar(0,1050,1060,950,1020,1)
        resistance=Bar(0,950,1050,940,980,1)
        for bar,kind in [(support,'L'),(resistance,'H')]:
            events=level_events([bar],1000,atrs=[100.0])
            self.assertEqual(len(events),1)
            self.assertEqual(events[0]['role'],'false_breakout')
            self.assertEqual(events[0]['kind'],kind)
            self.assertFalse(events[0]['confirms_level'])
            self.assertIsNone(events[0]['reaction_confirmed_time'])

    def test_body_crossing_or_close_on_level_is_not_a_one_bar_false_breakout(self):
        for bar in [Bar(0,950,1060,940,1020,1),Bar(0,1050,1060,950,980,1),
                    Bar(0,1050,1060,950,1000,1),Bar(0,1000,1060,950,1020,1)]:
            events=level_events([bar],1000,atrs=[100.0])
            self.assertFalse(any(e['role']=='false_breakout' for e in events))

    def test_adjacent_limit_contacts_are_distinct_and_react_afterward(self):
        bars = quiet_bars()
        bars[2] = candle(2,99,100,98,99)
        bars[3] = candle(3,99,99.9,98,99)
        profile = level_profile(bars,100,2,'H',atrs=[2.0]*len(bars))
        contacts = [e for e in profile['events'] if e['role'] in ('touch','near_touch')]
        self.assertEqual([(e['index'],e['role']) for e in contacts],[(2,'touch'),(3,'near_touch')])
        self.assertTrue(profile['strong'])
        self.assertIn('limit_level',profile['basis_tags'])
        self.assertNotIn('mirror_level',profile['basis_tags'])
        self.assertEqual(profile['qualified_index'],4)

    def test_false_breakout_does_not_supply_missing_limit_contact(self):
        bars = quiet_bars()
        bars[2] = candle(2,99,100,98,99)
        bars[5] = candle(5,99,102,95,96)
        bars[9] = candle(9,99,103,95,96)
        profile = level_profile(bars,100,2,'H',atrs=[2.0]*len(bars))
        self.assertEqual(profile['contact_count'],1)
        self.assertEqual([e['role'] for e in profile['events']],
                         ['touch','false_breakout','false_breakout'])
        self.assertNotIn('limit_level',profile['basis_tags'])
        self.assertIn('insufficient_structural_contacts',profile['rejection_reasons'])

    def test_extra_false_breakout_gives_no_strength_or_confirmation_bonus(self):
        bars=quiet_bars(30)
        bars[2]=candle(2,99,100,98,99)
        bars[3]=candle(3,99,100,98,99)
        original=level_profile(bars,100,2,'H',atrs=[2.0]*len(bars))
        bars[15]=candle(15,99,101,95,96)
        updated=level_profile(bars,100,2,'H',atrs=[2.0]*len(bars))
        self.assertEqual(updated['strength_score'],original['strength_score'])
        self.assertEqual(updated['contact_count'],original['contact_count'])
        self.assertEqual(updated['strong_reaction_count'],original['strong_reaction_count'])
        self.assertEqual(updated['qualified_time'],original['qualified_time'])
        event=next(e for e in updated['events'] if e['index']==15)
        self.assertTrue(event['entry_context_only'])
        self.assertFalse(event['confirms_level'])

    def test_two_bar_false_breakout_is_known_only_after_return(self):
        bars = quiet_bars(10)
        bars[4] = candle(4,99,102,98,101)
        bars[5] = candle(5,101,102,98,99)
        before = level_events(bars[:5],100,atrs=[2.0]*5)
        self.assertNotIn(4,[e['index'] for e in before])
        returned = level_events(bars[:6],100,atrs=[2.0]*6)
        event = next(e for e in returned if e['index']==4)
        self.assertEqual(event['role'],'false_breakout_two_bar')
        self.assertEqual(event['indices'],[4,5])
        self.assertEqual(event['known_time'],time_of(bars[5]))
        self.assertIsNone(event['reaction_confirmed_time'])
        after = level_events(bars[:7],100,atrs=[2.0]*7)
        event = next(e for e in after if e['index']==4)
        self.assertIsNone(event['reaction_confirmed_time'])
        self.assertFalse(event['confirms_level'])
        self.assertTrue(event['entry_context_only'])
        self.assertNotIn(5,[e['index'] for e in after])
        bars[5] = candle(5,101,103,100,102)
        self.assertNotIn(4,[e['index'] for e in level_events(bars,100,atrs=[2.0]*len(bars))])

    def test_false_breakout_bars_cannot_supply_reaction_for_earlier_contacts(self):
        contacts = [candle(i,99.8,100,99.5,99.8) for i in range(2)]
        one_bar = contacts + [candle(2,99.8,101,94,94.5)]
        two_bar = contacts + [candle(2,99.8,102,99.5,101),
                              candle(3,101,101.2,94,94.5)]
        for original in (one_bar, two_bar):
            inverse = [replace(b,open=200-b.open,high=200-b.low,
                               low=200-b.high,close=200-b.close) for b in original]
            for bars,kind in ((original,'H'),(inverse,'L')):
                with self.subTest(bars=len(bars),kind=kind):
                    profile = level_profile(bars,100,0,kind,atrs=[2.0]*len(bars))
                    self.assertEqual(profile['contact_count'],2)
                    self.assertEqual(profile['strong_reaction_count'],0)
                    self.assertFalse(profile['strong'])
                    reaction, confirmed = reaction_after(bars,0,100,kind,2.0,
                                                        StructureParams(),[2.0]*len(bars))
                    self.assertAlmostEqual(reaction,.1)
                    self.assertIsNone(confirmed)

    def test_ordinary_departure_after_two_bar_return_can_confirm_earlier_contacts(self):
        bars = [candle(i,99.8,100,99.5,99.8) for i in range(2)]
        bars += [candle(2,99.8,102,99.5,101),candle(3,101,101.2,94,94.5),
                 candle(4,94.5,96,94,95)]
        profile = level_profile(bars,100,0,'H',atrs=[2.0]*len(bars))
        self.assertTrue(profile['strong'])
        self.assertEqual(profile['qualified_index'],4)
        self.assertTrue(all(e['reaction_confirmed_index']==4 for e in profile['events']
                            if e['role']=='touch'))
        self.assertFalse(any(e['reaction_confirmed_index'] in (2,3) for e in profile['events']))

    def test_reaction_uses_local_wick_luft_but_strict_two_bar_closes(self):
        bars = [candle(0,99.8,100,99.5,99.8),candle(1,99.8,100.05,94,95)]
        # The later bar's ATR allows this small wick, even if the BSU ATR does not.
        maximum, confirmed = reaction_after(bars,0,100,'H',2,StructureParams(),[2.,10.])
        self.assertEqual((maximum,confirmed),(2.5,1))
        # With the same tail beyond the local luft it is a one-bar LP, not strength.
        maximum, confirmed = reaction_after(bars,0,100,'H',2,StructureParams(),[2.,2.])
        self.assertAlmostEqual(maximum,.1)
        self.assertIsNone(confirmed)
        bars = [candle(0,99.8,100,99.5,99.8),candle(1,99.8,100.01,99.5,100.01),
                candle(2,100.01,100.02,94,95)]
        maximum, confirmed = reaction_after(bars,0,100,'H',2,StructureParams(),[2.]*3)
        self.assertAlmostEqual(maximum,.1)
        self.assertIsNone(confirmed)

    def test_reaction_does_not_allow_gap_break_or_return_exactly_to_level(self):
        for opening,returned in ((101,95),(99.8,100)):
            bars = [candle(0,99.8,100,99.5,99.8),candle(1,opening,102,99.5,101),
                    candle(2,101,102,94,returned),candle(3,returned,100,94,95)]
            maximum, confirmed = reaction_after(bars,0,100,'H',2,StructureParams(),[2.]*4)
            self.assertAlmostEqual(maximum,.1)
            self.assertIsNone(confirmed)

    def test_mirror_requires_opposite_contacts_and_close_through_level(self):
        bars = quiet_bars(10)
        bars[1] = candle(1,99,100,98,99)
        bars[2] = candle(2,99,104,98,103)
        bars[3] = candle(3,103,104,100,103)
        profile = level_profile(bars,100,1,'H',atrs=[2.0]*len(bars))
        self.assertEqual(profile['mirror_pair'],[1,3])
        self.assertIn('mirror_level',profile['basis_tags'])
        self.assertTrue(profile['strong'])
        bars[2] = candle(2,99,100,98,99)
        bars[3] = candle(3,100,102,100,100)
        no_cross = level_profile(bars,100,1,'H',atrs=[2.0]*len(bars))
        self.assertNotIn('mirror_level',no_cross['basis_tags'])

    def test_repeated_weak_contacts_without_departure_are_rejected(self):
        bars = [candle(i,99,99.5,98.5,99) for i in range(20)]
        bars[2] = candle(2,99,100,98.5,99)
        bars[3] = candle(3,99,100,98.5,99)
        profile = level_profile(bars,100,2,'H',atrs=[2.0]*len(bars))
        self.assertEqual(profile['contact_count'],2)
        self.assertFalse(profile['strong'])
        self.assertIn('no_strong_close_reaction',profile['rejection_reasons'])

    def test_old_chop_does_not_permanently_erase_later_strong_level(self):
        bars = quiet_bars(70)
        for i in range(4,18):
            close = 104 if i%2 else 96
            bars[i] = candle(i,bars[i-1].close,max(close,bars[i-1].close)+1,
                             min(close,bars[i-1].close)-1,close)
        bars[50] = candle(50,99,100,98,99)
        bars[51] = candle(51,99,100,98,99)
        profile = level_profile(bars,100,50,'H',atrs=[2.0]*len(bars))
        self.assertTrue(profile['strong'])
        self.assertFalse(profile['currently_chopped'])
        self.assertEqual(profile['recent_close_switches'],[])

    def test_distant_long_tail_cannot_confirm_even_after_strong_reaction(self):
        bars = quiet_bars(8)
        bars[2] = candle(2,98.5,99.7,98.3,98.6)
        before = level_events(bars[:3],100,atrs=[2.0]*3)
        self.assertNotIn(2,[e['index'] for e in before])
        after = level_events(bars,100,atrs=[2.0]*len(bars))
        self.assertNotIn(2,[e['index'] for e in after])

    def test_prefix_does_not_claim_future_reaction_or_qualification(self):
        bars = quiet_bars(10)
        bars[2] = candle(2,99,100,98,99)
        bars[3] = candle(3,99,100,98,99)
        before = level_profile(bars[:4],100,2,'H',atrs=[2.0]*4)
        after = level_profile(bars[:5],100,2,'H',atrs=[2.0]*5)
        self.assertFalse(before['strong'])
        self.assertIsNone(before['qualified_time'])
        self.assertTrue(after['strong'])
        self.assertEqual(after['qualified_time'],time_of(bars[4]))
        for event in after['events']:
            self.assertLessEqual(event['known_index'],4)
            if event['reaction_confirmed_index'] is not None:
                self.assertLessEqual(event['reaction_confirmed_index'],4)

    def test_event_roles_and_atr_distances_are_symmetric_and_scale_free(self):
        bars = quiet_bars(10)
        bars[2] = candle(2,99,100,98,99)
        bars[3] = candle(3,99,99.9,98,99)
        bars[5] = candle(5,99,102,95,96)
        original = level_events(bars,100,atrs=[2.0]*len(bars))
        for scale,offset in [(0.01,5),(-1,300)]:
            transformed = [Bar(b.open_time,offset+scale*b.open,
                               offset+scale*(b.high if scale>0 else b.low),
                               offset+scale*(b.low if scale>0 else b.high),
                               offset+scale*b.close,b.volume) for b in bars]
            events = level_events(transformed,offset+scale*100,atrs=[abs(scale)*2]*len(bars))
            self.assertEqual([(e['index'],e['role']) for e in events],
                             [(e['index'],e['role']) for e in original])
            for actual,expected in zip(events,original):
                self.assertAlmostEqual(actual['gap_atr'],expected['gap_atr'])
                self.assertEqual(actual['known_index'],expected['known_index'])
                self.assertEqual(actual['kind'],expected['kind'] if scale>0 else
                                 'L' if expected['kind']=='H' else 'H')

    def test_atr_uses_strictly_previous_candles(self):
        bars = quiet_bars(20)
        original = atr_series(bars,14)
        changed = list(bars)
        changed[16] = replace(changed[16],high=500,low=1)
        recalculated = atr_series(changed,14)
        self.assertIsNone(original[14])
        self.assertEqual(original[16],recalculated[16])
        self.assertGreater(recalculated[17],original[17])


class ChannelEvidenceTests(unittest.TestCase):
    @staticmethod
    def profiles(bars):
        return [
            {'price':90.0,'bsu_index':0,'qualified_index':1,'strength_score':1,'channels':[],
             'events':[{'index':i,'kind':'L','role':'touch'} for i in (0,16)]},
            {'price':110.0,'bsu_index':8,'qualified_index':9,'strength_score':1,'channels':[],
             'events':[{'index':i,'kind':'H','role':'touch'} for i in (8,24)]},
        ]

    def test_channel_needs_four_alternating_excursions_spanning_time(self):
        bars = [candle(i,100,102,98,100) for i in range(25)]
        for i in (0,16): bars[i] = candle(i,95,96,90,94)
        for i in (8,24): bars[i] = candle(i,105,110,104,106)
        profiles = self.profiles(bars)
        attach_channels(bars,profiles,StructureParams(),[5.0]*len(bars))
        self.assertTrue(profiles[0]['channels'])
        channel = profiles[0]['channels'][0]
        self.assertEqual([e['side'] for e in channel['visits']],['L','H','L','H'])
        self.assertEqual(channel['known_time'],time_of(bars[24]))
        self.assertEqual(profiles[0]['channels'],profiles[1]['channels'])
        # Three visits do not establish the repeated wave sequence.
        shortened = self.profiles(bars[:24])
        shortened[1]['events'] = shortened[1]['events'][:1]
        attach_channels(bars[:24],shortened,StructureParams(),[5.0]*24)
        self.assertEqual(shortened[0]['channels'],[])

    def test_single_wide_bars_do_not_fake_alternating_channel_waves(self):
        bars = [candle(i,100,110,90,100) for i in range(25)]
        profiles = self.profiles(bars)
        attach_channels(bars,profiles,StructureParams(),[5.0]*len(bars))
        self.assertEqual(profiles[0]['channels'],[])
        self.assertEqual(profiles[1]['channels'],[])

    def test_false_breakouts_cannot_confirm_channel_boundaries(self):
        bars=[candle(i,100,102,98,100) for i in range(25)]
        for i in (0,16): bars[i]=candle(i,95,96,90,94)
        for i in (8,24): bars[i]=candle(i,105,110,104,106)
        profiles=self.profiles(bars)
        for profile in profiles:
            for event in profile['events']:
                event.update(role='false_breakout',gap_atr=.02)
        attach_channels(bars,profiles,StructureParams(),[5.0]*len(bars))
        self.assertEqual(profiles[0]['channels'],[])

    def test_same_boundaries_can_form_separate_later_channel(self):
        bars = [candle(i,130,131,129,130) for i in range(155)]
        for start in (0,130):
            for i in range(start,start+25): bars[i] = candle(i,100,102,98,100)
            for i in (start,start+16): bars[i] = candle(i,95,96,90,94)
            for i in (start+8,start+24): bars[i] = candle(i,105,110,104,106)
        profiles = self.profiles(bars)
        for profile,kind,indices in [(profiles[0],'L',(130,146)),(profiles[1],'H',(138,154))]:
            profile['events'].extend({'index':i,'kind':kind,'role':'touch'} for i in indices)
        attach_channels(bars,profiles,StructureParams(),[5.0]*len(bars))
        self.assertGreaterEqual(len(profiles[0]['channels']),2)
        self.assertTrue(any(c['end_index']<=24 for c in profiles[0]['channels']))
        self.assertTrue(any(c['start_index']>24 for c in profiles[0]['channels']))


class ReviewedEventTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        data=json.loads((Path(__file__).parent/'fixtures'/'inflection_review_btc_1d.json').read_text(encoding='utf-8'))
        cls.bars=[Bar(*row) for row in data['bars']]

    def test_l12_round_origin_qualifies_under_revised_1_6_body_threshold(self):
        rows=json.loads((Path(__file__).resolve().parents[1]/'_knowledge_base/manual_reviews/strong_levels_review_20260923/candles.json').read_text(encoding='utf-8'))['bars'][:-1]
        bars=[Bar(r['open_time_ms'],*[r[k] for k in ('open','high','low','close','volume')]) for r in rows]
        from level_history import daily_level_history
        bars,_=daily_level_history(bars,interval='1d')
        index=next(i for i,b in enumerate(bars) if time_of(b).startswith('2025-04-22'))
        profile=level_profile(bars,94000,index,'H')
        # Its real body is 1.813 prior ATR: the user's revised 1.6 threshold
        # qualifies it by body size, without reverting to the old range rule.
        body_atr=abs(bars[index].close-bars[index].open)/atr_series(bars,14)[index]
        self.assertAlmostEqual(body_atr,1.8127668496076539)
        origin=profile['reinforced_round_origin']
        self.assertEqual([time_of(bars[i])[:10] for i in origin['indices']],
                         ['2025-04-22','2025-04-24','2025-05-01','2025-05-04'])
        self.assertIn('paranormal_bar',profile['basis_tags'])
        self.assertIsNone(level_profile(bars[:origin['known_index']],94000,index,'H')['reinforced_round_origin'])
        self.assertIsNotNone(level_profile(bars[:origin['known_index']+1],94000,index,'H')['reinforced_round_origin'])
        params=replace(StructureParams(),excluded_contacts=((bars[index].open_time,'H',94000),))
        self.assertIsNone(level_profile(bars,94000,index,'H',params)['reinforced_round_origin'])

    def test_l10_clean_limit_group_uses_november_and_december_not_false_breakouts(self):
        rows=json.loads((Path(__file__).resolve().parents[1]/'_knowledge_base/manual_reviews/strong_levels_review_20260923/candles.json').read_text(encoding='utf-8'))['bars'][:-1]
        bars=[Bar(r['open_time_ms'],*[r[k] for k in ('open','high','low','close','volume')]) for r in rows]
        from level_history import daily_level_history
        bars,_=daily_level_history(bars,interval='1d')
        index=next(i for i,b in enumerate(bars) if time_of(b).startswith('2025-11-24'))
        profile=level_profile(bars,85211.1,index,'L')
        group=profile['clean_limit_group']
        self.assertEqual([time_of(bars[i])[:10] for i in group['indices']],
                         ['2025-11-24','2025-12-16','2025-12-17'])
        stop=group['indices'][-1]
        self.assertIsNone(level_profile(bars[:stop],85211.1,index,'L')['clean_limit_group'])
        self.assertIsNotNone(level_profile(bars[:stop+1],85211.1,index,'L')['clean_limit_group'])
        false_indices={i for e in profile['events'] if e['entry_context_only'] for i in e['indices']}
        self.assertFalse(false_indices.intersection(group['indices']))
        selected=discover_strong_levels(bars)
        self.assertIn(85211.1,[v['price'] for v in selected])
        self.assertNotIn(85104.7,[v['price'] for v in selected])

    def test_april_mirror_pair_keeps_earlier_actual_price(self):
        index=next(i for i,b in enumerate(self.bars) if time_of(b).startswith('2026-04-11'))
        profile=level_profile(self.bars,73800,index,'H')
        pair=profile['precise_mirror']
        self.assertIsNotNone(pair)
        self.assertTrue(pair['first_time'].startswith('2026-04-11'))
        self.assertTrue(pair['second_time'].startswith('2026-04-14'))
        self.assertAlmostEqual(pair['price_gap'],45.5)
        self.assertTrue(profile['strong'])
        selected=discover_strong_levels(self.bars)
        prices=[v['price'] for v in selected]
        self.assertIn(73800,prices)
        self.assertNotIn(74117.7,prices)
        april14=next(e for e in profile['events'] if e['time'].startswith('2026-04-14'))
        self.assertTrue(april14['confirms_level'])
        prefix=self.bars[:april14['index']+1]
        self.assertTrue(any(e['confirms_level'] for e in level_events(prefix,73800)
                            if e['time'].startswith('2026-04-14')))

    def test_june_3_is_false_breakout_not_limit_contact(self):
        events=level_events(self.bars,106718.0)
        event=next(e for e in events if e['time'].startswith('2025-06-03'))
        self.assertEqual(event['role'],'false_breakout')
        self.assertFalse(event['confirms_level'])
        self.assertIsNone(event['reaction_confirmed_time'])

    def test_two_touch_level_is_not_strengthened_by_distant_tails_or_false_breakouts(self):
        index=next(i for i,b in enumerate(self.bars) if time_of(b).startswith('2025-07-16'))
        profile=level_profile(self.bars,120114.6,index,'H')
        self.assertFalse(profile['strong'])
        self.assertIn('no_strong_close_reaction',profile['rejection_reasons'])
        self.assertEqual(profile['contact_count'],2)
        strong=[e for e in profile['events'] if e['reaction_confirmed_index'] is not None]
        self.assertEqual(strong,[])
        self.assertIn('limit_level',profile['basis_tags'])
        july23=next(e for e in profile['events'] if e['time'].startswith('2025-07-23'))
        self.assertEqual(july23['role'],'chop')
        self.assertIsNone(july23['reaction_confirmed_index'])

    def test_recent_chop_does_not_erase_historically_strong_79388(self):
        index=next(i for i,b in enumerate(self.bars) if time_of(b).startswith('2026-02-01'))
        profile=level_profile(self.bars,79388,index,'H')
        self.assertTrue(profile['strong'])
        self.assertTrue(profile['currently_chopped'])
        self.assertGreater(profile['strong_reaction_count'],0)


if __name__ == '__main__':
    unittest.main()
