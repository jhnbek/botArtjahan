"""Review nearby alternatives without silently reapplying working selection."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'knowledge_bot'))
from chart_level_modes import active_manual_prices, manual_candidate_levels
from level_discovery import Level
import desktop_app as ui

DAY = 86400000
KEY = ('bybit', 'BTCUSDT', '1d')
ROWS = [dict(open_time_ms=i*DAY, open=99, high=102, low=98, close=100, volume=1)
        for i in range(4)]


def candidate(price):
    return Level(price, 0, '1970-01-01', 'support', atr=2, basis_tags=['limit_level'])


def record(action='add_manual_level', price=100, **values):
    return dict(action=action, price=price, exchange=KEY[0], symbol=KEY[1], interval=KEY[2], **values)


class ManualCandidateTests(unittest.TestCase):
    def test_inclusive_one_percent_all_candidates_not_just_nearest_or_strong(self):
        levels = [candidate(p) for p in (98.999, 99, 99.5, 100, 100.5, 101, 101.001)]
        with patch('chart_level_modes.discover_levels', return_value=levels) as discover:
            actual = manual_candidate_levels(ROWS, [100], as_of_ms=3*DAY,
                                             interval='1d', excluded_contacts=((DAY, 'H', 100),))
        self.assertEqual([r['price'] for r in actual], [99, 99.5, 100, 100.5, 101])
        bars, params = discover.call_args.args
        self.assertEqual(len(bars), 3)  # Forming fourth bar cannot supply evidence.
        self.assertFalse(params.working_selection)
        self.assertEqual(params.excluded_contacts, ((DAY, 'H', 100),))
        self.assertEqual(discover.call_args.kwargs, {'as_of_ms':3*DAY, 'interval':'1d'})
        for report in actual:
            self.assertEqual(report['bsu']['index'], 0)
            self.assertTrue(report['chart_markers'])
            self.assertLessEqual(report['manual_neighbors'][0]['deviation_percent'], 1)

    def test_overlapping_manual_windows_draw_candidate_once_with_all_associations(self):
        with patch('chart_level_modes.discover_levels', return_value=[candidate(100)]):
            actual = manual_candidate_levels(ROWS, [100, 100.5, 100], as_of_ms=4*DAY)
        self.assertEqual(len(actual), 1)
        self.assertEqual([m['manual_price'] for m in actual[0]['manual_neighbors']], [100, 100.5])
        self.assertAlmostEqual(actual[0]['manual_neighbors'][1]['deviation_percent'], .5/100.5*100)

    def test_explicit_candidate_deletion_stays_hidden_even_with_selection_disabled(self):
        with patch('chart_level_modes.discover_levels', return_value=[candidate(100), candidate(100.5)]):
            actual = manual_candidate_levels(ROWS, [100], as_of_ms=4*DAY, excluded_level_prices=(100,))
        self.assertEqual([v['price'] for v in actual], [100.5])

    def test_no_active_manual_lines_does_not_fall_back_to_old_reference_prices(self):
        with patch('chart_level_modes.discover_levels') as discover:
            self.assertEqual(manual_candidate_levels(ROWS, [], as_of_ms=4*DAY), [])
            discover.assert_not_called()

    def test_actual_manual_state_replays_removals_clear_and_context(self):
        other = {**record(price=200), 'symbol':'ETHUSDT'}
        records = [record(), record(price=101), record('remove_manual_level',100), other,
                   record('update_manual_level', 102), record('hide_robot_level',101)]
        self.assertEqual(active_manual_prices(records, KEY), (101,))
        records += [record('clear_manual_levels'), record(price=103.1234)]
        self.assertEqual(active_manual_prices(records, KEY), (103.1234,))

    def test_saved_historical_candidates_use_current_detection_prices_without_manual_snapping(self):
        root = Path(__file__).resolve().parents[1]
        snapshot = json.loads((root/'_knowledge_base/manual_reviews/working_selection_20260924/historical/snapshot.json').read_text(encoding='utf-8'))
        report = json.loads((root/'_knowledge_base/manual_reviews/working_selection_20260924/historical/selection_report.json').read_text(encoding='utf-8'))
        actual = manual_candidate_levels(snapshot['bars'], [85211.738329556, 94000.00132259545, 106655.4495030607],
            as_of_ms=report['history_window']['as_of_ms'], interval='1d',
            excluded_contacts=tuple(tuple(v) for v in report['parameters']['excluded_contacts']))
        prices = {v['price'] for v in actual}
        self.assertTrue({85211.1, 94000, 106773.7} <= prices)
        self.assertTrue(any(abs(price/94000-1)<=.01 for price in prices))
        self.assertTrue(prices.isdisjoint({85211.738329556, 94000.00132259545, 106655.4495030607}))
        self.assertGreater(len(prices), 3)
        for level in actual:
            for marker in level['chart_markers']:
                row = snapshot['bars'][marker['index']]
                self.assertEqual(marker['price'], row['high' if marker['kind']=='H' else 'low'])

    def test_july_first_false_breakout_is_not_a_new_inflection_or_confirmed_bsu(self):
        root = Path(__file__).resolve().parents[1]
        source = root/'_knowledge_base/manual_reviews/working_selection_20260924/historical'
        snapshot = json.loads((source/'snapshot.json').read_text(encoding='utf-8'))
        report = json.loads((source/'selection_report.json').read_text(encoding='utf-8'))
        actual = manual_candidate_levels(snapshot['bars'], [58042.739897758634],
            as_of_ms=report['history_window']['as_of_ms'], interval='1d',
            excluded_contacts=tuple(tuple(v) for v in report['parameters']['excluded_contacts']))
        # The lower wick belongs to a false breakout of the June 25 origin;
        # it cannot establish a separate level even in raw-candidate review.
        self.assertNotIn(57755.8, {v['price'] for v in actual})
        original = next(v for v in actual if v['price']==58042.6)
        self.assertEqual(original['bsu']['time'][:10], '2026-06-25')
        self.assertIn('inflection', original['basis_tags'])
        self.assertNotIn('излом не подтверждён', original['chart_title'])
        july = [p for p in original['chart_markers'] if p['time'][:10]=='2026-07-01']
        self.assertTrue(july)
        self.assertTrue(all(p['symbol']=='x' for p in july))
        for point in july:
            for event in point['evidence']:
                self.assertEqual(event['role'], 'false_breakout')
                self.assertFalse(event['confirms_level'])
                self.assertEqual(event['reaction_atr'], 0)


class CandidateChartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = ui.QtWidgets.QApplication.instance() or ui.QtWidgets.QApplication([])

    def test_button_offline_render_delete_reason_reload_and_restore(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(ui.MainWindow, '_refresh_execution_state'), \
             patch.object(ui.MainWindow, '_refresh_scanner_feed'), \
             patch.object(ui, 'build_level_feedback_statistics'):
            journal = Path(temp)/'feedback.jsonl'
            journal.write_text(json.dumps(record())+'\n', encoding='utf-8')
            with patch.object(ui, 'USER_LEVEL_FEEDBACK_PATH', journal), \
                 patch.object(ui, 'feed_get_ohlc', side_effect=AssertionError('Use displayed candles')), \
                 patch('chart_level_modes.discover_levels', side_effect=lambda *a, **kw:[candidate(100), candidate(100.5)]), \
                 patch.object(ui.AnalyzeWorker, 'start', lambda self:self.run()):
                window = ui.MainWindow()
                window._timer.stop()
                try:
                    window.exchange_combo.setCurrentIndex(window.exchange_combo.findData('bybit'))
                    window.symbol_combo.setCurrentText('BTCUSDT')
                    window.interval_combo.setCurrentText('1d')
                    window._on_analysis_done(dict(exchange='bybit', symbol='BTCUSDT', interval='1d',
                        bars=ROWS, review_mode='mirror_limit', inflection_levels=[], analysis_as_of_ms=3*DAY))
                    window._on_fetch('manual_candidates')
                    self.assertEqual(window._review_mode, 'manual_candidates')
                    self.assertEqual(window._analysis_as_of_ms, 3*DAY)
                    self.assertEqual([v['price'] for v in window._visible_robot_levels], [100,100.5])
                    self.assertEqual(window._manual_level_prices, [100])
                    self.assertTrue(window._manual_level_items)
                    captions = [item.toPlainText() for _,item in window._robot_level_items if isinstance(item,ui.pg.TextItem)]
                    self.assertTrue(all('ваша 100' in c and 'Δ' in c for c in captions))
                    with patch.object(window, '_ask_level_removal_reason', return_value={
                            'reason_codes':['нет_реакции'], 'note':'Слабый отход от уровня'}) as ask:
                        window._remove_robot_level_at(100.5)
                        ask.assert_called_once()
                    saved = [json.loads(line) for line in journal.read_text(encoding='utf-8').splitlines()]
                    self.assertEqual(saved[-1]['review_mode'], 'manual_candidates')
                    self.assertEqual(saved[-1]['note'], 'Слабый отход от уровня')
                    self.assertEqual(saved[-1]['manual_neighbors'][0]['manual_price'], 100)
                    window._on_fetch('manual_candidates')
                    self.assertEqual([v['price'] for v in window._visible_robot_levels], [100])
                    window.restore_robot_levels_btn.click()
                    self.assertEqual([v['price'] for v in window._visible_robot_levels], [100,100.5])
                    self.assertEqual(window._manual_level_prices, [100])
                    self.assertTrue(window.show_mirror_limit_btn.isEnabled())
                finally:
                    window.close()


if __name__ == '__main__':
    unittest.main()
