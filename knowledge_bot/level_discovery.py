"""Layer 1: Level Discovery Engine (Gerchik level taxonomy).

The knowledge base ships a level *validator* (`validate_level_strength` in
`detector_prototype.py`) which judges a level that is ALREADY supplied with
`basis_tags`, `touch_count`, `nearest_level`, etc. The base intentionally leaves
the *discovery* of candidate levels as `Manual Remains`:

    level_selection_strength_spec.md / Manual Remains:
      - Initial discovery of candidate levels.
      - Visual equality of touches.

This module mechanizes exactly that missing discovery step, then feeds each
discovered level back into the existing KB validator so the *rules* stay owned
by the knowledge base, not by this script.

Gerchik level taxonomy distilled from rulebook evidence
(`_knowledge_base/rulebook/level_selection_strength.md`, lecture chunks
`lec_010_de07310a_0033/0034`, `lec_013_fe508895_0019/0020`,
`lec_015_26f6fc9d_0034`, `lec_011_6a80276d_0011`):

    structural bases: inflection, mirror_level, paranormal_bar, two_bar_limit,
                                        post_chop_acceptance, strong_movement_stop

  base rules:
    * a level needs >= 2 touches into the same price (luft allowed)
    * the base point (BSU) is a historical event (stop of a strong move),
      not the nearest right-hand bar
        * a stronger level is often visible on a higher timeframe and confirmed
            on a lower timeframe (weekly/monthly -> daily; daily -> H1)
        * local levels inside a channel are weaker trade anchors than the main
            upper/lower boundaries
    * a "chopped" level (bars pierce straight through) is NOT a level
    * user clarification: false breakouts are entry observations, never a
      structural basis, a confirmation, or a strength bonus for the level

Source specs:
  _knowledge_base/rulebook/level_selection_strength.md
  _knowledge_base/detector_specs/level_selection_strength_spec.md
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from level_history import daily_level_history, rebase_bar_indices
from level_structure import paranormal_bar, profile_priority, exact_price, round_price_context

from detector_prototype import (
    validate_level_strength,
)
from scn002_strict_kb_backtest import Bar, atr_at, load_history
from level_structure import StructureParams, discover_strong_levels, level_events, level_profile, atr_series, confirming_contact, contact_is_excluded
from level_origin_context import pending_reversal_origins, prior_origin_false_breakouts

SOURCE_SPECS = [
    "_knowledge_base/rulebook/level_selection_strength.md",
    "_knowledge_base/detector_specs/level_selection_strength_spec.md",
]

BASIS_EXPLANATIONS = {
    "inflection": {
        "summary": "уровень излома: сильное движение остановили и цена сильно ушла обратно",
        "source": "level_selection_strength: lec_010_de07310a_0034",
    },
    "mirror_level": {
        "summary": "зеркальный уровень: касания с двух сторон в одну цену с допустимым люфтом",
        "source": "level_selection_strength: lec_010_de07310a_0033, lec_015_26f6fc9d_0012",
    },
    "paranormal_bar": {
        "summary": "уровень по паранормальному бару: тело |Close − Open| не менее 1,6 ATR",
        "source": "user clarification 2026-09-25: real body >= 1.6 ATR",
    },
    "two_bar_limit": {
        "summary": "два бара/экстремума бьют в одну цену, уровень подтвержден точностью касаний",
        "source": "detector_casebook: level_pass_mirror_two_bar_limit_001",
    },
    "limit_level": {
        "summary": "лимитное подтверждение: повторный подход к хвосту БСУ с допустимым недоходом",
        "source": "manual_reviews/channel_review_20260922; explicit touch evidence",
    },
    "post_chop_acceptance": {
        "summary": "после распила уровень снова приняли и подтвердили реакцией",
        "source": "level_selection_strength: lec_060_90f233eb_0008, lec_058_0cb17e41_0000",
    },
    "strong_movement_stop": {
        "summary": "точка остановки сильного движения, историческая БСУ-основа уровня",
        "source": "level_selection_strength: lec_011_6a80276d_0011, lec_068_ac7d233c_0015",
    },
    "round_number": {
        "summary": "круглая цифра усиливает уровень, но не является уровнем без структуры",
        "source": "level_selection_strength_spec.md; lec_015_26f6fc9d_0012",
    },
}

REJECT_EXPLANATIONS = {
    "no_structural_level_basis": "нет структурной основы уровня; круглая цифра или случайная цена не подходят",
    "not_nearest_working_level": "не ближайший рабочий уровень для текущей цены/сценария",
    "level_inside_channel": "уровень внутри канала/локального шума между основными границами",
    "short_tail_without_confirmation": "короткий хвост ЛП без подтверждения вторым касанием",
    "level_chopped_without_winner": "уровень распилен закрытиями с обеих сторон без победителя",
}


# --------------------------------------------------------------------------- #
# Parameters
# --------------------------------------------------------------------------- #
@dataclass
class DiscoveryParams:
    excluded_level_prices: tuple = ()  # active explicit rejections, context/mode scoped by caller
    working_selection: bool = True     # False exposes raw candidates for diagnostics only
    min_level_distance_fraction: float = 0.015
    working_reaction_bars: int = 10
    working_min_reaction_atr: float = 2.0
    working_min_reaction_efficiency: float = 0.55
    # Reviewable engineering thresholds for a stop/held retest before departure.
    working_stop_lookback: int = 3
    working_min_arrival_atr: float = 0.5
    working_rejection_tail_atr: float = 0.2
    working_hold_max_gap: int = 3
    working_reaction_start_bars: int = 3
    working_reaction_start_atr: float = 0.5
    working_formation_chop_bars: int = 5
    working_interaction_chop_penalty: float = 4.0
    pivot_wing: int = 3                 # fractal wing for swing extremes (BSU candidates)
    atr_period: int = 14
    cluster_luft_atr: float = 0.08      # touches within this band = same price (luft)
    contact_tol_atr: float = 0.06       # wick within this band counts as a touch
    mirror_luft_atr: float = 0.04       # close top/bottom equality strengthens mirror
    two_bar_luft_atr: float = 0.04      # tighter equality for two-bar limit basis
    false_breakout_luft_atr: float = 0.02
    min_false_breakout_tail_atr: float = 0.08
    min_false_breakout_tail_ratio: float = 0.35
    min_touches: int = 2                # >=2 touches into same price = level
    strong_move_atr: float = 1.5        # move into the pivot >= this*ATR = strong-move stop
    inflection_move_atr: float = 2.5    # izlom is stronger/rarer than generic stop
    inflection_min_efficiency: float = 0.55  # net close move / travelled close path
    inflection_context_lookback: int = 60  # distinguish a new extreme from a local range wave
    inflection_replacement_window: int = 40  # nearby BSUs in the same evolving reversal
    inflection_replacement_distance_atr: float = 1.0
    reversal_lookahead: int = 20        # izlom needs an actual move away after the pivot
    paranormal_lookback: int = 20       # incoming-move window (paranormal body uses ATR)
    chop_window: int = 40               # window to test repeated-chop contamination
    chop_cross_ratio: float = 0.30      # fraction of closes on both sides => chopped
    post_chop_reaction_atr: float = 0.25
    mtf_luft_atr: float = 0.15          # weekly/monthly level may be a wider zone on daily
    nearest_window_atr: float = 3.0     # only report levels within this*ATR of last price
    round_step: float = 0.0             # optional round-number step (0 = auto by price)
    excluded_contacts: tuple = ()       # explicit, context-scoped user BSU corrections
    automatic_origin_exclusions: tuple = ()  # recomputed from OHLC, separate from manual corrections
    automatic_origin_events: tuple = ()      # protected origin and known-close provenance


def structure_params(p):
    return StructureParams(atr_period=p.atr_period, paranormal_lookback=p.paranormal_lookback,
                           excluded_contacts=p.excluded_contacts,
                           automatic_origin_exclusions=p.automatic_origin_exclusions,
                           automatic_origin_events=p.automatic_origin_events)


# --------------------------------------------------------------------------- #
# Discovered level
# --------------------------------------------------------------------------- #
@dataclass
class Level:
    price: float
    bsu_index: int
    bsu_time: str
    side: str                           # "support" | "resistance" | "mirror"
    basis_tags: list[str] = field(default_factory=list)
    touch_count: int = 0
    false_breakout_count: int = 0
    touch_indices: list[int] = field(default_factory=list)
    inside_channel: bool = False
    local_noise: bool = False
    short_tail_without_confirmation: bool = False
    repeated_chop: bool = False
    post_chop_acceptance: bool = False
    higher_timeframe_confirmed: bool = False
    higher_timeframe: str = ""
    scope: str = "local"
    source: str = "auto_discovery"
    distance_atr: float = 0.0
    atr: float = 0.0
    exact_touch_count: int = 0
    touch_error_atr: float = 0.0
    touch_quality: str = "unknown"
    close_side_switches: int = 0
    close_balance_ratio: float = 0.0
    active_after_last_touch: bool = False
    last_reaction_atr: float = 0.0
    automation_confidence: float = 0.0
    # filled by KB validator:
    kb_status: str = ""
    kb_score: float = 0.0
    kb_hard_rejects: list[str] = field(default_factory=list)
    kb_strength: list[str] = field(default_factory=list)
    inflection_check: dict = field(default_factory=dict)
    structure: dict = field(default_factory=dict)
    entry_events: list[dict] = field(default_factory=list)
    history_window: dict = field(default_factory=dict)
    selection: dict = field(default_factory=dict)
    automatic_origin_exclusions: list[dict] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def bar_time(b: Bar) -> str:
    return datetime.fromtimestamp(b.open_time / 1000, tz=timezone.utc).isoformat()


def swing_pivots(bars: list[Bar], wing: int) -> list[tuple[int, str]]:
    """Return (index, kind) for fractal highs ('H') and lows ('L')."""
    out: list[tuple[int, str]] = []
    for i in range(wing, len(bars) - wing):
        window = bars[i - wing:i + wing + 1]
        if bars[i].high >= max(b.high for b in window) and \
                any(bars[i].high > b.high for b in window):
            out.append((i, "H"))
        if bars[i].low <= min(b.low for b in window) and \
                any(bars[i].low < b.low for b in window):
            out.append((i, "L"))
    return out


def avg_range(bars: list[Bar], i: int, window: int) -> float:
    lo = max(0, i - window)
    seg = bars[lo:i]
    if not seg:
        return bars[i].high - bars[i].low
    return sum(b.high - b.low for b in seg) / len(seg)


def is_paranormal(bars: list[Bar], i: int, atr: float, p: DiscoveryParams) -> bool:
    return paranormal_bar(bars, i, atr, p.paranormal_lookback)


def strong_move_into(bars: list[Bar], i: int, kind: str, atr: float,
                     p: DiscoveryParams, required_atr: float | None = None) -> bool:
    """Did a strong directional move stop at this pivot (inflection/stop basis)?"""
    lo = max(0, i - p.paranormal_lookback)
    seg = bars[lo:i + 1]
    if len(seg) < 3:
        return False
    if kind == "H":
        move = bars[i].high - min(b.low for b in seg)
    else:
        move = max(b.high for b in seg) - bars[i].low
    threshold = p.strong_move_atr if required_atr is None else required_atr
    return move >= threshold * atr


def strong_reversal_after(bars: list[Bar], i: int, kind: str, atr: float,
                          p: DiscoveryParams, required_atr: float | None = None) -> bool:
    """Inflection needs not only an incoming move, but also a move away."""
    hi = min(len(bars), i + p.reversal_lookahead + 1)
    seg = bars[i + 1:hi]
    if not seg:
        return False
    if kind == "H":
        move = bars[i].high - min(b.low for b in seg)
    else:
        move = max(b.high for b in seg) - bars[i].low
    threshold = p.strong_move_atr if required_atr is None else required_atr
    return move >= threshold * atr


def historical_mirror_confirmation(bars: list[Bar], i: int, kind: str, atr: float,
                                   p: DiscoveryParams) -> dict:
    """Prior opposite-side touch, followed by a close through the same price."""
    price = bars[i].high if kind == 'H' else bars[i].low
    tolerance = p.mirror_luft_atr * atr
    prefix = bars[:i]
    params = structure_params(p)
    atrs = [value or atr for value in atr_series(prefix,p.atr_period)]
    for j in range(i-p.paranormal_lookback-1, -1, -1):
        old = bars[j]
        if kind == 'H':
            touched = abs(old.low-price) <= tolerance and old.close > price
            crossed = touched and any(b.close < price-p.post_chop_reaction_atr*atr for b in bars[j+1:i])
        else:
            touched = abs(old.high-price) <= tolerance and old.close < price
            crossed = touched and any(b.close > price+p.post_chop_reaction_atr*atr for b in bars[j+1:i])
        contact = confirming_contact(prefix,j,price,'L' if kind=='H' else 'H',params,atrs) if touched else None
        if crossed and contact is not None:
            return {'index':j, 'time':bar_time(old), 'price':old.low if kind=='H' else old.high,
                    'tolerance':tolerance}
    return {}


def _two_bar_rejection(bars: list[Bar], first: int, price: float,
                       upper: bool, end: int) -> dict:
    """One outside close followed immediately by a return inside the BSU tail.

    ``end`` is exclusive: an unfinished prefix cannot borrow a future return.
    Two consecutive outside closes are continuation, not this two-bar pattern.
    """
    if first + 1 >= min(len(bars), end):
        return {}
    started_inside = bars[first].open < price if upper else bars[first].open > price
    outside = bars[first].close > price if upper else bars[first].close < price
    returned = bars[first+1].close < price if upper else bars[first+1].close > price
    if not started_inside or not outside or not returned:
        return {}
    return {'kind': 'two_bar_false_breakout', 'breakout_index': first,
            'breakout_time': bar_time(bars[first]), 'breakout_close': bars[first].close,
            'return_index': first+1, 'return_time': bar_time(bars[first+1]),
            'return_close': bars[first+1].close, 'level_price': price}


def inflection_anchors(bars: list[Bar], p: DiscoveryParams) -> dict[tuple[int, str], dict]:
    """BSU tail boundary held by subsequent closes, with a confirmed reversal.

    Thresholds are implementation hypotheses for manual review, not quotations
    from the knowledge base. Confirmation always occurs after the BSU.
    """
    anchors = {}
    contact_params = structure_params(p)
    contact_atrs = atr_series(bars,p.atr_period)
    for i in range(max(p.atr_period + 1, p.paranormal_lookback), len(bars)-p.pivot_wing):
        atr = atr_at(bars, i, p.atr_period)
        if not atr or atr <= 0:
            continue
        previous = bars[i-p.paranormal_lookback:i]
        for kind in ('H', 'L'):
            upper = kind == 'H'
            close = bars[i].close
            # A wick-only overshoot during the same reversal does not move BSU.
            if any(k == kind and idx < i <= a['confirmation_index']
                   for (idx, k), a in anchors.items()):
                continue
            price = bars[i].high if upper else bars[i].low
            if contact_is_excluded(bars[i],price,kind,atr,contact_params):
                continue
            boundary = max(b.high for b in previous) if upper else min(b.low for b in previous)
            if (price <= boundary if upper else price >= boundary):
                continue
            start = (min if upper else max)(range(i-p.paranormal_lookback, i),
                                           key=lambda j: bars[j].close)
            incoming = abs(price-bars[start].close)
            travel = sum(abs(bars[j].close-bars[j-1].close) for j in range(start+1, i+1))
            efficiency = abs(close-bars[start].close) / travel if travel else 0.0
            mirror = historical_mirror_confirmation(bars, i, kind, atr, p)
            if incoming < p.inflection_move_atr*atr:
                continue
            context = inflection_context(bars, i, kind, atr, p)
            if not context['eligible']:
                continue
            price = bars[i].high if upper else bars[i].low
            confirmed = None
            false_breakouts = []
            retests = []
            end = min(len(bars), i+p.reversal_lookahead+1)
            j = i+1
            while j < end:
                # One outside close is allowed only after the next bar has
                # actually returned. Keep both bars as false-breakout evidence,
                # never ordinary limiting touches and never a replacement BSU.
                if (bars[j].close > price if upper else bars[j].close < price):
                    # Establish the BSU first. An immediate next-bar extension
                    # is still the incoming trend, not a breakout of a held level.
                    if j <= i+p.pivot_wing:
                        break
                    rejection = _two_bar_rejection(bars, j, price, upper, end)
                    if not rejection:
                        break
                    false_breakouts.append(rejection)
                    # Both bars are entry context, never the bar that earns a
                    # new strength/inflection confirmation for this level.
                    j += 2
                    continue
                else:
                    value = bars[j].high if upper else bars[j].low
                    local_atr = atr_at(bars,j,p.atr_period) or atr
                    penetration = value-price if upper else price-value
                    opened_inside = bars[j].open < price if upper else bars[j].open > price
                    closed_inside = bars[j].close < price if upper else bars[j].close > price
                    if opened_inside and closed_inside and penetration > StructureParams().penetration_atr*local_atr:
                        j += 1
                        continue
                    contact = confirming_contact(bars[:end],j,price,kind,contact_params,contact_atrs[:end])
                    if contact is not None:
                        retests.append({'index': j, 'time': bar_time(bars[j]),
                                        'price': value, 'gap_atr': abs(value-price)/local_atr,
                                        'known_index':contact['known_index'],
                                        'known_time':bar_time(bars[contact['known_index']])})
                reversal = price-bars[j].close if upper else bars[j].close-price
                if j >= i+p.pivot_wing and reversal >= p.inflection_move_atr*atr:
                    confirmed = j
                    break
                j += 1
            if confirmed is not None:
                retests = [touch for touch in retests if touch['known_index'] <= confirmed]
                # A less smooth incoming move can still stop at a repeatedly
                # defended extreme. Require two distinct held-side retests,
                # the same broad-range gate, and the full reversal threshold.
                repeated_hold = len(retests) >= p.min_touches
                if efficiency < p.inflection_min_efficiency and not (mirror or repeated_hold):
                    continue
                anchors[(i, kind)] = {'price': price, 'confirmation_index': confirmed,
                    'confirmation_time': bar_time(bars[confirmed]), 'incoming_atr': incoming/atr,
                    'efficiency': efficiency, 'atr_at_bsu': atr,
                    'mirror_confirmation':mirror,
                    'efficiency_exception':efficiency < p.inflection_min_efficiency,
                    'efficiency_exception_reason': ('historical_mirror' if mirror else 'repeated_held_retests')
                        if efficiency < p.inflection_min_efficiency else '',
                    'held_retests': retests, 'two_bar_false_breakouts': false_breakouts,
                    'context': context, 'status': 'confirmed'}
    annotate_inflection_lifecycle(bars, anchors, p)
    return anchors


def historical_boundary_confirmation(bars: list[Bar], i: int, price: float,
                                     atr: float, p: DiscoveryParams) -> dict:
    """An earlier range extreme reversed, then was accepted from the other side.

    Both reactions must already be known before the current BSU. A lone mirror
    touch or a pair of arbitrary nearby wicks cannot waive the context filter.
    """
    tolerance = p.contact_tol_atr * atr
    touches = []
    prefix = bars[:i]
    contact_params = structure_params(p)
    contact_atrs = atr_series(prefix,p.atr_period)
    for j in range(p.paranormal_lookback, i - p.paranormal_lookback):
        past = bars[j-p.paranormal_lookback:j]
        for kind, value in (('H', bars[j].high), ('L', bars[j].low)):
            if abs(value-price) > tolerance:
                continue
            old_atr = atr_at(bars, j, p.atr_period)
            if not old_atr or old_atr <= 0:
                continue
            contact = confirming_contact(prefix,j,price,kind,contact_params,contact_atrs)
            if contact is None:
                continue
            upper = kind == 'H'
            if (bars[j].close > price if upper else bars[j].close < price):
                continue
            confirmation = None
            for k in range(j+1, min(i, j+p.reversal_lookahead+1)):
                if (bars[k].close > value if upper else bars[k].close < value):
                    break
                move = value-bars[k].close if upper else bars[k].close-value
                if k >= j+p.pivot_wing and move >= p.inflection_move_atr*old_atr:
                    confirmation = k
                    break
            if confirmation is None:
                continue
            confirmation = max(confirmation,contact['known_index'])
            extreme = value > max(b.high for b in past) if upper else value < min(b.low for b in past)
            touches.append({'index': j, 'kind': kind, 'price': value,
                            'time': bar_time(bars[j]), 'range_extreme': extreme,
                            'confirmation_index': confirmation,
                            'confirmation_time': bar_time(bars[confirmation])})
    for first in touches:
        if not first['range_extreme']:
            continue
        for second in touches:
            if second['kind'] != first['kind'] and second['index'] > first['confirmation_index']:
                return {'original': first, 'opposite_touch': second, 'tolerance': tolerance}
    return {}


def inflection_context(bars: list[Bar], i: int, kind: str, atr: float,
                       p: DiscoveryParams) -> dict:
    """Broader range gate; uses only bars strictly preceding this BSU.

    Being outside the short window is insufficient: a pullback or a false exit
    from a smaller channel can still sit within the established broader range.
    These numeric horizons are reviewable heuristics, not learned thresholds.
    """
    start = max(0, i-max(p.inflection_context_lookback, p.paranormal_lookback))
    prior = bars[start:i]
    low, high = min(b.low for b in prior), max(b.high for b in prior)
    price = bars[i].high if kind == 'H' else bars[i].low
    # A negligible extension when an older extreme rolls out of the window
    # must not turn the same rejected pullback into a new BSU a few days later.
    extends = price > high+p.contact_tol_atr*atr if kind == 'H' else price < low-p.contact_tol_atr*atr
    boundary = historical_boundary_confirmation(bars, i, price, atr, p) if not extends else {}
    return {'eligible': extends or bool(boundary),
            'reason': 'broader_range_extreme' if extends else
                      'confirmed_historical_boundary' if boundary else 'local_move_in_broader_range',
            'range_low': low, 'range_high': high, 'lookback_bars': len(prior),
            'start_time': bar_time(bars[start]), 'end_time': bar_time(bars[i-1]),
            'boundary_confirmation': boundary}


def annotate_inflection_lifecycle(bars: list[Bar], anchors: dict,
                                 p: DiscoveryParams) -> None:
    """Keep original BSU, annotate near touches and a later stronger reversal.

    A wick alone never supersedes an anchor. The replacement must itself be
    confirmed and a close must have crossed the old tail. The effective time
    is the later of these two observations; a prefix cannot see the future.
    """
    contact_params = structure_params(p)
    contact_atrs = atr_series(bars,p.atr_period)
    for (i, kind), anchor in anchors.items():
        price, atr = anchor['price'], anchor['atr_at_bsu']
        upper = kind == 'H'
        touches = []
        later_rejections = []
        j = i+1
        while j < len(bars):
            if (bars[j].close > price if upper else bars[j].close < price):
                rejection = _two_bar_rejection(bars, j, price, upper, len(bars))
                if j <= i+p.pivot_wing or not rejection:
                    break
                if j > anchor['confirmation_index']:
                    later_rejections.append(rejection)
                j += 2
                continue
            value = bars[j].high if upper else bars[j].low
            penetration = value-price if upper else price-value
            local_atr = atr_at(bars,j,p.atr_period) or atr
            contact = confirming_contact(bars,j,price,kind,contact_params,contact_atrs)
            if contact is not None:
                touches.append({'index': j, 'time': bar_time(bars[j]), 'price': value,
                                'gap': abs(value-price), 'gap_atr': abs(value-price)/local_atr,
                                'known_index':contact['known_index'],
                                'known_time':bar_time(bars[contact['known_index']]),
                                'contact_rule':contact['contact_rule']})
            j += 1
        anchor['limit_confirmations'] = touches
        anchor['later_two_bar_false_breakouts'] = later_rejections
        for (j, new_kind), replacement in anchors.items():
            if new_kind != kind or not i < j <= i+p.inflection_replacement_window:
                continue
            if any(i < middle < j and middle_kind != kind and
                   middle_anchor['confirmation_index'] < j
                   for (middle, middle_kind), middle_anchor in anchors.items()):
                continue  # another structural swing separates these reversals
            extension = replacement['price']-price if upper else price-replacement['price']
            if not 0 < extension <= p.inflection_replacement_distance_atr*atr:
                continue
            # A separately confirmed, stronger nearby reversal can supersede
            # this BSU even when the crossing later returns. The crossing alone
            # is insufficient; the replacement must meet every inflection gate.
            crossed = next((k for k in range(j, min(len(bars), i+p.inflection_replacement_window+1))
                            if (bars[k].close > price if upper else bars[k].close < price)), None)
            if crossed is None:
                continue
            effective = max(crossed, replacement['confirmation_index'])
            anchor.update(status='superseded', superseded_by={
                'price': replacement['price'], 'bsu_time': bar_time(bars[j]),
                'close_cross_time': bar_time(bars[crossed]),
                'effective_time': bar_time(bars[effective]), 'effective_index': effective},
                retained_secondary=bool(touches))
            break


def apply_confirmed_inflections(bars: list[Bar], levels: list[Level], confirmations: list[dict]) -> list[Level]:
    """Apply explicitly reviewed BSUs, keeping manual origin separate from detection.

    Confirmations must already be scoped to exchange, symbol and timeframe.
    Dates must exist in this chart and their high/low must match the drawn price
    within 0.1%. No nearest-date fallback or silently inferred year is allowed.
    """
    result = list(levels)
    by_date = {bar_time(bar)[:10]: i for i, bar in enumerate(bars)}
    for review in confirmations:
        date = review.get('human_bsu_date') or {}
        if not all(date.get(key) for key in ('year', 'month', 'day')):
            continue
        try:
            day = datetime(int(date['year']), int(date['month']), int(date['day'])).strftime('%Y-%m-%d')
            requested = float(review['price'])
        except (ValueError, TypeError, KeyError):
            continue
        if day not in by_date or requested <= 0:
            continue
        i = by_date[day]
        bar = bars[i]
        price, side = min(((bar.high, 'resistance'), (bar.low, 'support')),
                          key=lambda value: abs(value[0]-requested))
        tolerance = requested*0.001
        if abs(price-requested) > tolerance:
            continue
        detected = next((lv for lv in result if lv.bsu_index == i
                         and abs(lv.price-price) <= tolerance and 'inflection' in lv.basis_tags), None)
        result = [lv for lv in result if abs(lv.price-price) > tolerance]
        atr = atr_at(bars, i, 14) or 0.0
        base = detected or Level(price=price, bsu_index=i, bsu_time=bar_time(bar), side=side,
                                 basis_tags=['inflection'], kb_status='warn', atr=atr)
        result.append(replace(base, source='human_confirmed_inflection',
            inflection_check={**base.inflection_check, 'origin':'human_confirmed', 'requested_price':requested,
                'reviewed_at':review.get('recorded_at'), 'bsu_date':day,
                'note':review.get('note',''), 'automatic_detection':detected is not None}))
    return result


def count_touches(bars: list[Bar], price: float, tol: float,
                  exclude: int) -> tuple[int, list[int]]:
    touches = 0
    idxs: list[int] = []
    in_contact = False
    for k, b in enumerate(bars):
        contact = (b.low <= price + tol) and (b.high >= price - tol)
        if contact and not in_contact:
            touches += 1
            idxs.append(k)
            in_contact = True
        elif not contact:
            in_contact = False
    return touches, idxs


def touch_quality_metrics(bars: list[Bar], price: float, touch_indices: list[int],
                          atr: float, p: DiscoveryParams) -> tuple[int, float, str]:
    if not touch_indices or atr <= 0:
        return 0, 0.0, "unknown"
    distances = [min(abs(bars[i].high - price), abs(bars[i].low - price)) for i in touch_indices]
    exact_tol = p.two_bar_luft_atr * atr
    exact_count = sum(1 for distance in distances if distance <= exact_tol)
    max_error_atr = max(distances) / atr
    if exact_count >= p.min_touches and max_error_atr <= p.contact_tol_atr:
        quality = "tight"
    elif exact_count >= p.min_touches and max_error_atr <= max(p.cluster_luft_atr, p.contact_tol_atr * 2):
        quality = "acceptable"
    else:
        quality = "loose"
    return exact_count, max_error_atr, quality


def close_side_metrics(bars: list[Bar], price: float, start_idx: int,
                       atr: float, p: DiscoveryParams) -> tuple[int, float, bool]:
    if atr <= 0:
        return 0, 0.0, False
    tol = p.contact_tol_atr * atr
    states: list[int] = []
    for b in bars[start_idx:]:
        if b.close > price + tol:
            states.append(1)
        elif b.close < price - tol:
            states.append(-1)
    if len(states) < 12:
        return 0, 0.0, False
    switches = sum(1 for prev, cur in zip(states, states[1:]) if prev != cur)
    above = sum(1 for state in states if state > 0)
    below = sum(1 for state in states if state < 0)
    balance = min(above, below) / len(states)
    chopped = switches >= 4 and balance >= p.chop_cross_ratio
    return switches, balance, chopped


def upper_tail(b: Bar) -> float:
    return b.high - max(b.open, b.close)


def lower_tail(b: Bar) -> float:
    return min(b.open, b.close) - b.low


def range_of(b: Bar) -> float:
    return max(b.high - b.low, 1e-12)


def false_breakout_events(bars: list[Bar], price: float, atr: float,
                          side: str, p: DiscoveryParams) -> tuple[int, list[int], bool]:
    """Count meaningful LP events, not every wick poke through the level."""
    tol = p.false_breakout_luft_atr * atr
    event_indices: list[int] = []
    short_tail_seen = False
    in_event = False
    for i, b in enumerate(bars):
        upper_sweep = b.open < price and b.high > price + tol and b.close < price
        lower_sweep = b.open > price and b.low < price - tol and b.close > price
        if side == "resistance":
            sweep = upper_sweep
            tail = upper_tail(b)
        elif side == "support":
            sweep = lower_sweep
            tail = lower_tail(b)
        else:
            sweep = upper_sweep or lower_sweep
            tail = max(upper_tail(b) if upper_sweep else 0.0,
                       lower_tail(b) if lower_sweep else 0.0)

        meaningful_tail = (
            tail >= p.min_false_breakout_tail_atr * atr and
            tail >= p.min_false_breakout_tail_ratio * range_of(b)
        )
        if sweep and meaningful_tail and not in_event:
            event_indices.append(i)
            in_event = True
        elif sweep and not meaningful_tail:
            short_tail_seen = True
        elif not sweep:
            in_event = False
    return len(event_indices), event_indices, short_tail_seen


def has_two_bar_limit(bars: list[Bar], touch_indices: list[int], price: float,
                      tol: float) -> bool:
    exact_hits = 0
    for i in touch_indices:
        b = bars[i]
        if min(abs(b.high - price), abs(b.low - price)) <= tol:
            exact_hits += 1
    return exact_hits >= 2


def has_post_chop_acceptance(bars: list[Bar], price: float, side: str,
                             touch_indices: list[int], atr: float,
                             p: DiscoveryParams) -> bool:
    """Level may be restored if a later touch is accepted and price departs."""
    for i in touch_indices:
        hi = min(len(bars), i + 6)
        seg = bars[i + 1:hi]
        if len(seg) < 2:
            continue
        if side == "support":
            closes_hold = sum(1 for b in seg if b.close >= price) >= 2
            departure = max(b.high for b in seg) - price >= p.post_chop_reaction_atr * atr
        elif side == "resistance":
            closes_hold = sum(1 for b in seg if b.close <= price) >= 2
            departure = price - min(b.low for b in seg) >= p.post_chop_reaction_atr * atr
        else:
            upper_hold = sum(1 for b in seg if b.close >= price) >= 2
            lower_hold = sum(1 for b in seg if b.close <= price) >= 2
            departure = max(max(b.high for b in seg) - price,
                            price - min(b.low for b in seg)) >= p.post_chop_reaction_atr * atr
            closes_hold = upper_hold or lower_hold
        if closes_hold and departure:
            return True
    return False


def reaction_after_last_touch(bars: list[Bar], price: float, side: str,
                              touch_indices: list[int], atr: float,
                              p: DiscoveryParams) -> tuple[bool, float]:
    if not touch_indices or atr <= 0:
        return False, 0.0
    i = touch_indices[-1]
    seg = bars[i + 1:min(len(bars), i + 7)]
    if not seg:
        return False, 0.0
    tol = p.contact_tol_atr * atr
    if side == "support":
        reaction = max(b.high for b in seg) - price
        holds = sum(1 for b in seg if b.close >= price - tol) >= min(2, len(seg))
    elif side == "resistance":
        reaction = price - min(b.low for b in seg)
        holds = sum(1 for b in seg if b.close <= price + tol) >= min(2, len(seg))
    else:
        reaction = max(max(b.high for b in seg) - price, price - min(b.low for b in seg))
        holds = True
    reaction_atr = max(0.0, reaction / atr)
    return holds and reaction_atr >= p.post_chop_reaction_atr, reaction_atr


def automation_confidence_for_level(lv: Level) -> float:
    score = 0.0
    if any(tag in lv.basis_tags for tag in BASIS_EXPLANATIONS if tag != "round_number"):
        score += 0.25
    if lv.touch_quality == "tight":
        score += 0.25
    elif lv.touch_quality == "acceptable":
        score += 0.15
    if lv.exact_touch_count >= 3:
        score += 0.15
    elif lv.exact_touch_count >= 2:
        score += 0.10
    if not lv.repeated_chop and lv.close_balance_ratio < 0.30:
        score += 0.15
    if lv.active_after_last_touch:
        score += 0.10
    if lv.higher_timeframe_confirmed:
        score += 0.10
    return round(min(score, 1.0), 3)


def ordered_basis(tags: list[str]) -> list[str]:
    order = [
        "inflection",
        "limit_level",
        "mirror_level",
        "paranormal_bar",
        "two_bar_limit",
        "post_chop_acceptance",
        "strong_movement_stop",
        "round_number",
    ]
    seen = set(tags)
    return [tag for tag in order if tag in seen]


def is_chopped(bars: list[Bar], price: float, idx: int, p: DiscoveryParams) -> bool:
    lo = max(0, idx - p.chop_window)
    hi = min(len(bars), idx + p.chop_window)
    seg = bars[lo:hi]
    if len(seg) < 10:
        return False
    above = sum(1 for b in seg if b.close > price)
    below = sum(1 for b in seg if b.close < price)
    total = above + below
    if total == 0:
        return False
    minority = min(above, below) / total
    return minority >= p.chop_cross_ratio and abs(above - below) < len(seg) * 0.2


def round_number_step(price: float) -> float:
    if price >= 10000:
        return 1000.0
    if price >= 1000:
        return 100.0
    if price >= 100:
        return 10.0
    if price >= 10:
        return 1.0
    if price >= 1:
        return 0.1
    return 0.01


def near_round_number(price: float, step: float) -> bool:
    if step <= 0:
        return round_price_context(price)
    nearest = round(price / step) * step
    return abs(price - nearest) <= step * 0.02


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #
def entry_event_records(bars: list[Bar], price: float, events: list[dict]) -> list[dict]:
    """Keep completed LP patterns for later entry analysis, independent of strength."""
    return [{
        'pattern': event['role'], 'level_price': event.get('protected_origin_price', price),
        **({'context_source': 'prior_origin', 'candidate_level_price': price,
            'protected_origin_time': event['protected_origin_time'],
            'origin_status': event['origin_status']} if event.get('context_source') == 'prior_origin' else {}),
        'direction': 'long' if event['kind'] == 'L' else 'short',
        'bar_count': len(event['indices']),
        'bars': [{'index': i, 'time': bar_time(bars[i]), 'open': bars[i].open,
                  'high': bars[i].high, 'low': bars[i].low, 'close': bars[i].close}
                 for i in event['indices']],
        'known_index': event['known_index'], 'known_time': event['known_time'],
        'time_semantics': 'after_close_of_named_bar', 'confirms_level': False,
    } for event in events if event['role'] in ('false_breakout','false_breakout_two_bar')]


def discover_levels(
    bars: list[Bar],
    p: DiscoveryParams,
    higher_levels: list[Level] | None = None,
    higher_timeframe: str = "",
    *, as_of_ms=None, interval=None, selection_audit=None,
) -> list[Level]:
    """Discover in the daily lifetime window, retaining original chart indices."""
    recent, history = daily_level_history(bars, interval=interval, as_of_ms=as_of_ms)
    local_audit=[] if selection_audit is not None else None
    result = _discover_levels_in_window(recent, p, higher_levels, higher_timeframe, local_audit)
    offset = history['start_index']
    if selection_audit is not None:
        selection_audit.extend(rebase_bar_indices(local_audit,offset))

    for level in result:
        level.bsu_index += offset
        level.touch_indices = [i+offset for i in level.touch_indices]
        level.structure = rebase_bar_indices(level.structure, offset)
        level.inflection_check = rebase_bar_indices(level.inflection_check, offset)
        level.entry_events = rebase_bar_indices(level.entry_events, offset)
        level.selection = rebase_bar_indices(level.selection, offset)
        level.automatic_origin_exclusions = rebase_bar_indices(level.automatic_origin_exclusions, offset)
        level.history_window = history
    return result


def _discover_levels_in_window(bars, p, higher_levels=None, higher_timeframe='', selection_audit=None):
    n = len(bars)
    if n < p.atr_period + p.pivot_wing * 2 + 2:
        return []
    last_atr = atr_at(bars, n - 1, p.atr_period) or 0.0
    if last_atr <= 0:
        return []
    last_price = bars[-1].close
    structural_params = structure_params(p)
    structural_atrs = atr_series(bars,p.atr_period)

    pivots = swing_pivots(bars, p.pivot_wing)
    anchors = inflection_anchors(bars, p)
    origins = pending_reversal_origins(
        bars, p, structural_params,
        lambda i, kind, atr: inflection_context(bars, i, kind, atr, p),
        lambda i, kind, atr: historical_mirror_confirmation(bars, i, kind, atr, p),
        anchors, structural_atrs)
    automatic_constraints, automatic_events = prior_origin_false_breakouts(
        bars, origins, structural_params, structural_atrs)
    p = replace(p, automatic_origin_exclusions=automatic_constraints,
                automatic_origin_events=tuple(automatic_events))
    structural_params = structure_params(p)
    anchors = {key: value for key, value in anchors.items()
               if not contact_is_excluded(bars[key[0]], value['price'], key[1],
                                          structural_atrs[key[0]], structural_params)}
    preferred = [(v['price'], idx, kind) for (idx, kind), v in anchors.items()]
    profiles = discover_strong_levels(bars, preferred, structural_params)
    pivots = sorted(set(pivots) | set(anchors))

    # cluster pivots that hit the same price (within luft)
    raw: list[tuple[float, int, str]] = []  # (price, index, kind)
    for idx, kind in pivots:
        price = bars[idx].high if kind == "H" else bars[idx].low
        if contact_is_excluded(bars[idx],price,kind,structural_atrs[idx] or last_atr,structural_params):
            continue
        raw.append((price, idx, kind))
    raw.sort(key=lambda t: t[0])

    luft = p.cluster_luft_atr * last_atr
    clusters: list[list[tuple[float, int, str]]] = []
    for item in raw:
        if clusters and abs(item[0] - clusters[-1][0][0]) <= luft:
            clusters[-1].append(item)
        else:
            clusters.append([item])

    levels: list[Level] = []
    for cl in clusters:
        # Never invent an average price between real wick contacts.
        counts = {value:sum(exact_price(value, other[0]) for other in cl) for value,_,_ in cl}
        price, bsu_idx, _ = min(cl, key=lambda c:(-counts[c[0]],c[1]))
        # A reversal supplies a candidate; exact repeated contacts can establish
        # an older, more precise price within its microscopic neighbourhood.
        anchor_keys = [(idx, kind) for _, idx, kind in cl if (idx, kind) in anchors]
        active_keys = [key for key in anchor_keys if anchors[key]['status'] == 'confirmed']
        chosen_anchor = max(active_keys or anchor_keys) if anchor_keys else None
        if chosen_anchor is not None:
            bsu_idx = chosen_anchor[0]
            price = anchors[chosen_anchor]['price']
        # A later reversal near an established exact price reinforces that price.
        # Keep its own inflection evidence separately from the older level BSU.
        consensus = [v for v in profiles if v['exact_price_contact_count'] >= 2
                     and abs(v['price']-price) <= structural_params.precision_atr *
                     min(v['atr_at_bsu'], structural_atrs[bsu_idx] or last_atr)]
        if consensus:
            canonical = min(consensus, key=profile_priority)
            price, bsu_idx = canonical['price'], canonical['bsu_index']
        kinds = {c[2] for c in cl}

        atr_at_bsu = atr_at(bars, bsu_idx, p.atr_period) or last_atr

        events = level_events(bars, price, structural_params, structural_atrs)
        t_idx = sorted({e['index'] for e in events if e['role'] in ('touch', 'near_touch')})
        touches = len(t_idx)

        # side classification
        if "H" in kinds and "L" in kinds:
            side = "mirror"
        elif "H" in kinds:
            side = "resistance"
        else:
            side = "support"

        # Entry observations remain available but cannot supply touch credit.
        fb = sum(e['role'].startswith('false_breakout') for e in events)

        # Structural bases are additive when each has separate evidence. The
        # casebook itself expects e.g. mirror_level + two_bar_limit together.
        bsu_kind = 'H' if abs(bars[bsu_idx].high-price) <= abs(bars[bsu_idx].low-price) else 'L'
        is_para = is_paranormal(bars, bsu_idx, atr_at_bsu, p)
        is_strong_stop = strong_move_into(bars, bsu_idx, bsu_kind, atr_at_bsu, p)
        is_mirror = side == "mirror" or bool(chosen_anchor and anchors[chosen_anchor].get('mirror_confirmation'))
        is_inflection = chosen_anchor is not None and anchors[chosen_anchor]['status'] == 'confirmed'
        is_two_bar_limit = has_two_bar_limit(
            bars, t_idx, price, p.two_bar_luft_atr * last_atr)

        basis: list[str] = []
        if is_inflection:
            basis.append("inflection")
        if chosen_anchor and anchors[chosen_anchor].get('limit_confirmations'):
            basis.append("limit_level")
        if is_mirror:
            basis.append("mirror_level")
        if is_para:
            basis.append("paranormal_bar")
        if is_two_bar_limit:
            basis.append("two_bar_limit")
        if is_strong_stop and not is_inflection:
            basis.append("strong_movement_stop")
        # round number is strengthening context only
        round_ctx = near_round_number(price, p.round_step)

        chopped = is_chopped(bars, price, bsu_idx, p)
        post_chop_acceptance = chopped and has_post_chop_acceptance(
            bars, price, side, t_idx, last_atr, p)
        side_switches, close_balance, historical_chop = close_side_metrics(
            bars, price, bsu_idx, last_atr, p)
        if historical_chop and not post_chop_acceptance:
            chopped = True
        if post_chop_acceptance:
            basis.append("post_chop_acceptance")
        exact_touch_count, touch_error_atr, touch_quality = touch_quality_metrics(
            bars, price, t_idx, last_atr, p)
        active_after_last_touch, last_reaction_atr = reaction_after_last_touch(
            bars, price, side, t_idx, last_atr, p)
        distance_atr = abs(last_price - price) / last_atr

        short_tail_without_confirmation = False
        if touches < p.min_touches and not basis and not short_tail_without_confirmation:
            continue

        lvl = Level(
            price=price, bsu_index=bsu_idx, bsu_time=bar_time(bars[bsu_idx]),
            side=side, basis_tags=ordered_basis(basis), touch_count=touches,
            false_breakout_count=fb, touch_indices=t_idx,
            entry_events=entry_event_records(bars,price,events),
            short_tail_without_confirmation=short_tail_without_confirmation,
            repeated_chop=chopped and not post_chop_acceptance,
            post_chop_acceptance=post_chop_acceptance,
            distance_atr=distance_atr, atr=last_atr,
            exact_touch_count=exact_touch_count,
            touch_error_atr=touch_error_atr,
            touch_quality=touch_quality,
            close_side_switches=side_switches,
            close_balance_ratio=close_balance,
            active_after_last_touch=active_after_last_touch,
            last_reaction_atr=last_reaction_atr,
            inflection_check=anchors[chosen_anchor] if chosen_anchor else {},
        )
        if round_ctx:
            lvl.basis_tags.append("round_number")
        if chosen_anchor and bsu_idx != chosen_anchor[0]:
            lvl.inflection_check = {**lvl.inflection_check, 'reinforcing_bsu': {
                'index':chosen_anchor[0], 'time':bar_time(bars[chosen_anchor[0]]),
                'price':anchors[chosen_anchor]['price'], 'established_level_price':price}}
        levels.append(lvl)

    # Structural limit/mirror evidence is independent of the fractal candidates.
    # Use actual held wick prices and local volatility, including adjacent bars.
    unique = {}
    for lv in levels:
        previous = unique.get(lv.price)
        if previous is None:
            unique[lv.price] = lv
            continue
        checks = [check for check in (previous.inflection_check, lv.inflection_check) if check]
        previous.basis_tags = ordered_basis(previous.basis_tags + lv.basis_tags)
        if checks:
            primary = max(checks, key=lambda check:(check.get('status') == 'confirmed',
                                                    check.get('confirmation_index', -1)))
            previous.inflection_check = {**primary, 'coincident_inflection_evidence':checks}
    levels = list(unique.values())
    structural_tags = {'mirror_level', 'limit_level', 'two_bar_limit'}
    for lv in levels:
        lv.basis_tags = [tag for tag in lv.basis_tags if tag not in structural_tags]
        if lv.side == 'mirror':
            bsu = bars[lv.bsu_index]
            lv.side = 'resistance' if abs(bsu.high-lv.price) <= abs(bsu.low-lv.price) else 'support'
    for profile in profiles:
        price = profile['price']
        lv = next((lv for lv in levels if abs(lv.price-price) < 1e-8), None)
        if lv is None:
            lv = Level(price=price, bsu_index=profile['bsu_index'], bsu_time=profile['bsu_time'],
                       side='resistance' if profile['kind'] == 'H' else 'support',
                       atr=last_atr, distance_atr=abs(last_price-price)/last_atr)
            levels.append(lv)
        lv.structure = profile
        if profile['round_price_context'] and 'round_number' not in lv.basis_tags:
            lv.basis_tags.append('round_number')
        lv.basis_tags = ordered_basis(lv.basis_tags + profile['basis_tags'])
        if 'mirror_level' in profile['basis_tags']:
            lv.side = 'mirror'
        contacts = [e for e in profile['events'] if e['role'] in ('touch', 'near_touch')]
        lv.touch_indices = sorted({e['index'] for e in contacts})
        lv.touch_count = len(lv.touch_indices)
        lv.false_breakout_count = sum(e['role'].startswith('false_breakout') for e in profile['events'])
        lv.entry_events = entry_event_records(bars,price,profile['events'])
        lv.exact_touch_count = profile['precise_contact_count']
        lv.touch_error_atr = max((e['gap_atr'] for e in contacts),default=0.0)
        lv.touch_quality = ('tight' if lv.exact_touch_count >= 2 and lv.touch_error_atr <= p.contact_tol_atr
                            else 'acceptable' if lv.exact_touch_count >= 2 else 'loose')
        lv.repeated_chop = profile['currently_chopped']
        lv.close_side_switches = len(profile['recent_close_switches'])
        lv.short_tail_without_confirmation = False
        lv.last_reaction_atr = contacts[-1]['reaction_atr'] if contacts else 0.0
        lv.active_after_last_touch = bool(contacts and contacts[-1]['reaction_confirmed_index'] is not None)

    if higher_levels:
        mtf_tol = p.mtf_luft_atr * last_atr
        for lv in levels:
            best = min(higher_levels, key=lambda h: abs(h.price - lv.price),
                       default=None)
            if best and abs(best.price - lv.price) <= mtf_tol:
                lv.higher_timeframe_confirmed = True
                lv.higher_timeframe = higher_timeframe

    for lv in levels:
        lv.automatic_origin_exclusions = [event for event in automatic_events
            if any(abs(lv.price-contact['price']) <= structural_params.merge_atr*
                   (structural_atrs[contact['index']] or 0.0)
                   for contact in event['suppressed_contacts'])]

    if p.working_selection:
        from level_selection import select_working_levels
        levels = select_working_levels(bars,levels,p,audit=selection_audit)

    # nearest-level flag = closest level to current price on each side
    levels = [lv for lv in levels if lv.distance_atr <= p.nearest_window_atr]
    if levels:
        nearest_above = min((lv for lv in levels if lv.price >= last_price),
                            key=lambda lv: lv.price, default=None)
        nearest_below = max((lv for lv in levels if lv.price < last_price),
                            key=lambda lv: lv.price, default=None)
        nearest_set = {id(x) for x in (nearest_above, nearest_below) if x}
    else:
        nearest_set = set()

    channel_top = nearest_above.price if levels and nearest_above else None
    channel_bottom = nearest_below.price if levels and nearest_below else None
    for lv in levels:
        is_main_boundary = id(lv) in nearest_set or lv.higher_timeframe_confirmed or bool(lv.structure.get('channels'))
        lv.scope = "main" if is_main_boundary else "local"
        if channel_top is not None and channel_bottom is not None:
            inside = channel_bottom < lv.price < channel_top and id(lv) not in nearest_set
            lv.inside_channel = inside
            lv.local_noise = inside and not lv.higher_timeframe_confirmed

    # run each discovered level through the KB validator (rules owned by base)
    for lv in levels:
        cfg = {
            "timeframe": "discovered",
            "level_price": lv.price,
            "current_price": last_price,
            "basis_tags": lv.basis_tags,
            "touch_count": lv.touch_count,
            "false_breakout_count": lv.false_breakout_count,
            "nearest_level": id(lv) in nearest_set,
            "inside_channel": lv.inside_channel,
            "local_noise": lv.local_noise,
            "short_tail_without_confirmation": lv.short_tail_without_confirmation,
            "repeated_chop_without_winner": lv.repeated_chop,
            "stop_anchor": "bsu_pivot" if lv.basis_tags else None,
        }
        out = validate_level_strength(cfg, None)
        lv.kb_status = out["status"]
        lv.kb_score = out["score"]
        lv.kb_hard_rejects = out["hard_rejects"]
        lv.kb_strength = out["strength_factors"]
        lv.automation_confidence = automation_confidence_for_level(lv)

    levels.sort(key=lambda lv: (-lv.kb_score, -lv.structure.get('strength_score', 0), lv.distance_atr))
    return levels


def build_drawn_level_candidate(bars: list[Bar], price: float, p: DiscoveryParams,
                                *, nearest_level: bool,
                                higher_levels: list[Level] | None = None,
                                higher_timeframe: str = "") -> Level | None:
    if len(bars) < p.atr_period + 2:
        return None
    last_atr = atr_at(bars, len(bars) - 1, p.atr_period) or 0.0
    if last_atr <= 0:
        return None
    last_price = bars[-1].close
    events = level_events(bars, price, structure_params(p))
    touch_indices = sorted({e['index'] for e in events if e['role'] in ('touch', 'near_touch')})
    touches = len(touch_indices)
    bsu_index = touch_indices[0] if touch_indices else len(bars) - 1

    bsu = bars[bsu_index]
    bsu_kind = 'H' if abs(bsu.high-price) <= abs(bsu.low-price) else 'L'
    structural_profile = level_profile(bars,price,bsu_index,bsu_kind,
                                       structure_params(p))
    # An outside close during a two-bar false break does not turn same-side
    # contacts into a mirror. Require an actual opposite-side contact pair.
    if structural_profile['mirror_pair']:
        side = "mirror"
    elif last_price >= price:
        side = "support"
    else:
        side = "resistance"

    false_breakout_count = sum(e['role'].startswith('false_breakout') for e in events)
    basis: list[str] = []
    if side == "mirror" and touches >= p.min_touches:
        basis.append("mirror_level")
    if has_two_bar_limit(bars, touch_indices, price, p.two_bar_luft_atr * last_atr):
        basis.append("two_bar_limit")
    if near_round_number(price, p.round_step):
        basis.append("round_number")

    chopped = is_chopped(bars, price, bsu_index, p)
    post_chop_acceptance = chopped and has_post_chop_acceptance(
        bars, price, side, touch_indices, last_atr, p)
    if post_chop_acceptance:
        basis.append("post_chop_acceptance")
    side_switches, close_balance, historical_chop = close_side_metrics(
        bars, price, bsu_index, last_atr, p)
    if historical_chop and not post_chop_acceptance:
        chopped = True

    exact_touch_count, touch_error_atr, touch_quality = touch_quality_metrics(
        bars, price, touch_indices, last_atr, p)
    active_after_last_touch, last_reaction_atr = reaction_after_last_touch(
        bars, price, side, touch_indices, last_atr, p)
    distance_atr = abs(last_price - price) / last_atr

    level = Level(
        price=price,
        bsu_index=bsu_index,
        bsu_time=bar_time(bars[bsu_index]),
        side=side,
        basis_tags=ordered_basis(basis),
        touch_count=touches,
        false_breakout_count=false_breakout_count,
        entry_events=entry_event_records(bars,price,events),
        touch_indices=touch_indices,
        short_tail_without_confirmation=False,
        repeated_chop=chopped and not post_chop_acceptance,
        post_chop_acceptance=post_chop_acceptance,
        distance_atr=distance_atr,
        atr=last_atr,
        exact_touch_count=exact_touch_count,
        touch_error_atr=touch_error_atr,
        touch_quality=touch_quality,
        close_side_switches=side_switches,
        close_balance_ratio=close_balance,
        active_after_last_touch=active_after_last_touch,
        last_reaction_atr=last_reaction_atr,
        source="drawn_level",
        structure=structural_profile,
    )

    if higher_levels:
        mtf_tolerance = p.mtf_luft_atr * last_atr
        best_higher = min(higher_levels, key=lambda higher: abs(higher.price - level.price), default=None)
        if best_higher and abs(best_higher.price - level.price) <= mtf_tolerance:
            level.higher_timeframe_confirmed = True
            level.higher_timeframe = higher_timeframe

    validator_input = {
        "timeframe": "drawn_level",
        "level_price": level.price,
        "current_price": last_price,
        "basis_tags": level.basis_tags,
        "touch_count": level.touch_count,
        "false_breakout_count": level.false_breakout_count,
        "nearest_level": nearest_level,
        "inside_channel": level.inside_channel,
        "local_noise": level.local_noise,
        "short_tail_without_confirmation": level.short_tail_without_confirmation,
        "repeated_chop_without_winner": level.repeated_chop,
        "stop_anchor": "drawn_level_validated_touch_cluster" if level.basis_tags else None,
    }
    validation = validate_level_strength(validator_input, None)
    level.kb_status = validation["status"]
    level.kb_score = validation["score"]
    level.kb_hard_rejects = validation["hard_rejects"]
    level.kb_strength = validation["strength_factors"]
    level.automation_confidence = automation_confidence_for_level(level)
    return level


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def level_decision(lv: Level) -> str:
    if lv.kb_status == "pass":
        return "working_level"
    if lv.kb_status == "warn":
        return "manual_review"
    return "reject"


def level_evidence(lv: Level) -> list[dict[str, str]]:
    evidence: list[dict[str, str]] = []
    for tag in lv.basis_tags:
        info = BASIS_EXPLANATIONS.get(tag)
        if info:
            evidence.append({"tag": tag, **info})

    if lv.touch_count >= 3:
        evidence.append({
            "tag": "three_or_more_exact_touches",
            "summary": f"{lv.touch_count} подтвержденных касаний в одну ценовую зону",
            "source": "level_selection_strength_spec.md: Strength Factors",
        })
    elif lv.touch_count >= 2:
        evidence.append({
            "tag": "two_exact_touches",
            "summary": "минимум два касания в одну цену, уровень имеет право существовать",
            "source": "level_selection_strength: lec_010_de07310a_0033",
        })

    if lv.higher_timeframe_confirmed:
        evidence.append({
            "tag": "higher_timeframe_confirmation",
            "summary": f"уровень подтвержден на старшем таймфрейме {lv.higher_timeframe}",
            "source": "level_selection_strength: lec_013_fe508895_0020, lec_019_52874787_0016",
        })
    if lv.touch_quality in {"tight", "acceptable"}:
        evidence.append({
            "tag": f"auto_touch_quality_{lv.touch_quality}",
            "summary": f"исторические касания сходятся в цену: exact={lv.exact_touch_count}, max_error={lv.touch_error_atr:.3f} ATR",
            "source": "level_discovery.py: OHLC historical touch audit",
        })
    if lv.active_after_last_touch:
        evidence.append({
            "tag": "auto_active_after_last_touch",
            "summary": f"после последнего касания была реакция {lv.last_reaction_atr:.2f} ATR",
            "source": "level_discovery.py: post-touch reaction audit",
        })
    return evidence


def level_rejections(lv: Level) -> list[dict[str, str]]:
    return [
        {"tag": tag, "summary": REJECT_EXPLANATIONS.get(tag, tag)}
        for tag in lv.kb_hard_rejects
    ]


def level_manual_review(lv: Level) -> list[str]:
    items: list[str] = []
    if lv.touch_quality == "loose":
        items.append("касания широкие: визуально проверить, что это одна цена, а не рядом стоящий шум")
    elif lv.touch_quality == "acceptable":
        items.append("касания допустимые, но не идеальные: нужна контрольная визуальная проверка")
    if not lv.active_after_last_touch:
        items.append("проверить, что уровень не потерял актуальность после последующей структуры")
    if lv.scope == "local":
        items.append("локальный уровень: проверить, не зажат ли он между основными границами")
    if not lv.higher_timeframe_confirmed:
        items.append("нет подтверждения старшим таймфреймом в текущем прогоне")
    if lv.repeated_chop:
        items.append(f"исторический распил: {lv.close_side_switches} смен стороны, balance={lv.close_balance_ratio:.2f}; нужен победитель после пилы")
    if lv.short_tail_without_confirmation:
        items.append("хвост ложного пробоя короткий/одиночный: нужно подтверждение вторым касанием")
    return items


def level_report(lv: Level) -> dict[str, object]:
    return {
        "price": round(lv.price, 8),
        "side": lv.side,
        "source": lv.source,
        "decision": level_decision(lv),
        "kb_status": lv.kb_status,
        "kb_score": lv.kb_score,
        "scope": lv.scope,
        "distance_atr": round(lv.distance_atr, 4),
        "touch_count": lv.touch_count,
        "false_breakout_count": lv.false_breakout_count,
        "entry_observations": {"false_breakout_count": lv.false_breakout_count,
                               "events": lv.entry_events,
                               "affects_level_strength": False,
                               "role": "entry_scenario_only"},
        "basis_tags": lv.basis_tags,
        "inflection_check": lv.inflection_check,
        "automatic_origin_exclusions": lv.automatic_origin_exclusions,
        "structure": lv.structure,
        "history_window": lv.history_window,
        "higher_timeframe_confirmed": lv.higher_timeframe_confirmed,
        "higher_timeframe": lv.higher_timeframe,
        "flags": {
            "inside_channel": lv.inside_channel,
            "local_noise": lv.local_noise,
            "short_tail_without_confirmation": lv.short_tail_without_confirmation,
            "repeated_chop": lv.repeated_chop,
            "post_chop_acceptance": lv.post_chop_acceptance,
        },
        "auto_quality": {
            "automation_confidence": lv.automation_confidence,
            "touch_quality": lv.touch_quality,
            "exact_touch_count": lv.exact_touch_count,
            "selection": lv.selection,
            "exact_price_contact_count": lv.structure.get('exact_price_contact_count'),
            "touch_error_atr": round(lv.touch_error_atr, 4),
            "close_side_switches": lv.close_side_switches,
            "close_balance_ratio": round(lv.close_balance_ratio, 4),
            "active_after_last_touch": lv.active_after_last_touch,
            "last_reaction_atr": round(lv.last_reaction_atr, 4),
        },
        "evidence": level_evidence(lv),
        "reject_reasons": level_rejections(lv),
        "manual_review": level_manual_review(lv),
        "bsu": {"index": lv.bsu_index, "time": lv.bsu_time},
    }


def build_report(symbol: str, interval: str, higher_interval: str,
                 last_price: float, levels: list[Level]) -> dict[str, object]:
    passed = [lv for lv in levels if lv.kb_status == "pass"]
    return {
        "symbol": symbol,
        "interval": interval,
        "higher_interval": higher_interval,
        "last_price": last_price,
        "source_specs": SOURCE_SPECS,
        "summary": {
            "candidate_count": len(levels),
            "working_level_count": len(passed),
            "rejected_count": len([lv for lv in levels if lv.kb_status == "reject"]),
            "manual_review_count": len([lv for lv in levels if lv.kb_status == "warn"]),
        },
        "levels": [level_report(lv) for lv in levels],
    }


def print_levels(symbol: str, levels: list[Level], last_price: float) -> None:
    print("=" * 78)
    print(f"LEVEL DISCOVERY  —  {symbol}   last_price={last_price:.4f}")
    print("rules: rulebook/level_selection_strength.md (Gerchik taxonomy)")
    print("=" * 78)
    if not levels:
        print("No qualifying levels (>=2 touches into same price) near price.")
        return
    passed = [lv for lv in levels if lv.kb_status == "pass"]
    print(f"Discovered {len(levels)} candidate levels "
          f"({len(passed)} pass KB validator)\n")
    for lv in levels:
        tag = ",".join(t for t in lv.basis_tags if t != "round_number") or "—"
        rnd = " +round#" if "round_number" in lv.basis_tags else ""
        mtf = f"  HTF={lv.higher_timeframe}" if lv.higher_timeframe_confirmed else ""
        flag = {"pass": "[PASS]", "warn": "[warn]", "reject": "[REJ ]"}.get(
            lv.kb_status, "[?   ]")
        print(f"{flag} {lv.price:>12.4f}  {lv.side:<10} "
              f"score={lv.kb_score:>4.2f}  touches={lv.touch_count} "
              f"fb={lv.false_breakout_count}  dist={lv.distance_atr:.2f}ATR "
              f"scope={lv.scope}  auto={lv.automation_confidence:.2f} "
              f"touch={lv.touch_quality}{mtf}")
        print(f"        basis: {tag}{rnd}")
        if lv.kb_hard_rejects:
            print(f"        rejects: {','.join(lv.kb_hard_rejects)}")
        if lv.inside_channel or lv.local_noise:
            print("        note: local/internal channel level")
        if lv.short_tail_without_confirmation:
            print("        note: short_tail_without_confirmation")
        if lv.repeated_chop:
            print("        note: repeated_chop_without_winner (contaminated)")
        if lv.post_chop_acceptance:
            print("        note: post_chop_acceptance")
        evidence = level_evidence(lv)
        if evidence:
            print("        why:")
            for item in evidence[:5]:
                print(f"          + {item['tag']}: {item['summary']}")
        rejections = level_rejections(lv)
        if rejections:
            print("        why rejected:")
            for item in rejections:
                print(f"          - {item['tag']}: {item['summary']}")
        manual = level_manual_review(lv)
        if manual:
            print("        manual review:")
            for item in manual[:3]:
                print(f"          ? {item}")
        print(f"        BSU: {lv.bsu_time}")
    print("=" * 78)


def main() -> None:
    ap = argparse.ArgumentParser(description="Layer 1: Gerchik level discovery")
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--interval", default="1d")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--higher-interval", default="1w",
                    help="optional higher timeframe for confirmation; empty disables MTF")
    ap.add_argument("--output-format", choices=["text", "json"], default="text")
    ap.add_argument("--pivot-wing", type=int, default=3)
    ap.add_argument("--min-touches", type=int, default=2)
    args = ap.parse_args()

    p = DiscoveryParams(pivot_wing=args.pivot_wing, min_touches=args.min_touches)
    print(f"Loading {args.symbol} {args.interval} {args.start}..{args.end} ...",
          file=sys.stderr)
    bars = load_history(args.symbol, args.interval, args.start, args.end)
    if not bars:
        print("No data.", file=sys.stderr)
        sys.exit(1)
    higher_levels: list[Level] | None = None
    higher_tf = args.higher_interval.strip()
    if higher_tf and higher_tf != args.interval:
        print(f"Loading higher timeframe {args.symbol} {higher_tf} ...",
              file=sys.stderr)
        higher_bars = load_history(args.symbol, higher_tf, args.start, args.end)
        higher_levels = discover_levels(higher_bars, p) if higher_bars else []
    levels = discover_levels(bars, p, higher_levels, higher_tf)
    if args.output_format == "json":
        report = build_report(args.symbol, args.interval, higher_tf, bars[-1].close, levels)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_levels(args.symbol, levels, bars[-1].close)


if __name__ == "__main__":
    main()
