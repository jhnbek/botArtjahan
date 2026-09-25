"""Quality of the actual price origin and the movements stopped by a level.

All inputs are ordinary contacts of closed bars. No manual target price enters
the calculation. The limits below are explicit ranking heuristics, not a claim
that a small candle can never form a useful limit group.
"""
from math import isclose, isfinite


def progressive_body_strength(body_atr):
    """Continuous size weight: 1 ATR = 1 point, tending to 2.5 points.

    This explicit ranking heuristic has no jump at the paranormal threshold.
    It keeps growing above the former hard cap while limiting outlier influence.
    """
    if not isfinite(body_atr) or body_atr < 0:
        raise ValueError('body / ATR must be finite and non-negative')
    return 2.5 * (body_atr / (body_atr + 1.5))


def body_strength_evidence(bars, contacts, events, contexts, sources):
    """Weight admissible ordinary bars, once per bar and never an LP bar.

    Sources are (index, justification, known_index). A clean group may support
    a bar even when it is not the best departure candle in its reaction episode.
    """
    forbidden = {i for e in events if e.get('entry_context_only')
                 for i in e.get('indices', [e['index']])}
    evidence = {}
    for index, source, known in sources:
        if index in forbidden:
            continue
        for event in contacts:
            if event['index'] != index or event.get('atr', 0) <= 0:
                continue
            context = contexts.get((index, event['kind']), {})
            if not context.get('basis'):
                continue
            ratio = abs(bars[index].close-bars[index].open)/event['atr']
            ready = max(index, known, context['known_index'], event.get('known_index', index))
            if ready >= len(bars):
                continue
            record = evidence.setdefault(index, {
                'index': index, 'known_index': ready, 'body_atr': ratio,
                'weight': progressive_body_strength(ratio), 'sources': [],
            })
            record['known_index'] = min(record['known_index'], ready)
            if source not in record['sources']:
                record['sources'].append(source)
    ranked = sorted(evidence.values(), key=lambda e: (-e['weight'], e['index']))
    for number, record in enumerate(ranked):
        record['contributes_to_score'] = number < 2
    return sorted(ranked, key=lambda e: e['index'])


def dense_stopping_group(contacts, contexts, price, precision_atr=0.02, chop_runs=()):
    """A compact group containing the quoted price, without chained outliers."""
    valid = sorted((e for e in contacts
                    if e.get('price') is not None and e.get('atr', 0) > 0
                    and contexts.get((e['index'], e['kind']), {}).get('basis')),
                   key=lambda e: (e['price'], e['index']))
    groups = []
    for start, first in enumerate(valid):
        group = []
        tolerance = float('inf')
        for event in valid[start:]:
            lo, hi = min(price, first['price']), max(price, event['price'])
            if hi - lo > tolerance:
                break
            proposed_tolerance = min(tolerance, precision_atr * event['atr'])
            if hi - lo > proposed_tolerance:
                continue
            tolerance = proposed_tolerance
            group.append(event)
            indices = sorted({e['index'] for e in group})
            if len(indices) < 2:
                continue
            if any(indices[0] < r['indices'][-1] and r['indices'][0] < indices[-1]
                   for r in chop_runs):
                continue
            span = max(e['price'] for e in group) - first['price']
            density = min(len(indices), 4) / (1 + span / tolerance)
            groups.append({'indices': indices, 'price_span': span,
                           'tolerance': tolerance, 'density': density,
                           'known_index': max(contexts[e['index'], e['kind']]['known_index']
                                              for e in group)})
    return min(groups, key=lambda g: (-g['density'], g['indices'][0])) if groups else None


def origin_evidence(bars, level, profile, contexts, episodes, p, chop_runs=()):
    """Do not nominate a weak isolated exact wick using another bar's reaction.

    A small BSU is valid when a dense held group supports it. Otherwise the
    origin itself needs an ordinary departure, or an independently established
    inflection / reinforced round origin. False-breakout contacts never count.
    """
    from level_selection import ordinary_departures

    contacts = [e for e in profile['events'] if e.get('confirms_level')]
    own = [e for e in contacts if e['index'] == level.bsu_index
           and e.get('price') is not None
           and isclose(e['price'], level.price, rel_tol=1e-12, abs_tol=1e-9)]
    group = dense_stopping_group(contacts, contexts, level.price, chop_runs=chop_runs)
    inflection = bool(level.inflection_check and level.inflection_check.get('status') == 'confirmed')
    round_origin = profile.get('reinforced_round_origin')
    clean_group = profile.get('clean_limit_group')
    if clean_group and any(min(clean_group['indices']) < r['indices'][-1]
                           and r['indices'][0] < max(clean_group['indices']) for r in chop_runs):
        clean_group = None
    structural = ((inflection and bool(own))
                  or bool(round_origin and level.bsu_index in round_origin['indices'])
                  or bool(clean_group and level.bsu_index in clean_group['indices'])
                  or bool(group and level.bsu_index in group['indices']))
    own_events = own + [e for e in profile['events'] if e.get('entry_context_only')]
    own_reactions = ordinary_departures(bars, level.price, own_events, p,
                                       contexts=contexts)
    valid = bool(own and (structural or own_reactions))
    first = own[0] if own else None
    body = (abs(bars[first['index']].close - bars[first['index']].open) / first['atr']
            if first and first['atr'] > 0 else None)

    # Arrival counts once per independent reaction. Body size is a separate
    # continuous factor, including legitimate dense-group confirmation bars.
    movements = []
    body_sources = []
    for episode in episodes:
        best = episode['best']
        i = best['index']
        event = next((e for e in contacts if e['index'] == i
                      and e['kind'] == best.get('kind')), None)
        context = best.get('stopping_context', {})
        if event is None or not context.get('basis'):
            continue
        body_atr = abs(bars[i].close - bars[i].open) / event['atr']
        arrival = max(0.0, context.get('arrival_atr', 0.0))
        magnitude = min(2.5, arrival)
        movements.append({'index': i, 'known_index': best['known_index'],
                          'body_atr': body_atr, 'arrival_atr': arrival,
                          'magnitude': magnitude})
        body_sources.append((i, 'ordinary_reaction', best['known_index']))
    if group:
        body_sources.extend((i, 'dense_group', group['known_index']) for i in group['indices'])
    if valid and first:
        origin_ready = []
        if own_reactions:
            origin_ready.append(min(e['best']['known_index'] for e in own_reactions))
        if inflection:
            origin_ready.append(level.inflection_check.get('confirmation_index', level.bsu_index))
        for foundation in (round_origin, clean_group, group):
            if foundation and level.bsu_index in foundation['indices']:
                origin_ready.append(foundation['known_index'])
        if origin_ready:
            body_sources.append((level.bsu_index, 'price_origin', min(origin_ready)))
    body_evidence = body_strength_evidence(bars, contacts, profile['events'], contexts, body_sources)
    body_score = sum(e['weight'] for e in body_evidence if e['contributes_to_score'])
    stopped_score = sum(sorted((m['magnitude'] for m in movements), reverse=True)[:2])
    density_score = group['density'] if group else 0.0
    return {'valid': valid, 'bsu_body_atr': body, 'dense_group': group,
            'own_reactions': own_reactions, 'stopped_movements': movements,
            'stopped_movement_score': stopped_score, 'density_score': density_score,
            'arrival_strength_score': stopped_score,
            'body_strength_evidence': body_evidence, 'body_strength_score': body_score,
            'body_strength_rule': '2.5 * body_atr / (body_atr + 1.5); top 2 distinct ordinary bars',
            'strength_bonus': stopped_score + density_score + body_score}
