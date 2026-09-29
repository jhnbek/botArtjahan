"""Build a portable, read-only Russian review of the two saved training reports.

No model fitting, image rewriting, external resources or browser fetch calls.
Metrics and predictions come only from the held-out report details. The page
keeps direction and entry readiness separate; it does not infer joint accuracy
or trading profitability. Run again to reproduce exactly the same HTML bytes.
"""
from __future__ import annotations

import argparse
import hashlib
from html import escape
import json
import math
import os
from pathlib import Path
from urllib.parse import quote


COLLECTION = (Path(__file__).resolve().parents[1] /
              "_knowledge_base/manual_reviews/scenarios_dzhahan_20260925")


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _text(value):
    return escape(str(value), quote=True)


def _url(path, parent):
    return quote(Path(os.path.relpath(path, parent)).as_posix(), safe="/.")


def _percentage(number):
    return "—" if number is None else f"{100*number:.1f}%".replace(".", ",")


def _finite(value):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("Review requires finite report scores and coordinates")
    return value


def _label(value):
    return {"long": "Лонг", "short": "Шорт", "entry": "Вход", "wait": "Ожидание"}.get(value, _text(value))


def _plot(collection, destination, sid, timeframe, extraction, *, model_input):
    source = collection / "images" / f"{timeframe}_{sid}.jpg"
    if not source.is_file():
        return f'<p class="muted">Оригинал {_text(timeframe)} отсутствует.</p>'
    url = _url(source, destination.parent)
    overlay, caption = "", "Оригинал для контекста; этот таймфрейм не подаётся данной модели."
    size = ""
    if extraction and extraction.get("width") and extraction.get("height"):
        width, height = int(extraction["width"]), int(extraction["height"])
        size = f' width="{width}" height="{height}"'
    if model_input:
        if not extraction or not extraction.get("usable"):
            raise ValueError(f"Missing numerical extraction for plotted model input {timeframe}/{sid}")
        width, height = int(extraction["width"]), int(extraction["height"])
        bars = extraction.get("bars", extraction.get("decoded_bars", []))
        cutoff = _finite(extraction.get("cutoff_x", bars[-1]["x"] if bars else None))
        anchor = _finite(extraction["signal_x"])
        pitch = _finite(extraction.get("bar_pitch_px", 0))
        if not 0 <= cutoff < width or not 0 <= anchor < width:
            raise ValueError(f"Off-image cutoff {timeframe}/{sid}")
        future_start = min(width, cutoff + max(pitch/2, 1))
        overlay = (
            f'<svg class="overlay" viewBox="0 0 {width} {height}" preserveAspectRatio="none" aria-hidden="true">'
            f'<rect class="future" x="{future_start:g}" y="0" width="{width-future_start:g}" height="{height}"/>'
            f'<line class="cutoff" x1="{cutoff:g}" x2="{cutoff:g}" y1="0" y2="{height}"/>'
            + (f'<line class="anchor" x1="{anchor:g}" x2="{anchor:g}" y1="0" y2="{height}"/>' if anchor != cutoff else "")
            + '</svg>'
        )
        caption = (f'Последний бар признаков: x={cutoff:g}. Авторская отметка: x={anchor:g}. '
                   'Серое поле — более поздние бары, которых модель не видела.')
        if anchor != cutoff:
            caption += ' Отмеченный час входа исключён: использован закрытый бар перед ним.'
    return (f'<figure><figcaption><strong>{timeframe}</strong> · '
            f'<a href="{url}" target="_blank" rel="noopener">Открыть оригинал</a></figcaption>'
            f'<div class="plot"><img loading="lazy" src="{url}" alt="Сценарий {sid}, {timeframe}"{size}>{overlay}</div>'
            f'<p class="caption">{_text(caption)}</p></figure>')


def _annotations(row):
    return ('<details class="annotations"><summary>Авторский контекст из сохранённой расшифровки</summary>'
            f'<p><strong>D1:</strong> {_text(row.get("daily_pattern", "—"))}</p>'
            f'<p><strong>H1:</strong> {_text(row.get("hourly_entry", "—"))}</p>'
            '<p class="muted">Текст показан только для разбора. Подписи, номер и последующий результат не являются признаками модели.</p></details>')


