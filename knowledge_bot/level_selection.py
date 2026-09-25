"""Select working levels from discovered candidates, without manual price targets.

The contact detector stays available for audits. This stage measures ordinary
departures and selects stronger, sufficiently spaced levels before KB ranking.
"""
from math import isclose, isfinite

from level_structure import StructureParams, atr_series, level_profile, chopping_runs, paranormal_bar
from level_evidence_strength import origin_evidence


def level_type_confluence(bars, level, profile):
    """Different evidenced types strengthen one price; aliases are one type."""
    tags = set(level.basis_tags)
    events = profile.get('events', [])
    forbidden = {i for e in events if e.get('entry_context_only')
                 for i in e.get('indices', [e['index']])}
    contacts = [e for e in events if e.get('confirms_level') and e['index'] not in forbidden]
    kinds = {kind: {e['index'] for e in contacts if e['kind'] == kind} for kind in ('H', 'L')}
    types = []
    if tags & {'limit_level', 'two_bar_limit'} and any(len(indices) >= 2 for indices in kinds.values()):
        types.append('limit_level')
    pair = profile.get('mirror_pair')
    if 'mirror_level' in tags and pair and (
            pair[0] in kinds['H'] and pair[1] in kinds['L']
            or pair[0] in kinds['L'] and pair[1] in kinds['H']):
        types.append('mirror_level')
    if 'paranormal_bar' in tags and any(
            e.get('atr', 0) > 0 and paranormal_bar(bars, e['index'], e['atr']) for e in contacts):
        types.append('paranormal_bar')
    if ('inflection' in tags and level.inflection_check.get('status') == 'confirmed'
            and any(e['index'] == level.bsu_index for e in contacts)):
        types.append('inflection')
    # Explicit initial ranking coefficient, not a learned probability.
    return {'types': types, 'type_count': len(types),
            'strength_bonus': 0.5 * max(0, len(types)-1)}


def enforce_minimum_spacing(levels, min_fraction, score_by_price):
    if not isfinite(min_fraction) or min_fraction < 0:
        raise ValueError('minimum spacing must be finite and non-negative')
    for level in levels:
        if not isfinite(level.price) or level.price <= 0:
            raise ValueError('level prices must be finite and positive')
        if not isfinite(score_by_price[level.price]):
            raise ValueError('strength scores must be finite')
    ranked=sorted(levels,key=lambda lv:(-score_by_price[lv.price],lv.bsu_index,lv.price))
    selected=[]
    audit=[]
    for candidate in ranked:
        conflict=None
        for stronger in selected:
            gap=(max(candidate.price,stronger.price)-min(candidate.price,stronger.price))/min(candidate.price,stronger.price)
            if gap < min_fraction and not isclose(gap,min_fraction,rel_tol=1e-12,abs_tol=1e-15):
                conflict=(stronger,gap)
                break
        if conflict:
            stronger,gap=conflict
            audit.append({'price':candidate.price,'decision':'rejected',
                          'reasons':['too_close_to_stronger_level'],
                          'stronger_price':stronger.price,'gap_percent':gap*100})
        else:
            selected.append(candidate)
            audit.append({'price':candidate.price,'decision':'kept','reasons':[]})
    return selected,audit


