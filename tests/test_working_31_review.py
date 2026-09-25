"""The 31-level button must show the reported set, independently of live feeds."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'knowledge_bot'))
from chart_level_modes import working_31_review_snapshot
import desktop_app as ui


class Working31ReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = ui.QtWidgets.QApplication.instance() or ui.QtWidgets.QApplication([])

    def test_saved_set_matches_original_report_and_markers_match_original_candles(self):
        result = working_31_review_snapshot()
        constraints = tuple(tuple(v) for v in result['provenance']['excluded_contacts'])
        prices = {lv['price'] for lv in result['inflection_levels']}
        self.assertEqual(len(prices), 31)
        self.assertNotIn(57755.8, prices)
        self.assertIn(58042.6, prices)
        self.assertEqual(len(result['bars']), 549)
        self.assertEqual(len(constraints), 20)
        for level in result['inflection_levels']:
            self.assertTrue(level['structure']['strong'])
            self.assertTrue({'mirror_level', 'limit_level', 'two_bar_limit'} & set(level['basis_tags']))
            self.assertTrue(level['chart_markers'])
            for marker in level['chart_markers']:
                row = result['bars'][marker['index']]
                self.assertEqual(marker['price'], row['high' if marker['kind'] == 'H' else 'low'])
                self.assertEqual(marker['time'][:10], row['open_time'][:10])
        root = Path(__file__).resolve().parents[1]
        report = json.loads((root/result['provenance']['source_report']).read_text(encoding='utf-8'))
        all_selected = {v['price'] for v in report['general_audit'] if v['decision'] == 'kept'}
        self.assertTrue(prices <= all_selected)
        self.assertEqual(len(prices), report['counts']['general_selected_mirror_limit'])

    def test_archive_offline_count_manual_lines_and_persisted_corrections(self):
        result = working_31_review_snapshot()
        price = result['inflection_levels'][0]['price']
        base = dict(exchange='bybit', symbol='BTCUSDT', interval='1d')
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(ui.MainWindow, '_refresh_execution_state'), \
             patch.object(ui.MainWindow, '_refresh_scanner_feed'), \
             patch.object(ui, 'build_level_feedback_statistics'):
            journal = Path(temp)/'feedback.jsonl'
            old_records = [dict(base, action='add_manual_level', price=58043),
                dict(base, action='hide_robot_level', price=price),
                dict(base, action='hide_robot_level', price=price, review_mode='mirror_limit')]
            journal.write_text(''.join(json.dumps(v)+'\n' for v in old_records), encoding='utf-8')
            with patch.object(ui, 'USER_LEVEL_FEEDBACK_PATH', journal), \
                 patch.object(ui, 'feed_get_ohlc', side_effect=AssertionError('Saved view must stay offline')), \
                 patch.object(ui, 'build_live_kb_chart_review_packet', side_effect=AssertionError('No new analysis')), \
                 patch.object(ui.AnalyzeWorker, 'start', lambda self:self.run()):
                window = ui.MainWindow()
                window._timer.stop()
                try:
                    window.symbol_combo.setCurrentText('ETHUSDT')
                    window.interval_combo.setCurrentText('1h')
                    window._on_fetch('working_31_review')
                    self.assertFalse(hasattr(window, 'show_working_31_btn'))
                    self.assertEqual(window._active_chart_key, ('bybit', 'BTCUSDT', '1d'))
                    self.assertEqual(window._review_mode, 'working_31_review')
                    self.assertEqual(len(window._visible_robot_levels), 31)
                    self.assertEqual(window._manual_level_prices, [58043])
                    self.assertTrue(window._manual_level_items)
                    self.assertEqual(journal.read_text(encoding='utf-8').count('\n'), 3)
                    self.assertIn('31', window.inflection_info.text())
                    self.assertIn('до применения удалений', window.analysis_view.toPlainText())
                    labels = [v.toPlainText() for _,v in window._robot_level_items if isinstance(v,ui.pg.TextItem)]
                    self.assertEqual(len(labels), 31)
                    self.assertTrue(all('робот' in v for v in labels))
                    points = [p for _,v in window._robot_level_items if isinstance(v,ui.pg.ScatterPlotItem)
                              for p in v.points()]
                    self.assertEqual(len(points), sum(len(v['chart_markers']) for v in result['inflection_levels']))
                    for button in (window.show_mirror_limit_btn, window.show_paranormal_btn,
                                   window.remove_bsu_btn, window.restore_bsu_btn,
                                   window.remove_robot_level_btn, window.restore_robot_levels_btn,
                                   window.add_manual_level_btn):
                        self.assertTrue(button.isEnabled())
                    with patch.object(window, '_ask_level_removal_reason', return_value={
                            'reason_codes':['нет_реакции'], 'note':'Нет сильной реакции'}) as ask:
                        window._remove_robot_level_at(price)
                        ask.assert_called_once()
                    records = [json.loads(line) for line in journal.read_text(encoding='utf-8').splitlines()]
                    self.assertEqual(records[-1]['review_mode'], 'working_31_review')
                    self.assertEqual(records[-1]['note'], 'Нет сильной реакции')
                    self.assertEqual(len(window._visible_robot_levels), 30)
                    window._on_fetch('working_31_review')
                    self.assertEqual(len(window._visible_robot_levels), 30)
                    window.restore_robot_levels_btn.click()
                    self.assertEqual(len(window._visible_robot_levels), 31)
                    window._on_fetch('working_31_review')
                    self.assertEqual(len(window._visible_robot_levels), 31)
                    window._set_analysis_buttons_enabled(False)
                    self.assertFalse(window.show_mirror_limit_btn.isEnabled())
                    with patch.object(ui.QtWidgets.QMessageBox, 'warning'):
                        window._on_analysis_failed('test')
                    self.assertTrue(window.show_mirror_limit_btn.isEnabled())
                    with patch.object(ui.AnalyzeWorker, 'start'):
                        window.show_mirror_limit_btn.click()
                        self.assertEqual(window._worker._review_rows, result['bars'])
                        self.assertEqual(window._worker._analysis_as_of_ms, result['analysis_as_of_ms'])
                        self.assertEqual(window._worker._review_mode, 'mirror_limit')
                    # Opening the new view never clears another mode's deletions.
                    window._review_mode = 'mirror_limit'
                    window._load_persisted_level_state(window._active_chart_key)
                    self.assertIn(price, window._hidden_robot_levels[window._active_chart_key])
                finally:
                    window.close()


if __name__ == '__main__':
    unittest.main()