def _direction_rows(details, annotations, original, reviewed, collection, destination):
    output = []
    for row in sorted(details, key=lambda value: value["scenario_id"]):
        sid = int(row["scenario_id"])
        failed = row["author_direction"] != row["predicted_direction"]
        token = "Ошибка" if failed else "Совпало"
        preview = _plot(collection, destination, sid, "1D", original[(sid, "1D")], model_input=True)
        preview += _plot(collection, destination, sid, "1H", reviewed.get((sid, "1H"), original.get((sid, "1H"))), model_input=False)
        output.append(
            f'<tbody class="case" data-error="{str(failed).lower()}" data-search="{_text(str(sid)+" "+row["instrument_group"])}">'
            f'<tr><th scope="row">№{sid}</th><td>{_text(row["instrument_group"])}</td>'
            f'<td>{_label(row["author_direction"])}</td><td>{_label(row["predicted_direction"])}</td>'
            f'<td><span class="badge {"bad" if failed else "good"}">{token}</span></td></tr>'
            '<tr class="detail-row"><td colspan="5"><details><summary>Показать графики и границу данных</summary>'
            f'<div class="charts">{preview}</div>{_annotations(annotations[sid])}</details></td></tr></tbody>'
        )
    return "".join(output)


def _entry_rows(details, cases, annotations, original, reviewed, collection, destination):
    output = []
    timing_names = {"before_bar_open": "Перед открытием отмеченного часа",
                    "after_bar_close": "После закрытия отмеченного часа"}
    resolution_names = {
        "closed_hourly_bar": "Закрытый часовой бар",
        "pre_hourly_bar_proxy_for_marked_intrabar_entry": "Приближение: закрытый бар перед входом внутри часа",
    }
    for row in sorted(details, key=lambda value: value["scenario_id"]):
        sid = int(row["scenario_id"])
        case = cases[sid]
        score = _finite(row["selected_state_score"])
        scores = [_finite(value) for value in row["scores"]]
        offsets = row["state_offsets_before_entry"]
        if len(scores) != len(offsets) or scores[-1] != score:
            raise ValueError(f"Inconsistent readiness detail #{sid}")
        # evaluate_readiness defines a raw score >= 0 as entry; scores already
        # include the fitted intercept. This page never recomputes model scores.
        failed = score < 0 or any(value >= 0 for value in scores[:-1])
        prediction = "entry" if score >= 0 else "wait"
        state_rows = []
        for index, (offset, value) in enumerate(zip(offsets, scores)):
            actual = "entry" if index == len(scores)-1 else "wait"
            guessed = "entry" if value >= 0 else "wait"
            state_rows.append(f'<tr><td>{int(offset)}</td><td>{_label(actual)}</td><td>{value:+.6f}</td>'
                              f'<td>{_label(guessed)}</td><td>{"Совпало" if guessed == actual else "Ошибка"}</td></tr>')
        states = ('<h4>Все проверенные состояния этого сценария</h4>'
                  '<table class="states"><thead><tr><th>Баров до ТВХ</th><th>Разметка</th><th>Оценка</th><th>Модель</th><th>Результат</th></tr></thead>'
                  '<tbody>'+"".join(state_rows)+'</tbody></table>')
        metadata = (f'<p><strong>Момент разметки:</strong> {_text(timing_names.get(case.get("decision_timing"), case.get("decision_timing", "—")))}.<br>'
                    f'<strong>Точность момента:</strong> {_text(resolution_names.get(case.get("entry_state_resolution"), case.get("entry_state_resolution", "—")))}.<br>'
                    f'<strong>Закрытие D1 на H1:</strong> x={_finite(case["daily_close_bar_x"]):g}. '
                    f'<strong>Направление, заданное модели ТВХ:</strong> {_label(case["direction"])}.</p>')
        preview = _plot(collection, destination, sid, "1D", original.get((sid, "1D")), model_input=False)
        preview += _plot(collection, destination, sid, "1H", reviewed[(sid, "1H")], model_input=True)
        output.append(
            f'<tbody class="case" data-error="{str(failed).lower()}" data-search="{_text(str(sid)+" "+row["group"])}">'
            f'<tr><th scope="row">№{sid}</th><td>{_text(row["group"])}</td><td>Вход</td><td>{score:+.6f}</td>'
            f'<td>{_label(prediction)}</td><td><span class="badge {"bad" if failed else "good"}">{"Ошибка" if failed else "Совпало"}</span></td></tr>'
            '<tr class="detail-row"><td colspan="6"><details><summary>Показать состояния и оригиналы</summary>'
            f'{metadata}{states}<div class="charts">{preview}</div>{_annotations(annotations[sid])}</details></td></tr></tbody>'
        )
    return "".join(output)


