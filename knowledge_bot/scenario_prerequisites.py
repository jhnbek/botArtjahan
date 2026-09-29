"""Causal, symmetric observations of both directions at a supplied price level.

These 24 features describe closed OHLC; they are not a trained model, votes or
probabilities. A separate supervised model learns how to combine them. Only
OHLC and the supplied level are read. The caller establishes D1 closure and
that the level was available at the decision time. Pixel OHLC is supported,
including its negative coordinates, without claiming it is an exchange price.

LP1, LP2, the mirror micro-overlap allowance and chop use level_structure's
shared implementation. Compression follows knowledge/catalog/items/2b/
2b225cbe667369dd5bef31540b38c9e967f8c91844ea5e93466ab2a1a3eecf18.md:
lows/closes rise toward resistance (mirrored highs/closes fall to support).
The four-bar compression window is an explicit observation convention, not a
learned constant or proof of hidden limit orders. The contract and current
user corrections define LP1/LP2; an LP never confirms or creates a level.
"""
from __future__ import annotations

from math import isfinite
from numbers import Integral
from types import SimpleNamespace
from typing import Mapping, Sequence

try:
    from .level_structure import StructureParams, chopping_runs, level_events
except ImportError:
    from level_structure import StructureParams, chopping_runs, level_events


FEATURE_SCHEMA_VERSION = "scenario_prerequisites_v1"
_SIDE_FEATURES = (
    "false_breakout_one_bar", "false_breakout_two_bar",
    "close_from_level_tr", "close_hold_fraction_3", "full_bar_beyond_level",
    "approach_fraction_4", "extreme_progress_fraction_4",
    "approach_progress_4_tr", "trend_8_tr", "impulse_body_tr", "rejection_wick_tr",
)
PREREQUISITE_FEATURE_NAMES = tuple(
    f"{side}_{name}" for side in ("long", "short") for name in _SIDE_FEATURES
) + ("range_contraction_3_tr", "current_chop")
# Compatible with callers that expect FEATURE_NAMES for a numerical schema.
FEATURE_NAMES = PREREQUISITE_FEATURE_NAMES
MIN_CLOSED_BARS = 16


def _closed_prefix(bars, level, decision_index):
    if decision_index is None:
        decision_index = len(bars) - 1
    if (isinstance(decision_index, bool) or not isinstance(decision_index, Integral)
            or not 0 <= decision_index < len(bars)):
        raise ValueError("decision_index must identify an available closed bar")
    decision_index = int(decision_index)
    # Cut BEFORE parsing: future OHLC, including invalid future bars, cannot
    # change an earlier decision, its scale or its evidence.
    prefix = bars[:decision_index + 1]
    if len(prefix) < MIN_CLOSED_BARS:
        raise ValueError(f"At least {MIN_CLOSED_BARS} complete closed bars are required")
    if isinstance(level, bool) or not isfinite(float(level)):
        raise ValueError("level must be finite")
    result = []
    for index, source in enumerate(prefix):
        try:
            values = {key: float(source[key]) for key in ("open", "high", "low", "close")}
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Missing or invalid OHLC at bar {index}") from exc
        if not all(isfinite(value) for value in values.values()):
            raise ValueError(f"Non-finite OHLC at bar {index}")
        if (values["high"] < max(values["open"], values["close"])
                or values["low"] > min(values["open"], values["close"])):
            raise ValueError(f"Invalid OHLC ordering at bar {index}")
        # Synthetic times are needed only by the structural helper. They are
        # never a feature or an asserted real timestamp.
        result.append(SimpleNamespace(**values, open_time=index * 86400000))
    return result, float(level), decision_index


def _prior_scales(bars):
    trs = [max(bar.high - bar.low, abs(bar.high - previous.close),
               abs(bar.low - previous.close)) for previous, bar in zip(bars, bars[1:])]
    scales = []
    for index in range(len(bars)):
        previous_trs = trs[max(0, index - 15):max(0, index - 1)]
        # A short prior history matches scenario_image_features' "up to 14"
        # convention; at least eight previous TR observations are required.
        scales.append(sum(previous_trs) / len(previous_trs) if len(previous_trs) >= 8 else None)
    if scales[-1] is None or scales[-1] <= 0:
        raise ValueError("Zero previous true range")
    return scales


