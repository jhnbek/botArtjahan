"""Typed chart views and OHLC evidence, independent of Qt."""
from datetime import datetime, timezone
import json
from math import isfinite, isclose
from pathlib import Path

from level_discovery import Bar, DiscoveryParams, discover_levels, level_report, bar_time
from level_history import daily_level_history
from level_structure import StructureParams, level_events, atr_series, contact_is_excluded


MODE_LABELS = {
    'all_levels': 'Все рабочие уровни',
    'inflection': 'Изломы тренда',
    'mirror_limit': 'Зеркальные и лимитные уровни',
    'paranormal': 'Уровни паранормального бара',
    'matched_review': '15 совпавших уровней · разбор 23.09.2026',
    'manual_candidates': 'Кандидаты рядом с моими уровнями (±1%)',
    'working_31_review': '31 уровень робота · сохранённый расчёт',
}


def working_31_review_snapshot():
    """Load the entire reported selection, without live data or a top-N cap."""
    path = (Path(__file__).resolve().parents[1] / '_knowledge_base/manual_reviews'
            / 'working_31_review_20260925/chart_snapshot.json')
    result = json.loads(path.read_text(encoding='utf-8'))
    levels = result['inflection_levels']
    if (result.get('review_mode') != 'working_31_review'
            or len(levels) != 31 or len({v['price'] for v in levels}) != 31):
        raise ValueError('Сохранённый набор из 31 уровня повреждён. Проверьте chart_snapshot.json.')
    return result


def matched_review_snapshot(*, excluded_contacts=()):
    """Display the reported matches on their original candles, never live prices."""
    from level_discovery import Level
    from level_structure import level_profile
    root = Path(__file__).resolve().parents[1]/'_knowledge_base/manual_reviews'
    comparison = json.loads((root/'strong_levels_algorithm_20260923/matched_15.json').read_text(encoding='utf-8'))
    snapshot = json.loads((root/'strong_levels_review_20260923/candles.json').read_text(encoding='utf-8'))
    instant = int(datetime.fromisoformat(comparison['as_of'].replace('Z','+00:00')).timestamp()*1000)
    rows = closed_candle_rows(snapshot['bars'],as_of_ms=instant)
    rows, history_window = daily_level_history(rows, interval='1d', as_of_ms=instant)
    bars = [Bar(int(r['open_time_ms']),*[float(r[k]) for k in ('open','high','low','close','volume')]) for r in rows]
    dates = {bar_time(b):i for i,b in enumerate(bars)}
    selected = [v for v in comparison['levels'] if v['price_match_0_1_percent']]
    if len(selected) != 15:
        raise ValueError('В сохранённом отчёте уже не 15 совпадений. Обновите режим сравнения с новым отчётом.')
    levels=[]
    expired=[]
    for match in selected:
        i = dates.get(match['bsu_time'])
        if i is None:
            # Keep archived prices fixed, but do not revive a BSU outside the
            # currently permitted historical window.
            expired.append({'review_id':match['id'], 'price':match['automatic_price'],
                            'bsu_time':match['bsu_time']})
            continue
        price = match['automatic_price']
        kind = 'H' if abs(bars[i].high-price) <= abs(bars[i].low-price) else 'L'
        structure = level_profile(bars,price,i,kind,StructureParams(excluded_contacts=excluded_contacts))
        level = Level(price,i,match['bsu_time'],'resistance' if kind=='H' else 'support',
                      basis_tags=match['basis_tags'],source='saved_automatic_review',structure=structure)
        report=level_report(level)
        report.update(review_mode='matched_review',review_id=match['id'],
                      matched_user_price=match['expected_price'],match_error_percent=match['price_error_percent'],
                      chart_title=match['id'],chart_markers=evidence_markers(bars,level,'mirror_limit',DiscoveryParams(excluded_contacts=excluded_contacts)))
        levels.append(report)
    return {'exchange':'bybit','market':'linear','symbol':'BTCUSDT','interval':'1d',
            'bars':rows,'inflection_levels':levels,'review_mode':'matched_review',
            'review_as_of':comparison['as_of'],'history_window':history_window,
            'archived_match_count':len(selected),'excluded_archived_levels':expired,'packet':{}}
MODE_TAGS = {
    'all_levels': {'mirror_level', 'limit_level', 'two_bar_limit', 'paranormal_bar', 'inflection'},
    'mirror_limit': {'mirror_level', 'limit_level', 'two_bar_limit'},
    'paranormal': {'paranormal_bar'},
}
EVENT_LABELS = {
    'touch': 'Касание',
    'near_touch': 'Подход с допустимым недоходом',
    'long_tail_rejection': 'Отбой длинным хвостом',
    'false_breakout': 'Ложный пробой одним баром · контекст входа',
    'false_breakout_two_bar': 'Ложный пробой двумя барами · контекст входа',
}


