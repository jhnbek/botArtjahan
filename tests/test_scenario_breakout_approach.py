import unittest

from knowledge_bot.scenario_breakout_approach import (
    assess_long_approach_path, assess_long_first_breakout,
)


class AuthorLongBreakoutApproachTests(unittest.TestCase):
    def test_below_half_permits_only_this_filter_for_any_pullback_flag(self):
        for distance in (0, .2, .499999):
            for flag in (True, False, None):
                with self.subTest(distance=distance, flag=flag):
                    result = assess_long_first_breakout(distance, flag)
                    self.assertIs(result['first_breakout_permitted'], True)
                    self.assertFalse(result['is_unconditional_entry_signal'])
                    self.assertEqual(result['direction'], 'long')

    def test_half_inclusive_to_one_exclusive_is_unspecified(self):
        for distance in (.5, .75, .999999):
            for flag in (True, False, None):
                with self.subTest(distance=distance, flag=flag):
                    result = assess_long_first_breakout(distance, flag)
                    self.assertIsNone(result['first_breakout_permitted'])
                    self.assertEqual(result['reason'], 'half_to_one_H1_ATR_interval_unspecified')

    def test_one_inclusive_requires_confirmed_uninterrupted_approach(self):
        for distance in (1, 1.000001, 3):
            result = assess_long_first_breakout(distance, True)
            self.assertIs(result['first_breakout_permitted'], False)
            self.assertEqual(result['action'], 'skip_first_breakout_wait_for_repeated_setup')
            self.assertIsNone(result['specific_later_breakout_number'])
            self.assertFalse(result['is_unconditional_entry_signal'])
            for flag in (False, None):
                self.assertIsNone(assess_long_first_breakout(distance, flag)['first_breakout_permitted'])

    def test_unknown_distance_never_becomes_permission_or_rejection(self):
        for flag in (True, False, None):
            result = assess_long_first_breakout(None, flag)
            self.assertIsNone(result['first_breakout_permitted'])
            self.assertEqual(result['reason'], 'initial_approach_distance_unknown')
        self.assertNotEqual(assess_long_first_breakout(1, False)['reason'],
                            assess_long_first_breakout(1, None)['reason'])

    def test_invalid_distances_are_rejected_instead_of_changing_permission(self):
        for value in (True, False, float('nan'), float('inf'), float('-inf'), -.001,
                      '0.3', [], {}, 10**1000):
            with self.subTest(value=str(value)[:30]):
                with self.assertRaisesRegex(ValueError, 'initial_distance_to_level_atr'):
                    assess_long_first_breakout(value, True)

    def test_non_boolean_flags_are_rejected_even_when_distance_is_small_or_unknown(self):
        for flag in (0, 1, 'true', '', [], {}):
            for distance in (None, .2, 1):
                with self.subTest(flag=flag, distance=distance):
                    with self.assertRaisesRegex(ValueError, 'uninterrupted_approach_confirmed'):
                        assess_long_first_breakout(distance, flag)


class WholeApproachTests(unittest.TestCase):
    def test_author_one_large_PN_bar_cannot_allow_first_breakout(self):
        result = assess_long_approach_path([100, 102], 102, 1, True)
        self.assertIs(result['first_breakout_permitted'], False)
        self.assertEqual(result['cumulative_net_progress_atr'], 2)
        self.assertTrue(result['level_reached_in_observed_prefix'])

    def test_author_two_bars_of_one_point_one_retain_two_point_two_approach(self):
        for prefix in ([100], [100, 101.1], [100, 101.1, 102.2]):
            result = assess_long_approach_path(prefix, 102.2, 1, True)
            self.assertAlmostEqual(result['initial_distance_to_level_atr'], 2.2)
            self.assertIs(result['first_breakout_permitted'], False)
        self.assertAlmostEqual(result['cumulative_net_progress_atr'], 2.2)

    def test_small_residual_never_replaces_whole_approach_origin(self):
        result = assess_long_approach_path([100, 100.8, 101.72], 102.2, 1, True)
        self.assertAlmostEqual(result['remaining_distance_to_level_atr'], .48)
        self.assertAlmostEqual(result['initial_distance_to_level_atr'], 2.2)
        self.assertIs(result['first_breakout_permitted'], False)
        self.assertFalse(result['level_reached_in_observed_prefix'])
        self.assertFalse(result['is_unconditional_entry_signal'])

    def test_adding_intermediate_prices_does_not_change_the_rule(self):
        direct = assess_long_approach_path([100, 102], 102, 1, True)
        subdivided = assess_long_approach_path([100, 100.2, 100.7, 101.5, 102], 102, 1, True)
        for key in ('first_breakout_permitted', 'initial_distance_to_level_atr',
                    'cumulative_net_progress_atr', 'remaining_distance_to_level_atr'):
            self.assertEqual(direct[key], subdivided[key])

    def test_actual_short_approach_is_permission_only_and_counts_net_displacement(self):
        result = assess_long_approach_path([100, 100.1, 100.2, 100.4], 100.4, 1, True)
        self.assertIs(result['first_breakout_permitted'], True)
        self.assertFalse(result['is_unconditional_entry_signal'])
        self.assertAlmostEqual(result['cumulative_net_progress_atr'], .4)
        self.assertFalse(result['candle_ranges_summed'])

    def test_unknown_or_interrupted_approach_does_not_claim_confirmed_skip(self):
        for flag in (False, None):
            result = assess_long_approach_path([100, 101.5, 101, 102], 102, 1, flag)
            self.assertIsNone(result['first_breakout_permitted'])
            self.assertEqual(result['cumulative_net_progress_atr'], 2)
        self.assertIsNone(assess_long_approach_path([100, 100.75], 100.75, 1, True)
                          ['first_breakout_permitted'])

    def test_invalid_path_or_normalization_is_rejected(self):
        for prices, level, atr in [([], 102, 1), ('100,102', 102, 1),
                                   ([100, True], 102, 1), ([100, float('nan')], 102, 1),
                                   ([100, 102], 102, 0), ([100, 102], 102, True),
                                   ([100, 102], float('inf'), 1), ([100, 102], 99, 1),
                                   ([100, 102], 102, 1e-320), ([0, 102], 102, 1)]:
            with self.subTest(prices=prices, level=level, atr=atr):
                with self.assertRaises(ValueError):
                    assess_long_approach_path(prices, level, atr, True)


if __name__ == '__main__':
    unittest.main()
