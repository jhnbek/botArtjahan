import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'knowledge_bot'))
from build_level_feedback_statistics import compare_inflection_feedback, build_statistics


class FeedbackComparisonTests(unittest.TestCase):
    def record(self, action, price=100, **extra):
        return dict(action=action, price=price, exchange='bybit', symbol='BTCUSDT', interval='1d', **extra)

    def compare(self, records):
        return compare_inflection_feedback([{'price':100}, {'price':200}], records, 'bybit', 'BTCUSDT', '1d')

    def test_latest_type_applies_without_duplicate_or_implicit_approval(self):
        records=[self.record('add_manual_level', reason_codes=['лимитный']),
                 self.record('update_manual_level', reason_codes=['излом_тренда'])]
        result=self.compare(records)
        self.assertEqual(result['expected_inflections'][0]['status'], 'matched')
        self.assertEqual(result['unconfirmed_robot_levels'], [200])
        stats=build_statistics(records)
        self.assertEqual(stats['active_positive_count'],1)
        self.assertEqual(stats['calibration_examples'][0]['expected_level_types'],['inflection'])

    def test_removed_labels_and_other_timeframes_do_not_apply(self):
        records=[self.record('add_manual_level', reason_codes=['излом_тренда']),
                 self.record('remove_manual_level')]
        records.append({**self.record('add_manual_level', reason_codes=['излом_тренда']), 'interval':'1h'})
        self.assertEqual(self.compare(records)['expected_inflections'],[])

    def test_restored_rejection_is_not_negative(self):
        records=[self.record('hide_robot_level', review_mode='inflection'),self.record('restore_robot_level')]
        self.assertEqual(self.compare(records)['repeated_rejections'],[])

    def test_secondary_limit_never_counts_as_an_inflection(self):
        records=[self.record('add_manual_level',reason_codes=['излом_тренда']),
                 self.record('hide_robot_level',price=200,review_mode='inflection')]
        levels=[{'price':100,'basis_tags':['limit_level']},
                {'price':200,'basis_tags':['limit_level']}]
        result=compare_inflection_feedback(levels,records,'bybit','BTCUSDT','1d')
        self.assertEqual(result['expected_inflections'][0]['status'],'missing')
        self.assertEqual(result['repeated_rejections'],[])
        self.assertEqual(result['unconfirmed_robot_levels'],[])

    def test_other_mode_hide_and_restore_do_not_change_inflection_review(self):
        records=[self.record('hide_robot_level',review_mode='inflection'),
                 self.record('hide_robot_level',review_mode='mirror_limit',basis_tags=['inflection','mirror_level']),
                 self.record('restore_robot_level',review_mode='mirror_limit')]
        self.assertEqual(len(self.compare(records)['repeated_rejections']),1)
        self.assertEqual(self.compare(records[1:])['repeated_rejections'],[])