def active_bsu_rejections(records, chart_key=None):
    """Replay bar-only corrections in journal order, without relabelling levels."""
    active={}
    for record in records:
        action=record.get('action')
        if action not in ('reject_robot_bsu','restore_robot_bsu'):
            continue
        try:
            context=tuple(str(record[k]) for k in ('exchange','symbol','interval'))
            key=(*context,float(record['price']),int(record['bar_open_time_ms']))
        except (KeyError,ValueError,TypeError):
            continue
        if chart_key is not None and context != tuple(chart_key):
            continue
        if action == 'reject_robot_bsu':
            active.pop(key,None)
            active[key]=record
        elif key in active:
            target=record.get('rejected_recorded_at')
            if target is None or target == active[key].get('recorded_at'):
                active.pop(key)
    return active


def rejected_contact_constraints(records, chart_key):
    """Only currently active, explicitly rejected bar/level associations."""
    result=[]
    for record in active_bsu_rejections(records,chart_key).values():
        kind=(record.get('marker') or {}).get('kind')
        if kind in ('H','L'):
            result.append((int(record['bar_open_time_ms']),kind,float(record['price'])))
    return tuple(result)


def closed_candle_rows(rows, *, as_of_ms=None):
    """Return only the closed prefix, preserving the original chart indices.

    Feeds provide chronological, fixed-interval candles. Infer that interval
    from the last two opening times; one candle cannot establish its duration.
    ``as_of_ms`` is an explicit UTC instant for deterministic historical checks.
    The caller can keep rendering the original rows, including the open candle.
    """
    if len(rows) < 2:
        return []
    duration = int(rows[-1]['open_time_ms']) - int(rows[-2]['open_time_ms'])
    if duration <= 0:
        return []
    instant = int(datetime.now(timezone.utc).timestamp()*1000) if as_of_ms is None else int(as_of_ms)
    end = len(rows)
    while end and int(rows[end-1]['open_time_ms'])+duration > instant:
        end -= 1
    return rows[:end]


def marker_tooltip(point):
    lines = ['; '.join(point['roles']), point['time'], f"Цена: {point['price']:g}"]
    for event in point.get('evidence', []):
        entry_only = event.get('role', '').startswith('false_breakout')
        if event.get('known_time'):
            lines.append(f"Событие подтверждено после закрытия бара: {event['known_time']}")
        if entry_only:
            lines.append('Контекст для точки входа; уровень не подтверждает и не усиливает.')
            if event.get('context_source') == 'prior_origin':
                lines.append(f"Ложный пробой прежнего уровня {event['protected_origin_price']:g} "
                             f"от {event['protected_origin_time']}; не касание этой линии.")
        elif event.get('reaction_confirmed_time'):
            lines.append(f"Сильная реакция после закрытия бара: {event['reaction_confirmed_time']}")
    return '\n'.join(dict.fromkeys(lines))


def feedback_applies_to_mode(record, mode):
    recorded = record.get('review_mode')
    if mode == 'working_31_review':
        # The user explicitly requested the pre-rejection set of 31. Preserve
        # previous reviews; only corrections made in this view hide its lines.
        return recorded == mode
    if not recorded:
        # Old restores were global; old inflection deletions have basis tags.
        recorded = ('inflection' if record.get('action') == 'hide_robot_level'
                    and 'inflection' in record.get('basis_tags', []) else 'all_levels')
    if recorded == 'secondary_limit':
        recorded = 'inflection'
    return recorded in (mode, 'all_levels')


def rejected_level_prices(records, chart_key, mode):
    """Replay whole-level corrections within one instrument and chart mode.

    These are explicit user constraints, separate from general strength rules.
    Replay only applicable records so restoring a level in another mode cannot
    undo its rejection here. Manual lines and bar-only corrections are unrelated.
    """
    active = {}
    context_key = tuple(str(value) for value in chart_key)
    for record in records:
        if not isinstance(record, dict):
            continue
        action = record.get('action')
        if action not in ('hide_robot_level', 'restore_robot_level'):
            continue
        if not feedback_applies_to_mode(record, mode):
            continue
        try:
            context = tuple(str(record[key]) for key in ('exchange', 'symbol', 'interval'))
            price = float(record['price'])
        except (KeyError, TypeError, ValueError):
            continue
        if context != context_key or not isfinite(price) or price <= 0:
            continue
        if action == 'hide_robot_level':
            active[price] = record
        else:
            active.pop(price, None)
    return tuple(sorted(active))


