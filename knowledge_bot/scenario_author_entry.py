"""Resolve explicit author entry corrections without using execution OHLC as input."""
from __future__ import annotations

from .scenario_hourly_atr import first_hourly_atr_entry

HOUR = 3_600_000
DAY = 24*HOUR


def resolve_author_entry(override, bars, daily_close, level, direction, visible_end):
    timing = override['timing']
    if timing == 'one_hourly_ATR_from_level_after_daily_close':
        resolved=first_hourly_atr_entry(bars,daily_close,level,direction,visible_end)
        if override.get('caption_OR_branch') is True:
            resolved.update(entry_label_source='caption_condition_derived_ATR_branch',
                            earliest_OR_execution_verified=False)
        return resolved
    cutoff = daily_close
    resolution = 'closed_hourly_bar'
    evidence = {}
    if timing == 'immediately_after_signal_daily_close':
        window = [cutoff, cutoff]
    elif timing == 'within_first_hour_after_signal_daily_close':
        window = [cutoff, cutoff+HOUR]
        resolution = 'pre_hour_proxy_not_exact_intrabar_entry'
    elif timing == 'first_level_breakout_after_daily_close':
        sign=1 if direction=='long' else -1
        candidate=None
        for b in bars:
            if not daily_close<=b['open_time_ms']<visible_end or b['close_time_ms']>visible_end:
                continue
            previous=next((p for p in bars if p['close_time_ms']==b['open_time_ms']),None)
            if previous is None:
                raise ValueError('author_breakout_prior_close_missing')
            extreme=b['high'] if direction=='long' else b['low']
            if min(sign*(previous['close']-level),sign*(b['open']-level))<=0 and sign*(extreme-level)>0:
                candidate=b
                break
        if candidate is None:
            raise ValueError('author_breakout_not_found_after_D1_close')
        cutoff=candidate['open_time_ms']
        window=[cutoff,cutoff+HOUR]
        resolution='pre_hour_proxy_not_exact_intrabar_entry'
        evidence.update(selected_authorized_branch='immediate_level_breakout',
                        alternative_H1_ATR_allowed=override.get('alternative_H1_ATR_allowed',False),
                        exact_intrabar_path_unknown=True)
    elif timing == 'first_full_bar_beyond_level_after_daily_close':
        candidates=[b for b in bars if daily_close<=b['open_time_ms']<visible_end
                    and b['close_time_ms']<=visible_end]
        beyond=lambda b:b['low']>level if direction=='long' else b['high']<level
        candidate=next((b for b in candidates if beyond(b)),None)
        if candidate is None:
            raise ValueError('author_full_bar_not_found_after_D1_close')
        number=(candidate['open_time_ms']-daily_close)//HOUR+1
        requested=override.get('confirmation_bar_number')
        if requested is not None and (type(requested) is not int or requested<1 or requested!=number):
            raise ValueError('author_confirmation_bar_number_mismatch')
        prefix=[b for b in candidates if b['open_time_ms']<=candidate['open_time_ms']]
        if [b['open_time_ms'] for b in prefix]!=[daily_close+i*HOUR for i in range(number)]:
            raise ValueError('author_full_bar_history_gap')
        cutoff=candidate['close_time_ms']
        window=[cutoff,cutoff]
        evidence.update(confirmation_bar_open_time_ms=candidate['open_time_ms'],
                        confirmation_bar_number=number,strict_wick_separation=True,
                        level_touch_does_not_confirm=True)
    elif timing == 'after_author_selected_hourly_close':
        hour=override['hour_open_utc']
        if type(hour) is not int or not 0<=hour<24:
            raise ValueError('invalid_author_entry_hour')
        opening=daily_close+hour*HOUR
        candidate=next((b for b in bars if b['open_time_ms']==opening and b['close_time_ms']==opening+HOUR),None)
        if candidate is None:
            raise ValueError('author_selected_hour_missing')
        cutoff=candidate['close_time_ms']
        window=[cutoff,cutoff]
        evidence['confirmation_bar_open_time_ms']=opening
    elif timing == 'hour_open_after_closed_breakout':
        hour = override['hour_utc']
        if type(hour) is not int or not 0 <= hour < 24:
            raise ValueError('invalid_author_entry_hour')
        cutoff = daily_close+hour*HOUR
        previous = next((b for b in bars if b['close_time_ms']==cutoff), None)
        sign = 1 if direction=='long' else -1
        if (previous is None or previous['open_time_ms']<daily_close
                or sign*(previous['open']-level)>0 or sign*(previous['close']-level)<=0):
            raise ValueError('author_hour_not_after_confirmed_breakout')
        window = [cutoff, cutoff]
        evidence['confirmation_bar_open_time_ms'] = previous['open_time_ms']
    elif timing in ('first_approach_within_percent_after_daily_close', 'approach_or_closest_visible_after_daily_close'):
        distance = override['max_distance_percent'] + override.get('rounding_tolerance_percentage_points',0)
        if isinstance(distance,bool) or not isinstance(distance,(int,float)) or not 0 < distance < 100:
            raise ValueError('invalid_author_distance_percent')
        lower, upper = ((level, level*(1+distance/100)) if direction=='long'
                        else (level*(1-distance/100),level))
        # Whole historical candles locate an authored condition retrospectively.
        # The caller must cut predictive features strictly before this hour.
        candidate = next((b for b in bars if daily_close<=b['open_time_ms']<visible_end
                          and b['close_time_ms']<=visible_end
                          and b['high']>lower and b['low']<upper), None)
        if candidate is None:
            if timing != 'approach_or_closest_visible_after_daily_close':
                raise ValueError('author_approach_not_found_in_visible_post_D1_history')
            candidates=[b for b in bars if daily_close<=b['open_time_ms']<visible_end
                        and b['close_time_ms']<=visible_end]
            if not candidates:
                raise ValueError('author_selection_window_incomplete')
            distance_to_level=lambda b:max(b['low']-level,level-b['high'],0)
            closest=min(distance_to_level(b) for b in candidates)
            selected=[b for b in candidates if distance_to_level(b)==closest]
            if len(selected)!=1:
                raise ValueError('author_closest_hour_not_unique')
            candidate=selected[0]
            evidence.update(retrospective_author_label=True,executable_live_rule=False,
                            author_selection_window_end_ms=visible_end,
                            selected_hour_distance_to_level=closest)
        cutoff = candidate['open_time_ms']
        window = [cutoff,cutoff+HOUR]
        resolution = 'pre_hour_proxy_not_exact_intrabar_entry'
        evidence.update(condition_zone_price_bounds=[lower,upper],
                        condition_hour_open_time_ms=cutoff,
                        exact_intrabar_path_unknown=True)
    elif timing == 'move_toward_level_from_daily_close':
        previous=next((b for b in bars if b['close_time_ms']==daily_close),None)
        if previous is None:
            raise ValueError('author_move_origin_daily_close_missing')
        origin=previous['close']
        minimum=override['move_min_percent']
        maximum=override['move_max_percent']
        tolerance=override.get('rounding_tolerance_percentage_points',0)
        if not 0 <= tolerance < minimum <= maximum < 100:
            raise ValueError('invalid_author_move_band')
        sign=1 if level>origin else -1
        bounds=sorted([origin*(1+sign*(minimum-tolerance)/100),
                       origin*(1+sign*(maximum+tolerance)/100)])
        lower,upper=bounds
        candidate=next((b for b in bars if daily_close<=b['open_time_ms']<visible_end
                        and b['close_time_ms']<=visible_end and b['high']>=lower and b['low']<=upper),None)
        if candidate is None:
            raise ValueError('author_move_not_found_in_visible_post_D1_history')
        cutoff=candidate['open_time_ms']
        window=[cutoff,cutoff+HOUR]
        resolution='pre_hour_proxy_not_exact_intrabar_entry'
        evidence.update(origin_price=origin,origin_time_ms=daily_close,
                        condition_zone_price_bounds=bounds,
                        rounding_tolerance_percentage_points=tolerance,
                        exact_intrabar_path_unknown=True)
    elif timing == 'author_selected_closest_of_first_hours':
        hours=override['hours_after_daily_close']
        if type(hours) is not int or hours<1 or hours>24:
            raise ValueError('invalid_author_retrospective_window')
        candidates=[b for b in bars if daily_close<=b['open_time_ms']<daily_close+hours*HOUR
                    and b['close_time_ms']<=visible_end]
        if len(candidates)!=hours or [b['open_time_ms'] for b in candidates]!=[daily_close+i*HOUR for i in range(hours)]:
            raise ValueError('author_selection_window_incomplete')
        distance=lambda b:max(b['low']-level,level-b['high'],0)
        closest=min(distance(b) for b in candidates)
        selected=[b for b in candidates if distance(b)==closest]
        if len(selected)!=1:
            raise ValueError('author_closest_hour_not_unique')
        cutoff=selected[0]['open_time_ms']
        window=[cutoff,cutoff+HOUR]
        resolution='pre_hour_proxy_not_exact_intrabar_entry'
        evidence.update(retrospective_author_label=True,executable_live_rule=False,
                        author_selection_window_end_ms=daily_close+hours*HOUR,
                        selected_hour_distance_to_level=closest,exact_intrabar_path_unknown=True)
    else:
        raise ValueError('author_entry_rule_requires_clarification')
    if cutoff < daily_close or cutoff > visible_end or (cutoff==visible_end and resolution!='closed_hourly_bar'):
        raise ValueError('author_entry_outside_verified_visible_period')
    result=dict(decision_time_ms=cutoff, entry_resolution=resolution,
                authored_entry_time_window_ms=window,
                entry_label_source='explicit_author_correction_'+timing,
                correction_evidence=evidence, exact_execution_price=None)
    if timing=='first_full_bar_beyond_level_after_daily_close' and override.get('caption_OR_branch') is True:
        result.update(entry_label_source='caption_condition_derived_closed_branch',
                      earliest_OR_execution_verified=False,author_exact_execution_hour_verified=False)
    return result
