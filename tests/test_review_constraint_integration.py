"""Reviewed candidate removals must survive a subsequent live recalculation."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'knowledge_bot'))
from chart_level_modes import (rejected_level_prices, reviewed_level_rejections,
                               working_rejected_level_prices, working_31_review_snapshot)
import desktop_app as ui

KEY = ('bybit', 'BTCUSDT', '1d')
DAY = 86400000


def record(price, mode, action='hide_robot_level', **values):
    return dict(exchange=KEY[0], symbol=KEY[1], interval=KEY[2], price=price,
                review_mode=mode, action=action, **values)


class ReviewConstraintTests(unittest.TestCase):
    def test_restoring_one_review_cannot_cancel_another_active_objection(self):
        records = [record(100, 'manual_candidates'), record(100, 'working_31_review'),
                   record(100, 'manual_candidates', 'restore_robot_level'),
                   record(100, 'mirror_limit', 'restore_robot_level')]
        self.assertEqual(reviewed_level_rejections(records, KEY),
                         {'manual_candidates': (), 'working_31_review': (100,)})
        self.assertEqual(working_rejected_level_prices(records, KEY, 'mirror_limit'), (100,))
        records.append(record(100, 'working_31_review', 'restore_robot_level'))
        self.assertEqual(working_rejected_level_prices(records, KEY, 'mirror_limit'), ())
        records.append(record(100, 'manual_candidates'))
        self.assertEqual(working_rejected_level_prices(records, KEY, 'mirror_limit'), (100,))

    def test_legacy_wrong_inflection_is_not_a_rejection_of_valid_other_types(self):
        records = [record(100, 'inflection', basis_tags=['inflection', 'limit_level']),
                   record(101, None, basis_tags=['inflection', 'mirror_level']),
                   record(102, 'mirror_limit'), record(103, 'working_31_review')]
        self.assertEqual(working_rejected_level_prices(records, KEY, 'mirror_limit'), (102, 103))
        self.assertEqual(working_rejected_level_prices(records, KEY, 'paranormal'), (103,))
        self.assertEqual(working_rejected_level_prices(records, KEY, 'inflection'), (100, 101, 103))

    def test_archive_and_candidate_views_keep_their_own_review_state(self):
        records = [record(100, 'working_31_review'), record(101, 'manual_candidates'),
                   record(102, 'mirror_limit'), record(103, 'matched_review')]
        self.assertEqual(working_rejected_level_prices(records, KEY, 'working_31_review'), (100,))
        self.assertEqual(working_rejected_level_prices(records, KEY, 'manual_candidates'), (101,))
        self.assertEqual(working_rejected_level_prices(records, KEY, 'matched_review'), (103,))
        self.assertEqual(working_rejected_level_prices(records, KEY, 'mirror_limit'), (100, 101, 102))

    def test_context_invalid_prices_manual_and_bar_edits_do_not_become_price_bans(self):
        valid = record(100, 'manual_candidates')
        records = [valid,
                   {**valid, 'price': 101, 'exchange': 'binance'},
                   {**valid, 'price': 102, 'symbol': 'ETHUSDT'},
                   {**valid, 'price': 103, 'interval': '1h'},
                   record(104, 'working_31_review', 'reject_robot_bsu'),
                   record(105, 'working_31_review', 'remove_manual_level'),
                   record(106, 'manual_candidates', 'add_manual_level'),
                   record(float('nan'), 'manual_candidates'),
                   record(float('inf'), 'manual_candidates'),
                   record(-1, 'working_31_review'), {}, None]
        self.assertEqual(working_rejected_level_prices(iter(records), KEY, 'mirror_limit'), (100,))

    def test_actual_twenty_removals_are_constraints_not_a_forced_eleven_level_cap(self):
        review = json.loads((ROOT / '_knowledge_base/manual_reviews/working_31_feedback_20260925/review.json')
                            .read_text(encoding='utf-8'))
        prior = json.loads((ROOT / '_knowledge_base/manual_reviews/bar_rules_20260925/review.json')
                           .read_text(encoding='utf-8'))
        records = [*prior['feedback_records'], *review['changes']]
        removed = {float(r['price']) for r in review['changes'] if r['action'] == 'hide_robot_level'}
        self.assertEqual(len(removed), 20)
        original = {v['price'] for v in working_31_review_snapshot()['inflection_levels']}
        self.assertEqual(len(original), 31)
        self.assertEqual(len(original - removed), 11)
        # Every explicit removal reaches every live working mode; untouched
        # lines and the new manually drawn 117896.72 line are not price bans.
        for mode in ('mirror_limit', 'paranormal', 'inflection'):
            exclusions = set(working_rejected_level_prices(records, KEY, mode))
            self.assertTrue(removed <= exclusions)
            self.assertFalse((original - removed) & exclusions)
            self.assertEqual(len(exclusions), 29)  # 62846.8 removed in both reviews.
        self.assertEqual(set(rejected_level_prices(records, KEY, 'working_31_review')), removed)
        self.assertEqual({v['price'] for v in working_31_review_snapshot()['inflection_levels']}, original)

    def test_live_workers_pass_reviewed_constraints_to_discovery_before_filtering(self):
        records = [record(100, 'working_31_review'), record(101, 'manual_candidates'),
                   record(102, 'inflection', basis_tags=['inflection', 'limit_level'])]
        rows = [dict(open_time_ms=i*DAY, open=100, high=101, low=99, close=100, volume=1)
                for i in range(3)]
        with tempfile.TemporaryDirectory() as temp:
            journal = Path(temp) / 'journal.jsonl'
            journal.write_text(''.join(json.dumps(r) + '\n' for r in records), encoding='utf-8')
            before = journal.read_bytes()
            with patch.object(ui, 'USER_LEVEL_FEEDBACK_PATH', journal), \
                 patch.object(ui, 'feed_get_ohlc', side_effect=AssertionError('Offline review')), \
                 patch.object(ui, 'typed_review_levels', return_value=[]) as typed, \
                 patch.object(ui, 'inflection_review_levels', return_value=[]) as inflections, \
                 patch.object(ui, 'compare_inflection_feedback', return_value={}):
                for mode in ('mirror_limit', 'paranormal', 'inflection'):
                    worker = ui.AnalyzeWorker('BTCUSDT', '1d', 3, review_mode=mode,
                        review_rows=rows, analysis_as_of_ms=3*DAY)
                    results, errors = [], []
                    worker.finished_ok.connect(results.append)
                    worker.failed.connect(errors.append)
                    worker.run()
                    self.assertEqual(errors, [])
                    self.assertEqual(len(results), 1)
                    call = inflections.call_args if mode == 'inflection' else typed.call_args
                    self.assertEqual(call.kwargs['excluded_level_prices'],
                                     (100, 101, 102) if mode == 'inflection' else (100, 101))
            self.assertEqual(journal.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