WORKING_LEVEL_MODES = frozenset(('all_levels', 'mirror_limit', 'paranormal', 'inflection'))
WHOLE_LEVEL_REVIEW_MODES = ('manual_candidates', 'working_31_review')


def reviewed_level_rejections(records, chart_key):
    """Active whole-candidate removals, replayed separately for each review.

    The user requested these chart reviews to affect subsequent live discovery.
    Restores cancel only the source review's removal; another review's active
    objection remains. Old inflection/type-only reviews are deliberately absent:
    a bar can be an invalid inflection and still supply a valid limit level.
    """
    records = tuple(records)
    return {
        mode: rejected_level_prices(
            (record for record in records
             if isinstance(record, dict) and record.get('review_mode') == mode),
            chart_key, mode)
        for mode in WHOLE_LEVEL_REVIEW_MODES
    }


def working_rejected_level_prices(records, chart_key, mode):
    """Apply reviewed whole-level negatives before live strength/spacing selection.

    Archived/candidate chart views keep their own display state. Live modes keep
    their existing type-scoped corrections and also respect whole-level reviews.
    This excludes exact reviewed prices, without snapping prices to manual lines,
    treating untouched candidates as accepted, or inventing a forbidden zone.
    """
    records = tuple(records)
    active = set(rejected_level_prices(records, chart_key, mode))
    if mode in WORKING_LEVEL_MODES:
        for prices in reviewed_level_rejections(records, chart_key).values():
            active.update(prices)
    return tuple(sorted(active))


def active_manual_prices(records, chart_key):
    """Use actual current chart lines, not historical approximate references."""
    active = set()
    for record in records:
        if tuple(record.get(k) for k in ('exchange', 'symbol', 'interval')) != tuple(chart_key):
            continue
        action = record.get('action')
        if action == 'clear_manual_levels':
            active.clear()
            continue
        try:
            price = float(record['price'])
        except (KeyError, TypeError, ValueError):
            continue
        if not isfinite(price) or price <= 0:
            continue
        if action == 'add_manual_level':
            active.add(price)
        elif action == 'remove_manual_level':
            active.discard(price)
    return tuple(sorted(active))


def manual_candidate_levels(rows, manual_prices, *, as_of_ms=None, interval=None,
                            excluded_contacts=(), excluded_level_prices=()):
    """Review all discovered price candidates within 1% of an active manual line.

    This is a supervised chart review, not automatic working-level selection.
    Keep the detector's price/BSU rules and bypass strength/spacing selection.
    """
    references = sorted({float(p) for p in manual_prices if isfinite(float(p)) and float(p) > 0})
    rows = closed_candle_rows(rows, as_of_ms=as_of_ms)
    if not references or not rows:
        return []
    bars = [Bar(int(r['open_time_ms']), *[float(r[k]) for k in
            ('open', 'high', 'low', 'close', 'volume')]) for r in rows]
    params = DiscoveryParams(nearest_window_atr=float('inf'), working_selection=False,
                             excluded_contacts=excluded_contacts)
    result = []
    for level in discover_levels(bars, params, as_of_ms=as_of_ms, interval=interval):
        if any(isclose(level.price, price, rel_tol=0, abs_tol=1e-9) for price in excluded_level_prices):
            continue
        matches = [{'manual_price':price, 'deviation_percent':abs(level.price-price)/price*100}
                   for price in references
                   if abs(level.price-price)/price <= .01
                   or isclose(abs(level.price-price)/price, .01, rel_tol=1e-12, abs_tol=1e-15)]
        if not matches:
            continue
        names = {'inflection':'излом тренда', 'paranormal_bar':'паранормальный бар',
                 'strong_movement_stop':'остановка движения', 'mirror_level':'зеркальный',
                 'limit_level':'лимитный', 'two_bar_limit':'лимитный двумя барами',
                 'round_number':'круглая цена'}
        basis = ', '.join(names[tag] for tag in level.basis_tags if tag in names) or 'экстремум'
        confirmed_inflection = level.inflection_check.get('status') == 'confirmed'
        title = 'Кандидат · ' + basis
        if not confirmed_inflection:
            title += ' · излом не подтверждён'
        markers = evidence_markers(bars, level, 'mirror_limit', params)
        if not confirmed_inflection and not level.structure.get('strong'):
            for marker in markers:
                marker['roles'] = ['Бар-кандидат' if role == 'БСУ' else role for role in marker['roles']]
        report = level_report(level)
        report.update(structure=level.structure, review_mode='manual_candidates',
                      chart_title=title, manual_neighbors=matches, chart_markers=markers)
        result.append(report)
    return sorted(result, key=lambda level: level['price'])


