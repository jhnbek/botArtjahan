"""Portable coverage review of saved market examples; no fitting or image edits."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
from html import escape
import json
import os
from pathlib import Path
from urllib.parse import quote

from .scenario_corpus_scope import user_scope
from .prepare_scenario_training import load_reviews

COLLECTION = Path(__file__).resolve().parents[1] / "_knowledge_base/manual_reviews/scenarios_dzhahan_20260925"
REASONS = {
    "reviewed_entry_with_causal_market_OHLC": "Числовой пример подготовлен",
    "daily_market_match_pending": "Нужно подтвердить совпадение дневки с рынком",
    "hourly_market_match_pending": "Нужно подтвердить совпадение часовика с рынком",
    "entry_semantics_unresolved_in_review": "Нужно уточнить чтение авторской ТВХ",
    "direction_or_entry_interpretation_pending": "Нужно уточнить прочтение направления или входа",
    "daily_event_is_unclosed_requires_semantic_review": "Нужно определить роль отмеченного дневного бара",
    "entry_before_confirmed_author_daily_signal": "Отмеченный вход предшествует закрытию сигнальной дневки",
    "daily_and_hourly_event_window_disagree": "Нужно согласовать события D1 и H1",
    "daily_event_anchor_pending": "Нужно определить отмеченный дневной бар",
    "multiple_blue_levels_require_working_level_review": "Нужно выбрать авторский рабочий уровень",
    "working_level_differs_between_D1_and_H1": "Нужно согласовать рабочий уровень D1 и H1",
    "entry_decision_timing_unresolved": "Не определён момент принятия решения",
    "entry_arrow_review_pending": "Не проверена отметка входа",
    "daily_arrow_role_awaits_user_clarification": "Ожидается уточнение автора о дневном сигнале и ТВХ",
    "awaiting_author_TVX_after_verified_D1_close": "Нужна ТВХ от автора после фактического закрытия D1",
    "awaiting_author_TVX_after_actual_D1_close": "Нужна ТВХ от автора после фактического закрытия D1",
    "explicit_author_corrected_D1_close_entry": "Вход после закрытия D1 прямо уточнён автором",
}


def _read(path):
    return json.loads(path.read_text(encoding="utf8"))


def _lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf8").splitlines() if line.strip()]


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _text(value):
    return escape(str(value), quote=True)


def _url(path, parent):
    return quote(Path(os.path.relpath(path, parent)).as_posix(), safe="/.")


def _utc(value):
    if value is None:
        return "Не подтверждено"
    return datetime.fromtimestamp(int(value) / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _link(path, parent, label):
    return f'<a href="{_url(path, parent)}">{_text(label)}</a>'


def _image(collection, destination, sid, timeframe, timing=None, dimensions=None):
    path = collection / f"images/{timeframe}_{sid}.jpg"
    if not path.is_file():
        return '<p class="warning">Оригинал отсутствует.</p>'
    url = _url(path, destination.parent)
    raster = f'<img loading="lazy" src="{url}" alt="Авторский сценарий {sid}, {timeframe}">'
    if timeframe == "1H" and timing and dimensions:
        width, height = dimensions["width"], dimensions["height"]
        x = timing.get("last_daily_hour_bar_x")
        if x is not None and 0 <= x < width:
            raster = (f'<div class="chart-overlay">{raster}<svg viewBox="0 0 {width} {height}" aria-label="Последний час перед фактическим закрытием D1; это не новая ТВХ">'
                      f'<line x1="{x}" x2="{x}" y1="0" y2="{height}" stroke="#a315a9" stroke-width="3" stroke-dasharray="7 5"/>'
                      '</svg></div><p class="muted">Фиолетовый пунктир — последний час перед фактическим закрытием D1 (23:00–00:00 UTC). Это ориентир для проверки, не новая ТВХ. Оригинал не изменён.</p>')
    return (f'<figure><figcaption>{timeframe} · <a href="{url}" target="_blank" rel="noopener">Открыть оригинал</a></figcaption>{raster}</figure>')


def _timing_facts(question):
    if not question:
        return ""
    exact = question.get("original_entry_resolution") == "after_hour_close"
    hour = ("После закрытия H1: " + str(question.get("original_entry_hour_close_utc", "—")) if exact
            else "Внутри H1: " + str(question.get("original_entry_hour_open_utc", "—")) + " — " + str(question.get("original_entry_hour_close_utc", "—")) + "; точная минута неизвестна")
    return ('<aside class="warning"><strong>Нужна новая ТВХ от автора.</strong> Отмеченный вход расположен раньше закрытия сигнальной дневки. '
            'Фактическая граница дневки проверена по рынку. Замена входа не сгенерирована.</aside><dl>'
            f'<dt>Исходная отметка входа / UTC</dt><dd>{_text(hour)}</dd>'
            f'<dt>Фактическое закрытие сигнальной D1 / UTC</dt><dd>{_text(_utc(question.get("required_daily_close_time_ms")))}</dd>'
            f'<dt>То же закрытие / Москва</dt><dd>{_text(question.get("required_daily_close_moscow", "—"))}</dd>'
            f'<dt>Последний H1 перед закрытием D1, x в оригинале</dt><dd>{_text(question.get("last_daily_hour_bar_x", "—"))}</dd></dl>')


def _timing_summary(questions, collection, destination):
    if not questions:
        return ""
    rows = []
    for sid, question in sorted(questions.items()):
        interval = (str(question.get("original_entry_hour_open_utc", "—")) + " — " + str(question.get("original_entry_hour_close_utc", "—")))
        resolution = "Вход после закрытия этого часа" if question.get("original_entry_resolution") == "after_hour_close" else "Вход внутри этого часа; точная минута неизвестна"
        links = " · ".join(_link(collection / f"images/{tf}_{sid}.jpg", destination.parent, tf) for tf in ("1D", "1H"))
        rows.append(f'<tr><th><a href="#scenario-{sid}">№{sid}</a><small>{_text(question.get("symbol", "—"))}</small></th>'
                    f'<td>{_text(interval)}<small>{resolution}</small></td><td>{_text(_utc(question.get("required_daily_close_time_ms")))}'
                    f'<small>Москва: {_text(question.get("required_daily_close_moscow", "—"))}</small></td>'
                    f'<td>{_text(question.get("last_daily_hour_bar_x", "—"))}<small><a href="#scenario-{sid}">Показать бар на H1</a></small></td><td>{links}</td></tr>')
    return (f'<section id="timing-questions"><h2>Нужно уточнить ТВХ: {len(questions)} сценариев</h2>'
            '<p>В этих сценариях исходная ТВХ находится раньше фактического закрытия сигнальной дневки. По просьбе автора показана правильная граница D1. '
            'Новый вход должен указать автор; эти случаи ожидают уточнения. Нажмите номер: в часовике фиолетовым пунктиром отмечен последний час дневки, без изменения оригинала.</p>'
            '<div class="table-wrap"><table><thead><tr><th>Сценарий</th><th>Час исходной ТВХ / UTC</th><th>Фактическое закрытие D1</th><th>Последний H1, x</th><th>Оригиналы</th></tr></thead><tbody>'
            + ''.join(rows) + '</tbody></table></div></section>')


def _case_facts(case):
    if not case:
        return '<p class="muted">Время входа и закрытие сигнала пока не подтверждены для допуска числового примера.</p>'
    proxy = case["entry_resolution"] != "closed_hourly_bar"
    resolution = ("Приближение перед отмеченным часом: точная минута входа внутри него ещё не восстановлена."
                  if proxy else "Состояние после закрытия отмеченного часа. Точная цена исполнения не восстановлена.")
    if case.get('original_H1_image_excluded_from_event_mapping'):
        resolution += " Автор уточнил вход сразу после D1. Приложенный H1 относится к другому событию; для признаков загружены правильные рыночные часы."
    if case.get('entry_label_source') == 'caption_condition_derived_closed_branch':
        resolution += " Использована разрешённая в подписи ветка с закрытым баром. Более раннее выполнение альтернативы ATR не исключено."
    if case.get('entry_label_source') == 'caption_condition_derived_ATR_branch':
        resolution += " Использована разрешённая исходной подписью ветка ATR. Это не подтверждение того, какую из альтернатив автор выбрал первой; состояния ожидания не добавлены."
    facts = [
        ("Источник рынка", f'{case.get("market_symbol", "—")} / {case.get("market_category", "—")} / Bybit'),
        ("Состояние признаков / UTC", _utc(case.get("decision_time_ms"))),
        ("Последнее фактическое закрытие D1 / UTC", _utc(case.get("latest_closed_d1_time_ms"))),
        ("Начало сигнального D1 / UTC", _utc(case.get("signal_d1_open_time_ms"))),
        ("Авторская отметка закрытия D1 на H1 / UTC", _utc(case.get("authored_daily_close_marker_time_ms"))),
        ("Смещение авторской отметки от закрытия UTC, часов", case.get("marker_offset_from_UTC_daily_close_hours", "—")),
        ("Рабочий уровень, восстановленный по рисунку", f'{case["level"]:.8g}' if case.get("level") is not None else "—"),
        ("Опорная цена закрытого H1, не цена исполнения", f'{case["reference_entry_price"]:.8g}' if case.get("reference_entry_price") is not None else "—"),
        ("Разбиение", case.get("split", "—")),
        ("Состояний ожидания", max(0, len(case.get("state_close_times_ms", [])) - 1)),
    ]
    if case.get('authored_entry_time_window_ms'):
        title = 'Расчётное окно ТВХ / UTC' if case.get('correction_evidence', {}).get('hourly_atr') else 'Авторское окно ТВХ / UTC'
        facts.append((title, ' — '.join(_utc(t) for t in case['authored_entry_time_window_ms'])))
    atr = case.get('correction_evidence', {}).get('hourly_atr')
    if atr:
        facts.extend([('Расчётный ATR H1', f'{atr["value"]:.8g}'),
                      ('ATR известен после закрытия / UTC', _utc(atr['as_of_close_time_ms'])),
                      ('Порог 1 ATR от уровня', f'{case["correction_evidence"]["threshold_price"]:.8g}'),
                      ('Метод ATR', atr['method'])])
        resolution += ' Час вычислен из разрешённого условия на 1 H1 ATR по выбранной реализации индикатора из базы (5 / 150% / 50%). Автор подтвердил условие, но отдельно этот час не указывал. ATR использует только предшествующие закрытые H1.'
    if case.get('correction_evidence', {}).get('retrospective_author_label'):
        resolution += ' Ближайший час выбран ретроспективно в указанном автором окне; это учебная метка, а не исполнимое правило поиска будущего минимума. Признаки ограничены началом выбранного часа.'
    return ('<p class="note">' + _text(resolution) + '</p><dl>'
            + ''.join(f'<dt>{_text(k)}</dt><dd>{_text(v)}</dd>' for k, v in facts)
            + '</dl><p class="muted">Опорная цена и время состояния не являются подтверждённым исполнением сделки. '
              'Смещение авторской отметки показано отдельно от фактической границы UTC.</p>')


def _matched_text(match):
    if not match:
        return "Нет сопоставления"
    return ("Подтверждено" if match.get("accepted") else "Ожидает проверки") + ": " + str(match.get("reason", "—"))


def _training_summary(path, preparation_path, cases_path, destination):
    """Show saved metrics only when their exact prepared dataset still matches."""
    if not path.is_file():
        return '<p class="note">Отчёт обучения рыночных моделей ещё не сохранён. Число подготовленных примеров выше не означает, что на них уже обучены веса.</p>', {}
    report = _read(path)
    provenance = report.get("provenance", {})
    current = (provenance.get("preparation_report_sha256") == _hash(preparation_path)
               and provenance.get("cases_sha256") == _hash(cases_path))
    link = _link(path, destination.parent, "Сохранённый отчёт обучения")
    if not current:
        return f'<p class="warning">{link} относится к другой версии подготовленных примеров. Его метрики не показаны как результат текущего набора.</p>', {}
    if report.get("trained") is not True:
        return f'<p class="note">{link}: завершённое обучение не подтверждено.</p>', {}
    direction, entry = report.get("direction", {}), report.get("entry", {})
    dm, em = direction.get("held_out_test", {}), entry.get("held_out_test", {})
    dc, ec = dm.get("confusion_true_rows_predicted_columns", {}), em.get("confusion", {})
    direction_errors = sum(dc.get(a, {}).get(b, 0) for a, b in (("long", "short"), ("short", "long")))
    entry_misses, premature = ec.get("entry_as_wait", 0), ec.get("wait_as_entry", 0)
    entry_total = ec.get("entry_as_entry", 0) + entry_misses
    wait_total = ec.get("wait_as_wait", 0) + premature
    row_notes = {}
    for row in dm.get("details", []):
        sid = int(row["scenario_id"])
        right = row.get("author_direction") == row.get("predicted_direction")
        row_notes.setdefault(sid, []).append(f'Направление на историческом test: автор {row.get("author_direction")}, модель {row.get("predicted_direction")} — {"совпало" if right else "ошибка"}.')
    for row in em.get("details", []):
        sid = int(row["scenario_id"])
        scores = row.get("scores", [])
        if scores:
            misses = int(scores[-1] < 0)
            early = sum(score >= 0 for score in scores[:-1])
            row_notes.setdefault(sid, []).append(f'Вход на историческом test: {"пропуск авторской ТВХ" if misses else "авторская ТВХ распознана"}; ранних срабатываний на проверенных состояниях ожидания {early} из {len(scores)-1}.')
    summary = (f'<section><h2>Сохранённое обучение и ошибки</h2><p>{link}. Веса направления обучены на {len(direction.get("fitted_scenario_ids", []))} сценариях, '
               f'веса входа — на {len(entry.get("fitted_scenario_ids", []))}. Примеры test не обновляли веса.</p>'
               f'<ul><li>Направление: {direction_errors} ошибок из {dm.get("scenarios", 0)} примеров test.</li>'
               f'<li>Вход: {entry_misses} пропусков из {entry_total} авторских ТВХ; {premature} ранних срабатываний из {wait_total} проверенных состояний ожидания.</li></ul>'
               '<p class="muted">Это совпадение с авторскими метками, а не доходность. Исторический test уже просматривался; результаты не являются новой независимой проверкой. Ожидание не означает убыточную сделку.</p></section>')
    return summary, row_notes


def _additional_results(collection, destination, preparation_path, stale):
    """Keep direction-only coverage distinct from the unresolved hourly task."""
    html, notes, paths = [], {}, []
    expanded = collection/'training/market_direction_expanded'
    path = expanded/'training_report.json'
    if path.is_file():
        paths.append(path)
        report = _read(path)
        provenance = report.get('provenance', {})
        model_path, cases_path = expanded/'direction_model.json', expanded/'cases.jsonl'
        current = (not stale and report.get('trained') is True and model_path.is_file() and cases_path.is_file()
                   and report.get('model_sha256') == _hash(model_path)
                   and report.get('cases_sha256') == _hash(cases_path)
                   and provenance.get('base_market_v2', {}).get('preparation_report_sha256') == _hash(preparation_path))
        for relative, digest in provenance.get('source_sha256', {}).items():
            source = collection/relative
            current = current and source.is_file() and _hash(source) == digest
        for name, digest in provenance.get('code_sha256', {}).items():
            source = Path(__file__).with_name(name)
            current = current and source.is_file() and _hash(source) == digest
        if current:
            paths.extend([model_path, cases_path])
            test = report['held_out_test']
            confusion = test['confusion_true_rows_predicted_columns']
            correct = sum(confusion[side][side] for side in ('long', 'short'))
            html.append(f'<section><h2>Направление без назначения спорной ТВХ</h2><p>Для отдельной модели D1 допущены '
                        f'{report["eligible_direction_scenarios"]} сценариев; веса обучены на {len(report["fitted_scenario_ids"])}. '
                        f'Совпадение на историческом test: {correct}/{test["scenarios"]}. '
                        f'Для входа по-прежнему ожидают уточнения {report["entry_pending_scenarios"]} сценариев.</p>'
                        '<p>Закрытие D1 здесь — момент наблюдения направления, а не назначенная точка входа. '
                        'Новые ТВХ не созданы. Эта модель сохранена отдельно от пары market_v2.</p>'
                        + _link(path, destination.parent, 'Отчёт отдельного обучения направления') + '</section>')
            for sid in report['supplemental_direction_ids']:
                notes[sid] = 'Направление использовано в отдельном обучении по закрытому D1. ТВХ остаётся неподтверждённой; вход не перенесён.'
        else:
            html.append('<p class="warning">Отдельный отчёт направления относится к изменившимся источникам; его метрики не показаны как текущие.</p>')
    path = collection/'training/market_v2/pipeline_evaluation.json'
    if path.is_file():
        paths.append(path)
        report = _read(path)
        folder = path.parent
        current = (not stale and report.get('preparation_provenance', {}).get('preparation_report_sha256') == _hash(preparation_path)
                   and report.get('training_report_sha256') == _hash(folder/'training_report.json'))
        for name, digest in report.get('model_sha256', {}).items():
            current = current and (folder/name).is_file() and _hash(folder/name) == digest
        if current:
            joint, waits = report['joint'], report['verified_waits']
            html.append(f'<section><h2>Проверка связки market_v2</h2><p>Одновременно верное направление и положительная оценка входа: '
                        f'{joint["correct_direction_and_nonnegative_entry_score"]}/{joint["total"]}. '
                        f'Ранние срабатывания: {waits["false_positives"]}/{waits["states"]} проверенных ожиданий.</p>'
                        '<p>Признаки H1 пересчитаны по предсказанной стороне. Проверена сохранённая пара market_v2; '
                        'отдельная расширенная модель D1 в эту связку не подставлялась. '
                        'Это выбранные авторские эпизоды, а не поиск на непрерывном рынке или проверка доходности.</p>'
                        + _link(path, destination.parent, 'Отчёт проверки связки') + '</section>')
        else:
            html.append('<p class="warning">Отчёт связки устарел относительно текущих моделей или источников.</p>')
    return ''.join(html), notes, paths


def _context_results(collection, destination, preparation_path, stale):
    folder=collection/'training/market_context_direction'
    names=('training_report.json','model.json','cases.jsonl','pipeline_evaluation.json')
    paths=[folder/name for name in names]
    if not paths[0].is_file():
        return '',[]
    if not all(p.is_file() for p in paths):
        return '<p class="warning">Артефакты модели направления D1/H1 пока неполны.</p>',[p for p in paths if p.is_file()]
    report,model,pipeline=_read(paths[0]),_read(paths[1]),_read(paths[3])
    provenance=report.get('provenance',{})
    current=(not stale and report.get('trained') is True
             and report.get('model_sha256')==_hash(paths[1])
             and provenance.get('context_cases_sha256')==_hash(paths[2])
             and model.get('provenance')==provenance
             and provenance.get('base_market_v2',{}).get('preparation_report_sha256')==_hash(preparation_path)
             and pipeline.get('context_provenance')==provenance
             and pipeline.get('context_model_sha256')==_hash(paths[1])
             and pipeline.get('entry_model_sha256')==_hash(collection/'training/market_v2/scenario_model.json')
             and pipeline.get('entry_training_report_sha256')==_hash(collection/'training/market_v2/training_report.json'))
    for name,expected in provenance.get('code_sha256',{}).items():
        code=Path(__file__).with_name(name)
        current=current and code.is_file() and _hash(code)==expected
    if not current:
        return '<p class="warning">Отчёт направления D1/H1 относится к другой версии источников или моделей; метрики не показаны как актуальные.</p>',paths
    test=report['held_out_test'];joint=pipeline['joint'];waits=pipeline['verified_waits']
    html=(f'<section><h2>Направление по закрытым D1 и H1</h2>'
          f'<p>Допущены {report["eligible_direction_scenarios"]} сценария, включая случаи, где сторона определяется по H1. '
          f'Веса обучены на {len(report["fitted_scenario_ids"])}; test — {test["scenarios"]}. '
          'Авторское направление не используется для преобразования признаков этой модели.</p>'
          f'<p>Направление на test: {test["correct"]}/{test["scenarios"]}. '
          f'Совместно направление и вход по предсказанной стороне: {joint["correct"]}/{joint["total"]}. '
          f'Ранних срабатываний: {waits["false_positives"]}/{waits["states"]} проверенных ожиданий.</p>'
          f'<p>{_link(paths[0],destination.parent,"Отчёт направления D1/H1")} · '
          f'{_link(paths[3],destination.parent,"Проверка связки на всём test")}</p>'
          '<p class="muted">Это проверка выбранных исторических состояний при заданном уровне, не доходность и не сканирование непрерывного рынка.</p></section>')
    return html,paths


def build_review(collection: Path = COLLECTION, destination: Path | None = None) -> dict:
    collection = Path(collection)
    training = collection / "training"
    market = training / "market_v2"
    destination = Path(destination) if destination else market / "results/index.html"
    skipped, target = user_scope(collection)
    report_path = market / "preparation_report.json"
    report = _read(report_path)
    ledger = _lines(market / "ledger.jsonl")
    case_rows = _lines(market / "cases.jsonl")
    cases = {int(row["scenario_id"]): row for row in case_rows}
    reviews, _ = load_reviews(collection)
    training_path = market / "training_report.json"
    ids = {int(row["scenario_id"]) for row in ledger}
    expected = set(range(1, 410)) - skipped
    if ids != expected or len(ledger) != target or len(cases) != len(case_rows):
        raise ValueError("Coverage review requires each current target ID exactly once and unique cases")
    eligible = {int(row["scenario_id"]) for row in ledger if row["status"] == "eligible"}
    if eligible != set(cases) or report["target_pairs"] != target or report["eligible_pairs"] != len(cases):
        raise ValueError("Preparation report, cases, and ledger disagree")
    if report.get("cases_sha256") and report["cases_sha256"] != _hash(market / "cases.jsonl"):
        raise ValueError("Cases changed since preparation report")
    training_html, model_notes = _training_summary(training_path, report_path, market / "cases.jsonl", destination)
    annotations = {row["scenario_id"]: row for row in _lines(collection / "visual_analysis/scenario_analysis.jsonl")}
    daily = {row["scenario_id"]: row for row in _lines(training / "market_daily_alignment.jsonl")}
    hourly = {row["scenario_id"]: row for row in _lines(training / "market_hourly_alignment.jsonl")}
    timing_path = training / "market_timing_questions.jsonl"
    historical_timing_rows = _lines(timing_path) if timing_path.is_file() else []
    contract = _read(training/'user_scope.json')
    pending_timing_ids = set(contract.get('entry_time_requires_user_confirmation_ids', []))
    timing_rows = [row for row in historical_timing_rows if row['scenario_id'] in pending_timing_ids and row['scenario_id'] not in skipped]
    timing_questions = {int(row["scenario_id"]): row for row in timing_rows}
    if len(timing_questions) != len(timing_rows) or set(timing_questions) & eligible:
        raise ValueError("Timing questions must be unique pending scenarios, never prepared entry labels")
    if not set(timing_questions).issubset(ids):
        raise ValueError("Timing questions refer to IDs outside the current user scope")
    if any(row.get("corrected_entry_generated") is not False for row in timing_questions.values()):
        raise ValueError("Timing questions must preserve pending author entry clarification")
    for sid, question in timing_questions.items():
        expected = annotations[sid]["image_sha256"]
        if any(question.get("source_images_sha256", {}).get(tf) != expected.get(f"images/{tf}_{sid}.jpg") for tf in ("1D", "1H")):
            raise ValueError(f"Timing question image provenance mismatch #{sid}")
        match = hourly.get(sid, {})
        if match.get("accepted") is not True:
            raise ValueError(f"Timing question has no accepted H1 alignment #{sid}")
        fp = match["fingerprint"]
        slot = round((question["last_daily_hour_bar_x"] - fp["origin_x"]) / fp["pitch"])
        actual_close = match["first_open_time_ms"] + (slot - match["first_slot"] + 1) * match["interval_ms"]
        if actual_close != question["required_daily_close_time_ms"]:
            raise ValueError(f"Timing question closure coordinate no longer matches market #{sid}")
    timing_html = _timing_summary(timing_questions, collection, destination)
    reasons = Counter(row["reason"] for row in ledger if row["status"] != "eligible")
    pending = target - len(cases)
    d_count = sum(bool(daily.get(sid, {}).get("accepted")) for sid in ids)
    h_count = sum(bool(hourly.get(sid, {}).get("accepted")) for sid in ids)
    proxy_count = sum(case["entry_resolution"] != "closed_hourly_bar" for case in cases.values())
    stale = []
    for relative, digest in {**report.get("source_sha256", {}), **report.get("review_sources_sha256", {})}.items():
        path = collection / relative
        if not path.is_file() or _hash(path) != digest:
            stale.append(relative)
    additional_html, direction_only_notes, additional_paths = _additional_results(collection, destination, report_path, stale)
    context_html,context_paths=_context_results(collection,destination,report_path,stale)
    additional_html+=context_html
    additional_paths.extend(context_paths)
    manifest_paths = [report_path, market / "ledger.jsonl", market / "cases.jsonl", training / "market_daily_alignment.jsonl", training / "market_hourly_alignment.jsonl"]
    manifest_paths.extend(additional_paths)
    if training_path.is_file():
        manifest_paths.append(training_path)
    if timing_path.is_file():
        manifest_paths.append(timing_path)
    for name in ("market_daily_alignment_report.json", "market_hourly_alignment_report.json", "user_scope.json"):
        if (training / name).is_file():
            manifest_paths.append(training / name)
    provenance = {path.relative_to(collection).as_posix(): _hash(path) for path in manifest_paths}
    row_html = []
    for item in sorted(ledger, key=lambda row: row["scenario_id"]):
        sid = int(item["scenario_id"])
        case, annotation = cases.get(sid), annotations[sid]
        dm, hm = daily.get(sid), hourly.get(sid)
        symbol = case.get("market_symbol") if case else (dm or {}).get("symbol")
        symbol = symbol or annotation.get("instrument") or "Инструмент не подтверждён"
        ready = item["status"] == "eligible"
        reason = item["reason"]
        label = REASONS.get(reason, reason)
        status_text = "Подготовлен" if ready else "Нужна доработка"
        state_time = _utc(case.get("decision_time_ms")) if case else "Не подтверждено"
        closure = _utc(case.get("latest_closed_d1_time_ms")) if case else "Не подтверждено"
        resolution = ("Перед часом · приближение" if case and case["entry_resolution"] != "closed_hourly_bar" else "Закрытый час" if case else "—")
        source_links = []
        if case:
            for key, title in (("source_review_file", "Проверка авторской отметки"), ("source_daily_cache", "OHLC дневки"), ("source_hourly_cache", "OHLC часовика")):
                if case.get(key):
                    source_links.append(_link(collection / case[key], destination.parent, title))
        review = reviews.get(sid, {})
        pending_note = ''
        if not ready and sid not in timing_questions:
            explanations = {
                'pending_ATR_definition_or_author_TVX': 'Нужно определить использованный ATR на H1 либо указать конкретную ТВХ.',
                'entry_condition_not_stated': 'На H1 не удалось однозначно определить час входа по стрелке или условию; нужна конкретная ТВХ.',
                'signal_arrow_not_unique_execution_timing': 'Стрелка отмечает пробой; нужно уточнить вход внутри этого часа или после его закрытия.',
                'breakout_OR_ATR_selected_branch_unresolved': 'Указаны варианты пробоя и ATR. Первый пробой раньше закрытия D1; нужно уточнить выбранную ТВХ.',
                'entry_arrow_between_adjacent_hours': 'Стрелка между соседними часами; нужно уточнить выбранный бар и момент входа.',
                'caption_says_take_not_entry': 'Подпись прочитана как тейк, поэтому её нельзя автоматически считать отметкой входа; нужна ТВХ.',
                'qualitative_chop_end_not_numerical': 'Конец проторговки описан словами; для точной обучающей метки нужно указать час входа.',
            }
            note = explanations.get(review.get('semantics_review_status'), 'Нужно уточнить конкретный час ТВХ и вход внутри него или после закрытия.')
            pending_note = '<p class="warning">' + _text(note) + '</p>'
            if review.get('review_file'):
                source_links.append(_link(collection/review['review_file'], destination.parent, 'Подробная проверка отметки'))
        row_html.append(
            f'<tbody class="case" id="scenario-{sid}" data-sid="{sid}" data-status="{_text(item["status"])}" '
            f'data-reason="{_text(reason)}" data-search="{_text(str(sid)+" "+str(symbol)+" "+str(annotation.get("date_label", "")))}">'
            f'<tr><th scope="row"><a href="#scenario-{sid}">№{sid}</a></th><td>{_text(symbol)}</td>'
            f'<td><span class="badge {"good" if ready else "pending"}">{status_text}</span><small>{_text(label)}</small></td>'
            f'<td>{_text(state_time)}<small>{resolution}</small></td><td>{_text(closure)}</td></tr>'
            '<tr class="detail"><td colspan="5"><details><summary>Разбор, источники и оригиналы</summary>'
            f'<p><strong>Подпись автора:</strong> {_text(annotation.get("date_label", "—"))}. '
            f'<strong>Направление с учётом уточнений:</strong> {_text(case.get("direction", "—") if case else annotation.get("direction", "—"))}.</p>'
            f'<p><strong>D1:</strong> {_text(_matched_text(dm))}<br><strong>H1:</strong> {_text(_matched_text(hm))}</p>'
            + _case_facts(case)
            + _timing_facts(timing_questions.get(sid))
            + pending_note
            + ('<p class="note">'+_text(direction_only_notes[sid])+'</p>' if sid in direction_only_notes else '')
            + ''.join('<p class="note">'+_text(note)+'</p>' for note in model_notes.get(sid, []))
            + ('<p class="sources">' + ' · '.join(source_links) + '</p>' if source_links else '')
            + f'<p><strong>Авторский контекст D1:</strong> {_text(annotation.get("daily_pattern", "—"))}</p>'
            + f'<p><strong>Авторский контекст H1:</strong> {_text(annotation.get("hourly_entry", "—"))}</p>'
            + '<p class="note">Ниже оригиналы для проверки разметки. Они содержат последующие бары; полный рисунок не является входом числовой модели.</p>'
            + '<div class="charts">' + _image(collection, destination, sid, "1D") + _image(collection, destination, sid, "1H", timing_questions.get(sid), (hm or {}).get("fingerprint")) + '</div>'
            + '</details></td></tr></tbody>'
        )
    reason_options = ''.join(f'<option value="{_text(reason)}">{_text(REASONS.get(reason, reason))} ({count})</option>' for reason, count in sorted(reasons.items(), key=lambda pair: (-pair[1], pair[0])))
    reason_rows = ''.join(f'<li>{_text(REASONS.get(reason, reason))}: <strong>{count}</strong> <code>{_text(reason)}</code></li>' for reason, count in sorted(reasons.items(), key=lambda pair: (-pair[1], pair[0])))
    status_sentence = (f'Для ТВХ подготовлены {len(cases)} из {target}; ещё {pending} требуют уточнения.' if pending else f'Все {target} числовых примеров ТВХ подготовлены. Это само по себе не означает, что обучение и независимая проверка завершены.')
    warning = ('<aside class="warning"><strong>Источники изменились после подготовки.</strong> Пересоберите набор перед оценкой текущего состояния: ' + ', '.join(_text(name) for name in stale) + '</aside>') if stale else ''
    source_html = ''.join(f'<li>{_link(path, destination.parent, path.relative_to(collection).as_posix())}</li>' for path in manifest_paths)
    page = f'''<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Проверка {target} сценариев · Bybit</title><style>
.chart-overlay{{position:relative}}.chart-overlay svg{{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}}
:root{{color-scheme:light;--ink:#192c36;--muted:#526a76;--line:#d5e0e5;--blue:#075b8c;--good:#17694b;--pending:#825612}}
*{{box-sizing:border-box}}body{{margin:0;background:#f2f6f8;color:var(--ink);font:16px/1.5 system-ui,sans-serif}}main{{max-width:1440px;margin:auto;padding:28px}}h1{{font-size:clamp(1.55rem,3vw,2.4rem);line-height:1.2;margin:0 0 12px}}h2{{font-size:1.2rem}}a{{color:var(--blue)}}p{{margin:10px 0}}.muted,small{{color:var(--muted)}}small{{display:block;font-size:.8rem;margin-top:4px}}.cards{{display:flex;flex-wrap:wrap;gap:12px;margin:22px 0}}.card{{background:white;border:1px solid var(--line);border-radius:10px;flex:1;min-width:160px;padding:16px}}.card strong{{font-size:1.65rem;display:block}}.card span{{color:var(--muted);font-size:.85rem}}.note,.warning{{padding:12px 16px;border-left:4px solid #aac5d6;background:#e8f0f4}}.warning{{border-color:#c38c2d;background:#fff0cf}}.toolbar{{position:sticky;top:0;z-index:1;display:flex;gap:12px;flex-wrap:wrap;padding:16px;background:#f2f6f8;border:1px solid var(--line);border-radius:8px;margin:24px 0 12px}}label{{display:flex;flex-direction:column;font-size:.85rem;gap:5px}}input,select{{font:inherit;padding:9px;border:1px solid #aac0ca;border-radius:5px;background:white;max-width:100%}}#reason{{max-width:470px}}.table-wrap{{overflow:auto;background:white;border:1px solid var(--line);border-radius:8px}}table{{border-collapse:collapse;width:100%}}th,td{{text-align:left;vertical-align:top;padding:14px;border-bottom:1px solid var(--line)}}thead th{{background:#e7eef2;font-size:.85rem}}.badge{{font-size:.8rem;font-weight:700;padding:4px 8px;border-radius:5px;white-space:nowrap}}.good{{color:var(--good);background:#e2f3e9}}.pending{{color:var(--pending);background:#fff0d4}}.detail td{{padding-top:0}}.case>tr:first-child td,.case>tr:first-child th{{border-bottom:0}}summary{{cursor:pointer;color:var(--blue);padding:8px 0}}.charts{{display:grid;grid-template-columns:1fr 1fr;gap:18px}}figure{{margin:8px 0;min-width:0}}figcaption{{font-weight:600;margin-bottom:7px}}img{{display:block;width:100%;height:auto;border:1px solid var(--line)}}dl{{display:grid;grid-template-columns:minmax(190px,1fr) 2fr;gap:6px 20px}}dt{{color:var(--muted)}}dd{{margin:0;overflow-wrap:anywhere}}code{{font-size:.8rem;color:var(--muted);overflow-wrap:anywhere}}li{{margin:7px 0}}[hidden]{{display:none!important}}footer{{font-size:.85rem;color:var(--muted);margin:28px 0}}@media(max-width:800px){{main{{padding:16px}}.charts{{grid-template-columns:1fr}}dl{{grid-template-columns:1fr}}dd{{margin-bottom:8px}}th,td{{padding:10px}}.toolbar{{position:static}}}}
</style></head><body><main>
<h1>Проверка {target} авторских сценариев</h1>
<p>{_text(status_sentence)}</p>
<p class="muted">Исключены по просьбе автора: {', '.join('№'+str(sid) for sid in sorted(skipped))}. Исходные изображения сохранены.</p>
{warning}<div class="cards"><div class="card"><strong>{target}</strong><span>Целевых пар D1 / H1</span></div><div class="card"><strong>{len(cases)}</strong><span>Подготовлено числовых примеров</span></div><div class="card"><strong>{pending}</strong><span>Требуют доработки чтения или сопоставления</span></div><div class="card"><strong>{d_count} / {h_count}</strong><span>Подтверждено сопоставлений D1 / H1</span></div><div class="card"><strong>{proxy_count}</strong><span>Приближённых состояний перед часом входа</span></div></div>
<p class="note">Подготовка примера, обучение весов и проверка качества — разные этапы. Эта страница показывает покрытие сохранённого набора и причины недопуска. Метрики доходности здесь не рассчитываются; автоматических сделок нет. Прежний test уже просматривался и не является новой независимой проверкой.</p>
{training_html}
{additional_html}
{timing_html}
<details><summary>Что ещё нужно разобрать ({pending})</summary><ul>{reason_rows or '<li>Нет недопущенных примеров в текущем наборе.</li>'}</ul></details>
<div class="toolbar"><label>Номер, монета или дата<input id="search" type="search" placeholder="Например, 173 или WLD"></label><label>Статус<select id="status"><option value="all">Все сценарии</option value="eligible">Подготовлены</option value="pending">Требуют доработки</option></select></label><label>Причина доработки<select id="reason"><option value="all">Все причины</option>{reason_options}</select></label></div>
<p id="visible" class="muted" aria-live="polite">Показано {target} из {target}</p>
<div class="table-wrap"><table><thead><tr><th>Сценарий</th><th>Инструмент</th><th>Подготовка</th><th>Время состояния H1</th><th>Фактическое закрытие D1</th></tr></thead>{''.join(row_html)}</table></div>
<footer><details><summary>Исходные отчёты и контрольные суммы</summary><ul>{source_html}</ul><pre>{_text(json.dumps(provenance,ensure_ascii=False,indent=2))}</pre></details><p>Страница работает локально, без сети. Изображения подгружаются из соседней папки корпуса. Обновление: python -m knowledge_bot.build_market_training_review</p></footer>
</main><script>
const cases=Array.from(document.querySelectorAll('tbody.case'));
const search=document.getElementById('search'),status=document.getElementById('status'),reason=document.getElementById('reason');
function filter(){{const query=search.value.trim().toLocaleLowerCase('ru');let n=0;for(const row of cases){{const show=(!query||row.dataset.search.toLocaleLowerCase('ru').includes(query))&&(status.value==='all'||row.dataset.status===status.value)&&(reason.value==='all'||row.dataset.reason===reason.value);row.hidden=!show;if(show)n++;}}document.getElementById('visible').textContent=`Показано ${{n}} из ${{cases.length}}`;}}
for(const input of [search,status,reason])input.addEventListener('input',filter);
function openScenario(){{if(location.hash.startsWith('#scenario-')){{const row=document.getElementById(location.hash.slice(1));if(row){{if(row.hidden){{search.value='';status.value='all';reason.value='all';filter();}}row.querySelector('details').open=true;row.scrollIntoView({{block:'start'}});}}}}}}
window.addEventListener('hashchange',openScenario);openScenario();
</script></body></html>'''
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(page, encoding="utf8")
    manifest = dict(target_pairs=target, eligible_pairs=len(cases), pending_pairs=pending,
                    output=destination.relative_to(collection).as_posix() if destination.is_relative_to(collection) else str(destination),
                    rows=len(ledger), user_skipped=sorted(skipped), stale_preparation_sources=stale,
                    pending_author_entry_ids=sorted(timing_questions),
                    source_sha256=provenance, html_sha256=_hash(destination),
                    review_builder_sha256=_hash(Path(__file__)), images_modified=False)
    (destination.parent / "review_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+"\n", encoding="utf8")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", type=Path, default=COLLECTION)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    print(json.dumps(build_review(args.collection, args.output), ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
