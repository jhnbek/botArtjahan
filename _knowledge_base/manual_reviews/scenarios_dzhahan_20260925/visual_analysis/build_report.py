"""Assemble manually inspected pairs; never infer a review from file existence."""
import json
import hashlib
from pathlib import Path
from collections import Counter
from html import escape

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
rows = []
for path in sorted(HERE.glob('batch_*.jsonl')):
    rows.extend(json.loads(line) for line in path.read_text(encoding='utf-8-sig').splitlines() if line.strip())
rows.sort(key=lambda row: row['scenario_id'])
assert [row['scenario_id'] for row in rows] == list(range(1, 410)), 'Incomplete or duplicate review'
manifest = json.loads((ROOT/'manifest.json').read_text(encoding='utf-8'))
images = {im['relative_path']: im for sc in manifest['scenarios'] for files in sc['timeframes'].values() for im in files}
reviewed = []
for row in rows:
    assert row['direction'] in ('long', 'short', 'unclear')
    assert len(row['image_files']) == row['reviewed_images_count']
    row['image_sha256'] = {}
    for name in row['image_files']:
        assert name in images
        digest = hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
        assert digest == images[name]['sha256']
        row['image_sha256'][name] = digest
        reviewed.append(name)
    row.update(review_status='visually_reviewed', outcome_source='user_described_successful',
               outcome_verified=False, levels_source='user_blue_horizontal_lines',
               market_verified=False, training_status='annotation_only',
               price_level=None, entry_price=None, stop_price=None, realized_return=None)
assert len(reviewed) == len(set(reviewed)) == 817
(HERE/'scenario_analysis.jsonl').write_text(''.join(json.dumps(row, ensure_ascii=False)+'\n' for row in rows),encoding='utf-8')
summary = {'scenarios_reviewed': len(rows), 'images_visually_reviewed': len(reviewed),
           'complete_pairs': 408, 'incomplete_pairs': [85],
           'directions': dict(Counter(row['direction'] for row in rows)),
           'scenarios_with_review_notes': [row['scenario_id'] for row in rows if row['ambiguity']],
           'image_sha256_verified': 817, 'outcomes_verified': 0, 'model_training_run': False}
