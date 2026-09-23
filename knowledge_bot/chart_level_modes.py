"""Typed chart views and OHLC evidence, independent of Qt."""
from level_discovery import Bar, DiscoveryParams, discover_levels, level_report, bar_time


MODE_LABELS = {
    'inflection': 'Изломы тренда',
    'mirror_limit': 'Зеркальные и лимитные уровни',
    'paranormal': 'Уровни паранормального бара',
}
MODE_TAGS = {
    'mirror_limit': {'mirror_level', 'limit_level', 'two_bar_limit'},
    'paranormal': {'paranormal_bar'},
}


def feedback_applies_to_mode(record, mode):
    recorded = record.get('review_mode')
    if not recorded:
        # Old restores were global; old inflection deletions have basis tags.
        recorded = ('inflection' if record.get('action') == 'hide_robot_level'
                    and 'inflection' in record.get('basis_tags', []) else 'all_levels')
    if recorded == 'secondary_limit':
        recorded = 'inflection'
    return recorded in (mode, 'all_levels')


def evidence_markers(bars, level, mode, params):
    """All high/low near touches, including consecutive bars; never body crossings.

    A BSU marker is always explicit. Plot the actual wick price, not a fabricated
    point at the averaged level. Duplicate roles at the same point are merged.
    """
    points = {}

    def add(index, kind, role):
        if not 0 <= index < len(bars):
            return
        bar = bars[index]
        point = points.setdefault((index, kind), {
            'index': index, 'time': bar_time(bar), 'kind': kind,
            'price': bar.high if kind == 'H' else bar.low, 'roles': [],
        })
        if role not in point['roles']:
            point['roles'].append(role)

    i = level.bsu_index
    if 0 <= i < len(bars):
        kind = 'H' if abs(bars[i].high-level.price) <= abs(bars[i].low-level.price) else 'L'
        add(i, kind, 'Паранормальный бар' if mode == 'paranormal' else 'БСУ')
    if mode == 'mirror_limit':
        tolerance = params.contact_tol_atr * level.atr
        for j, bar in enumerate(bars):
            for kind, value in (('H', bar.high), ('L', bar.low)):
                if abs(value-level.price) <= tolerance:
                    add(j, kind, 'Касание верхним хвостом' if kind == 'H' else 'Касание нижним хвостом')
        for touch in level.inflection_check.get('limit_confirmations', []):
            j = touch['index']
            kind = 'H' if abs(bars[j].high-level.price) <= abs(bars[j].low-level.price) else 'L'
            add(j, kind, 'Лимитное подтверждение')
        mirror = level.inflection_check.get('mirror_confirmation') or {}
        if mirror:
            j = mirror['index']
            kind = 'H' if abs(bars[j].high-level.price) <= abs(bars[j].low-level.price) else 'L'
            add(j, kind, 'Зеркальное подтверждение')
    return [points[key] for key in sorted(points)]


def typed_review_levels(rows, mode):
    if mode not in MODE_TAGS:
        raise ValueError(f'Unknown typed chart mode: {mode}')
    bars = [Bar(int(row['open_time_ms']), *[float(row[key]) for key in
            ('open', 'high', 'low', 'close', 'volume')]) for row in rows]
    params = DiscoveryParams(nearest_window_atr=float('inf'))
    result = []
    for level in discover_levels(bars, params):
        if not MODE_TAGS[mode].intersection(level.basis_tags):
            continue
        report = level_report(level)
        report['review_mode'] = mode
        report['chart_markers'] = evidence_markers(bars, level, mode, params)
        report['marker_tolerance'] = params.contact_tol_atr * level.atr
        if mode == 'paranormal':
            report['chart_title'] = 'Паранормальный бар'
        else:
            names = []
            if 'mirror_level' in level.basis_tags:
                names.append('Зеркальный')
            if {'limit_level', 'two_bar_limit'}.intersection(level.basis_tags):
                names.append('Лимитный')
            report['chart_title'] = ' / '.join(names)
        result.append(report)
    return result
