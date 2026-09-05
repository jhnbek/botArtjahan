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

    structural bases: inflection, mirror_level, paranormal_bar,
                                        long_false_breakout_tail, two_bar_limit,
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
    * a false-breakout tail can only be a level if confirmed by another
      touch into the same price

Source specs:
  _knowledge_base/rulebook/level_selection_strength.md
  _knowledge_base/detector_specs/level_selection_strength_spec.md
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone

from detector_prototype import (
    PARANORMAL_RANGE_ATR,
    PARANORMAL_RANGE_MULTIPLIER,
    validate_level_strength,
)
from scn002_strict_kb_backtest import Bar, atr_at, load_history

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
        "summary": "уровень по паранормальному бару/длинному диапазону",
        "source": "level_selection_strength: lec_013_fe508895_0020, lec_016_596d35da_0050",
    },
    "long_false_breakout_tail": {
        "summary": "уровень подтвержден значимым хвостом ложного пробоя",
        "source": "level_selection_strength: lec_013_fe508895_0019, lec_024_c5818820_0024",
    },
    "two_bar_limit": {
        "summary": "два бара/экстремума бьют в одну цену, уровень подтвержден точностью касаний",
        "source": "detector_casebook: level_pass_mirror_two_bar_limit_001",
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
    reversal_lookahead: int = 20        # izlom needs an actual move away after the pivot
    paranormal_lookback: int = 20       # avg-range window for paranormal-bar test
    chop_window: int = 40               # window to test repeated-chop contamination
    chop_cross_ratio: float = 0.30      # fraction of closes on both sides => chopped
    post_chop_reaction_atr: float = 0.25
    mtf_luft_atr: float = 0.15          # weekly/monthly level may be a wider zone on daily
    nearest_window_atr: float = 3.0     # only report levels within this*ATR of last price
    round_step: float = 0.0             # optional round-number step (0 = auto by price)


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
    rng = bars[i].high - bars[i].low
    avg = avg_range(bars, i, p.paranormal_lookback)
    return rng >= PARANORMAL_RANGE_MULTIPLIER * avg or rng >= PARANORMAL_RANGE_ATR * atr


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
        upper_sweep = b.high > price + tol and b.close < price
        lower_sweep = b.low < price - tol and b.close > price
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
        "mirror_level",
        "paranormal_bar",
        "long_false_breakout_tail",
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
        step = round_number_step(price)
    nearest = round(price / step) * step
    return abs(price - nearest) <= step * 0.02


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #
def discover_levels(
    bars: list[Bar],
    p: DiscoveryParams,
    higher_levels: list[Level] | None = None,
    higher_timeframe: str = "",
) -> list[Level]:
    n = len(bars)
    if n < p.atr_period + p.pivot_wing * 2 + 2:
        return []
    last_atr = atr_at(bars, n - 1, p.atr_period) or 0.0
    if last_atr <= 0:
        return []
    last_price = bars[-1].close

    pivots = swing_pivots(bars, p.pivot_wing)

    # cluster pivots that hit the same price (within luft)
    raw: list[tuple[float, int, str]] = []  # (price, index, kind)
    for idx, kind in pivots:
        price = bars[idx].high if kind == "H" else bars[idx].low
        raw.append((price, idx, kind))
    raw.sort(key=lambda t: t[0])

    luft = p.cluster_luft_atr * last_atr
    tol = p.contact_tol_atr * last_atr

    clusters: list[list[tuple[float, int, str]]] = []
    for item in raw:
        if clusters and abs(item[0] - clusters[-1][0][0]) <= luft:
            clusters[-1].append(item)
        else:
            clusters.append([item])

    levels: list[Level] = []
    for cl in clusters:
        prices = [c[0] for c in cl]
        price = sum(prices) / len(prices)
        # representative BSU = earliest pivot that stopped the strongest move
        bsu_idx = min(c[1] for c in cl)
        kinds = {c[2] for c in cl}

        atr_at_bsu = atr_at(bars, bsu_idx, p.atr_period) or last_atr

        touches, t_idx = count_touches(bars, price, tol, bsu_idx)

        # side classification
        if "H" in kinds and "L" in kinds:
            side = "mirror"
        elif "H" in kinds:
            side = "resistance"
        else:
            side = "support"

        fb, fb_indices, short_tail_seen = false_breakout_events(
            bars, price, last_atr, side, p)

        # Structural bases are additive when each has separate evidence. The
        # casebook itself expects e.g. mirror_level + two_bar_limit together.
        bsu_kind = next((c[2] for c in cl if c[1] == bsu_idx), "H")
        is_para = is_paranormal(bars, bsu_idx, atr_at_bsu, p)
        is_strong_stop = strong_move_into(bars, bsu_idx, bsu_kind, atr_at_bsu, p)
        is_mirror = side == "mirror"
        is_inflection = (
            strong_move_into(bars, bsu_idx, bsu_kind, atr_at_bsu, p,
                             p.inflection_move_atr) and
            strong_reversal_after(bars, bsu_idx, bsu_kind, atr_at_bsu, p,
                                  p.inflection_move_atr)
        )
        is_two_bar_limit = has_two_bar_limit(
            bars, t_idx, price, p.two_bar_luft_atr * last_atr)

        basis: list[str] = []
        if is_inflection:
            basis.append("inflection")
        if is_mirror:
            basis.append("mirror_level")
        if is_para:
            basis.append("paranormal_bar")
        if fb > 0:
            basis.append("long_false_breakout_tail")
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

        short_tail_without_confirmation = short_tail_seen and touches < p.min_touches
        if touches < p.min_touches and not basis and not short_tail_without_confirmation:
            continue

        lvl = Level(
            price=price, bsu_index=bsu_idx, bsu_time=bar_time(bars[bsu_idx]),
            side=side, basis_tags=ordered_basis(basis), touch_count=touches,
            false_breakout_count=fb, touch_indices=t_idx,
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
        )
        if round_ctx:
            lvl.basis_tags.append("round_number")
        levels.append(lvl)

    if higher_levels:
        mtf_tol = p.mtf_luft_atr * last_atr
        for lv in levels:
            best = min(higher_levels, key=lambda h: abs(h.price - lv.price),
                       default=None)
            if best and abs(best.price - lv.price) <= mtf_tol:
                lv.higher_timeframe_confirmed = True
                lv.higher_timeframe = higher_timeframe

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
        is_main_boundary = id(lv) in nearest_set or lv.higher_timeframe_confirmed
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

    levels.sort(key=lambda lv: (-lv.kb_score, lv.distance_atr))
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
    tolerance = p.contact_tol_atr * last_atr
    touches, touch_indices = count_touches(bars, price, tolerance, -1)
    bsu_index = touch_indices[0] if touch_indices else len(bars) - 1

    close_states = [
        1 if bar.close > price + tolerance else -1 if bar.close < price - tolerance else 0
        for bar in bars
    ]
    has_above = any(state > 0 for state in close_states)
    has_below = any(state < 0 for state in close_states)
    if has_above and has_below:
        side = "mirror"
    elif last_price >= price:
        side = "support"
    else:
        side = "resistance"

    false_breakout_count, _false_breakout_indices, short_tail_seen = false_breakout_events(
        bars, price, last_atr, side, p)
    basis: list[str] = []
    if side == "mirror" and touches >= p.min_touches:
        basis.append("mirror_level")
    if has_two_bar_limit(bars, touch_indices, price, p.two_bar_luft_atr * last_atr):
        basis.append("two_bar_limit")
    if false_breakout_count > 0:
        basis.append("long_false_breakout_tail")
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
        touch_indices=touch_indices,
        short_tail_without_confirmation=short_tail_seen and touches < p.min_touches,
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

    if lv.false_breakout_count > 0:
        evidence.append({
            "tag": "false_breakout_confirmation",
            "summary": f"{lv.false_breakout_count} значимых ложных пробоев усиливают уровень",
            "source": "level_selection_strength: lec_015_26f6fc9d_0012",
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
        "basis_tags": lv.basis_tags,
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
