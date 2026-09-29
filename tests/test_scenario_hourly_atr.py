import unittest
from copy import deepcopy

from knowledge_bot.scenario_hourly_atr import closed_hourly_atr,first_hourly_atr_entry,HOUR

START=14*HOUR
CLOSE=24*HOUR


def bar(i,span=10):
    return dict(open_time_ms=START+i*HOUR,close_time_ms=START+(i+1)*HOUR,
                open=100,high=100+span/2,low=100-span/2,close=100)


class HourlyKnowledgeAtrTests(unittest.TestCase):
    def test_excerpt_reuses_newer_passing_bar_instead_of_scanning_older(self):
        history=[bar(i,span) for i,span in enumerate([10,10,10,10,10,10,40,10,10,20])]
        r=closed_hourly_atr(history,CLOSE)
        self.assertEqual(r['value'],10)
        self.assertEqual(r['raw_last_five_mean'],18)
        self.assertEqual(r['selected_bar_open_times_ms'],[history[i]['open_time_ms'] for i in [8,7,7,5,4]])

    def test_failed_previous_bar_returns_raw_mean_including_last_closed(self):
        history=[bar(i,span) for i,span in enumerate([10]*8+[50,10])]
        r=closed_hourly_atr(history,CLOSE)
        self.assertEqual(r['value'],18)
        self.assertIn('failed',r['branch'])

    def test_unfinished_and_future_bars_cannot_change_atr(self):
        history=[bar(i) for i in range(13)]
        before=closed_hourly_atr(history,CLOSE)
        for b in history[10:]: b.update(high=float('inf'),low=-999,close=-999)
        self.assertEqual(before,closed_hourly_atr(history,CLOSE))

    def test_missing_hours_zero_ranges_and_daily_intervals_abstain(self):
        history=[bar(i) for i in range(10)]
        with self.assertRaisesRegex(ValueError,'ten_closed'):
            closed_hourly_atr(history[1:],CLOSE)
        history[3]['open_time_ms']-=HOUR
        history[3]['close_time_ms']-=HOUR
        with self.assertRaisesRegex(ValueError,'gap'):
            closed_hourly_atr(history,CLOSE)
        history=[bar(i) for i in range(10)]
        history[3]=bar(3,0)
        with self.assertRaisesRegex(ValueError,'zero_range'):
            closed_hourly_atr(history,CLOSE)
        history[3]=bar(3)
        history[3]['open_time_ms']-=23*HOUR
        with self.assertRaisesRegex(ValueError,'H1_intervals'):
            closed_hourly_atr(history,CLOSE)

    def test_first_reaching_hour_and_pre_D1_excursion(self):
        history=[bar(i) for i in range(12)]
        history[10].update(high=109,low=99)
        history[11].update(high=111,low=100)
        r=first_hourly_atr_entry(history,CLOSE,100,'long',CLOSE+2*HOUR)
        self.assertEqual(r['decision_time_ms'],CLOSE+HOUR)
        self.assertEqual(r['correction_evidence']['threshold_price'],110)
        self.assertFalse(r['author_exact_execution_hour_verified'])
        self.assertIsNone(r['exact_execution_price'])
        history[11]['high']=109
        with self.assertRaisesRegex(ValueError,'not_reached'):
            first_hourly_atr_entry(history,CLOSE,100,'long',CLOSE+2*HOUR)

    def test_short_and_boundary_already_met_use_correct_side(self):
        history=[bar(i) for i in range(11)]
        history[10].update(low=89)
        r=first_hourly_atr_entry(history,CLOSE,100,'short',CLOSE+HOUR)
        self.assertEqual(r['correction_evidence']['threshold_price'],90)
        self.assertEqual(r['authored_entry_time_window_ms'],[CLOSE,CLOSE+HOUR])
        r=first_hourly_atr_entry(history,CLOSE,115,'short',CLOSE+HOUR)
        self.assertTrue(r['correction_evidence']['condition_already_met_at_boundary'])
        self.assertEqual(r['authored_entry_time_window_ms'],[CLOSE,CLOSE])


if __name__=='__main__':unittest.main()
