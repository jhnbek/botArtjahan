"""Causal wick roles relative to an earlier reversal origin.

A sweep is entry context even when its own extreme happens to lie near another
candidate price. Pending origins protect the earlier wick without being called
confirmed inflections. All evidence carries the close at which it was known.
"""
from level_structure import (atr_series, contact_is_excluded, penetration_limit,
                             time_of, repeated_wick_sweeps)


def pending_reversal_origins(bars, params, structure_params, context_at,
                             mirror_at, anchors=(), atrs=None):
    """Find origins from past OHLC only; callbacks inspect their named prefix.

The independent inflection lifecycle can later replace an older origin. A
replacement is not borrowed before its recorded effective close.
"""
    atrs = atrs if atrs is not None else atr_series(bars, params.atr_period)
    anchors = dict(anchors)
    origins = {}
    first = max(params.atr_period + 1, params.paranormal_lookback)
    for index in range(first, len(bars)):
        bar, atr = bars[index], atrs[index]
        if not atr or atr <= 0:
            continue
        previous = bars[index-params.paranormal_lookback:index]
        for kind in ('H', 'L'):
            upper = kind == 'H'
            price = bar.high if upper else bar.low
            if contact_is_excluded(bar, price, kind, atr, structure_params):
                continue
            boundary = max(b.high for b in previous) if upper else min(b.low for b in previous)
            if price <= boundary if upper else price >= boundary:
                continue
            start = (min if upper else max)(range(index-params.paranormal_lookback, index),
                                           key=lambda j: bars[j].close)
            incoming = abs(price-bars[start].close)
            travel = sum(abs(bars[j].close-bars[j-1].close) for j in range(start+1, index+1))
            efficiency = abs(bar.close-bars[start].close)/travel if travel else 0.0
            if incoming < params.inflection_move_atr*atr:
                continue
            context = context_at(index, kind, atr)
            if not context['eligible']:
                continue
            mirror = mirror_at(index, kind, atr)
            if efficiency < params.inflection_min_efficiency and not mirror:
                continue
            origins[index, kind] = {
                'index': index, 'time': time_of(bar), 'kind': kind, 'price': price,
                'atr': atr, 'known_index': index, 'incoming_atr': incoming/atr,
                'efficiency': efficiency, 'status': 'pending',
                'expires_index': index+params.reversal_lookahead,
                'two_bar_earliest_index': index+params.pivot_wing+1,
            }
    for key, anchor in anchors.items():
        index, kind = key
        if not 0 <= anchor['confirmation_index'] < len(bars):
            continue
        if key not in origins:
            # An efficiency exception established by later clean retests cannot
            # protect a historical bar before those retests actually existed.
            origins[key] = {
                'index': index, 'time': time_of(bars[index]), 'kind': kind,
                'price': anchor['price'], 'atr': atrs[index],
                'known_index': anchor['confirmation_index'], 'status': 'confirmed',
            }
        origins[key]['confirmation_index'] = anchor['confirmation_index']
        origins[key].pop('expires_index', None)
    # The previous lifecycle explicitly permits a separately confirmed reversal
    # to take over after an outside close. Once this becomes known, the old
    # origin no longer vetoes the replacement BSU or its legitimate retests.
    for key, anchor in anchors.items():
        replacement = anchor.get('superseded_by')
        if key not in origins or not replacement:
            continue
        effective = replacement['effective_index']
        if effective >= len(bars):
            continue
        target = next((new_key for new_key, origin in origins.items()
                       if new_key[1] == key[1]
                       and origin['time'] == replacement['bsu_time']
                       and origin['price'] == replacement['price']), None)
        if target is None:
            continue
        origins[key]['retired_from_index'] = target[0]
        origins[key]['replacement_known_index'] = effective
        origins[target]['known_index'] = max(origins[target]['known_index'], effective)
        origins[target]['replacement_known_index'] = effective
    # A new structural swing can occur after the original lifecycle's nearby
    # replacement window. An independently confirmed BSU plus an actual outside
    # close still takes priority over labelling its own wick an old-level entry.
    # Only the as-of prefix containing BOTH observations can make this change.
    for key, earlier in sorted(origins.items()):
        sign = 1 if key[1] == 'H' else -1
        for later_key, anchor in sorted(anchors.items()):
            j, kind = later_key
            if (later_key not in origins or kind != key[1] or j <= key[0]
                    or j >= earlier.get('retired_from_index', len(bars))
                    or sign*(anchor['price']-earlier['price']) <= 0):
                continue
            # A continuation already closing outside is not a false wick and
            # will naturally retire the old origin in the chronological scan.
            if (sign*(bars[j].open-earlier['price']) >= 0
                    or sign*(bars[j].close-earlier['price']) >= 0):
                continue
            confirmed = anchor['confirmation_index']
            crossed = next((k for k in range(j+1, min(len(bars), confirmed+1))
                            if sign*(bars[k].close-earlier['price']) > 0), None)
            if crossed is None:
                continue
            effective = max(crossed, confirmed)
            earlier['retired_from_index'] = j
            earlier['replacement_known_index'] = effective
            origins[later_key]['known_index'] = max(origins[later_key]['known_index'], effective)
            origins[later_key]['replacement_known_index'] = effective
    return sorted(origins.values(), key=lambda origin: (origin['index'], origin['kind']))