(HERE/'coverage.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
details = ['# Разбор всех сценариев «Джахан»', '',
           'Синие линии приняты как заданные уровни. Подписи автора и визуальная интерпретация сохранены раздельно. Направление — интерпретация отмеченного входа, не торговая рекомендация.', '',
           'Дата и тикер переписаны с картинки; цены, ATR, стопы и результат не восстановлены догадкой. Движение справа от входа — известный исход, а не признак для обучения. Приблизительные оценки по пикселям публикуются отдельно.', '']
for row in rows:
    n = row['scenario_id']
    details += [f'## Сценарий {n}', '',
                f"**{row['instrument'] or 'Тикер не указан'} · {row['date_label'] or 'Дата не указана'} · {row['direction']}**", '',
                ' · '.join(f'[{Path(p).stem}](../{p})' for p in row['image_files']), '',
                f"Дневка: {row['daily_pattern']}", '', f"Часовик: {row['hourly_entry']}", '',
                'Подписи/пояснения автора: ' + '; '.join(row['annotations']), '']
    if row['ambiguity']:
        details += ['Нужна сверка: ' + '; '.join(row['ambiguity']), '']
(HERE/'all_scenarios.md').write_text('\n'.join(details),encoding='utf-8')
data = json.dumps(rows, ensure_ascii=False).replace('<','\\u003c')
html = '''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Джахан — разбор 409 сценариев</title>
<style>body{font:17px/1.5 system-ui;margin:0;background:#101827;color:#e8eef8}header,main{max-width:1500px;margin:auto;padding:24px}header{border-bottom:1px solid #42506a}input,select,button{font:inherit;padding:8px;margin:4px;color:#e8eef8;background:#20304a;border:1px solid #64748b;border-radius:6px}nav{display:flex;gap:8px;flex-wrap:wrap;align-items:center}a{color:#93c5fd}.pair{display:grid;grid-template-columns:1fr 1fr;gap:16px}.pair img{width:100%;height:auto}figure{margin:0}#notes{border-left:4px solid #e7b553;padding-left:16px}li{margin:8px 0}.muted{color:#bec9d9}@media(max-width:850px){.pair{grid-template-columns:1fr}}</style>
<header><h1>409 сценариев · дневка и часовой вход</h1><p>Синие линии — ваши уровни. Разобраны 817 изображений. У №85 отсутствует часовик.</p><p class="muted">Это визуальная разметка примеров. Успех сделки, точные цены и ATR по биржевым данным не проверялись. Отметки «ПН» и «1 ATR» — подписи автора; отдельные оценки по пикселям приблизительны.</p><nav><label>Поиск <input id="search" placeholder="Номер, монета, дата, условие"></label><label><input type="checkbox" id="uncertain">Только с замечаниями</label><button id="prev">←</button><select id="cases" aria-label="Сценарий"></select><button id="next">→</button><span id="count"></span></nav></header>
<main><h2 id="heading"></h2><p id="daily"></p><p id="hourly"></p><ul id="annotations"></ul><p id="notes"></p><div class="pair" id="images"></div><p><a href="README.md">Общие выводы</a> · <a href="all_scenarios.md">Все карточки текстом</a> · <a href="scenario_analysis.jsonl">Разметка JSONL</a> · <a href="atr_pixel_examples.md">Примерный ATR по пикселям</a></p></main>
<script>const rows=__DATA__;const $=id=>document.getElementById(id);let filtered=rows;
function show(){const r=filtered.find(r=>String(r.scenario_id)===$('cases').value);if(!r){$('heading').textContent='Нет совпадений';['daily','hourly','notes','annotations','images'].forEach(id=>$(id).replaceChildren());return}location.hash=String(r.scenario_id);$('heading').textContent=`№${r.scenario_id} · ${r.instrument||'Тикер не указан'} · ${r.date_label||'Дата не указана'} · ${r.direction}`;$('daily').textContent='Дневка: '+r.daily_pattern;$('hourly').textContent='Часовик: '+r.hourly_entry;$('annotations').replaceChildren(...r.annotations.map(a=>{const li=document.createElement('li');li.textContent=a;return li}));$('notes').textContent=r.ambiguity.length?'Нужна сверка: '+r.ambiguity.join(' '):'Отдельных замечаний к прочтению нет; сверка по OHLC ещё не выполнена.';$('images').replaceChildren(...r.image_files.map(p=>{const f=document.createElement('figure'),a=document.createElement('a'),img=document.createElement('img'),cap=document.createElement('figcaption');a.href='../'+p;img.src=a.href;img.alt=p;cap.textContent=p.split('/').pop();a.append(img);f.append(a,cap);return f}));}
function filter(){const q=$('search').value.toLowerCase().trim(),old=$('cases').value||location.hash.slice(1);filtered=rows.filter(r=>(!$('uncertain').checked||r.ambiguity.length)&&(String(r.scenario_id)===q||!q||[r.instrument,r.date_label,r.daily_pattern,r.hourly_entry,...r.annotations,...r.ambiguity].join(' ').toLowerCase().includes(q)));$('cases').replaceChildren(...filtered.map(r=>{const o=document.createElement('option');o.value=r.scenario_id;o.textContent=`№${r.scenario_id} ${r.instrument||'—'}`;return o}));if(filtered.some(r=>String(r.scenario_id)===old))$('cases').value=old;$('count').textContent=filtered.length+' из 409';show()}
$('search').oninput=filter;$('uncertain').onchange=filter;$('cases').onchange=show;for(const [id,step] of [['prev',-1],['next',1]])$(id).onclick=()=>{if(!filtered.length)return;$('cases').selectedIndex=($('cases').selectedIndex+step+filtered.length)%filtered.length;show()};filter();</script></html>'''.replace('__DATA__',data)
(HERE/'index.html').write_text(html,encoding='utf-8')
manifest['summary']['visually_inspected_scenario_ids'] = [r['scenario_id'] for r in rows]
manifest['summary']['visually_inspected_image_count'] = 817
manifest['visual_analysis'] = {'report':'visual_analysis/README.md','cards':'visual_analysis/all_scenarios.md',
                             'dataset':'visual_analysis/scenario_analysis.jsonl','gallery':'visual_analysis/index.html',
                             'method':'visual_reading_of_original_images','market_verified':False,'training_run':False}
for sc in manifest['scenarios']:
    sc['content_review_status'] = 'visually_reviewed'
    sc['analysis_card'] = f"visual_analysis/index.html#{sc['scenario_id']}"
(ROOT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False))
