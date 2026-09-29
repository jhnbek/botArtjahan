import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from knowledge_bot import prepare_market_training as preparation
from knowledge_bot.prepare_market_training import closed_prefix, HOUR, DAY


class MarketPrefixTests(unittest.TestCase):
    def setUp(self):
        self.bars=[dict(open_time_ms=i*HOUR,close_time_ms=(i+1)*HOUR,
                        open=100,high=102,low=99,close=101) for i in range(40)]

    def test_future_candles_do_not_enter_the_prefix(self):
        prefix=closed_prefix(self.bars,20*HOUR,HOUR)
        future=[dict(b,open=-999,high=float('inf')) for b in self.bars[20:]]
        self.assertEqual(prefix,closed_prefix(self.bars[:20]+future,20*HOUR,HOUR))
        self.assertEqual(prefix[-1]['close_time_ms'],20*HOUR)

    def test_execution_hour_not_available_at_its_open(self):
        prefix=closed_prefix(self.bars,20*HOUR,HOUR)
        self.assertNotIn(self.bars[20],prefix)

    def test_gap_and_missing_decision_close_abstain(self):
        with self.assertRaisesRegex(ValueError,'history_gap'):
            closed_prefix(self.bars[:17]+self.bars[18:],20*HOUR,HOUR)
        with self.assertRaisesRegex(ValueError,'exact_market_decision'):
            closed_prefix(self.bars[:19],20*HOUR,HOUR)

    def test_close_time_must_be_real_boundary(self):
        self.bars[10]['close_time_ms']=self.bars[10]['open_time_ms']
        with self.assertRaisesRegex(ValueError,'close_boundary'):
            closed_prefix(self.bars,20*HOUR,HOUR)


class AuthorDailyCloseCorrectionTests(unittest.TestCase):
    def test_unrelated_hourly_image_does_not_set_the_corrected_entry(self):
        annotation=dict(scenario_id=251,direction='short',image_sha256={
            'images/1D_251.jpg':'daily_hash','images/1H_251.jpg':'hourly_hash'})
        def matched(tf,accepted):
            return dict(scenario_id=251,accepted=accepted,symbol='LINKUSDT',category='linear',
                        fingerprint={'source_sha256':tf+'_hash'},
                        cache_file=tf+'.json',cache_file_sha256=tf+'_cache_hash')
        daily,hourly=matched('daily',True),matched('hourly',False)
        anchor=dict(signal_x=400,source_sha256='daily_hash')
        def bars(interval,count):
            return [dict(open_time_ms=i*interval,close_time_ms=(i+1)*interval,
                         open=100+i*.01,high=102+i*.01,low=99+i*.01,close=101+i*.01)
                    for i in range(count)]
        histories={'1D':bars(DAY,25),'1H':bars(HOUR,25*24)}
        with patch.object(preparation,'load_matched_history',side_effect=lambda c,r,tf:histories[tf]), \
             patch.object(preparation,'time_at_x',return_value=20*DAY) as map_time, \
             patch.object(preparation,'choose_level',return_value=(100,300,'single_existing_blue_level')):
            case=preparation.user_daily_close_case(Path('.'),annotation,daily,hourly,anchor,{'LINK':'test'})
            map_time.assert_called_once_with(daily['fingerprint'],daily,400)
            self.assertEqual(case['decision_time_ms'],21*DAY)
            self.assertEqual(case['state_close_times_ms'],[21*DAY])
            self.assertTrue(case['original_H1_image_excluded_from_event_mapping'])
            self.assertEqual(case['split'],'test')
            original=deepcopy(case)
            for history in histories.values():
                for bar in history:
                    if bar['close_time_ms']>21*DAY:
                        bar.update(open=-999,high=float('inf'),low=-999,close=-999)
            self.assertEqual(original,preparation.user_daily_close_case(
                Path('.'),annotation,daily,hourly,anchor,{'LINK':'test'}))
            with self.assertRaisesRegex(ValueError,'original_daily_extraction_hash_mismatch'):
                preparation.user_daily_close_case(Path('.'),annotation,daily,hourly,
                    dict(anchor,source_sha256='stale'),{'LINK':'test'})

    def test_reviewed_closed_context_excludes_execution_day(self):
        images={'images/1D_58.jpg':'d','images/1H_58.jpg':'h'}
        a=dict(scenario_id=58,direction='long',image_sha256=images)
        def match(sha):return dict(accepted=True,symbol='BTCUSDT',category='linear',
                                  fingerprint={'source_sha256':sha},cache_file=sha,cache_file_sha256=sha)
        def bars(interval,count):
            return [dict(open_time_ms=i*interval,close_time_ms=(i+1)*interval,open=100,high=102,low=99,close=101) for i in range(count)]
        histories={'1D':bars(DAY,25),'1H':bars(HOUR,25*24)}
        context=dict(source_images_sha256=images,closed_daily_anchor_x=300,observation_close_time_ms=21*DAY)
        with patch.object(preparation,'load_matched_history',side_effect=lambda c,r,tf:histories[tf]), \
             patch.object(preparation,'time_at_x',side_effect=lambda fp,m,x:{300:20*DAY,400:21*DAY}[x]), \
             patch.object(preparation,'choose_level',return_value=(100,300,'single_existing_blue_level')):
            case=preparation.user_daily_close_case(Path('.'),a,match('d'),match('h'),
                 dict(signal_x=400,source_sha256='d'),{'BTC':'train'},daily_level_review=context)
            self.assertEqual(case['decision_time_ms'],21*DAY)
            self.assertEqual(case['signal_d1_open_time_ms'],20*DAY)
            self.assertEqual(case['annotated_daily_event_open_time_ms'],21*DAY)
            with self.assertRaisesRegex(ValueError,'daily_context_time_mismatch'):
                preparation.user_daily_close_case(Path('.'),a,match('d'),match('h'),
                    dict(signal_x=400,source_sha256='d'),{'BTC':'train'},
                    daily_level_review=dict(context,observation_close_time_ms=22*DAY))


if __name__=='__main__':
    unittest.main()