def _joint_review(training, destination):
    """Optional saved joint evaluation; never combine the individual rates."""
    path = training / "pipeline_evaluation.json"
    if not path.is_file():
        return "", False
    try:
        report = _read(path)
    except (OSError, json.JSONDecodeError):
        return "", False  # An incomplete optional report leaves the old page usable.
    needed = {"number_of_cases", "scenario_ids", "correct_direction_and_entry", "details", "waiting_states"}
    if not isinstance(report, dict) or not needed.issubset(report):
        return "", False
    count = report["number_of_cases"]
    correct = report["correct_direction_and_entry"]
    ids = report["scenario_ids"]
    waiting = report["waiting_states"]
    if not {"total", "correct_direction_and_wait"}.issubset(waiting):
        return "", False
    if (not isinstance(count, int) or count <= 0 or len(ids) != count
            or len(report["details"]) != count or len(set(ids)) != count
            or sorted(row["scenario_id"] for row in report["details"]) != sorted(ids)
            or correct != sum(row["correct_direction_and_entry"] is True for row in report["details"])):
        raise ValueError("Joint evaluation totals do not agree with its saved case details")
    url = _url(path, destination.parent)
    html = (
        '<article class="card"><h3>Совместная проверка направления и входа</h3>'
        f'<div class="number">{correct} / {count}</div>'
        '<p>На пересечении тестовых наборов совпали и направление, и выбранное автором состояние входа.</p>'
        f'<p><strong>Малая выборка: всего {count} сценариев.</strong> '
        'Результат получен отдельным прогоном связки моделей, а не перемножением их метрик. Это не winrate.</p>'
        f'<p>Номера: {_text(", ".join(str(sid) for sid in sorted(ids)))}. '
        f'Верное направление и ожидание: {waiting["correct_direction_and_wait"]}/{waiting["total"]} более ранних состояний после закрытия D1.</p>'
        f'<small><a href="{url}">Открыть полный отчёт совместной проверки</a></small></article>'
    )
    return html, True


