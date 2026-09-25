import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'knowledge_bot'))
from detector_prototype import Candle, detect_sweep, validate_level_strength, validate_tbx_entry_model


class LevelStrengthTests(unittest.TestCase):
    def test_one_bar_false_breakout_opens_and_closes_on_original_side(self):
        examples = [
            ('long', Candle('support', 1050, 1060, 950, 1020)),
            ('short', Candle('resistance', 950, 1050, 940, 980)),
        ]
        for direction, candle in examples:
            with self.subTest(direction=direction):
                sweep, returned, tail = detect_sweep(candle, direction, 1000, 2)
                self.assertTrue(sweep)
                self.assertTrue(returned)
                self.assertEqual(tail, 70)

    def test_body_crossing_from_other_side_is_not_one_bar_false_breakout(self):
        examples = [
            ('long', Candle('cross_up', 980, 1060, 950, 1020)),
            ('short', Candle('cross_down', 1020, 1050, 940, 980)),
            ('long', Candle('open_at_level', 1000, 1060, 950, 1020)),
            ('short', Candle('open_at_level', 1000, 1050, 940, 980)),
        ]
        for direction, candle in examples:
            with self.subTest(direction=direction, candle=candle.time):
                sweep, returned, _ = detect_sweep(candle, direction, 1000, 2)
                self.assertFalse(sweep)
                self.assertFalse(returned)

    def test_one_bar_false_breakout_requires_strict_return_and_existing_luft(self):
        for direction, candle in [
            ('long', Candle('close_at_level', 1050, 1060, 950, 1000)),
            ('short', Candle('close_at_level', 950, 1050, 940, 1000)),
        ]:
            with self.subTest(direction=direction):
                sweep, returned, _ = detect_sweep(candle, direction, 1000, 2)
                self.assertTrue(sweep)
                self.assertFalse(returned)
        near_level = Candle('inside_existing_luft', 1050, 1060, 999, 1020)
        self.assertEqual(detect_sweep(near_level, 'long', 1000, 2)[:2], (False, False))

    def test_confirmed_limit_type_is_a_structural_basis(self):
        config={'level_price':100,'nearest_level':True,'touch_count':3}
        limit=validate_level_strength({**config,'basis_tags':['limit_level']},'TEST')
        legacy=validate_level_strength({**config,'basis_tags':['two_bar_limit']},'TEST')
        self.assertNotIn('no_structural_level_basis',limit['hard_rejects'])
        self.assertEqual(limit['score'],legacy['score'])

    def test_false_breakout_count_never_promotes_a_weak_level(self):
        config = {'level_price': 100, 'nearest_level': True,
                  'basis_tags': ['mirror_level'], 'touch_count': 1}
        baseline = validate_level_strength(config, 'TEST')
        self.assertEqual(baseline['status'], 'warn')
        for count in (1, 2, 100):
            with self.subTest(count=count):
                result = validate_level_strength(
                    {**config, 'false_breakout_count': count}, 'TEST')
                for key in ('score', 'status', 'strength_factors', 'hard_rejects'):
                    self.assertEqual(result[key], baseline[key])
                self.assertEqual(result['level_validation']['false_breakout_count'], count)

    def test_false_breakout_tail_alone_cannot_create_a_level(self):
        result = validate_level_strength({
            'level_price': 100, 'nearest_level': True,
            'basis_tags': ['long_false_breakout_tail'], 'false_breakout_count': 3,
        }, 'TEST')
        self.assertEqual(result['status'], 'reject')
        self.assertIn('no_structural_level_basis', result['hard_rejects'])
        self.assertEqual(result['level_validation']['structural_basis_count'], 0)

    def test_legacy_false_breakout_tag_does_not_strengthen_valid_structure(self):
        config = {'level_price': 100, 'nearest_level': True,
                  'basis_tags': ['mirror_level', 'two_bar_limit'], 'touch_count': 3}
        baseline = validate_level_strength(config, 'TEST')
        result = validate_level_strength({
            **config, 'basis_tags': config['basis_tags'] + ['long_false_breakout_tail'],
            'false_breakout_count': 2,
        }, 'TEST')
        self.assertEqual(baseline['status'], 'pass')
        for key in ('score', 'status', 'strength_factors', 'hard_rejects'):
            self.assertEqual(result[key], baseline[key])

    def test_false_breakout_remains_an_entry_indicator_requiring_return(self):
        config = {'direction': 'short', 'daily_scenario_valid': True,
                  'stop_defined': True, 'stop_size_atr': 0.1, 'room_to_target_r': 4,
                  'lp_sweep': True, 'returned_beyond_level': True}
        for model in ('false_breakout_return', 'false_breakout_stop_market'):
            with self.subTest(model=model):
                valid = validate_tbx_entry_model({**config, 'entry_model': model}, 'TEST')
                self.assertEqual(valid['status'], 'pass')
                missing_return = validate_tbx_entry_model({
                    **config, 'entry_model': model, 'returned_beyond_level': False,
                }, 'TEST')
                self.assertEqual(missing_return['status'], 'reject')
                self.assertIn('lp_entry_without_return', missing_return['hard_rejects'])


if __name__ == '__main__':
    unittest.main()