def stopping_contexts(bars, price, events, p, runs=()):
    """A close wick needs an arrival/rejection or a clean held series.

    A candle opening near the line while continuing the previous move is not
    an independent stop. Clean adjacent contacts need no large rejection tail.
    """
    contacts = [e for e in events if e.get('confirms_level')]
    forbidden = {i for e in events if e.get('entry_context_only')
                 for i in e.get('indices', [e['index']])}
    contexts = {}
    for event in contacts:
        i, kind, atr = event['index'], event['kind'], event['atr']
        if atr <= 0 or i in forbidden:
            continue
        direction = 1 if kind == 'L' else -1
        bar = bars[i]
        tail = (min(bar.open, bar.close)-bar.low if kind == 'L'
                else bar.high-max(bar.open, bar.close)) / atr
        start = max(0, i-p.working_stop_lookback)
        arrival = (direction*(bars[start].close-bars[i-1].close)/atr if i > start else 0.0)
        arrived_from_held_side = i > 0 and direction*(bars[i-1].close-price) >= 0
        holds = []
        hold_ready = {}
        for other in contacts:
            j = other['index']
            if other['kind'] != kind or not 0 < abs(j-i) <= p.working_hold_max_gap:
                continue
            lo, hi = sorted((i, j))
            if any(k in forbidden or direction*(bars[k].open-price) < 0
                   or direction*(bars[k].close-price) < 0 for k in range(lo, hi+1)):
                continue
            holds.append(j)
            hold_ready[j] = max(j, other.get('known_index', j))
        nearby_runs = [r for r in runs if i-p.working_formation_chop_bars <= r['indices'][-1] <= i]
        # A new clean series can establish a fresh foundation after old chop.
        after_chop_holds = [j for j in holds if not nearby_runs
                           or j > max(r['indices'][-1] for r in nearby_runs)]
        clean_hold = bool(after_chop_holds)
        partner = (min(after_chop_holds, key=lambda j: (max(i, hold_ready[j]), j))
                   if clean_hold else None)
        reason = ('clean_hold' if clean_hold else 'rejection_tail'
                  if tail >= p.working_rejection_tail_atr else 'incoming_stop'
                  if arrived_from_held_side and arrival >= p.working_min_arrival_atr else None)
        if nearby_runs and not clean_hold:
            reason = None
        contexts[i, kind] = {
            'basis': reason, 'tail_atr': max(0.0, tail), 'arrival_atr': arrival,
            'hold_indices': sorted([i, partner]) if clean_hold else [],
            'known_index': max(event.get('known_index', i),
                               hold_ready[partner] if clean_hold else i),
            'formation_chop': nearby_runs,
        }
    return contexts


def interaction_chopping(bars, price, events, atrs, p):
    """Distant future candles cannot dilute historical interaction damage."""
    params = StructureParams(atr_period=p.atr_period)
    pairs = {tuple(e.get('indices', [])) for e in events
             if e.get('role') == 'false_breakout_two_bar'}
    all_runs = chopping_runs(bars, price, params, atrs)
    runs = [r for r in all_runs
            if tuple(r['indices']) not in pairs]
    # An isolated two-bar return is not a hard invalidation, but its crossings
    # still belong to the price's interaction history rather than disappearing.
    crossings = {i for run in all_runs for i in run['indices']}
    interactions = [i for i, bar in enumerate(bars) if atrs[i]
                    and bar.low <= price+params.chop_side_atr*atrs[i]
                    and bar.high >= price-params.chop_side_atr*atrs[i]]
    return {'runs': runs, 'crossing_count': len(crossings),
            'interaction_count': len(interactions),
            'fraction': len(crossings)/max(1, len(interactions))}


def ordinary_departures(bars, price, events, p, minimum=None, *, contexts=None):
    """Fast efficient reactions only; entry-pattern bars cannot supply strength."""
    minimum=p.working_min_reaction_atr if minimum is None else minimum
    forbidden={i for e in events if e.get('entry_context_only') for i in e.get('indices',[e['index']])}
    patterns = [set(e.get('indices', [e['index']])) for e in events if e.get('entry_context_only')]
    result=[]
    for event in events:
        if not event.get('confirms_level') or event['index'] in forbidden:
            continue
        atr=event['atr']
        if atr <= 0:continue
        context = contexts.get((event['index'], event['kind']), {}) if contexts is not None else None
        if context is not None and not context.get('basis'):
            continue
        direction=1 if event['kind']=='L' else -1
        previous=price
        path=0.0
        best=None
        entry_gain = 0.0
        seen_patterns = set()
        started = False
        for i in range(event['index'],min(len(bars),event['index']+p.working_reaction_bars+1)):
            close=bars[i].close
            displacement=direction*(close-price)
            step = direction*(close-previous)
            # Keep the full path until the LP's positive contribution is
            # removed symmetrically from displacement and efficiency below.
            path+=abs(close-previous)
            previous=close
            if i in forbidden:
                entry_gain += max(0.0, step)
                seen_patterns.update(n for n, indices in enumerate(patterns) if i in indices)
                # A previously established clean series can survive repeated
                # sweeps. They still contribute zero positive displacement;
                # only a later ordinary departure can supply strength.
                if len(seen_patterns) > 1 and not (context and context['basis'] == 'clean_hold'
                                                  and context['known_index'] <= i):
                    break
                continue
            if displacement < 0:break
            ordinary_displacement = max(0.0, displacement-entry_gain)
            distance=ordinary_displacement/atr
            # A large LP departure cannot earn strength or dilute a later,
            # independent ordinary reaction. Opposite steps still penalize
            # efficiency, including an outside close in a two-bar LP.
            ordinary_path = max(0.0, path-entry_gain)
            efficiency=ordinary_displacement/ordinary_path if ordinary_path else 0
            if distance >= p.working_reaction_start_atr:
                started = True
            if (context is not None and context['basis'] != 'clean_hold' and not started
                    and i-event['index'] >= p.working_reaction_start_bars):
                break
            if (distance >= minimum and efficiency >= p.working_min_reaction_efficiency
                    and i >= event.get('known_index',event['index'])
                    and (context is None or i >= context['known_index'])):
                value=min(distance,4.0)*efficiency
                if best is None or value>best['quality']:
                    best={'index':event['index'],'known_index':i,'kind':event['kind'],
                          'distance_atr':distance,'efficiency':efficiency,'quality':value}
                    if context is not None:
                        best['stopping_context'] = context
                    best['entry_gain_excluded_atr'] = entry_gain/atr
        if best:result.append(best)
    # Consecutive touches followed by the same departure are one episode.
    episodes=[]
    for item in sorted(result,key=lambda e:e['index']):
        if episodes and item['index'] <= episodes[-1]['end_index']:
            episodes[-1]['end_index']=max(episodes[-1]['end_index'],item['known_index'])
            if item['quality']>episodes[-1]['best']['quality']:episodes[-1]['best']=item
        else:
            episodes.append({'index':item['index'],'end_index':item['known_index'],'best':item})
    return episodes


