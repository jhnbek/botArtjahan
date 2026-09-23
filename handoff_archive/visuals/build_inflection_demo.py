"""Render actual robot inflection candidates from cached OHLC, without network."""
import hashlib
import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'botArtjahan/knowledge_bot'))
from level_discovery import Bar, DiscoveryParams, atr_at, discover_levels, swing_pivots

source = ROOT / 'botArtjahan/_knowledge_base/live_market_cache/bybit_linear_BTCUSDT_1d.json'
raw = source.read_bytes()
payload = json.loads(raw)
bars = [Bar(int(b['open_time_ms']), *[float(b[k]) for k in ('open', 'high', 'low', 'close', 'volume')])
        for b in payload['bars']]
p = DiscoveryParams()

def date(i):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(bars[i].open_time / 1000, timezone.utc).strftime('%Y-%m-%d')

def render(end, index, kind):
    history = bars[:end]
    levels = discover_levels(history, p)
    lv = next(l for l in levels if l.bsu_index == index and 'inflection' in l.basis_tags)
    atr = atr_at(history, index, 14) or atr_at(history, end - 1, 14)
    tail_atr = atr_at(history, end - 1, 14)
    before = range(max(0, index-20), index+1)
    after = range(index+1, min(end, index+21))
    peak = bars[index].high if kind == 'H' else bars[index].low
    start = min(before, key=lambda j: bars[j].low) if kind == 'H' else max(before, key=lambda j: bars[j].high)
    finish = min(after, key=lambda j: bars[j].low) if kind == 'H' else max(after, key=lambda j: bars[j].high)
    start_price = bars[start].low if kind == 'H' else bars[start].high
    finish_price = bars[finish].low if kind == 'H' else bars[finish].high
    incoming, reversal = abs(peak-start_price), abs(peak-finish_price)
    first = next(j for j in after if (peak-bars[j].low if kind == 'H' else bars[j].high-peak) >= 2.5*atr)
    available = max(index+3, first)
    lo, hi = max(0,index-24), min(end,index+25)
    visible = bars[lo:hi]
    bottom, top = min(b.low for b in visible), max(b.high for b in visible)
    pad = (top-bottom)*.12
    bottom -= pad; top += pad
    x = lambda j: 75+(j-lo+.5)*1010/(hi-lo)
    y = lambda price: 55+(top-price)*390/(top-bottom)
    svg = ['<svg viewBox="0 0 1200 520" role="img" aria-label="Свечной график излома BTCUSDT">']
    svg.append(f'<rect x="{x(index):.2f}" y="55" width="{1085-x(index):.2f}" height="390" fill="#20324a"/>')
    for k in range(6):
        value=bottom+(top-bottom)*k/5
        svg.append(f'<path d="M75 {y(value):.2f}H1085" stroke="#293748"/><text x="1093" y="{y(value)+4:.2f}" fill="#b2c1d1" font-size="12">{value:,.0f}</text>')
    w=max(3,1010/(hi-lo)*.6)
    for j in range(lo,hi):
        b=bars[j]; color='#40d3ab' if b.close>=b.open else '#ef7485'
        title=html.escape(f'{date(j)} UTC | O {b.open} H {b.high} L {b.low} C {b.close}')
        svg.append(f'<g><title>{title}</title><path d="M{x(j):.2f} {y(b.high):.2f}V{y(b.low):.2f}" stroke="{color}"/><rect x="{x(j)-w/2:.2f}" y="{min(y(b.open),y(b.close)):.2f}" width="{w:.2f}" height="{max(1,abs(y(b.open)-y(b.close))):.2f}" fill="{color}"/></g>')
    svg.append(f'<path d="M75 {y(lv.price):.2f}H1085" stroke="#ffd166" stroke-width="2"/><text x="80" y="{y(lv.price)-9:.2f}" fill="#ffd166" font-size="14">Линия робота {lv.price:,.2f}</text>')
    svg.append(f'<path d="M{x(index)-25:.2f} {y(bars[index].close):.2f}h50" stroke="white" stroke-width="3"/><circle cx="{x(index):.2f}" cy="{y(peak):.2f}" r="7" fill="none" stroke="#ffd166" stroke-width="2"/>')
    svg.append(f'<path d="M{x(start):.2f} {y(start_price):.2f}L{x(index):.2f} {y(peak):.2f}L{x(finish):.2f} {y(finish_price):.2f}" fill="none" stroke="#89baff" stroke-width="2" stroke-dasharray="6 4"/>')
    svg.append(f'<path d="M{x(available):.2f} 55V445" stroke="#c69aff" stroke-dasharray="4 4"/><text x="{x(available)+5:.2f}" y="43" fill="#c69aff" font-size="12">Подтверждение: {date(available)}</text>')
    svg.append(f'<text x="{x(index):.2f}" y="469" text-anchor="middle" fill="#ffd166" font-size="13">БСУ {date(index)}</text>')
    for j in [lo,hi-1]:
        svg.append(f'<text x="{x(j):.2f}" y="493" text-anchor="middle" fill="#b2c1d1" font-size="12">{date(j)}</text>')
    svg.append('</svg>')
    title = 'Верхний излом: рост → разворот вниз' if kind=='H' else 'Нижний излом: падение → разворот вверх'
    info = {'history_end':date(end-1),'bsu':date(index),'line':lv.price,'bsu_high_low':peak,
            'bsu_close':bars[index].close,'atr14':atr,'incoming_atr':incoming/atr,
            'reversal_atr':reversal/atr,'earliest_structural_confirmation':date(available),
            'touches':lv.touch_count,'status':lv.kb_status,'rejects':lv.kb_hard_rejects,
            'last_atr':tail_atr,'basis':lv.basis_tags}
    return f'''<section><h2>{title}</h2><p>BTCUSDT · Bybit linear · D1 · срез истории до {date(end-1)} UTC.</p>
    {''.join(svg)}<div class="metrics"><span>Движение к БСУ: <b>{incoming/atr:.2f} ATR</b></span>
    <span>Обратное движение: <b>{reversal/atr:.2f} ATR</b></span><span>ATR14 у БСУ: <b>{atr:,.2f}</b></span>
    <span>Касаний во всём срезе: <b>{lv.touch_count}</b></span><span>Валидатор на конец среза: <b>{lv.kb_status}</b></span></div>
    <p>High/low БСУ: <b>{peak:,.2f}</b>; close БСУ (белая риска): <b>{bars[index].close:,.2f}</b>.
    Линия робота: <b>{lv.price:,.2f}</b>. Касания и статус выше вычислены на всём срезе, а не в день БСУ.</p>
    <details><summary>Точные расчёты и признаки</summary><pre>{html.escape(json.dumps(info,ensure_ascii=False,indent=2))}</pre></details></section>''', info