STYLE = """
:root{color-scheme:light;--ink:#182d3b;--muted:#526675;--line:#dce5e8;--teal:#087c84;--paper:#f3f6f7}
*{box-sizing:border-box}body{margin:0;font:16px/1.55 system-ui,-apple-system,Segoe UI,sans-serif;background:var(--paper);color:var(--ink)}
header{background:#173642;color:#fff;padding:40px max(24px,calc((100vw - 1240px)/2)) 34px}header p{max-width:850px;color:#d7e6e9}h1{font-size:clamp(26px,4vw,40px);line-height:1.2;margin:8px 0 16px}h2{font-size:25px;margin:0 0 12px}h3{font-size:18px;margin:0 0 12px}h4{font-size:16px;margin:22px 0 10px}a{color:#08737b;text-underline-offset:3px}header a{color:#beedf0}main{max-width:1288px;margin:auto;padding:26px 24px 50px}.kicker{font-size:13px;letter-spacing:.09em;text-transform:uppercase}.muted,.caption{color:var(--muted);font-size:14px}.lead{font-size:18px}.cards{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px;margin:0 0 22px}.card,section,.notice{background:white;border:1px solid var(--line);border-radius:13px;padding:24px}.card .number{font-size:42px;letter-spacing:-.04em;font-weight:700;line-height:1.2}.card p{margin:7px 0}.card small{color:var(--muted)}.notice{border-left:5px solid #c5942f;background:#fffdf5;margin:18px 0}.notice p{margin:0 0 8px}.notice p:last-child{margin:0}.toolbar{position:sticky;top:0;z-index:5;background:#f3f6f7f5;backdrop-filter:blur(8px);padding:16px 0;display:flex;gap:14px;flex-wrap:wrap;align-items:center}.filters{display:flex;border:1px solid #a9bdc4;border-radius:8px;overflow:hidden}.filters button{border:0;background:#fff;font:inherit;padding:9px 16px;color:var(--ink);cursor:pointer}.filters button[aria-pressed=true]{background:#173642;color:#fff}.search{border:1px solid #a9bdc4;border-radius:8px;padding:10px 13px;font:inherit;min-width:210px}label{font-size:14px;display:flex;gap:8px;align-items:center}section{margin:18px 0 26px;overflow:hidden}.table-wrap{overflow-x:auto}table{width:100%;border-collapse:collapse;text-align:left}th,td{padding:12px 13px;border-bottom:1px solid var(--line);vertical-align:top}thead{background:#f1f6f7;color:#3a5462;font-size:14px}tbody.case>tr:first-child th{white-space:nowrap}.detail-row>td{padding-top:0;padding-bottom:14px}.detail-row details>summary{font-size:14px;color:var(--teal);cursor:pointer;padding:7px 0}.badge{display:inline-block;padding:2px 10px;border-radius:20px;font-size:13px;white-space:nowrap}.good{background:#e1f1e9;color:#246044}.bad{background:#fff0d9;color:#925214}.charts{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px;margin-top:20px}figure{min-width:0;margin:0;background:#f6f8f9;border:1px solid var(--line);border-radius:9px;overflow:hidden}figcaption{padding:10px 12px;font-size:14px}.plot{position:relative;line-height:0}.plot img{display:block;width:100%;height:auto}.overlay{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}.future{fill:#0b1f2d;fill-opacity:.52}.cutoff{stroke:#00a9ba;stroke-width:3;vector-effect:non-scaling-stroke}.anchor{stroke:#ffb63d;stroke-width:2;stroke-dasharray:6 4;vector-effect:non-scaling-stroke}body.show-future .future{fill-opacity:0}.caption{margin:0;padding:11px 12px}.annotations{margin:15px 0;padding:12px 15px;background:#f2f6f7;border-radius:8px}.annotations p{font-size:14px}.states{max-width:900px;font-size:14px}.legend{display:flex;gap:20px;flex-wrap:wrap;font-size:13px;color:var(--muted)}.legend b{display:inline-block;width:18px;height:3px;vertical-align:middle;margin-right:6px}.source-list{font-size:13px;overflow-wrap:anywhere}.source-list li{margin-bottom:6px}footer{font-size:13px;color:var(--muted);padding-top:12px}[hidden]{display:none!important}
@media(max-width:800px){.cards,.charts{grid-template-columns:1fr}.card{padding:19px}.card .number{font-size:34px}section{padding:16px}header{padding:26px 22px}main{padding:16px 12px}th,td{padding:10px 8px}.toolbar{gap:9px}.search{min-width:160px;width:100%}}@media print{.toolbar{position:static}.future{fill-opacity:.35}details{break-inside:avoid}section{break-before:page}}
"""

SCRIPT = """
(()=>{let errorsOnly=false;const search=document.getElementById('search');
function apply(){const query=search.value.toLowerCase().trim();document.querySelectorAll('tbody.case').forEach(row=>{row.hidden=(errorsOnly&&row.dataset.error!=='true')||!row.dataset.search.toLowerCase().includes(query);});document.querySelectorAll('[data-count-for]').forEach(label=>{const rows=[...document.querySelectorAll('#'+label.dataset.countFor+' tbody.case')];label.textContent='Показано '+rows.filter(row=>!row.hidden).length+' из '+rows.length;});}
document.querySelectorAll('[data-filter]').forEach(button=>button.addEventListener('click',()=>{errorsOnly=button.dataset.filter==='errors';document.querySelectorAll('[data-filter]').forEach(item=>item.setAttribute('aria-pressed',String(item===button)));apply();}));search.addEventListener('input',apply);document.getElementById('future').addEventListener('change',event=>document.body.classList.toggle('show-future',event.target.checked));apply();})();
"""


