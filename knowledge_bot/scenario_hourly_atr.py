"""Closed-H1 port of the locally documented Pine 5/150%/50% excerpt.

This is an explicitly chosen project convention, not the distinct manual
selection of five similar candles. The full Pine program is unavailable.
"""
from __future__ import annotations

import math

HOUR = 3_600_000
METHOD = 'pine_screenshot_5_150_50_closed_h1'


def closed_hourly_atr(bars, cutoff):
    """Return the excerpt's value at the last CLOSED hour, with source slots.

    Positive ranges avoid unresolved Pine var/na initialization semantics.
    The filter references each bar's own raw five-bar mean. Failed older
    slots reuse a more recent passing slot, as the original loop does.
    """
    history=[b for b in bars if b['close_time_ms']<=cutoff][-10:]
    if len(history)!=10 or history[-1]['close_time_ms']!=cutoff:
        raise ValueError('hourly_ATR_requires_ten_closed_hours')
    if any(b['close_time_ms']!=b['open_time_ms']+HOUR for b in history):
        raise ValueError('hourly_ATR_requires_H1_intervals')
    if any(a['close_time_ms']!=b['open_time_ms'] for a,b in zip(history,history[1:])):
        raise ValueError('hourly_ATR_history_gap')
    ranges=[]
    for b in history:
        if any(isinstance(b[k],bool) or not math.isfinite(b[k]) or b[k]<=0 for k in ('open','high','low','close')):
            raise ValueError('hourly_ATR_invalid_price')
        if b['low']>min(b['open'],b['close']) or b['high']<max(b['open'],b['close']):
            raise ValueError('hourly_ATR_invalid_OHLC')
        ranges.append(b['high']-b['low'])
    if min(ranges)<=0:
        raise ValueError('hourly_ATR_zero_range_needs_Pine_startup_review')
    raw={i:sum(ranges[i-4:i+1])/5 for i in range(4,10)}
    passing={i:.5*raw[i]<=ranges[i]<=1.5*raw[i] for i in range(4,10)}
    if not passing[8]:
        selected=list(range(5,10))
        branch='previous_filter_failed_use_current_raw_mean'
    else:
        selected=[next(j for j in range(9-offset,9) if passing[j]) for offset in range(1,6)]
        branch='previous_filter_passed_use_prior_slots_with_replacement'
    value=sum(ranges[i] for i in selected)/5
    return dict(method=METHOD,value=value,as_of_close_time_ms=cutoff,
                timeframe='1H',period=5,filter_lower=.5,filter_upper=1.5,
                branch=branch,raw_last_five_mean=raw[9],
                selected_bar_open_times_ms=[history[i]['open_time_ms'] for i in selected],
                selected_bar_ranges=[ranges[i] for i in selected],
                source_history_open_times_ms=[b['open_time_ms'] for b in history],
                indicator_choice_individually_confirmed_by_author=False,
                source_excerpt_port_not_full_Pine_execution=True)


def first_hourly_atr_entry(bars,daily_close,level,direction,visible_end):
    """Find the first permitted hour reaching one locally calculated H1 ATR.

    ATR freezes at each hour's open. That hour's future OHLC identifies only
    the derived teacher label, never the ATR or model's predictive inputs.
    """
    if direction not in ('long','short') or not math.isfinite(level) or level<=0:
        raise ValueError('invalid_hourly_ATR_entry_side_or_level')
    sign=1 if direction=='long' else -1
    candidates=[b for b in bars if daily_close<=b['open_time_ms']<visible_end
                and b['close_time_ms']<=visible_end]
    if not candidates or candidates[0]['open_time_ms']!=daily_close:
        raise ValueError('hourly_ATR_post_D1_history_missing')
    for b in candidates:
        cutoff=b['open_time_ms']
        atr=closed_hourly_atr(bars,cutoff)
        target=level+sign*atr['value']
        previous=next(p for p in reversed(bars) if p['close_time_ms']==cutoff)
        at_boundary=sign*(previous['close']-target)>=0
        extreme=b['high'] if direction=='long' else b['low']
        if at_boundary or sign*(extreme-target)>=0:
            return dict(decision_time_ms=cutoff,
                        entry_resolution='closed_hourly_bar' if at_boundary else 'pre_hour_proxy_not_exact_intrabar_entry',
                        authored_entry_time_window_ms=[cutoff,cutoff if at_boundary else cutoff+HOUR],
                        entry_label_source='author_condition_derived_one_H1_ATR_from_level',
                        author_exact_execution_hour_verified=False,
                        correction_evidence=dict(hourly_atr=atr,threshold_price=target,
                            level_distance_origin=level,condition_already_met_at_boundary=at_boundary,
                            threshold_recalculation='after_each_closed_H1_then_frozen_during_next_hour',
                            exact_intrabar_path_unknown=not at_boundary,
                            entry_hour_derived_under_project_ATR_convention=True),
                        exact_execution_price=None)
    raise ValueError('author_one_H1_ATR_not_reached_in_visible_post_D1_history')