sections=[]; examples=[]
for end,index,kind in [(250,240,'H'),(200,43,'L')]:
    section,info=render(end,index,kind); sections.append(section); examples.append(info)
page='''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Как робот определяет излом тренда</title><style>
body{background:#0c1420;color:#e6edf5;font:16px/1.6 system-ui;margin:0 auto;max-width:1200px;padding:30px}
h1{font-size:32px}h2{font-size:23px}p{color:#bfcbd9}section{background:#121f30;border:1px solid #2b3c50;border-radius:14px;padding:24px;margin:24px 0}
svg{width:100%;height:auto}.metrics{display:flex;gap:20px;flex-wrap:wrap}.metrics span{background:#20324a;padding:10px;border-radius:7px}
pre{white-space:pre-wrap}a{color:#89baff}.note{border-left:4px solid #ffd166;padding:14px;background:#252638}li{margin:12px 0}</style>
<h1>Как робот определяет уровень излома тренда</h1>
<p>Два примера из сохранённых свечей. Это разбор фактического кода, без изменения стратегии и без запроса котировок в сеть.</p>
<p>Жёлтая линия — цена уровня; жёлтый круг — high/low БСУ; белая риска — его закрытие.
Синяя ломаная — измеряемые движения. Затемнение справа от БСУ — последующие бары,
которые алгоритм использует для подтверждения. Наведите курсор на свечу для OHLC.</p>
<div class="note">Это историческая разметка. В день БСУ будущий разворот ещё неизвестен.
Фиолетовая линия — первый бар, после которого известны и правое крыло экстремума,
и обратное движение ≥ 2,5 ATR. Это не момент допуска к сделке.</div>
'''+''.join(sections)+'''
<section><h2>Алгоритм текущего кода</h2><ol>
<li>Найти локальные high/low: сравнить бар с тремя барами слева и тремя справа.</li>
<li>Сгруппировать близкие экстремумы: расстояние до первого элемента группы ≤ 0,08 последнего ATR14.
Цена линии — среднее цен в группе, БСУ — самый ранний бар группы.</li>
<li>Вычислить ATR14 по 14 барам строго перед БСУ. Для ранней истории, где ATR ещё нет, код подставляет последний ATR среза.</li>
<li>Для верхнего излома: high БСУ − минимальный low участка от i−20 до i ≥ 2,5 ATR.
Для нижнего: максимальный high этого участка − low БСУ ≥ 2,5 ATR.</li>
<li>Проверить обратный ход на барах i+1…i+20: для верхнего high БСУ − минимальный low ≥ 2,5 ATR;
для нижнего максимальный high − low БСУ ≥ 2,5 ATR. При успехе добавить признак inflection.</li>
<li>Посчитать группы касаний с допуском 0,06 последнего ATR, ложные пробои, распил и другие признаки.
Оставить уровни не далее трёх последних ATR от текущей цены; передать их валидатору.
Признак inflection сам по себе не означает pass и не гарантирует отображение в live-пакете.</li></ol>
<h2>Граница между правилом базы и реализацией</h2>
<p>Запись MM-010-035 описывает остановку сильного движения с заметным разворотом.
Порог 2,5 ATR, окна 20 баров и крыло 3 — параметры реализации, а не цитата этого правила.</p>
<p>MM-010-037 отдельно указывает выбирать бар по закрытию и не переносить уровень на хвост последующего ложного пробоя.
Текущий код использует экстремумы high/low и среднюю цену группы. Поэтому его линию нельзя автоматически считать точным исполнением этого правила.
Белая риска показывает close выбранного кодом БСУ, но не является автоматически исправленным уровнем по базе.</p>
<p>Код также измеряет диапазон до экстремума, а не доказывает последовательный направленный тренд.
Разметка может меняться при добавлении свечей из-за нового ATR, группировки и подтверждений.</p>
</section></html>'''
output=Path(__file__).parent
(output/'inflection_explained.html').write_text(page,encoding='utf-8')
(output/'inflection_examples.json').write_text(json.dumps({'source':str(source),'sha256':hashlib.sha256(raw).hexdigest(),'examples':examples},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(examples,ensure_ascii=True,indent=2))