def build_review(collection: Path | str = COLLECTION, output: Path | str | None = None) -> Path:
    collection = Path(collection).resolve()
    destination = Path(output).resolve() if output is not None else collection/"training/results/index.html"
    training = collection/"training"
    direction_report = _read(training/"direction_training_report.json")
    entry_report = _read(training/"training_report.json")
    annotations = {row["scenario_id"]: row for row in _jsonl(collection/"visual_analysis/scenario_analysis.jsonl")}
    cases = {row["scenario_id"]: row for row in _jsonl(training/"entry_training_cases.jsonl")}
    original = {(row["scenario_id"], row["timeframe"].upper()): row
                for row in _read(training/"image_extraction_audit.json")["records"]}
    reviewed = {(row["scenario_id"], row["timeframe"].upper()): row
                for row in _read(training/"reviewed_entry_extraction.json")["records"]}
    dm = direction_report["held_out_test_after_refit"]
    em = entry_report["test_evaluation"]
    direction_correct = sum(row["author_direction"] == row["predicted_direction"] for row in dm["details"])
    dc = dm["confusion_true_rows_predicted_columns"]
    ec = em["confusion"]
    entry_total = ec["entry_as_entry"]+ec["entry_as_wait"]
    wait_total = ec["wait_as_wait"]+ec["wait_as_entry"]
    if len(dm["details"]) != dm["scenarios"] or len(em["details"]) != entry_total:
        raise ValueError("Report details do not agree with saved evaluation totals")
    recomputed = {"entry_as_entry": 0, "entry_as_wait": 0, "wait_as_entry": 0, "wait_as_wait": 0}
    for row in em["details"]:
        for index, score in enumerate(row["scores"]):
            actual = "entry" if index == len(row["scores"])-1 else "wait"
            recomputed[f'{actual}_as_{"entry" if _finite(score) >= 0 else "wait"}'] += 1
    if recomputed != ec:
        raise ValueError("Saved readiness confusion disagrees with raw score decisions")
    direction_html = _direction_rows(dm["details"], annotations, original, reviewed, collection, destination)
    entry_html = _entry_rows(em["details"], cases, annotations, original, reviewed, collection, destination)
    joint_html, joint_available = _joint_review(training, destination)
    joint_note = ("Совместный результат показан отдельно только для пересечения двух тестовых наборов."
                  if joint_available else "Общей оценки связки здесь нет.")
    baseline_correct = sum(row["author_direction"] == dm["majority_baseline"]["direction_chosen_from_fit_partition"] for row in dm["details"])
    source_names = ("direction_training_report.json", "training_report.json", "direction_model.json", "scenario_model.json")
    if joint_available:
        source_names += ("pipeline_evaluation.json",)
    sources = "".join(f'<li><a href="{_url(training/name, destination.parent)}">{name}</a> · SHA-256 '
                      f'<code>{hashlib.sha256((training/name).read_bytes()).hexdigest()}</code></li>' for name in source_names)
    page = f'''<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Джахан · Разбор обучения ТВХ</title><style>{STYLE}</style></head><body>
<header><div class="kicker">Джахан · 409 авторских сценариев</div><h1>Что модели уже повторяют<br>и где ошибаются</h1>
<p class="lead">Разбор сохранённой проверки на инструментах, которые не участвовали в подборе весов. Две задачи: выбрать направление по D1 и оценить состояние H1 после закрытия дневки.</p>
<p><a href="#direction">Направление D1</a> · <a href="#entry">Момент входа H1</a> · <a href="#sources">Исходные отчёты</a></p></header>
<main><div class="cards">
<article class="card"><h3>Направление D1</h3><div class="number">{direction_correct} / {dm['scenarios']}</div><p>Совпадений с разметкой автора · {_percentage(dm['accuracy'])}</p>
<small>Лонг: {dc['long']['long']}/{sum(dc['long'].values())}. Шорт: {dc['short']['short']}/{sum(dc['short'].values())}. Сбалансированная точность: {_percentage(dm['balanced_accuracy'])}.</small></article>
<article class="card"><h3>Отмеченные автором входы H1</h3><div class="number">{ec['entry_as_entry']} / {entry_total}</div><p>Распознаны как вход · {_percentage(em['entry_recall'])}</p>
<small>{ec['entry_as_wait']} отмеченных входа модель пока отнесла к ожиданию. Направление в этой проверке задано автором.</small></article>
<article class="card"><h3>Ожидание после закрытия D1</h3><div class="number">{ec['wait_as_wait']} / {wait_total}</div><p>Распознано как ожидание</p>
<small>В независимом тесте всего {wait_total} такое состояние. Этого недостаточно для устойчивой оценки умения ждать.</small></article></div>{joint_html}
<div class="notice"><p><strong>Это не winrate и не доля прибыльных сделок.</strong> Сравнивается выбор модели с авторской разметкой. Стопы, выходы, комиссии и доходность этой проверкой не измеряются.</p>
<p>Направление обучено на {direction_report['eligible_scenarios']} сценариях, готовность входа — на {entry_report['eligible_scenarios']}. Их независимые тесты содержат разные наборы примеров. Метрики не складываются и не перемножаются. {joint_note}</p>
<p>Картинки содержат дальнейшее движение для ручного разбора. В признаки попадали только разрешённые закрытые бары до отмеченной границы. Оригиналы и авторские подписи не изменены.</p></div>
<div class="toolbar"><div class="filters" aria-label="Фильтр результатов"><button data-filter="all" aria-pressed="true">Все примеры</button><button data-filter="errors" aria-pressed="false">Только ошибки</button></div>
<input class="search" id="search" type="search" aria-label="Номер сценария или монета" placeholder="Номер или монета">
<label><input type="checkbox" id="future">Показать дальнейшие бары без затенения</label></div>
<div class="legend"><span><b style="background:#00a9ba"></b>Последний бар признаков</span><span><b style="background:#ffb63d"></b>Отметка входа, если она позже</span><span>Оверлей можно сверить с неизменённым оригиналом</span></div>
<section id="direction"><h2>Направление по закрытой дневке</h2><p>Модель видит числовые признаки обоих направлений и заданный синий уровень. Простая базовая стратегия «всегда {_label(dm['majority_baseline']['direction_chosen_from_fit_partition']).lower()}» совпала бы в {baseline_correct}/{dm['scenarios']} случаях. Это также не прибыльность.</p>
<p class="muted" data-count-for="direction"></p><div class="table-wrap"><table><thead><tr><th>Сценарий</th><th>Монета</th><th>Авторская разметка</th><th>Модель</th><th>Результат</th></tr></thead>{direction_html}</table></div></section>
<section id="entry"><h2>Готовность входа на часовике</h2><p>Проверено {entry_total} отмеченных входов и {wait_total} более раннее состояние после закрытия D1. Оценка — исходный числовой результат модели: от нуля — «вход», ниже нуля — «ожидание». Это не вероятность тейк-профита.</p>
<p>Для отметки входа внутри часа используется состояние перед этим часом: будущие OHLC отмеченного часа исключены. Подробности момента и точности разметки указаны у каждого примера.</p>
<p class="muted" data-count-for="entry"></p><div class="table-wrap"><table><thead><tr><th>Сценарий</th><th>Монета</th><th>Авторская разметка</th><th>Оценка входа</th><th>Модель</th><th>Результат</th></tr></thead>{entry_html}</table></div></section>
<section id="sources"><h2>Откуда взяты результаты</h2><p>Значения таблиц прочитаны из сохранённых разделов независимой проверки. Эта страница ничего не обучает, не отправляет ордера и работает без интернета.</p>
<p>Извлечение OHLC из пикселей приблизительное. Синие уровни заданы автором. В выборке собраны отобранные сценарии, а не непрерывная история всех рыночных возможностей. У D1 вручную просмотрено 30 оригиналов: 29 приняты, у №52 наше автоматическое ограничение данных заканчивалось до возвратного бара двухбарного ЛП. Этот пример исключён из обучения направления; авторская разметка сохранена.</p>
<ul class="source-list">{sources}</ul></section><footer>Локальный обзор · Для переноса сохраните папки training и images в прежней структуре. Графики доступны по относительным ссылкам.</footer></main>
<script>{SCRIPT}</script></body></html>'''
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(page, encoding="utf-8", newline="\n")
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("collection", type=Path, nargs="?", default=COLLECTION)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    print(build_review(args.collection, args.output))