def prior_origin_false_breakouts(bars, origins, p, atrs=None):
    """Return automatic contact constraints and their separate provenance.

Two-bar events are unavailable until the returning close. A continuation with
no immediate return retires the origin. Only the swept wick side is excluded;
the other extreme can still belong to an independent level.
"""
    atrs = atrs if atrs is not None else atr_series(bars, p.atr_period)
    records, constraints = [], set()
    for origin in origins:
        index, kind, price = origin['index'], origin['kind'], origin['price']
        if origin['known_index'] >= len(bars):
            continue
        if any(timestamp == bars[index].open_time and side == kind
               and abs(price-wick) <= p.merge_atr*(atrs[index] or origin['atr'])
               for timestamp, side, wick in constraints):
            continue
        sign = 1 if kind == 'H' else -1
        stop = min(len(bars), origin.get('expires_index', len(bars)-1)+1,
                   origin.get('retired_from_index', len(bars)))
        repeated = {i for run in repeated_wick_sweeps(bars, price, kind, p, atrs) for i in run}
        current = index+1
        while current < stop:
            bar = bars[current]
            local_atr = atrs[current] or origin['atr']
            opened = sign*(bar.open-price)
            closed = sign*(bar.close-price)
            wick = bar.high if kind == 'H' else bar.low
            related = [current]
            if closed > 0:
                if (current < origin.get('two_bar_earliest_index', index+1)
                        or opened >= 0 or current+1 >= stop
                        or sign*(bars[current+1].close-price) >= 0):
                    break
                related.append(current+1)
                role = 'false_breakout_two_bar'
            elif (opened < 0 and closed < 0
                  and sign*(wick-price) > penetration_limit(
                      bars, current, price, kind, local_atr, p, atrs)):
                role = 'chop' if current in repeated else 'false_breakout'
            else:
                current += 1
                continue
            known = max(related[-1], origin['known_index'])
            if role == 'chop' and current-1 not in repeated:
                known = max(known, current+1)
            contacts = [{'index': j, 'time': time_of(bars[j]),
                         'timestamp': bars[j].open_time, 'kind': kind,
                         'price': bars[j].high if kind == 'H' else bars[j].low}
                        for j in related]
            record = {
                'index': current, 'indices': related, 'time': time_of(bar),
                'kind': kind, 'role': role, 'known_index': known,
                'known_time': time_of(bars[known]), 'context_source': 'prior_origin',
                'entry_context_only': role != 'chop', 'confirms_level': False,
                'origin_index': index, 'origin_time': origin['time'],
                'origin_price': price, 'origin_known_index': origin['known_index'],
                'origin_status': ('confirmed' if origin.get('confirmation_index', len(bars)) <= known
                                  else 'pending'),
                'suppressed_contacts': contacts,
            }
            if 'replacement_known_index' in origin:
                record['replacement_known_index'] = origin['replacement_known_index']
            records.append(record)
            constraints.update((contact['timestamp'], kind, contact['price']) for contact in contacts)
            current += len(related)
    return tuple(sorted(constraints)), records