def unresolved_chopping(profile, first_recent_index):
    """A confirmed two-bar entry pattern alone does not invalidate its level."""
    if not profile.get('currently_chopped'):
        return False
    entry_pairs={tuple(e.get('indices', [])) for e in profile['events']
                 if e.get('role') == 'false_breakout_two_bar'}
    runs=[run for run in profile.get('chopping_runs', [])
          if run['indices'][-1] >= first_recent_index
          and tuple(run['indices']) not in entry_pairs]
    if not runs:
        return False
    last_cross=max(run['indices'][-1] for run in runs)
    recovered=any(e.get('confirms_level') and e['index'] > last_cross
                  and e.get('reaction_confirmed_index') is not None
                  for e in profile['events'])
    return not recovered


def select_working_levels(bars, levels, p, audit=None):
    params=StructureParams(atr_period=p.atr_period,paranormal_lookback=p.paranormal_lookback,
                           excluded_contacts=p.excluded_contacts,
                           automatic_origin_exclusions=p.automatic_origin_exclusions,
                           automatic_origin_events=p.automatic_origin_events)
    atrs=atr_series(bars,p.atr_period)
    records={}
    eligible=[]
    scores={}
    profiles={}
    for level in levels:
        kind='H' if abs(bars[level.bsu_index].high-level.price) <= abs(bars[level.bsu_index].low-level.price) else 'L'
        profile=level.structure or level_profile(bars,level.price,level.bsu_index,kind,params,atrs)
        profiles[level.price]=profile
        chop = interaction_chopping(bars, level.price, profile['events'], atrs, p)
        contexts = stopping_contexts(bars, level.price, profile['events'], p, chop['runs'])
        inflection=bool(level.inflection_check and level.inflection_check.get('status')=='confirmed')
        anchor_context = contexts.get((level.bsu_index, kind))
        if inflection and anchor_context and not anchor_context['formation_chop']:
            # The inflection detector already checked the larger incoming move
            # and reversal. Do not replace it with a three-bar arrival shortcut.
            anchor_context['basis'] = anchor_context['basis'] or 'confirmed_inflection'
            anchor_context['known_index'] = max(anchor_context['known_index'],
                level.inflection_check.get('confirmation_index', level.bsu_index))
        minimum=1.5 if profile.get('clean_limit_group') or profile.get('reinforced_round_origin') else p.working_min_reaction_atr
        episodes=ordinary_departures(bars,level.price,profile['events'],p,minimum,contexts=contexts)
        evidence=origin_evidence(bars,level,profile,contexts,episodes,p,chop['runs'])
        confluence=level_type_confluence(bars,level,profile)
        contacts=[e for e in profile['events'] if e.get('confirms_level')]
        reasons=[]
        if not level.structure.get('strong') and not inflection:
            reasons.append('no_confirmed_structural_basis')
        if any(isclose(level.price,price,rel_tol=1e-12,abs_tol=1e-9) for price in p.excluded_level_prices):
            reasons.append('explicit_user_rejection')
        if not episodes:reasons.append('no_fast_strong_reaction')
        if not evidence['valid']:reasons.append('weak_price_origin')
        if len({e['index'] for e in contacts})<2 and not inflection:
            reasons.append('insufficient_clean_confirmations')
        if unresolved_chopping(profile, len(bars)-params.chop_window):
            reasons.append('unresolved_chop')
        quality=sorted((e['best']['quality'] for e in episodes),reverse=True)
        score=(sum(quality[:2]) + 0.4*min(len(episodes),4)
               + 0.35*min(profile.get('precise_contact_count',0),4)
               + (1.0 if profile.get('mirror_pair') else 0.0)
               + (1.0 if profile.get('clean_limit_group') else 0.0)
               + (1.0 if profile.get('reinforced_round_origin') else 0.0)
               + (4.0 if inflection else 0.0)
               + (0.5 if profile.get('round_price_context') else 0.0)
               + (1.0 if level.higher_timeframe_confirmed else 0.0)
               + evidence['strength_bonus'] + confluence['strength_bonus'])
        score=max(0.0,score-p.working_interaction_chop_penalty*chop['fraction'])
        details={'price':level.price,'bsu_index':level.bsu_index,'bsu_time':level.bsu_time,
                 'decision':'rejected' if reasons else 'eligible','reasons':reasons,
                 'strength_score':score,'reaction_episodes':episodes,
                 'clean_contact_count':len({e['index'] for e in contacts}),
                 'min_distance_percent':p.min_level_distance_fraction*100,
                 'confirmed_inflection':inflection}
        details['interaction_chopping'] = chop
        details['origin_evidence'] = evidence
        details['type_confluence'] = confluence
        details['stopping_contacts'] = [{'index':i, 'kind':kind, **context}
                                        for (i,kind),context in sorted(contexts.items())]
        records[level.price]=details
        if not reasons:
            eligible.append(level)
            scores[level.price]=score
    # A weak local reaction within an evidenced channel is less useful than its
    # boundaries. Restrict the penalty to the channel's actual time span.
    channels={ (c['lower'],c['upper'],c['start_index'],c['end_index'])
               for profile in profiles.values() for c in profile.get('channels',[]) }
    base_scores=dict(scores)
    for level in eligible:
        details=records[level.price]
        internal=[{'lower':lo,'upper':hi,'start_index':start,'end_index':end}
                  for lo,hi,start,end in sorted(channels)
                  if lo<level.price<hi
                  and any(start<=e['best']['index']<=end for e in details['reaction_episodes'])
                  and lo in profiles and hi in profiles
                  and profiles[lo].get('strong') and profiles[hi].get('strong')
                  and lo in base_scores and hi in base_scores
                  and base_scores[level.price] < min(base_scores[lo], base_scores[hi])
                  and not details['confirmed_inflection']]
        if internal:
            details['channel_context']=internal
            own_hold = any(e['best'].get('stopping_context', {}).get('basis') == 'clean_hold'
                           for e in details['reaction_episodes'])
            confined = all(any(c['start_index'] <= e['best']['index'] <= c['end_index']
                               for c in internal) for e in details['reaction_episodes'])
            own_boundary = any(
                c['start_index'] <= e['best']['index'] <= c['end_index']
                for c in profiles[level.price].get('channels', [])
                for e in details['reaction_episodes'])
            if confined and not own_hold and not own_boundary and len(details['reaction_episodes']) < 2:
                details.update(decision='rejected', reasons=[*details['reasons'], 'weak_channel_interior'])
            # A strong internal level may survive on its independent evidence.
            scores[level.price]*=0.75
            details['strength_score']=scores[level.price]
    eligible = [v for v in eligible if records[v.price]['decision'] != 'rejected']
    selected,spacing=enforce_minimum_spacing(eligible,p.min_level_distance_fraction,scores)
    for item in spacing:records[item['price']].update(item)
    for level in levels:level.selection=records[level.price]
    if audit is not None:audit.extend(records.values())
    return selected