def analyze_prerequisites(
    bars: Sequence[Mapping[str, float]], level: float, *, decision_index: int | None = None,
) -> dict:
    """Describe both sides after the indicated closed bar, without choosing one.

    ``decision_index`` is inclusive. Omitting it asserts all supplied bars are
    already closed. Daily closure/timestamps cannot be inferred from raw OHLC.
    Scale is the mean of up to 14 prior TRs, excluding the decision bar.
    The returned feature dictionary always has PREREQUISITE_FEATURE_NAMES order.
    Evidence indices refer only to the supplied prefix, never future candles.
    """
    observed, level, index = _closed_prefix(bars, level, decision_index)
    scales = _prior_scales(observed)
    scale = scales[-1]
    params = StructureParams()
    events = [event for event in level_events(observed, level, params, scales)
              if event["known_index"] == index and event["role"].startswith("false_breakout")]
    chop = [run for run in chopping_runs(observed, level, params, scales)
            if run["indices"][-1] == index]
    latest = observed[-1]
    recent = observed[-4:]
    features = {}
    evidence = {"long": [], "short": []}

    def note(side, code, text, indices, **details):
        evidence[side].append({"code": code, "text": text, "bar_indices": indices,
                               "known_index": index, **details})

    for side, sign, kind in (("long", 1, "L"), ("short", -1, "H")):
        direction_word = "вверх" if sign == 1 else "вниз"
        held_word = "выше" if sign == 1 else "ниже"
        swept_word = "снизу" if sign == 1 else "сверху"
        side_events = {event["role"]: event for event in events if event["kind"] == kind}
        closes = [bar.close for bar in recent]
        extremes = [bar.low if sign == 1 else bar.high for bar in recent]
        approach = [sign * (close - level) <= 0 for close in closes]
        progress = [sign * (b - a) for a, b in zip(extremes, extremes[1:])]
        close_progress = [sign * (b - a) for a, b in zip(closes, closes[1:])]
        hold_fraction = sum(sign * (bar.close - level) > 0 for bar in observed[-3:]) / 3
        full_bar = sign * ((latest.low if sign == 1 else latest.high) - level) > 0
        trend = sign * (latest.close - observed[-9].close) / scale
        impulse = sign * (latest.close - latest.open) / scale
        wick = (min(latest.open, latest.close) - latest.low if sign == 1
                else latest.high - max(latest.open, latest.close)) / scale
        values = (
            float("false_breakout" in side_events), float("false_breakout_two_bar" in side_events),
            sign * (latest.close - level) / scale, hold_fraction, float(full_bar),
            sum(approach) / 4, sum(value > 0 for value in progress) / 3,
            sign * (closes[-1] - closes[0]) / scale if all(approach) else 0.0,
            trend, impulse, wick,
        )
        features.update((f"{side}_{name}", float(value)) for name, value in zip(_SIDE_FEATURES, values))

        for role, event in side_events.items():
            bars_word = "одним закрытым баром" if role == "false_breakout" else "двумя закрытыми барами"
            note(side, role, f"Ложный пробой {swept_word} {bars_word}: возврат и закрытие {held_word} уровня.",
                 event["indices"], supports_direction=True, confirms_level=False)
        if all(approach) and all(value > 0 for value in progress) and all(value > 0 for value in close_progress):
            extreme_word = "минимумы" if sign == 1 else "максимумы"
            note(side, "compression_toward_level",
                 f"Четыре закрытых бара: {extreme_word} и закрытия последовательно движутся {direction_word} к уровню.",
                 list(range(index - 3, index + 1)), supports_direction=True,
                 close_progress_tr=features[f"{side}_approach_progress_4_tr"])
        if sign * (latest.close - level) > 0:
            note(side, "close_on_direction_side", f"Последний закрытый бар закрылся {held_word} уровня.",
                 [index], supports_direction=True, distance_tr=features[f"{side}_close_from_level_tr"])
        if hold_fraction == 1:
            note(side, "three_closes_hold_side", f"Три последних закрытия удерживаются {held_word} уровня.",
                 list(range(index - 2, index + 1)), supports_direction=True)
        if full_bar:
            note(side, "full_bar_beyond_level", f"Последний бар целиком {held_word} уровня.",
                 [index], supports_direction=True)
        if trend > 0:
            note(side, "eight_bar_close_change", f"Изменение закрытия за восемь баров направлено {direction_word}.",
                 [index - 8, index], supports_direction=True, change_tr=trend)
        if impulse > 0:
            note(side, "directional_body", f"Тело последнего закрытого бара направлено {direction_word}.",
                 [index], supports_direction=True, body_tr=impulse)
        if chop:
            note(side, "current_chop", "Последние соседние бары образуют распил уровня; это не отдельные сигналы ЛП1.",
                 sorted({i for run in chop for i in run["indices"]}), supports_direction=False)

    ranges = [bar.high - bar.low for bar in observed]
    features["range_contraction_3_tr"] = (sum(ranges[-6:-3]) - sum(ranges[-3:])) / (3 * scale)
    features["current_chop"] = float(bool(chop))
    if not all(isfinite(value) for value in features.values()):
        raise ValueError("OHLC feature arithmetic overflow")
    return {
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "features": features, "evidence": evidence,
        "decision_index": index, "scale_prior_tr": scale,
        "decision_convention": "after_caller_confirmed_bar_close",
        "caveats": ["caller_must_verify_daily_close_and_prior_level_availability",
                    "observations_are_not_votes_or_probabilities",
                    "four_bar_compression_is_an_explicit_observation_window"],
    }


def prerequisite_features_from_ohlc(
    bars: Sequence[Mapping[str, float]], level: float, *, decision_index: int | None = None,
) -> dict[str, float]:
    """Return the fixed 24-feature numerical schema for supervised training."""
    return analyze_prerequisites(bars, level, decision_index=decision_index)["features"]
