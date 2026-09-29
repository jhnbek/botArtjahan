"""Author's partial LONG first-breakout filter; no model or trading integration.

The caller must supply the causally available approach-start anchor and its
distance to the level in H1 ATR, not the current remaining gap. Selection of
accumulation boundaries and the ATR observation time is outside this helper.
The uninterrupted-approach flag must be independently established: H1 OHLC
does not prove absence of an intrabar pullback. No detector is inferred here.
"""
from __future__ import annotations

import math


def assess_long_first_breakout(
    initial_distance_to_level_atr: float | None,
    uninterrupted_approach_confirmed: bool | None,
) -> dict:
    """Return True/False/None permission from this single author filter.

    True permits considering the first breakout, not entering unconditionally.
    False requires waiting for a repeated setup, without guaranteeing entry on
    a particular later breakout. None means the author has not specified this
    combination or the required observation is unavailable. The rule is LONG
    only; mirroring it to short is not authorized by this contract.
    """
    if (uninterrupted_approach_confirmed is not None
            and type(uninterrupted_approach_confirmed) is not bool):
        raise ValueError('uninterrupted_approach_confirmed_must_be_bool_or_none')
    distance = initial_distance_to_level_atr
    if distance is not None:
        if isinstance(distance, bool) or not isinstance(distance, (int, float)):
            raise ValueError('initial_distance_to_level_atr_must_be_finite_nonnegative_number_or_none')
        try:
            distance = float(distance)
        except OverflowError as error:
            raise ValueError('initial_distance_to_level_atr_must_be_finite_nonnegative_number_or_none') from error
        if not math.isfinite(distance) or distance < 0:
            raise ValueError('initial_distance_to_level_atr_must_be_finite_nonnegative_number_or_none')

    permission = None
    action = 'author_rule_unspecified'
    if distance is None:
        reason = 'initial_approach_distance_unknown'
    elif distance < .5:
        permission = True
        reason = 'initial_distance_below_half_H1_ATR'
        action = 'first_breakout_permitted_by_this_filter_only'
    elif distance < 1:
        reason = 'half_to_one_H1_ATR_interval_unspecified'
    elif uninterrupted_approach_confirmed is True:
        permission = False
        reason = 'at_least_one_H1_ATR_without_pullback'
        action = 'skip_first_breakout_wait_for_repeated_setup'
    elif uninterrupted_approach_confirmed is None:
        reason = 'uninterrupted_approach_unknown'
    else:
        reason = 'approach_with_pullback_rule_unspecified'

    return dict(direction='long', first_breakout_permitted=permission, reason=reason,
                action=action, initial_distance_to_level_atr=distance,
                uninterrupted_approach_confirmed=uninterrupted_approach_confirmed,
                is_unconditional_entry_signal=False,
                specific_later_breakout_number=None)


def assess_long_approach_path(
    observed_prices: list[float] | tuple[float, ...],
    level: float,
    hourly_atr_at_start: float,
    uninterrupted_approach_confirmed: bool | None,
) -> dict:
    """Assess a whole approach prefix without resetting at each new bar.

    The first price is the independently established approach origin. Append
    observations as they become available; retain that first observation.
    All observations must be available by the caller's decision time. A
    completed breakout-bar close/high cannot represent its earlier crossing.

    ATR is frozen at the origin for this explicit research convention. It
    must be calculated from H1 bars already closed at that origin. Neither
    the ATR snapshot convention nor the origin detector is learned here.

    Prices describe net displacement, not sums of candle H-L ranges or ATRs
    recalculated separately per bar. Green bars do not reset the approach;
    the caller establishes whether a meaningful interruption occurred. The
    helper does not deduce intrabar continuity or a repeat-entry signal.
    """
    if not isinstance(observed_prices, (list, tuple)) or not observed_prices:
        raise ValueError('approach_requires_nonempty_price_observations')

    def positive_number(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError('approach_requires_finite_positive_prices_and_ATR')
        try:
            result = float(value)
        except OverflowError as error:
            raise ValueError('approach_requires_finite_positive_prices_and_ATR') from error
        if not math.isfinite(result) or result <= 0:
            raise ValueError('approach_requires_finite_positive_prices_and_ATR')
        return result

    prices = [positive_number(p) for p in observed_prices]
    level, atr = positive_number(level), positive_number(hourly_atr_at_start)
    origin = prices[0]
    if origin >= level:
        raise ValueError('long_approach_origin_must_be_below_level')
    initial_distance = (level - origin) / atr
    net_progress = (prices[-1] - origin) / atr
    remaining_distance = max(level - prices[-1], 0.) / atr
    if not all(math.isfinite(v) for v in (initial_distance, net_progress, remaining_distance)):
        raise ValueError('approach_normalized_distances_must_be_finite')
    result = assess_long_first_breakout(initial_distance, uninterrupted_approach_confirmed)
    result.update(approach_origin_price=origin, level=level,
                  hourly_atr_at_start=atr, atr_basis='frozen_at_approach_start',
                  latest_observed_price=prices[-1], observations=len(prices),
                  cumulative_net_progress_atr=net_progress,
                  remaining_distance_to_level_atr=remaining_distance,
                  level_reached_in_observed_prefix=max(prices) >= level,
                  first_observation_retained_as_origin=True,
                  candle_ranges_summed=False,
                  remaining_gap_used_as_initial_distance=False)
    return result