def evidence_markers(bars, level, mode, params):
    """Use the detector's classified evidence, preserving consecutive contacts.

    A BSU marker is always explicit. Plot the actual wick price, not a fabricated
    point at the averaged level. Duplicate roles at the same point are merged.
    """
    points = {}

    def add(index, kind, role, symbol='o', evidence=None):
        if not 0 <= index < len(bars):
            return
        bar = bars[index]
        point = points.setdefault((index, kind, symbol), {
            'index': index, 'time': bar_time(bar), 'kind': kind,
            'price': bar.high if kind == 'H' else bar.low, 'roles': [],
            'symbol': symbol, 'evidence': [],
        })
        if role not in point['roles']:
            point['roles'].append(role)
        if evidence and evidence not in point['evidence']:
            point['evidence'].append(evidence)

    i = level.bsu_index
    if 0 <= i < len(bars):
        kind = 'H' if abs(bars[i].high-level.price) <= abs(bars[i].low-level.price) else 'L'
        local_atr = atr_series(bars,params.atr_period)[i] or level.atr
        if not contact_is_excluded(bars[i],level.price,kind,local_atr,StructureParams(excluded_contacts=params.excluded_contacts)):
            add(i, kind, 'Паранормальный бар' if mode == 'paranormal' else 'БСУ')
    if mode in ('mirror_limit', 'all_levels'):
        structure = getattr(level, 'structure', None)
        if structure:
            for event in structure.get('events', []):
                role = event.get('role', 'touch')
                if role == 'chop':
                    continue
                symbol = 'x' if role in ('false_breakout', 'false_breakout_two_bar') else 'o'
                label = EVENT_LABELS.get(role, role)
                indices = event.get('indices') or [event.get('index')]
                for number, index in enumerate(indices):
                    if not isinstance(index, int) or not 0 <= index < len(bars):
                        continue
                    kind = event.get('kind')
                    if kind not in ('H', 'L'):
                        kind = 'H' if abs(bars[index].high-level.price) <= abs(bars[index].low-level.price) else 'L'
                    suffix = (' · выход' if number == 0 else ' · возврат') if role == 'false_breakout_two_bar' else ''
                    add(index, kind, label+suffix, symbol, event)
            return [points[key] for key in sorted(points)]
        # Compatibility for saved reports without classified evidence. New
        # automatic typed views require a strength profile below.
        atrs = [value or level.atr for value in atr_series(bars,params.atr_period)]
        for event in level_events(bars,level.price,StructureParams(atr_period=params.atr_period,excluded_contacts=params.excluded_contacts),atrs):
            if event['confirms_level']:
                add(event['index'],event['kind'],EVENT_LABELS[event['role']],evidence=event)
    return [points[key] for key in sorted(points)]


def typed_review_levels(rows, mode, *, as_of_ms=None, interval=None, excluded_contacts=(),
                        excluded_level_prices=()):
    if mode not in MODE_TAGS:
        raise ValueError(f'Unknown typed chart mode: {mode}')
    rows = closed_candle_rows(rows, as_of_ms=as_of_ms)
    if not rows:
        return []
    bars = [Bar(int(row['open_time_ms']), *[float(row[key]) for key in
            ('open', 'high', 'low', 'close', 'volume')]) for row in rows]
    params = DiscoveryParams(nearest_window_atr=float('inf'),excluded_contacts=excluded_contacts,
                             excluded_level_prices=excluded_level_prices)
    result = []
    for level in discover_levels(bars, params, as_of_ms=as_of_ms, interval=interval):
        if not MODE_TAGS[mode].intersection(level.basis_tags):
            continue
        structure = getattr(level, 'structure', {}) or {}
        if mode == 'mirror_limit' and not structure.get('strong'):
            continue
        report = level_report(level)
        report['structure'] = structure
        report['review_mode'] = mode
        report['chart_markers'] = evidence_markers(bars, level, mode, params)
        report['marker_tolerance'] = None if mode in ('mirror_limit', 'all_levels') else params.contact_tol_atr * level.atr
        report['marker_tolerance_basis'] = ('local_atr_at_each_contact' if mode in ('mirror_limit', 'all_levels') else 'latest_atr')
        if mode == 'paranormal':
            report['chart_title'] = 'Паранормальный бар'
        else:
            names = []
            if 'mirror_level' in level.basis_tags:
                names.append('Зеркальный')
            if {'limit_level', 'two_bar_limit'}.intersection(level.basis_tags):
                names.append('Лимитный')
            if mode == 'all_levels':
                if 'paranormal_bar' in level.basis_tags:
                    names.append('Паранормальный бар')
                if 'inflection' in level.basis_tags:
                    names.append('Излом тренда')
            report['chart_title'] = ' / '.join(names)
        result.append(report)
    return result
