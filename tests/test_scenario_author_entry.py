import unittest
from knowledge_bot.scenario_author_entry import resolve_author_entry,HOUR,DAY


def candle(hour,opening,high,low,close):
    return dict(open_time_ms=DAY+hour*HOUR,close_time_ms=DAY+(hour+1)*HOUR,
                open=opening,high=high,low=low,close=close)


class AuthorEntryRulesTests(unittest.TestCase):
    def test_immediate_breakout_requires_crossing_after_daily_close(self):
        bars=[candle(-1,101,103,98,101),candle(0,101,102,100,101),candle(1,101,102,99,99)]
        rule=dict(timing='first_level_breakout_after_daily_close',alternative_H1_ATR_allowed=True)
        r=resolve_author_entry(rule,bars,DAY,100,'short',DAY+2*HOUR)
        self.assertEqual(r['decision_time_ms'],DAY+HOUR)
        self.assertEqual(r['entry_resolution'],'pre_hour_proxy_not_exact_intrabar_entry')
        self.assertTrue(r['correction_evidence']['alternative_H1_ATR_allowed'])

    def test_full_bar_requires_both_wicks_beyond_level_and_closed(self):
        bars=[candle(0,99,101,98,99),candle(1,99,100,98,99),candle(2,99,99.9,98,98)]
        rule=dict(timing='first_full_bar_beyond_level_after_daily_close',caption_OR_branch=True)
        r=resolve_author_entry(rule,bars,DAY,100,'short',DAY+3*HOUR)
        self.assertEqual(r['decision_time_ms'],DAY+3*HOUR)
        self.assertEqual(r['entry_resolution'],'closed_hourly_bar')
        self.assertEqual(r['correction_evidence']['confirmation_bar_number'],3)
        self.assertFalse(r['earliest_OR_execution_verified'])

    def test_sixth_confirmation_bar_enters_seventh_not_sixth(self):
        bars=[candle(i,100,101,99,100) for i in range(5)]+[candle(5,99,99.5,98,98)]
        rule=dict(timing='first_full_bar_beyond_level_after_daily_close',confirmation_bar_number=6)
        r=resolve_author_entry(rule,bars,DAY,100,'short',DAY+7*HOUR)
        self.assertEqual(r['authored_entry_time_window_ms'],[DAY+6*HOUR]*2)
        bars[2]=candle(2,99,99.5,98,98)
        with self.assertRaisesRegex(ValueError,'number_mismatch'):
            resolve_author_entry(rule,bars,DAY,100,'short',DAY+7*HOUR)

    def test_author_selected_close_uses_close_not_open_of_hour(self):
        rule=dict(timing='after_author_selected_hourly_close',hour_open_utc=2)
        r=resolve_author_entry(rule,[candle(2,99,101,98,100)],DAY,100,'long',DAY+4*HOUR)
        self.assertEqual(r['decision_time_ms'],DAY+3*HOUR)

    def test_ATR_OR_branch_does_not_claim_exact_author_choice(self):
        bars=[candle(i,100,101,99,100) for i in range(-10,1)]
        bars[-1]['high']=103
        rule=dict(timing='one_hourly_ATR_from_level_after_daily_close',caption_OR_branch=True)
        result=resolve_author_entry(rule,bars,DAY,100,'long',DAY+HOUR)
        self.assertEqual(result['entry_label_source'],'caption_condition_derived_ATR_branch')
        self.assertFalse(result['author_exact_execution_hour_verified'])
        self.assertFalse(result['earliest_OR_execution_verified'])
        self.assertEqual(result['decision_time_ms'],DAY)

    def test_closest_fallback_requires_explicit_author_permission(self):
        bars=[candle(0,103,104,102,103),candle(1,103,104,101.5,103)]
        rule=dict(timing='approach_or_closest_visible_after_daily_close',max_distance_percent=1)
        r=resolve_author_entry(rule,bars,DAY,100,'long',DAY+2*HOUR)
        self.assertEqual(r['decision_time_ms'],DAY+HOUR)
        self.assertTrue(r['correction_evidence']['retrospective_author_label'])
        rule['timing']='first_approach_within_percent_after_daily_close'
        with self.assertRaisesRegex(ValueError,'not_found'):
            resolve_author_entry(rule,bars,DAY,100,'long',DAY+2*HOUR)

    def test_first_hour_window_does_not_claim_its_close_is_entry(self):
        r=resolve_author_entry({'timing':'within_first_hour_after_signal_daily_close'},[],DAY,100,'short',DAY+3*HOUR)
        self.assertEqual(r['decision_time_ms'],DAY)
        self.assertEqual(r['authored_entry_time_window_ms'],[DAY,DAY+HOUR])
        self.assertEqual(r['entry_resolution'],'pre_hour_proxy_not_exact_intrabar_entry')

    def test_entry_at_four_requires_preceding_closed_breakout(self):
        rule=dict(timing='hour_open_after_closed_breakout',hour_utc=4)
        r=resolve_author_entry(rule,[candle(3,99,102,98,101)],DAY,100,'long',DAY+8*HOUR)
        self.assertEqual(r['decision_time_ms'],DAY+4*HOUR)
        with self.assertRaisesRegex(ValueError,'confirmed_breakout'):
            resolve_author_entry(rule,[candle(3,99,100,98,99)],DAY,100,'long',DAY+8*HOUR)

    def test_level_distance_finds_first_hour_without_claiming_exact_fill(self):
        rule=dict(timing='first_approach_within_percent_after_daily_close',max_distance_percent=.5)
        bars=[candle(0,99,99.4,98,99),candle(1,99,99.7,98,99.2)]
        r=resolve_author_entry(rule,bars,DAY,100,'short',DAY+2*HOUR)
        self.assertEqual(r['decision_time_ms'],DAY+HOUR)
        self.assertIsNone(r['exact_execution_price'])
        with self.assertRaisesRegex(ValueError,'not_found'):
            resolve_author_entry(dict(rule,max_distance_percent=.2),bars,DAY,100,'short',DAY+2*HOUR)

    def test_price_movement_is_measured_from_daily_close_not_distance_to_level(self):
        rule=dict(timing='move_toward_level_from_daily_close',move_min_percent=.83,
                  move_max_percent=.83,rounding_tolerance_percentage_points=.005)
        bars=[candle(-1,200,201,199,200),candle(0,200,201,198.344,199)]
        r=resolve_author_entry(rule,bars,DAY,190,'long',DAY+HOUR)
        self.assertEqual(r['decision_time_ms'],DAY)
        self.assertEqual(r['correction_evidence']['origin_price'],200)
        self.assertGreater(100*(198.344/190-1),4)

    def test_retrospective_label_never_claims_live_minimum_detection(self):
        rule=dict(timing='author_selected_closest_of_first_hours',hours_after_daily_close=3)
        bars=[candle(0,99,99.3,98,99),candle(1,99,99.5,98,99),candle(2,99,99.9,98,99)]
        r=resolve_author_entry(rule,bars,DAY,100,'short',DAY+3*HOUR)
        self.assertEqual(r['decision_time_ms'],DAY+2*HOUR)
        self.assertTrue(r['correction_evidence']['retrospective_author_label'])
        self.assertFalse(r['correction_evidence']['executable_live_rule'])
        with self.assertRaisesRegex(ValueError,'incomplete'):
            resolve_author_entry(rule,bars[:2],DAY,100,'short',DAY+3*HOUR)
        bars[0]['high']=99.9
        with self.assertRaisesRegex(ValueError,'not_unique'):
            resolve_author_entry(rule,bars,DAY,100,'short',DAY+3*HOUR)


if __name__=='__main__':unittest.main()
