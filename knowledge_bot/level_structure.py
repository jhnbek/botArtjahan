"""Price-level events and strength from OHLC, without manual price/date overrides.

The numerical defaults are explicit heuristics for review, not learned rules.
Evidence timestamps identify the candle AFTER WHOSE CLOSE it became known.
The caller must supply closed candles; a full-chart result is retrospective.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from math import isclose, floor, log10
from detector_prototype import is_paranormal_body


@dataclass(frozen=True)
class StructureParams:
    atr_period: int = 14
    paranormal_lookback: int = 20
    touch_atr: float = 0.08
    touch_price_fraction: float = 0.0015
    penetration_atr: float = 0.01
    confirmed_near_touch_atr: float = 0.06
    confirmed_near_price_fraction: float = 0.0020
    confirmed_near_tail_min_atr: float = 0.20
    reaction_bars: int = 15
    strong_reaction_atr: float = 1.50
    precision_atr: float = 0.02
    mirror_pair_precision_atr: float = 0.02
    mirror_overlap_range_fraction: float = 0.025
    clean_limit_precision_atr: float = 0.02
    clean_limit_price_fraction: float = 0.001
    merge_atr: float = 0.15
    chop_window: int = 40
    chop_side_atr: float = 0.08  # close-side noise band, independent of touch acceptance
    chop_min_bars: int = 2
    channel_windows: tuple = (30, 60, 90)
    channel_min_inside: float = 0.60
    excluded_contacts: tuple = ()  # explicit (bar timestamp, wick side, reviewed level price)
    automatic_origin_exclusions: tuple = ()  # computed sweeps; never written to user feedback
    automatic_origin_events: tuple = ()     # causal provenance of those wick exclusions


def time_of(bar):
    return datetime.fromtimestamp(bar.open_time / 1000, tz=timezone.utc).isoformat()


def exact_price(first, second):
    """Float representation tolerance only, never the ATR contact/luft band."""
    return isclose(first, second, rel_tol=1e-12, abs_tol=1e-12)


def round_price_context(price):
    """Round hundreds at BTC scale; observed price is never snapped to a grid.

    Grid size is a scale heuristic (three significant decimal places), not a
    quoted KB constant. Roundness strengthens existing structure only.
    """
    if price <= 0:
        return False
    step = 10.0 ** (floor(log10(price))-2)
    return exact_price(price, round(price/step)*step)


def atr_series(bars, period):
    ranges = [0.0] + [max(b.high-b.low, abs(b.high-bars[i-1].close),
                         abs(b.low-bars[i-1].close)) for i,b in enumerate(bars) if i]
    return [None if i < period+1 else sum(ranges[i-period:i])/period for i in range(len(bars))]


def penetration_limit(bars, index, price, kind, atr, p, atrs):
    """Tiny mirror overlap is allowed only against an earlier clean exact wick.

    This uses past candles only; ordinary same-side sweep tolerance is unchanged.
    """
    limit=p.penetration_atr*atr
    wick=bars[index].high if kind=='H' else bars[index].low
    sign=1 if kind=='H' else -1
    if (sign*(wick-price) <= limit or abs(wick-price) > p.mirror_pair_precision_atr*atr
            or abs(wick-price) > p.mirror_overlap_range_fraction*(bars[index].high-bars[index].low)):
        return limit
    opposite='L' if kind=='H' else 'H'
    for j in range(index):
        previous=bars[j]
        prior_atr=atrs[j]
        prior_wick=previous.low if opposite=='L' else previous.high
        if not prior_atr or not exact_price(prior_wick,price):continue
        if sign*(previous.open-price) < 0 or sign*(previous.close-price) < 0:continue
        if j and sign*(bars[j-1].open-price)>0>sign*(bars[j-1].close-price):continue
        if contact_is_excluded(previous,price,opposite,prior_atr,p):continue
        if abs(wick-price) <= p.mirror_pair_precision_atr*min(atr,prior_atr):
            return p.mirror_pair_precision_atr*min(atr,prior_atr)
    return limit


def reaction_after(bars, index, price, kind, atr, p, atrs=None):
    """Departure on ordinary bars; false-breakout bars cannot supply strength.

    A two-bar return may preserve the earlier contact, but both entry-pattern
    bars are skipped. Wick luft uses the ATR at that bar, just as level_events.
    """
    atrs = atrs if atrs is not None else atr_series(bars, p.atr_period)
    direction = -1 if kind == 'H' else 1
    maximum = 0.0
    confirmed = None
    contextual_forbidden = {j for event in contextual_entry_events(bars, price, p, atrs)
                            if event['kind'] == kind for j in event['indices']}
    j = index
    stop = min(len(bars), index+p.reaction_bars+1)
    while j < stop:
        if j in contextual_forbidden:
            j += 1
            continue
        distance = direction*(bars[j].close-price)
        opening_distance = direction*(bars[j].open-price)
        if distance < 0:
            if (opening_distance <= 0 or j+1 >= stop
                    or direction*(bars[j+1].close-price) <= 0):
                break
            j += 2
            continue
        value = bars[j].high if kind == 'H' else bars[j].low
        penetration = -direction*(value-price)
        local_atr = atrs[j] or atr
        if (opening_distance > 0 and distance > 0
                and penetration > penetration_limit(bars,j,price,kind,local_atr,p,atrs)):
            j += 1
            continue
        maximum = max(maximum, distance/atr)
        if confirmed is None and maximum >= p.strong_reaction_atr:
            confirmed = j
        j += 1
    return maximum, confirmed


def contact_is_excluded(bar, price, kind, atr, p):
    """A user's rejected association also covers small moves in its price zone.

    Exclusions must be instrument/timeframe scoped by the caller. They never
    remove OHLC candles or suppress distant unrelated levels on the same bar.
    """
    return any(bar.open_time == timestamp and kind == side and
               abs(price-reviewed_price) <= p.merge_atr*atr
               for timestamp,side,reviewed_price in
               (*p.excluded_contacts, *p.automatic_origin_exclusions))


def contextual_entry_events(bars, price, p, atrs):
    """Entry bars at a prior origin cannot become contacts at their own wick.

