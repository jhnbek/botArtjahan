import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'knowledge_bot'))
from chart_level_modes import (typed_review_levels, evidence_markers, feedback_applies_to_mode,
                               marker_tooltip, closed_candle_rows, MODE_TAGS)
from level_discovery import Bar, Level, DiscoveryParams
from chart_level_modes import matched_review_snapshot, active_bsu_rejections


class ChartModeTests(unittest.TestCase):
    def test_all_levels_uses_one_selection_and_keeps_combined_types(self):
        rows = [dict(open_time_ms=i*86400000, open=98, high=100, low=97, close=99, volume=1)
                for i in range(3)]
        levels = [Level(100+i, 0, '1970-01-01', 'support', atr=2, basis_tags=tags)
                  for i, tags in enumerate([
                      ['limit_level', 'mirror_level'], ['paranormal_bar'], ['inflection']])]
        with patch('chart_level_modes.discover_levels', return_value=levels) as discover:
            result = typed_review_levels(rows, 'all_levels')
        discover.assert_called_once()
        self.assertEqual([r['price'] for r in result], [100, 101, 102])
        self.assertEqual(result[0]['chart_title'], 'Зеркальный / Лимитный')
        self.assertEqual(result[1]['chart_title'], 'Паранормальный бар')
        self.assertEqual(result[2]['chart_title'], 'Излом тренда')
        self.assertTrue(all(r['chart_markers'] for r in result))

    def test_prior_origin_breakout_tooltip_identifies_the_actual_protected_level(self):
        point = {'roles': ['Ложный пробой'], 'time': '2026-02-06', 'price': 59807.5,
                 'evidence': [{'role': 'false_breakout', 'context_source': 'prior_origin',
                               'protected_origin_price': 60000.0,
                               'protected_origin_time': '2026-02-05'}]}
        tooltip = marker_tooltip(point)
        self.assertIn('прежнего уровня 60000 от 2026-02-05', tooltip)
        self.assertIn('не касание этой линии', tooltip)
        self.assertIn('уровень не подтверждает и не усиливает', tooltip)

    def test_bsu_restore_replays_only_target_rejection_and_context(self):
        first={'action':'reject_robot_bsu','exchange':'bybit','symbol':'BTCUSDT','interval':'1d',
               'price':100,'bar_open_time_ms':86400000,'recorded_at':'first'}
        newer={**first,'recorded_at':'newer'}
        restore={**first,'action':'restore_robot_bsu','rejected_recorded_at':'first'}
        self.assertEqual(active_bsu_rejections([first,restore]),{})
        self.assertEqual(list(active_bsu_rejections([first,newer,restore]).values()),[newer])
        other={**first,'symbol':'ETHUSDT'}
        self.assertEqual(list(active_bsu_rejections([first,other,restore]).values()),[other])
        self.assertEqual(active_bsu_rejections([other],('bybit','BTCUSDT','1d')), {})
        self.assertEqual(active_bsu_rejections([{**first,'action':'hide_robot_level'}]),{})

    def test_saved_matches_keep_report_prices_within_daily_lifetime(self):
        result=matched_review_snapshot()
        levels=result['inflection_levels']
        self.assertEqual((result['symbol'],result['interval']),('BTCUSDT','1d'))
        self.assertEqual(len(result['bars']),549)
        self.assertEqual(len(levels),13)
        self.assertEqual([v['matched_user_price'] for v in levels],
                         [58042,59081,62205,65705,67293,73768,74900,79388,81787,97963,111968,123742,125849])
        for level in levels:
            self.assertLessEqual(level['match_error_percent'],.1)
            self.assertTrue(level['chart_markers'])
            self.assertEqual(level['source'],'saved_automatic_review')
            for point in level['chart_markers']:
                bar=result['bars'][point['index']]
                self.assertEqual(point['price'],bar['high' if point['kind']=='H' else 'low'])

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
                elif mode == 'mirror_limit':
                    self.assertTrue(level['structure']['strong'])
            if mode=='mirror_limit':
                self.assertTrue(any(len(l['chart_markers'])>2 for l in levels))

    def test_classified_contacts_and_false_breakouts_have_distinct_symbols(self):
        bars = [Bar(i*86400000,98,100,97,99,1) for i in range(7)]
        bars[1] = Bar(86400000,101,103,100,102,1)
        bars[2] = Bar(172800000,101,102,100.05,101,1)
        bars[3] = Bar(259200000,98,105,96,99,1)
        bars[4] = Bar(345600000,99,107,98,104,1)
        bars[5] = Bar(432000000,104,106,98,99,1)
        level = Level(100,0,'1970-01-01','mirror',atr=2)
        level.structure = {'strong': True, 'events': [
            {'index':0,'kind':'H','role':'touch'},
            {'index':1,'kind':'L','role':'touch'},
            {'index':2,'kind':'L','role':'near_touch'},
            {'index':3,'kind':'H','role':'false_breakout'},
            {'index':4,'indices':[4,5],'kind':'H','role':'false_breakout_two_bar',
             'known_time':'1970-01-06', 'reaction_confirmed_time':'1970-01-07'},
        ]}
        points = evidence_markers(bars,level,'mirror_limit',DiscoveryParams())
        self.assertEqual([(p['index'],p['symbol']) for p in points],
                         [(0,'o'),(1,'o'),(2,'o'),(3,'x'),(4,'x'),(5,'x')])
        self.assertIn('БСУ',points[0]['roles'])
        self.assertIn('выход',marker_tooltip(points[4]))
        self.assertIn('возврат',marker_tooltip(points[5]))
        self.assertIn('1970-01-06',marker_tooltip(points[5]))
        self.assertNotIn('1970-01-07',marker_tooltip(points[5]))
        self.assertIn('уровень не подтверждает и не усиливает',marker_tooltip(points[5]))
        self.assertEqual(len(evidence_markers(bars,level,'paranormal',DiscoveryParams())),1)

    def test_automatic_mirror_limit_mode_requires_explicit_strength_profile(self):
        strong = Level(100,0,'1970-01-01','mirror',basis_tags=['mirror_level'],atr=2)
        strong.structure = {'strong':True,'events':[{'index':0,'kind':'H','role':'touch'}]}
        weak = Level(99,0,'1970-01-01','support',basis_tags=['limit_level'],atr=2)
        weak.structure = {'strong':False,'events':[{'index':0,'kind':'L','role':'touch'}]}
        legacy = Level(98,0,'1970-01-01','support',basis_tags=['two_bar_limit'],atr=2)
        rows = [{'open_time_ms':i*86400000,'open':98,'high':100,'low':97,'close':99,'volume':1}
                for i in range(2)]
        with patch('chart_level_modes.discover_levels',return_value=[strong,weak,legacy]):
            actual = typed_review_levels(rows,'mirror_limit')
        self.assertEqual([level['price'] for level in actual],[100])

    def test_closed_prefix_preserves_indices_and_treats_close_boundary_as_closed(self):
        day = 86400000
        rows = [{'open_time_ms':i*day} for i in range(4)]
        self.assertEqual(closed_candle_rows(rows,as_of_ms=3*day+1),rows[:3])
        self.assertEqual(closed_candle_rows(rows,as_of_ms=4*day-1),rows[:3])
        self.assertEqual(closed_candle_rows(rows,as_of_ms=4*day),rows)
        self.assertEqual(closed_candle_rows(rows,as_of_ms=day),rows[:1])
        self.assertEqual(closed_candle_rows(rows[:1],as_of_ms=4*day),[])
        self.assertEqual(closed_candle_rows([],as_of_ms=4*day),[])
        self.assertEqual(len(rows),4)
        with patch('chart_level_modes.discover_levels') as discover:
            self.assertEqual(typed_review_levels(rows[:1],'mirror_limit',as_of_ms=4*day),[])
            discover.assert_not_called()

    @staticmethod
    def forming_bar_fixture():
        rows = [dict(open_time_ms=i*86400000,open=96,high=97,low=95,close=96,volume=1)
                for i in range(26)]
        # Three clean contacts remain eligible for working selection; the
        # departure at index 22 must still be closed before earning strength.
        for i,opening,high,low,close in [(19,99,100,98,99),(20,99,100,98,99),(21,99,100,98,99),
                                       (24,99,102,98,101),(25,101,102,95,96)]:
            rows[i].update(open=opening,high=high,low=low,close=close)
        return rows

    def test_forming_return_bar_cannot_confirm_two_bar_false_breakout(self):
        day = 86400000
        rows = self.forming_bar_fixture()
        before = typed_review_levels(rows,'mirror_limit',as_of_ms=25*day+day//2)
        after = typed_review_levels(rows,'mirror_limit',as_of_ms=26*day)
        first = next(level for level in before if level['price']==100)
        last = next(level for level in after if level['price']==100)
        self.assertFalse(any(e['role']=='false_breakout_two_bar' for e in first['structure']['events']))
        event = next(e for e in last['structure']['events'] if e['role']=='false_breakout_two_bar')
        self.assertEqual(event['indices'],[24,25])
        self.assertFalse(any(p['index']==25 for level in before for p in level['chart_markers']))
        self.assertEqual(len(rows),26)

    def test_forming_reaction_bar_cannot_qualify_strong_level(self):
        day = 86400000
        rows = self.forming_bar_fixture()[:23]
        before = typed_review_levels(rows,'mirror_limit',as_of_ms=22*day+day//2)
        after = typed_review_levels(rows,'mirror_limit',as_of_ms=23*day)
        self.assertNotIn(100,[level['price'] for level in before])
        level = next(level for level in after if level['price']==100)
        self.assertEqual(level['structure']['qualified_index'],22)

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
    def test_main_button_requests_all_types_and_typed_button_keeps_its_filter(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(ui.MainWindow, '_refresh_execution_state'), \
             patch.object(ui.MainWindow, '_refresh_scanner_feed'), \
             patch.object(ui, 'USER_LEVEL_FEEDBACK_PATH', Path(temp)/'feedback.jsonl'):
            window = ui.MainWindow(); window._timer.stop()
            try:
                with patch.object(window, '_on_fetch') as fetch:
                    window.fetch_btn.click()
                    fetch.assert_called_once_with('all_levels')
                    fetch.reset_mock()
                    window.show_mirror_limit_btn.click()
                    fetch.assert_called_once_with('mirror_limit')
            finally:
                window.close()

    def test_restore_recalculates_excluded_live_level_offline_and_preserves_manual_lines(self):
        context = {'exchange': 'bybit', 'symbol': 'BTCUSDT', 'interval': '1d'}
        for mode in ('all_levels', 'mirror_limit', 'paranormal', 'inflection'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp, \
                 patch.object(ui.MainWindow, '_refresh_execution_state'), \
                 patch.object(ui.MainWindow, '_refresh_scanner_feed'), \
                 patch.object(ui, 'build_level_feedback_statistics'):
                journal = Path(temp) / 'feedback.jsonl'
                records = [
                    {**context, 'action': 'hide_robot_level', 'review_mode': mode,
                     'price': price, 'comment': 'Saved removal reason'} for price in (100, 101)
                ] + [{**context, 'action': 'add_manual_level', 'price': 105,
                      'reason_labels': ['Лимитный уровень']}]
                journal.write_text('\n'.join(json.dumps(row) for row in records) + '\n', encoding='utf-8')
                with patch.object(ui, 'USER_LEVEL_FEEDBACK_PATH', journal):
                    window = ui.MainWindow(); window._timer.stop()
                    payload = self.bsu_removal_payload()
                    restored_level = payload['inflection_levels'][0]
                    cutoff = 3 * 86400000 + 43200000
                    payload.update(review_mode=mode, inflection_levels=[], analysis_as_of_ms=cutoff,
                                   inflection_only=mode == 'inflection')
                    try:
                        window._on_analysis_done(payload)
                        self.assertEqual(window._visible_robot_levels, [])
                        # Control edits must not change the chart being restored.
                        window.symbol_combo.setCurrentText('ETHUSDT')
                        review_name = 'inflection_review_levels' if mode == 'inflection' else 'typed_review_levels'
                        with patch.object(ui, 'feed_get_ohlc', side_effect=AssertionError('Restore must be offline')) as fetch, \
                             patch.object(ui, review_name, return_value=[restored_level]) as review, \
                             patch.object(ui.AnalyzeWorker, 'start', new=lambda worker: worker.run()), \
                             patch.object(window, '_on_analysis_failed') as failed:
                            window.restore_robot_levels_btn.click()
                            failed.assert_not_called()
                            fetch.assert_not_called()
                        for call in review.call_args_list:
                            self.assertEqual(call.kwargs['excluded_level_prices'], (100,))
                            self.assertEqual(call.kwargs['as_of_ms'], cutoff)
                            self.assertEqual(call.args[0], payload['bars'])
                        self.assertEqual(window._active_chart_key, ('bybit', 'BTCUSDT', '1d'))
                        self.assertEqual([row['price'] for row in window._visible_robot_levels], [101])
                        self.assertEqual(window._hidden_robot_levels[window._active_chart_key], [100])
                        self.assertEqual(window._manual_level_prices, [105])
                        self.assertEqual(len(window._manual_level_items), 2)
                        saved = [json.loads(line) for line in journal.read_text(encoding='utf-8').splitlines()]
                        self.assertEqual(saved[:-1], records)
                        self.assertEqual(saved[-1]['action'], 'restore_robot_level')
                        self.assertEqual(saved[-1]['review_mode'], mode)
                        self.assertEqual(saved[-1]['price'], 101)
                    finally:
                        window.close()

    def test_restore_previous_bsu_undoes_one_at_a_time_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(ui.MainWindow,'_refresh_execution_state'), patch.object(ui.MainWindow,'_refresh_scanner_feed'), patch.object(ui,'build_level_feedback_statistics'):
            journal=Path(temp)/'feedback.jsonl'
            with patch.object(ui,'USER_LEVEL_FEEDBACK_PATH',journal):
                window=ui.MainWindow();window._timer.stop()
                payload=self.bsu_removal_payload()
                try:
                    window._on_analysis_done(payload)
                    with patch.object(window,'_ask_bsu_removal_reason',return_value='Причина'):
                        for point in payload['inflection_levels'][0]['chart_markers'][:2]:
                            window._reject_bsu_marker(101,point)
                    self.assertEqual(window._rejected_bsu_marks,{(101,86400000),(101,172800000)})
                    with patch.object(window,'_append_level_feedback',side_effect=OSError('test')), patch.object(ui.QtWidgets.QMessageBox,'warning'):
                        window.restore_bsu_btn.click()
                    self.assertEqual(len(window._rejected_bsu_marks),2)
                    window.restore_bsu_btn.click()
                    self.assertEqual(window._rejected_bsu_marks,{(101,86400000)})
                    window.close()
                    window=ui.MainWindow();window._timer.stop()
                    window._on_analysis_done(payload)
                    self.assertEqual(window._rejected_bsu_marks,{(101,86400000)})
                    window.restore_bsu_btn.click()
                    self.assertEqual(window._rejected_bsu_marks,set())
                    self.assertEqual(sum(len(item.points()) for _,item in window._robot_level_items if isinstance(item,ui.pg.ScatterPlotItem)),3)
                    saved=journal.read_bytes()
                    window.restore_bsu_btn.click()
                    self.assertEqual(journal.read_bytes(),saved)
                    records=[json.loads(line) for line in saved.decode('utf-8').splitlines()]
                    self.assertEqual([r['action'] for r in records],['reject_robot_bsu']*2+['restore_robot_bsu']*2)
                finally:
                    window.close()

    def test_real_mouse_click_near_circle_and_overlapping_cross_opens_reason(self):
        from PySide6 import QtTest
        with tempfile.TemporaryDirectory() as temp, patch.object(ui.MainWindow,'_refresh_execution_state'), patch.object(ui.MainWindow,'_refresh_scanner_feed'), patch.object(ui,'USER_LEVEL_FEEDBACK_PATH',Path(temp)/'feedback.jsonl'):
            window=ui.MainWindow();window._timer.stop()
            try:
                payload=self.bsu_removal_payload()
                points=payload['inflection_levels'][0]['chart_markers']
                payload['inflection_levels'][0]['chart_markers']=[points[0],points[2]]
                window._on_analysis_done(payload)
                window.resize(1400,900);window.show()
                window.chart.setRange(xRange=(-50,100),yRange=(97,103),padding=0)
                ui.QtWidgets.QApplication.processEvents()
                position=window.chart.getViewBox().mapViewToScene(ui.QtCore.QPointF(1,101))
                for offset in (18,0):
                    window.remove_bsu_btn.setChecked(True)
                    target=window.chart.mapFromScene(position+ui.QtCore.QPointF(offset,0))
                    with patch.object(window,'_ask_bsu_removal_reason',return_value=None) as ask:
                        QtTest.QTest.mouseClick(window.chart.viewport(),ui.QtCore.Qt.LeftButton,ui.QtCore.Qt.NoModifier,target)
                        ui.QtWidgets.QApplication.processEvents()
                        ask.assert_called_once()
                        self.assertEqual(ask.call_args.args[0],101)
                    self.assertFalse(window.remove_bsu_btn.isChecked())
            finally:
                window.close()

    @staticmethod
    def bsu_removal_payload():
        rows=[{'open_time_ms':i*86400000,'open':99,'high':101,'low':98,'close':100,'volume':1} for i in range(4)]
        points=[{'index':i,'time':f'1970-01-0{i+1}','price':101,'kind':'H','symbol':symbol,'roles':[role]}
                for i,symbol,role in [(1,'o','БСУ'),(2,'o','Касание'),(1,'x','Ложный пробой')]]
        return {'exchange':'bybit','symbol':'BTCUSDT','interval':'1d','bars':rows,'review_mode':'mirror_limit',
                'inflection_levels':[{'price':101,'side':'resistance','basis_tags':['limit_level'],
                    'bsu':{'index':1,'time':'1970-01-02'},'chart_markers':points}]}

    def test_bsu_removal_requires_reason_persists_by_date_and_keeps_line_and_lp(self):
        import copy
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temp, patch.object(ui.MainWindow,'_refresh_execution_state'), patch.object(ui.MainWindow,'_refresh_scanner_feed'), patch.object(ui,'build_level_feedback_statistics'):
            journal=Path(temp)/'feedback.jsonl'
            with patch.object(ui,'USER_LEVEL_FEEDBACK_PATH',journal):
                window=ui.MainWindow();window._timer.stop()
                payload=self.bsu_removal_payload()
                def markers():
                    return [p.data() for _,item in window._robot_level_items if isinstance(item,ui.pg.ScatterPlotItem) for p in item.points()]
                try:
                    window._on_analysis_done(payload)
                    point=payload['inflection_levels'][0]['chart_markers'][0]
                    for reason in (None,'   '):
                        with patch.object(window,'_ask_bsu_removal_reason',return_value=reason):
                            window._reject_bsu_marker(101,point)
                        self.assertEqual(len(markers()),3)
                        self.assertFalse(journal.exists())
                    with patch.object(window,'_ask_bsu_removal_reason',return_value='Движение внутри канала'):
                        window.remove_bsu_btn.click()
                        item=next(item for _,item in window._robot_level_items if isinstance(item,ui.pg.ScatterPlotItem))
                        item.sigClicked.emit(item,[item.points()[0]],SimpleNamespace(button=lambda:ui.QtCore.Qt.LeftButton))
                    self.assertEqual([(p['index'],p['symbol']) for p in markers()],[(2,'o'),(1,'x')])
                    self.assertEqual(len(window._visible_robot_levels),1)
                    self.assertEqual(sum(isinstance(item,ui.pg.InfiniteLine) for _,item in window._robot_level_items),1)
                    record=json.loads(journal.read_text(encoding='utf-8'))
                    self.assertEqual(record['action'],'reject_robot_bsu')
                    self.assertEqual(record['comment'],'Движение внутри канала')
                    self.assertEqual(record['bar_open_time_ms'],86400000)
                    self.assertEqual(record['scope'],'bar_marker_only')
                    window.close()
                    window=ui.MainWindow();window._timer.stop()
                    shifted=copy.deepcopy(payload)
                    shifted['bars'].insert(0,{**payload['bars'][0],'open_time_ms':-86400000})
                    for point in shifted['inflection_levels'][0]['chart_markers']: point['index']+=1
                    window._on_analysis_done(shifted)
                    self.assertEqual([(p['index'],p['symbol']) for p in markers()],[(3,'o'),(2,'x')])
                    window._on_analysis_done({**payload,'symbol':'ETHUSDT'})
                    self.assertEqual(len(markers()),3)
                finally:
                    window.close()

    def test_bsu_dialog_disallows_blank_comment_and_failed_save_keeps_marker(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(ui.MainWindow,'_refresh_execution_state'), patch.object(ui.MainWindow,'_refresh_scanner_feed'), patch.object(ui,'USER_LEVEL_FEEDBACK_PATH',Path(temp)/'feedback.jsonl'):
            window=ui.MainWindow();window._timer.stop()
            payload=self.bsu_removal_payload()
            point=payload['inflection_levels'][0]['chart_markers'][0]
            try:
                window._on_analysis_done(payload)
                states=[]
                def fill():
                    dialog=ui.QtWidgets.QApplication.activeModalWidget()
                    editor=dialog.findChild(ui.QtWidgets.QPlainTextEdit)
                    ok=dialog.findChild(ui.QtWidgets.QDialogButtonBox).button(ui.QtWidgets.QDialogButtonBox.Ok)
                    states.append(ok.isEnabled())
                    editor.setPlainText('  ');states.append(ok.isEnabled())
                    editor.setPlainText('Неверный БСУ');states.append(ok.isEnabled())
                    ok.click()
                ui.QtCore.QTimer.singleShot(0,fill)
                self.assertEqual(window._ask_bsu_removal_reason(101,point),'Неверный БСУ')
                self.assertEqual(states,[False,False,True])
                with patch.object(window,'_ask_bsu_removal_reason',return_value='Причина'), patch.object(window,'_append_level_feedback',side_effect=OSError('test')), patch.object(ui.QtWidgets.QMessageBox,'warning') as warning:
                    window._reject_bsu_marker(101,point)
                    warning.assert_called_once()
                self.assertEqual(window._rejected_bsu_marks,set())
                self.assertEqual(sum(len(item.points()) for _,item in window._robot_level_items if isinstance(item,ui.pg.ScatterPlotItem)),3)
            finally:
                window.close()

    def test_matched_archive_loads_offline_and_omits_expired_bsus(self):
        results=[]
        worker=ui.AnalyzeWorker('ETHUSDT','1h',200,review_mode='matched_review')
        worker.finished_ok.connect(results.append)
        with patch.object(ui,'feed_get_ohlc',side_effect=AssertionError('Archive must not fetch live candles')):
            worker.run()
        self.assertEqual(len(results),1)
        with tempfile.TemporaryDirectory() as temp, patch.object(ui.MainWindow,'_refresh_execution_state'), patch.object(ui.MainWindow,'_refresh_scanner_feed'), patch.object(ui,'USER_LEVEL_FEEDBACK_PATH',Path(temp)/'feedback.jsonl'):
            window=ui.MainWindow();window._timer.stop()
            try:
                with patch.object(ui.AnalyzeWorker,'start') as start:
                    window._on_fetch('matched_review')
                    self.assertEqual(window._worker._review_mode,'matched_review')
                    start.assert_called_once()
                window._on_analysis_done(results[0])
                self.assertEqual(len(window._visible_robot_levels),13)
                labels=[item.toPlainText() for _,item in window._robot_level_items if isinstance(item,ui.pg.TextItem)]
                self.assertEqual(len(labels),13)
                self.assertTrue(all('робот' in label and 'ваша' in label for label in labels))
                self.assertIn('Снимок сравнения',window.analysis_view.toPlainText())
                self.assertFalse(hasattr(window, 'clear_manual_levels_btn'))
                for button in (window.add_manual_level_btn, window.classify_manual_level_btn,
                               window.remove_selected_manual_level_btn, window.restore_robot_levels_btn,
                               window.remove_bsu_btn, window.restore_bsu_btn):
                    self.assertTrue(button.isEnabled())
                price = window._visible_robot_levels[0]['price']
                with patch.object(window, '_ask_level_removal_reason', return_value={'comment': 'test'}), patch.object(ui, 'build_level_feedback_statistics'):
                    window._remove_robot_level_at(price)
                    self.assertEqual(len(window._visible_robot_levels), 12)
                    window._on_analysis_done(results[0])
                    self.assertEqual(len(window._visible_robot_levels), 12)
                    window.restore_robot_levels_btn.click()
                    self.assertEqual(len(window._visible_robot_levels), 13)
                    window._on_analysis_done(results[0])
                    self.assertEqual(len(window._visible_robot_levels), 13)

                self.assertTrue(window.remove_robot_level_btn.isEnabled())
                window._on_analysis_done({'exchange':'bybit','symbol':'BTCUSDT','interval':'1d',
                                          'bars':results[0]['bars'],'inflection_levels':[],
                                          'review_mode':'inflection','inflection_only':True})
                self.assertTrue(window.remove_robot_level_btn.isEnabled())
                self.assertEqual(window._robot_level_items,[])
            finally:
                window.close()

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

    def test_inflection_worker_analyzes_closed_prefix_but_keeps_open_candle_on_chart(self):
        day = 86400000
        rows = ChartModeTests.forming_bar_fixture()[:23]
        instant = 22*day+day//2
        worker = ui.AnalyzeWorker('BTCUSDT','1d',500,review_mode='inflection')
        results = []; errors = []
        worker.finished_ok.connect(results.append); worker.failed.connect(errors.append)
        with patch.object(ui,'feed_get_ohlc',return_value={'bars':rows}), \
             patch.object(ui,'read_records',return_value=[]), \
             patch.object(ui,'discover_levels',return_value=[]) as discover, \
             patch.object(ui,'datetime') as clock:
            clock.now.return_value = datetime.fromtimestamp(instant/1000,tz=timezone.utc)
            worker.run()
        self.assertEqual(errors,[])
        self.assertEqual(len(results),1)
        self.assertEqual(results[0]['bars'],rows)
        self.assertEqual(results[0]['analysis_as_of_ms'],instant)
        self.assertEqual(discover.call_count,2)
        self.assertTrue(all(len(call.args[0])==22 for call in discover.call_args_list))

    def test_chart_draws_two_bar_false_breakout_crosses_next_to_bsu_circle(self):
        rows = [dict(open_time_ms=i*86400000,open=o,high=h,low=l,close=c,volume=1)
                for i,(o,h,l,c) in enumerate([(99,101,98,100),(100,105,99,104),(104,104,98,99)])]
        points = [{'index':i,'price':rows[i]['high'],'time':f'1970-01-0{i+1}',
                   'roles':['БСУ' if i==0 else 'Ложный пробой двумя барами'],
                   'symbol':'o' if i==0 else 'x'} for i in range(3)]
        level = {'price':101,'side':'resistance','bsu':{'index':0},'kb_status':'pass',
                 'basis_tags':['limit_level'],'structure':{'strong':True},'chart_markers':points}
        with tempfile.TemporaryDirectory() as temp, patch.object(ui,'USER_LEVEL_FEEDBACK_PATH',Path(temp)/'feedback.jsonl'), \
             patch.object(ui.MainWindow,'_refresh_execution_state'), patch.object(ui.MainWindow,'_refresh_scanner_feed'):
            window = ui.MainWindow(); window._timer.stop()
            try:
                window._on_analysis_done({'exchange':'bybit','symbol':'BTCUSDT','interval':'1d',
                                          'bars':rows,'inflection_levels':[level],'review_mode':'mirror_limit'})
                markers = [item for _,item in window._robot_level_items if isinstance(item,ui.pg.ScatterPlotItem)]
                self.assertEqual(len(markers),1)
                self.assertEqual(list(markers[0].data['symbol']),['o','x','x'])
                self.assertIn('Крестики',window.analysis_view.toPlainText())
            finally:
                window.close()

    def test_switch_replaces_lines_and_draws_every_evidence_marker(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(ui.MainWindow,'_refresh_execution_state'), patch.object(ui.MainWindow,'_refresh_scanner_feed'):
            journal=Path(temp)/'feedback.jsonl'
            context={'exchange':'bybit','symbol':'BTCUSDT','interval':'1d'}
            records=[
                {**context,'action':'add_manual_level','price':58042.74,'reason_labels':['Излом тренда']},
                {**context,'action':'add_manual_level','price':97963.55,'reason_labels':['Зеркальный']},
                {**context,'action':'add_manual_level','price':126158},
                {**context,'action':'remove_manual_level','price':126158},
                {**context,'symbol':'ETHUSDT','action':'add_manual_level','price':2000},
                {**context,'interval':'1h','action':'add_manual_level','price':60000},
            ]
            journal.write_text('\n'.join(json.dumps(r,ensure_ascii=False) for r in records)+'\n',encoding='utf-8')
            original_journal=journal.read_bytes()
            with patch.object(ui,'USER_LEVEL_FEEDBACK_PATH',journal):
                window=ui.MainWindow();window._timer.stop()
                try:
                    for mode in ['mirror_limit','paranormal','mirror_limit']:
                        levels=typed_review_levels(self.rows,mode)
                        window._on_analysis_done({'exchange':'bybit','symbol':'BTCUSDT','interval':'1d',
                                                  'bars':self.rows,'inflection_levels':levels,'review_mode':mode})
                        self.assertEqual(len(window._visible_robot_levels),len(levels))
                        self.assertEqual(len(window._manual_level_items),4)
                        lines=[item for item in window._manual_level_items if isinstance(item,ui.pg.InfiniteLine)]
                        self.assertEqual([line.value() for line in lines],[58042.74,97963.55])
                        labels=[item.toPlainText() for item in window._manual_level_items if isinstance(item,ui.pg.TextItem)]
                        self.assertTrue(any('Излом тренда' in label for label in labels))
                        self.assertTrue(any('Зеркальный' in label for label in labels))
                        old_items=list(window._manual_level_items)
                        window._draw_manual_levels()
                        self.assertEqual(len(window._manual_level_items),4)
                        self.assertTrue(all(item.scene() is None for item in old_items))
                        markers=[item for _,item in window._robot_level_items if isinstance(item,ui.pg.ScatterPlotItem)]
                        self.assertEqual(len(markers),len(levels))
                        self.assertEqual(sum(len(m.points()) for m in markers),sum(len(l['chart_markers']) for l in levels))
                        actual_symbols=sorted(symbol for marker in markers for symbol in marker.data['symbol'])
                        expected_symbols=sorted(point.get('symbol','o') for level in levels for point in level['chart_markers'])
                        self.assertEqual(actual_symbols,expected_symbols)
                        self.assertTrue(all(l['review_mode']==mode for l in window._visible_robot_levels))
                        self.assertTrue(window.add_manual_level_btn.isEnabled())
                    window._on_analysis_done({'exchange':'bybit','symbol':'BTCUSDT','interval':'1d',
                                              'bars':self.rows,'inflection_levels':[], 'review_mode':'inflection','inflection_only':True})
                    self.assertEqual(window._robot_level_items,[])
                    self.assertEqual(len(window._manual_level_items),4)
                    self.assertTrue(window.add_manual_level_btn.isEnabled())
                    self.assertEqual(journal.read_bytes(),original_journal)
                finally:
                    window.close()
