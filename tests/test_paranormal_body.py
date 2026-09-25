"""The user-defined 1.6 ATR body gate is shared by live and backtest paths."""
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'knowledge_bot'))
from detector_prototype import (is_paranormal_body, detect_false_breakout_reversal,
                                validate_level_strength, DISABLED_LEVEL_BASIS)
from level_discovery import Bar, DiscoveryParams, is_paranormal
from level_structure import paranormal_bar
from approach_context import ApproachParams, approach_motion
from scn002_spec_driven_backtest import SpecParams, approach_features
from scn002_strict_kb_backtest import Params
from build_level_feedback_statistics import LEVEL_TYPES


class ParanormalBodyTests(unittest.TestCase):
    def test_body_boundary_inclusive_and_symmetric(self):
        for scale in (.001, 1, 1000):
            for sign in (-1, 1):
                with self.subTest(scale=scale, sign=sign):
                    opening=100*scale
                    self.assertTrue(is_paranormal_body(opening,opening+sign*16*scale,10*scale))
                    self.assertFalse(is_paranormal_body(opening,opening+sign*15.999*scale,10*scale))
        self.assertFalse(is_paranormal_body(100,100,10))

    def test_missing_volatility_and_invalid_numbers_cannot_qualify(self):
        for atr in (0,-1,math.inf,math.nan):
            self.assertFalse(is_paranormal_body(100,150,atr))
        self.assertFalse(is_paranormal_body(math.nan,150,10))
        with self.assertRaises(ValueError):
            is_paranormal_body(100,150,10,minimum=1.5)

    def test_wicks_and_old_boolean_flag_do_not_qualify_small_body_in_any_path(self):
        for close,expected in ((101,False),(115.999,False),(116,True),(84,True),(118,True),(125,True),(75,True)):
            with self.subTest(close=close):
                bars=[Bar(i*86400000,100,101,99,100,1) for i in range(20)]
                bars.append(Bar(20*86400000,100,1000,1,close,1))
                self.assertEqual(paranormal_bar(bars,20,10),expected)
                self.assertEqual(is_paranormal(bars,20,10,DiscoveryParams()),expected)
                motion=approach_motion(bars,100,10,'long',True,ApproachParams())
                self.assertEqual(motion['paranormal_bar_to_level'],expected)
                backtest=approach_features(bars,20,100,10,'long',SpecParams())
                self.assertEqual(backtest['paranormal_approach_candle'],expected)
                config={'direction':'long','level_price':100,'atr':10,
                        'paranormal_bar_to_level':True,
                        'candles':[dict(time=str(b.open_time),open=b.open,high=b.high,low=b.low,close=b.close) for b in bars]}
                detection=detect_false_breakout_reversal(config,'TEST')['false_breakout_features']
                self.assertEqual(detection['paranormal_bar_to_level'],expected)
                self.assertAlmostEqual(detection['approach_body_atr'],abs(close-100)/10)
        self.assertEqual(Params().paranormal_atr_mult,1.6)
        self.assertEqual(SpecParams().paranormal_atr_mult,1.6)

    def test_gap_consolidation_and_lp_cannot_create_or_strengthen_levels(self):
        base={'level_price':100,'nearest_level':True,'touch_count':3}
        accepted=validate_level_strength({**base,'basis_tags':['limit_level']},'TEST')
        for disabled in DISABLED_LEVEL_BASIS:
            with self.subTest(disabled=disabled):
                rejected=validate_level_strength({**base,'basis_tags':[disabled]},'TEST')
                self.assertIn('no_structural_level_basis',rejected['hard_rejects'])
                self.assertEqual(rejected['level_validation']['basis_tags'],[])
                mixed=validate_level_strength({**base,'basis_tags':['limit_level',disabled]},'TEST')
                self.assertEqual(mixed['score'],accepted['score'])
                self.assertEqual(mixed['level_validation']['basis_tags'],['limit_level'])
        self.assertNotIn('проторговка',LEVEL_TYPES)
        self.assertNotIn('ложный_пробой',LEVEL_TYPES)


if __name__=='__main__':unittest.main()