Keep the actual protected price in the evidence: this does not claim that the
bar falsely broke the newly inspected candidate price.
"""
    result = []
    for source in p.automatic_origin_events:
        if source['known_index'] >= len(bars):
            continue
        related = source['suppressed_contacts']
        if not any(abs(price-contact['price']) <= p.merge_atr*(atrs[contact['index']] or 0.0)
                   for contact in related):
            continue
        index, kind = source['index'], source['kind']
        atr = atrs[index]
        if not atr:
            continue
        value = bars[index].high if kind == 'H' else bars[index].low
        result.append({
            **source, 'price': value, 'atr': atr, 'gap_atr': abs(value-price)/atr,
            'contact_rule': None, 'distance_limit_atr': None,
            'distance_limit_price_fraction': None, 'reaction_atr': 0.0,
            'reaction_confirmed_index': None, 'reaction_confirmed_time': None,
            'protected_origin_price': source['origin_price'],
            'protected_origin_time': source['origin_time'],
        })
    return result


def confirming_contact(bars, index, price, kind, p=None, atrs=None):
    """A held wick close enough to this level, never a distant rebound.

    Wider near touches require a visible rejection tail AND an ordinary strong
    departure already present in the supplied closed prefix. This preserves
    legitimate small misses without crediting every bar near a prior reversal.
    The thresholds are reviewable hypotheses from the manual distance audit.
    """
    p = p or StructureParams()
    atrs = atrs if atrs is not None else atr_series(bars,p.atr_period)
    atr = atrs[index]
    if not atr or atr <= 0:
        return None
    bar = bars[index]
    if contact_is_excluded(bar,price,kind,atr,p):
        return None
    sign = 1 if kind == 'H' else -1
    value = bar.high if kind == 'H' else bar.low
    if sign*(bar.open-price) > 0 or sign*(bar.close-price) > 0:
        return None
    if index and sign*(bars[index-1].open-price) < 0 < sign*(bars[index-1].close-price) and sign*(bar.close-price) < 0:
        return None  # returning bar of a two-bar false breakout, including gaps
    if sign*(value-price) > penetration_limit(bars,index,price,kind,atr,p,atrs):
        return None
    gap = abs(value-price)/atr
    distance = abs(value-price)
    extended = gap > p.touch_atr or distance > abs(price)*p.touch_price_fraction
    if extended:
        tail = bar.high-max(bar.open,bar.close) if kind=='H' else min(bar.open,bar.close)-bar.low
        if (gap > p.confirmed_near_touch_atr or distance > abs(price)*p.confirmed_near_price_fraction
                or tail < p.confirmed_near_tail_min_atr*atr):
            return None
    reaction, confirmation = reaction_after(bars,index,price,kind,atr,p,atrs)
    if extended and confirmation is None:
        return None
    return {'role':'touch' if gap <= p.penetration_atr else 'near_touch',
            'contact_rule':'tail_and_confirmed_reaction' if extended else 'close_wick',
            'distance_limit_atr':p.confirmed_near_touch_atr if extended else p.touch_atr,
            'distance_limit_price_fraction':p.confirmed_near_price_fraction if extended else p.touch_price_fraction,
            'known_index':confirmation if extended else index,
            'reaction_atr':reaction,'reaction_confirmed_index':confirmation}


def repeated_wick_sweeps(bars, price, kind, p, atrs):
    """Closed adjacent same-side wick returns are chopping, not entry signals."""
    sign = 1 if kind == 'H' else -1
    runs, current = [], []
    for i, bar in enumerate(bars):
        atr = atrs[i]
        value = bar.high if kind == 'H' else bar.low
        swept = (atr and sign*(bar.open-price) < 0 and sign*(bar.close-price) < 0
                 and sign*(value-price) > penetration_limit(bars,i,price,kind,atr,p,atrs))
        if swept:
            current.append(i)
        else:
            if len(current) >= 2:
                runs.append(current)
            current = []
    if len(current) >= 2:
        runs.append(current)
    return runs


def level_events(bars, price, p=None, atrs=None):
    """One role per bar/side: touch, near touch, long-tail rejection or false break.

    Adjacent contacts are retained. Small rounding penetration is distinguished
    from a false break, and a two-bar event is emitted only after its return.
    """
    p = p or StructureParams()
    atrs = atrs or atr_series(bars, p.atr_period)
    events = contextual_entry_events(bars, price, p, atrs)
    consumed = {(index, event['kind']) for event in events for index in event['indices']}
    repeated = {(i, kind) for kind in ('H', 'L')
                for run in repeated_wick_sweeps(bars, price, kind, p, atrs) for i in run}
    for i, bar in enumerate(bars):
        atr = atrs[i]
        if not atr or atr <= 0:
            continue
        for kind in ('H', 'L'):
            if (i, kind) in consumed:
                continue
            sign = 1 if kind == 'H' else -1
            value = bar.high if kind == 'H' else bar.low
            penetration = sign*(value-price)
            start_side = sign*(bar.open-price)
            end_side = sign*(bar.close-price)
            # A return to the opening side is essential; crossing from an
            # opening already beyond the level is not a one-bar false break.
            if start_side > 0:
                continue
            known = i
            related = [i]
            # A close beyond the level followed by the next close back defines
            # the two-bar pattern. Wick luft only applies while closes hold.
            if end_side > 0:
                if start_side >= 0 or i+1 >= len(bars) or sign*(bars[i+1].close-price) >= 0:
                    continue
                role = 'false_breakout_two_bar'
                known = i+1
                related.append(known)
                consumed.add((known, kind))
            elif penetration > penetration_limit(bars,i,price,kind,atr,p,atrs):
                if start_side >= 0 or end_side >= 0:
                    continue
                role = 'chop' if (i, kind) in repeated else 'false_breakout'
                if role == 'chop' and (i-1, kind) not in repeated:
                    known = i+1
            else:
                contact = confirming_contact(bars,i,price,kind,p,atrs)
                if contact is None:
                    continue
                role = contact['role']
                known = contact['known_index']
            entry_only = role.startswith('false_breakout')
            non_contact = entry_only or role == 'chop'
            reaction, confirmation = ((0.0, None) if non_contact else
                                      (contact['reaction_atr'],contact['reaction_confirmed_index']))
            events.append({'index': i, 'time': time_of(bar), 'kind': kind, 'price': value,
                           'role': role, 'indices': related, 'known_index': known,
                           'contact_rule':None if non_contact else contact['contact_rule'],
                           'distance_limit_atr':None if non_contact else contact['distance_limit_atr'],
                           'distance_limit_price_fraction':None if non_contact else contact['distance_limit_price_fraction'],
                           'confirms_level':not non_contact,'entry_context_only':entry_only,
                           'known_time': time_of(bars[known]), 'gap_atr': abs(value-price)/atr,
                           'atr': atr, 'reaction_atr': reaction,
                           'reaction_confirmed_index': confirmation,
                           'reaction_confirmed_time': time_of(bars[confirmation]) if confirmation is not None else None})
    return sorted(events, key=lambda event: (event['index'], event['kind']))


def chopping_runs(bars, price, p=None, atrs=None, start=0):
    """Two or more consecutive bodies cross both sides beyond the noise band.

    Consecutive same-side wick returns also form a run. Isolated sweeps
    and gaps do not form a run. A false-breakout entry event remains independently classified.
    """
    p = p or StructureParams()
    atrs = atrs or atr_series(bars, p.atr_period)
    runs, current = [], []
    for i in range(start, len(bars)):
        atr = atrs[i]
        band = p.chop_side_atr * atr if atr else None
        bar = bars[i]
        crosses = band is not None and (
            min(bar.open, bar.close) < price-band and
            max(bar.open, bar.close) > price+band)
        if crosses:
            current.append(i)
        else:
            if len(current) >= p.chop_min_bars:
                runs.append({'indices':current})
            current = []
    if len(current) >= p.chop_min_bars:
        runs.append({'indices':current})
    for kind in ('H', 'L'):
        for indices in repeated_wick_sweeps(bars, price, kind, p, atrs):
            indices = [i for i in indices if i >= start]
            if len(indices) >= 2:
                runs.append({'indices': indices, 'kind': kind, 'role': 'repeated_wick_sweeps'})
    return sorted(runs, key=lambda run: run['indices'][0])


def clean_limit_group(contacts, price, p):
    """Three close same-side wicks, including a consecutive clean limit pair.

    Anchor the group at its earliest actual price. Full group span is bounded,
    so chains of individually nearby wicks cannot join distant endpoints.
    Only confirming events enter; false breakouts and rejected BSUs cannot vote.
    """
    candidates=[]
    for kind in ('H','L'):
        ordered=sorted([e for e in contacts if e['kind']==kind],key=lambda e:e['price'])
        for start,first in enumerate(ordered):
            group=[]
            tolerance=float('inf')
            for event in ordered[start:]:
                tolerance=min(tolerance,p.clean_limit_precision_atr*event['atr'],abs(price)*p.clean_limit_price_fraction)
                if event['price']-first['price'] > tolerance:break
                group.append(event)
                chronological=sorted(group,key=lambda e:e['index'])
                if len(chronological)<3 or not exact_price(chronological[0]['price'],price):continue
                pairs=[[a['index'],b['index']] for a,b in zip(chronological,chronological[1:]) if b['index']==a['index']+1]
                if not pairs:continue
                reacted=[e for e in chronological if e['reaction_confirmed_index'] is not None]
                if not reacted:continue
                candidates.append({'indices':[e['index'] for e in chronological],
                    'kind':kind,'price_span':event['price']-first['price'],
                    'limit_pairs':[{'indices':pair} for pair in pairs],
                    'known_index':max(max(e['known_index'] for e in chronological),min(e['reaction_confirmed_index'] for e in reacted))})
    return min(candidates,key=lambda g:(-len(g['indices']),g['price_span'],g['indices'][0])) if candidates else None


def paranormal_bar(bars, index, atr, lookback=20):
    # Keep lookback in the signature for old callers; range averages no longer
    # qualify a candle. ATR is supplied from history before this bar.
    return is_paranormal_body(bars[index].open, bars[index].close, atr)


def reinforced_round_origin(bars, price, seed_index, kind, contacts, p, atrs):
    """Round paranormal origin, held retest, then a strongly reacting mirror.

    Every event must pass the shared confirmation gate. Earlier or false-breakout
    wicks cannot supply a later origin with retrospective supporting evidence.
    """
    if not round_price_context(price) or not atrs[seed_index]:return None
    if not paranormal_bar(bars,seed_index,atrs[seed_index],p.paranormal_lookback):return None
    if not any(e['index']==seed_index and e['kind']==kind and exact_price(e['price'],price) for e in contacts):return None
    same=[e for e in contacts if e['kind']==kind and e['index']>seed_index]
    opposite=[e for e in contacts if e['kind']!=kind and e['index']>seed_index]
    for retest in same:
        for mirror in opposite:
            if mirror['index']<=retest['index'] or mirror['reaction_confirmed_index'] is None:continue
            later=next((e for e in opposite if e['index']>mirror['index']),None)
            if later is None:continue
            known=max(retest['known_index'],mirror['known_index'],mirror['reaction_confirmed_index'],later['known_index'])
            return {'indices':[seed_index,retest['index'],mirror['index'],later['index']],
                    'known_index':known,'known_time':time_of(bars[known]),
                    'kind':kind,'reaction_atr':mirror['reaction_atr']}
    return None


def level_profile(bars, price, seed_index, seed_kind, p=None, atrs=None):
    p = p or StructureParams()
    atrs = atrs or atr_series(bars, p.atr_period)
    events = level_events(bars, price, p, atrs)
    contacts = [e for e in events if e['role'] in ('touch','near_touch')]
    # Explicit user rule: false breakouts are ENTRY context only. They cannot
    # establish a level, add a confirmation, improve strength or form a channel.
    strong = [e for e in events if e['confirms_level'] and e['reaction_confirmed_index'] is not None]
    by_kind = {kind:[e for e in contacts if e['kind']==kind] for kind in ('H','L')}
    limit = any(len({e['index'] for e in by_kind[kind]}) >= 2 for kind in by_kind)
    mirror_pair = None
    precise_mirror = None
    for first in contacts:
        for second in contacts:
            if first['kind']==second['kind'] or second['index']<=first['index']:
                continue
            sign = 1 if first['kind']=='H' else -1
            if any(sign*(bars[j].close-price) > p.penetration_atr*second['atr']
                   for j in range(first['index']+1, second['index']+1)):
                pair = [first['index'],second['index']]
                if mirror_pair is None:
                    mirror_pair = pair
                if abs(first['price']-second['price']) <= p.mirror_pair_precision_atr*min(first['atr'],second['atr']):
                    candidate_mirror = {
                        'indices':pair,'first_time':first['time'],'second_time':second['time'],
                        'price_gap':abs(first['price']-second['price']),
                        'gap_atr':abs(first['price']-second['price'])/min(first['atr'],second['atr']),
                        'known_index':max(first['known_index'],second['known_index']),
                        'formation_atr':first['atr']}
                    if precise_mirror is None or (candidate_mirror['known_index'], pair) < (precise_mirror['known_index'], precise_mirror['indices']):
                        precise_mirror = candidate_mirror
    lifetime_start=min(seed_index,mirror_pair[0]) if mirror_pair else seed_index
    runs=chopping_runs(bars,price,p,atrs,lifetime_start)
    lifetime_switches=[i for run in runs for i in run['indices']]
    recent_runs=[run for run in runs if run['indices'][-1] >= len(bars)-p.chop_window]
    switches=[i for run in recent_runs for i in run['indices']]
    recovery = any(e['index'] > switches[-1] for e in strong) if switches else False
    chopped = bool(recent_runs) and not recovery
    chop_rate=len(lifetime_switches)/max(1,len(bars)-lifetime_start)*100
    failures = []
    if not (limit or mirror_pair): failures.append('insufficient_structural_contacts')
    if not strong: failures.append('no_strong_close_reaction')
    # Consecutive contacts followed by the same departure are one reaction episode.
    episodes = []
    for event in sorted(strong, key=lambda e:e['index']):
        if not episodes or event['index'] > episodes[-1][1]:
            episodes.append([event['index'],event['reaction_confirmed_index']])
        else:
            episodes[-1][1] = max(episodes[-1][1],event['reaction_confirmed_index'])
    precise = [e for e in contacts if e['gap_atr'] <= p.precision_atr]
    exact = sorted({e['index'] for e in contacts if exact_price(e['price'], price)})
    round_origin=reinforced_round_origin(bars,price,seed_index,seed_kind,contacts,p,atrs)
    clean_group=clean_limit_group(contacts,price,p)
    round_context = round_price_context(price)
    score = len(episodes)*2 + len(precise) + min(len(contacts),8)*0.1 + (1 if mirror_pair else 0)
    if round_context:
        score += 0.5
    if chopped:
        score = max(0.0, score-min(3.0,len(switches)*.5))
    score=max(0.0,score-chop_rate*.25)
    tags = (['limit_level'] if limit else []) + (['mirror_level'] if mirror_pair else [])
    if round_origin:tags.append('paranormal_bar')
    contact_ready = [sorted(e['known_index'] for e in by_kind[k])[1] for k in by_kind if len(by_kind[k]) >= 2]
    if mirror_pair:
        contact_ready.append(max(e['known_index'] for e in contacts if e['index'] in mirror_pair))
    qualified = max(seed_index, min(contact_ready), min(e['reaction_confirmed_index'] for e in strong)) if contact_ready and strong else None
    return {'price':price,'bsu_index':seed_index,'bsu_time':time_of(bars[seed_index]),
            'kind':seed_kind, 'atr_at_bsu':atrs[seed_index], 'events':events,
            'contact_count':len(contacts),'precise_contact_count':len(precise),
            'exact_price_contact_count':len(exact), 'exact_price_indices':exact,
            'round_price_context':round_context,'clean_limit_group':clean_group,
            'reinforced_round_origin':round_origin,
            'strong_reaction_count':len(episodes),
            'strong':not failures,'rejection_reasons':failures,'basis_tags':tags,
            'qualified_index':qualified,'qualified_time':time_of(bars[qualified]) if qualified is not None else None,
            'as_of_time':time_of(bars[-1]),'time_semantics':'after_close_of_named_bar',
            'mirror_pair':mirror_pair, 'recent_close_switches':switches,
            'chopping_runs':runs,'chopping_definition':'consecutive_body_crossings',
            'precise_mirror':precise_mirror,'lifetime_close_switches':lifetime_switches,
            'lifetime_chop_rate_per_100_bars':chop_rate,
            'currently_chopped':chopped,'strength_score':score,'channels':[]}


def discover_strong_levels(bars, preferred=(), p=None):
    """Consider every held wick, including flat/consecutive non-fractal contacts."""
    p = p or StructureParams()
    atrs = atr_series(bars,p.atr_period)
    seeds = {}
    for i,bar in enumerate(bars):
        atr=atrs[i]
        if not atr or atr<=0:
            continue
        for kind,value in (('H',bar.high),('L',bar.low)):
            # A weak initial touch may be followed by a stronger clean retest.
            if value not in seeds:
                seeds[value]=(i,kind)
    profiles=[]
    for price,(index,kind) in seeds.items():
        profile=level_profile(bars,price,index,kind,p,atrs)
        exact_contacts = [e for e in profile['events'] if e['confirms_level']
                          and exact_price(e['price'], price)]
        if not exact_contacts:
            continue
        first = exact_contacts[0]
        if (index, kind) != (first['index'], first['kind']):
            profile = level_profile(bars,price,first['index'],first['kind'],p,atrs)
        if profile['strong']:
            profiles.append(profile)
    # Most repeated EXACT wick price wins over a later inflection or near touch.
    # A tied vote first prefers a clean consecutive limit group, then mirror evidence.
    # Each candle has at most one vote; all seeds are found chronologically.
    profiles.sort(key=profile_priority)
    selected=[]
    for candidate in profiles:
        if any(same_price_zone(candidate,old,p) for old in selected):
            continue
        selected.append(candidate)
    for selected_profile in selected:
        neighbours = [v for v in profiles if same_price_zone(v,selected_profile,p)]
        selected_profile['price_selection'] = {
            'rule':'exact_majority_then_reinforced_round_origin_then_clean_limit_group',
            'retrospective_as_of':time_of(bars[-1]),
            'alternatives':[{'price':v['price'],'exact_contacts':v['exact_price_contact_count'],
                             'first_time':v['bsu_time']} for v in neighbours],
            'near_touches_have_no_exact_price_vote':True,
            'false_breakouts_have_no_vote':True,
        }
    attach_channels(bars,selected,p,atrs)
    return selected


def same_price_zone(candidate, established, p):
    old_pair=established.get('precise_mirror')
    new_pair=candidate.get('precise_mirror')
    if old_pair:
        width=old_pair['formation_atr']
        if new_pair:
            width=min(width,new_pair['formation_atr'])
    else:
        width=min(candidate['atr_at_bsu'],established['atr_at_bsu'])
    return abs(candidate['price']-established['price']) <= p.merge_atr*width


def profile_priority(profile):
    """Prefer exact votes, a clean limit group, then the early mirror evidence."""
    mirror=profile.get('precise_mirror')
    group=profile.get('clean_limit_group')
    return (-profile['exact_price_contact_count'],not bool(profile.get('reinforced_round_origin')),not bool(group),
            -len(group['indices']) if group else 0,
            group['indices'][0] if group else float('inf'),not bool(mirror),
            mirror['known_index'] if mirror else float('inf'),
            -profile['strength_score'],profile['bsu_index'],profile['price'])


def attach_channels(bars, profiles, p, atrs):
    """Evidence of alternating excursions, not a hard ban on internal levels."""
    ordered=sorted(profiles,key=lambda v:v['price'])
    for low_pos,lower in enumerate(ordered):
        # Internal strong levels must not displace wider channel boundaries.
        for upper in ordered[low_pos+1:]:
            width=upper['price']-lower['price']
            def boundary_contact(e, kind):
                return e['kind']==kind and e['role'] in ('touch','near_touch','long_tail_rejection')
            lower_hits={e['known_index'] if 'known_index' in e else e['index'] for e in lower['events'] if boundary_contact(e,'L')}
            upper_hits={e['known_index'] if 'known_index' in e else e['index'] for e in upper['events'] if boundary_contact(e,'H')}
            if not lower_hits or not upper_hits:
                continue
            candidates=[]
            # Evaluate only at known boundary contacts, not every possible date.
            for end in sorted(lower_hits|upper_hits):
                if not atrs[end] or not 0.75*atrs[end] <= width <= 6*atrs[end]:
                    continue
                for window in p.channel_windows:
                    start=max(0,end-window+1)
                    lo = {j for j in lower_hits if start<=j<=end}
                    hi = {j for j in upper_hits if start<=j<=end}
                    if end-start<12 or len(lo)<2 or len(hi)<2:
                        continue
                    contacts = []
                    for j in sorted(lo|hi):
                        if j in lo and j in hi: continue
                        side = 'L' if j in lo else 'H'
                        if not contacts or contacts[-1] != side: contacts.append(side)
                    if len(contacts) < 3:
                        continue
                    inside=sum(lower['price']<=b.close<=upper['price'] for b in bars[start:end+1])/(end-start+1)
                    if inside < p.channel_min_inside:
                        continue
                    visits=[]
                    for i in range(start,end+1):
                        b=bars[i]
                        if b.close < lower['price'] or b.close > upper['price']:
                            continue
                        near_low=b.low<=lower['price']+width*.2
                        near_high=b.high>=upper['price']-width*.2
                        if near_low==near_high:continue  # one wide bar is not two waves
                        side='L' if near_low else 'H'
                        if not visits or visits[-1]['side']!=side:
                            visits.append({'index':i,'side':side})
                    if len(visits)<4 or visits[-1]['index']-visits[0]['index']<12:
                        continue
                    score=len(contacts)*2+len(visits)*.25+inside
                    candidates.append({'lower':lower['price'],'upper':upper['price'],
                              'start_index':start,'end_index':end,
                              'start_time':time_of(bars[start]),'end_time':time_of(bars[end]),
                              'known_time':time_of(bars[max(end, lower['qualified_index'], upper['qualified_index'],lower['bsu_index'],upper['bsu_index'])]),'visits':visits,
                              'close_inside_ratio':inside,'score':score})
            episodes=[]
            for candidate in sorted(candidates,key=lambda c:c['score'],reverse=True):
                if any(channel_overlap(candidate,old)>=.5 for old in episodes):
                    continue
                episodes.append(candidate)
            for episode in episodes:
                lower['channels'].append(episode);upper['channels'].append(episode)
    # Keep the best-supported partner for each boundary in a heavily overlapping
    # period. Neighboring or later channels remain separate observations.
    candidates = { (c['lower'],c['upper'],c['start_index'],c['end_index']):c for v in profiles for c in v['channels'] }
    selected_channels=[]
    for channel in sorted(candidates.values(),key=lambda c:c['score'],reverse=True):
        if any((channel['lower'] in (old['lower'],old['upper']) or channel['upper'] in (old['lower'],old['upper']))
               and channel_overlap(channel,old)>=.5
               for old in selected_channels):
            continue
        selected_channels.append(channel)
    for profile in profiles:
        profile['channels']=[c for c in selected_channels if profile['price'] in (c['lower'],c['upper'])]
        profile['channels'].sort(key=lambda c:c['score'],reverse=True)
        profile['strength_score'] += min(2,len(profile['channels']))


def channel_overlap(first, second):
    overlap = max(0,min(first['end_index'],second['end_index'])-max(first['start_index'],second['start_index'])+1)
    return overlap/min(first['end_index']-first['start_index']+1,second['end_index']-second['start_index']+1)
