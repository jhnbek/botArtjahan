"""Calendar window for daily level discovery, independent of the wall clock.

Historical reviews use the last supplied candle's UTC day unless an explicit
evaluation instant is supplied. The retained entries are the original objects
and keep their original order; metadata records the index offset for charts.

Old bars are excluded completely, including ATR warmup. Callers must recompute
ATR on the retained history and accept its initial warmup period. This helper
does not decide whether the latest candle has closed: that remains the feed or
caller's responsibility.
"""

from bisect import bisect_left, bisect_right
from calendar import monthrange
from collections.abc import Mapping
from datetime import date, datetime, timezone


DAY_MS = 86_400_000
DAILY_LEVEL_MONTHS = 18


def rebase_bar_indices(value, offset, key=''):
    """Move evidence coordinates from a sliced history to the original chart."""
    if key == 'history_window':
        return value
    if isinstance(value, dict):
        return {name:rebase_bar_indices(item, offset, name) for name,item in value.items()}
    if isinstance(value, list):
        indices = key == 'indices' or key.endswith('_indices') or key in ('mirror_pair','recent_close_switches','lifetime_close_switches')
        return [item+offset if indices and isinstance(item,int) else rebase_bar_indices(item, offset) for item in value]
    if isinstance(value, int) and (key == 'index' or key.endswith('_index')):
        return value+offset
    return value


def subtract_calendar_months(value: date, months: int) -> date:
    """Subtract months, clamping the day at the target month's final day."""
    if not isinstance(months, int) or isinstance(months, bool) or months < 0:
        raise ValueError("months must be a non-negative integer")
    month_index = value.year * 12 + value.month - 1 - months
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    return date(year, month, min(value.day, monthrange(year, month)[1]))


def _open_time_ms(bar) -> int:
    if isinstance(bar, Mapping):
        if "open_time_ms" in bar:
            return int(bar["open_time_ms"])
        return int(bar["open_time"])
    if hasattr(bar, "open_time_ms"):
        return int(bar.open_time_ms)
    return int(bar.open_time)


def _is_daily(times, interval) -> bool:
    if interval is not None:
        return str(interval).strip().lower() == "1d"
    differences = [right - left for left, right in zip(times, times[1:])]
    # A real one-day gap is required: all-weekly or all-two-day histories are
    # ambiguous and must not be silently treated as daily. Weekend/holiday gaps
    # are allowed, but any shorter/partial-day gap identifies another cadence.
    return bool(differences) and min(differences) == DAY_MS and all(
        difference % DAY_MS == 0 for difference in differences
    )


def daily_level_history(bars, *, interval=None, as_of_ms=None,
                        months=DAILY_LEVEL_MONTHS):
    """Return ``(retained_bars, metadata)`` for the daily calendar lookback.

    ``bars`` may contain Bar objects or mappings with epoch-millisecond
    ``open_time_ms``/``open_time``. An explicit interval is authoritative; only
    ``1d`` is restricted. Without one, a daily cadence is inferred conservatively.
    Non-daily data is returned in full, even when ``as_of_ms`` is supplied.

    For daily bars, the inclusive lower bound is midnight UTC on the date
    ``months`` calendar months before the evaluation day. An explicit as-of
    also excludes bars opening after that instant. With no explicit as-of,
    the last supplied bar determines the evaluation date, making this function
    deterministic for backtests and synthetic data. Inputs must be chronological
    so ``start_index``/``end_index`` describe a contiguous original slice.
    """
    if not isinstance(months, int) or isinstance(months, bool) or months < 0:
        raise ValueError("months must be a non-negative integer")
    original = list(bars)
    times = [_open_time_ms(bar) for bar in original]
    if any(right < left for left, right in zip(times, times[1:])):
        raise ValueError("level history must be ordered from oldest to newest")
    is_daily = _is_daily(times, interval)
    reference = (int(as_of_ms) if as_of_ms is not None
                 else times[-1] if times else None)
    reference_date = (datetime.fromtimestamp(reference / 1000, timezone.utc).date()
                      if reference is not None else None)
    metadata = {
        "applied": is_daily and reference is not None,
        "is_daily": is_daily,
        "months": months,
        "reference_source": ("explicit_as_of" if as_of_ms is not None
                             else "last_supplied_bar" if times else "unavailable"),
        "as_of_ms": reference,
        "as_of_date": reference_date.isoformat() if reference_date else None,
        "cutoff_ms": None,
        "cutoff_date": None,
        "start_index": 0,
        "end_index": len(original),
        "input_count": len(original),
        "retained_count": len(original),
        "excluded_before": 0,
        "excluded_after": 0,
        "older_bars_used_for_atr": False,
    }
    if not metadata["applied"]:
        return original, metadata

    cutoff_date = subtract_calendar_months(reference_date, months)
    cutoff_ms = int(datetime.combine(cutoff_date, datetime.min.time(),
                                     tzinfo=timezone.utc).timestamp() * 1000)
    start = bisect_left(times, cutoff_ms)
    end = bisect_right(times, reference)
    metadata.update({
        "cutoff_ms": cutoff_ms,
        "cutoff_date": cutoff_date.isoformat(),
        "start_index": start,
        "end_index": end,
        "retained_count": end - start,
        "excluded_before": start,
        "excluded_after": len(original) - end,
    })
    return original[start:end], metadata
