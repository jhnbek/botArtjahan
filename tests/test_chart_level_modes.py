import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'knowledge_bot'))
from chart_level_modes import typed_review_levels, evidence_markers, feedback_applies_to_mode, MODE_TAGS
from level_discovery import Bar, Level, DiscoveryParams


class ChartModeTests(unittest.TestCase):
    def test_all_tail_touches_including_adjacent_bars_but_not_body_crossings(self):
        bars=[Bar(0,98,100,97,99,1), Bar(86400000,101,103,100,102,1),
              Bar(172800000,101,102,100.05,101,1), Bar(259200000,95,105,90,104,1)]
        level=Level(100,0,'1970-01-01','mirror',atr=2)
        points=evidence_markers(bars,level,'mirror_limit',DiscoveryParams())
        self.assertEqual([p['index'] for p in points],[0,1,2])
        self.assertEqual([p['price'] for p in points],[100,100,100.05])
        self.assertIn('БСУ',points[0]['roles'])
        self.assertEqual(len(evidence_markers(bars,level,'paranormal',DiscoveryParams())),1)

    def test_modes_filter_the_real_robot_output_and_preserve_marker_coordinates(self):
        fixture=json.loads((Path(__file__).parent/'fixtures/inflection_review_btc_1d.json').read_text(encoding='utf-8'))
        rows=[dict(zip(fixture['columns'],row)) for row in fixture['bars']]
        for mode in MODE_TAGS:
            levels=typed_review_levels(rows,mode)
            self.assertTrue(levels)
            for level in levels:
                self.assertTrue(MODE_TAGS[mode].intersection(level['basis_tags']))
                self.assertTrue(level['chart_markers'])
                for point in level['chart_markers']:
                    bar=rows[point['index']]
                    self.assertEqual(point['price'],bar['high' if point['kind']=='H' else 'low'])
                if mode=='paranormal':
                    self.assertEqual(len(level['chart_markers']),1)
                    self.assertEqual(level['chart_markers'][0]['index'],level['bsu']['index'])
            if mode=='mirror_limit':
                self.assertTrue(any(len(l['chart_markers'])>2 for l in levels))

    def test_feedback_is_scoped_to_the_reviewed_type(self):
        old={'action':'hide_robot_level','basis_tags':['inflection','mirror_level']}
        self.assertTrue(feedback_applies_to_mode(old,'inflection'))
        self.assertFalse(feedback_applies_to_mode(old,'mirror_limit'))
        typed={**old,'review_mode':'mirror_limit'}
        self.assertTrue(feedback_applies_to_mode(typed,'mirror_limit'))
        self.assertFalse(feedback_applies_to_mode(typed,'paranormal'))


os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
try:
    import desktop_app as ui
except ModuleNotFoundError:
    ui=None


@unittest.skipIf(ui is None, 'Optional desktop dependencies are not installed')
class ChartModeGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=ui.QtWidgets.QApplication.instance() or ui.QtWidgets.QApplication([])
        fixture=json.loads((Path(__file__).parent/'fixtures/inflection_review_btc_1d.json').read_text(encoding='utf-8'))
        cls.rows=[dict(zip(fixture['columns'],row)) for row in fixture['bars']]

    def test_buttons_start_their_own_mode_and_recover_after_error(self):
        with patch.object(ui.MainWindow,'_refresh_execution_state'), patch.object(ui.MainWindow,'_refresh_scanner_feed'):
            window=ui.MainWindow();window._timer.stop()
            try:
                for button,mode in [(window.show_mirror_limit_btn,'mirror_limit'),(window.show_paranormal_btn,'paranormal'),(window.show_inflection_btn,'inflection')]:
                    window._worker=None
                    with patch.object(ui,'AnalyzeWorker') as worker, patch.object(ui.QtWidgets.QMessageBox,'warning'):
                        button.click()
                        self.assertEqual(worker.call_args.kwargs['review_mode'],mode)
                        worker.return_value.start.assert_called_once()
                        self.assertFalse(window.show_mirror_limit_btn.isEnabled())
                        self.assertFalse(window.show_paranormal_btn.isEnabled())
                        window._on_analysis_failed('test')
                        self.assertTrue(window.show_mirror_limit_btn.isEnabled())
                        self.assertTrue(window.show_paranormal_btn.isEnabled())
            finally:
                window._worker=None;window.close()

    def test_worker_modes_fetch_once_without_general_analysis(self):
        for mode in MODE_TAGS:
            worker=ui.AnalyzeWorker('BTCUSDT','1d',500,review_mode=mode)
            results=[];errors=[]
            worker.finished_ok.connect(results.append);worker.failed.connect(errors.append)
            with patch.object(ui,'feed_get_ohlc',return_value={'bars':self.rows}) as fetch, patch.object(ui,'build_live_kb_chart_review_packet') as general:
                worker.run()
            self.assertEqual(errors,[])
            self.assertEqual(len(results),1)
            self.assertEqual(results[0]['review_mode'],mode)
            fetch.assert_called_once();general.assert_not_called()

    def test_switch_replaces_lines_and_draws_every_evidence_marker(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(ui.MainWindow,'_refresh_execution_state'), patch.object(ui.MainWindow,'_refresh_scanner_feed'):
            with patch.object(ui,'USER_LEVEL_FEEDBACK_PATH',Path(temp)/'feedback.jsonl'):
                window=ui.MainWindow();window._timer.stop()
                try:
                    for mode in ['mirror_limit','paranormal','mirror_limit']:
                        levels=typed_review_levels(self.rows,mode)
                        window._on_analysis_done({'exchange':'bybit','symbol':'BTCUSDT','interval':'1d',
                                                  'bars':self.rows,'inflection_levels':levels,'review_mode':mode})
                        self.assertEqual(len(window._visible_robot_levels),len(levels))
                        self.assertEqual(window._manual_level_items,[])
                        markers=[item for _,item in window._robot_level_items if isinstance(item,ui.pg.ScatterPlotItem)]
                        self.assertEqual(len(markers),len(levels))
                        self.assertEqual(sum(len(m.points()) for m in markers),sum(len(l['chart_markers']) for l in levels))
                        self.assertTrue(all(l['review_mode']==mode for l in window._visible_robot_levels))
                        self.assertFalse(window.add_manual_level_btn.isEnabled())
                    window._on_analysis_done({'exchange':'bybit','symbol':'BTCUSDT','interval':'1d',
                                              'bars':self.rows,'inflection_levels':[], 'review_mode':'inflection','inflection_only':True})
                    self.assertEqual(window._robot_level_items,[])
                    self.assertTrue(window.add_manual_level_btn.isEnabled())
                finally:
                    window.close()
