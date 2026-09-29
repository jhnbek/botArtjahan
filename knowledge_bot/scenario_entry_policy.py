"""Combine learned direction and entry timing as closed-bar research advice.

This module never places orders, changes existing entry rules or infers a stop.
The last closed H1 price is only a planning reference, not an execution price.
"""
from __future__ import annotations

import importlib
from datetime import timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

if __package__:
    from . import scenario_model
    from .scenario_training_rules import calculate_take_profit_targets
else:
    import scenario_model
    from scenario_training_rules import calculate_take_profit_targets


def _direction_module():
    if __package__:
        return importlib.import_module(".scenario_direction_model", __package__)
    return importlib.import_module("scenario_direction_model")


def _ohlc(bar: Mapping[str, Any]) -> dict[str, float]:
    prices = {key: scenario_model._number(bar[key]) for key in ("open", "high", "low", "close")}
    if any(value <= 0 for value in prices.values()):
        raise ValueError("OHLC prices must be positive")
    if prices["high"] < max(prices["open"], prices["close"]) or prices["low"] > min(prices["open"], prices["close"]):
        raise ValueError("Invalid OHLC ordering")
    return prices


def _closed_daily_ohlc(bars: Sequence[Any], cutoff: Any) -> list[dict[str, float]]:
    """Check all supplied daily observations; never replace the signal with an older bar."""
    decision_time = scenario_model._utc_time(cutoff)
    previous = None
    result = []
    for original in bars:
        bar = scenario_model._bar_mapping(original)
        opening = scenario_model._bar_open_time(bar)
        if previous is not None and opening <= previous:
            raise ValueError("Daily timestamps must be unique and ascending")
        if opening + timedelta(days=1) > decision_time:
            raise ValueError("Every supplied daily bar must be closed at decision time")
        previous = opening
        result.append(_ohlc(bar))
    return result


def build_learned_entry_advice(
    context_bars: Sequence[Any], execution_bars: Sequence[Any], level: Any, *,
    as_of: Any = None, direction_model_path: Path | str | None = None,
    entry_model_path: Path | str | None = None, structural_stop: float | None = None,
) -> dict[str, Any]:
    """Assess D1 direction, H1 timing and optional 3R/4R planning geometry.

    Inputs are chronological D1 and H1 mappings or Bar objects, with timestamps
    denoting their opens. The latest supplied D1 is the required signal bar.
    An explicitly supplied stop remains the caller's structural interpretation;
    valid arithmetic does not independently verify that interpretation.
    """
    result: dict[str, Any] = {
        "status": "research_only",
        "action": "no_order",
        "research_only": True,
        "automatic_order_execution_allowed": False,
        "changes_entry_or_risk_rules": False,
        "evaluated_profit": False,
        "predicted_direction": None,
        "prerequisites": None,
        "evidence": {"long": [], "short": []},
        "direction_advice": None,
        "entry_timing": None,
        "readiness": None,
        "reference_entry_price": None,
        "reference_entry_price_source": "last_closed_h1_close_for_planning_not_execution",
        "structural_stop": structural_stop,
        "technical_stop_verified": False,
        "take_profit_targets": None,
    }
    gate = scenario_model.daily_gate_for_context(
        context_bars, execution_bars, "1d", "1h", as_of=as_of,
    )
    result["daily_confirmation"] = gate
    if not gate["confirmed"]:
        return {
            **result,
            "status": "waiting_for_daily_close" if gate["status"] == "waiting_for_daily_close" else "abstain",
            "reason": gate["status"],
        }

    # The gate's cutoff is the latest actually closed H1 bar. Use that same
    # timestamp in both model calls and in the optional price calculation.
    cutoff = gate["as_of"]
    try:
        daily = _closed_daily_ohlc(context_bars, cutoff)
        closed_hourly = scenario_model.closed_hourly_bars(execution_bars, cutoff)
        hourly = [{**_ohlc(bar), "open_time": bar["open_time"]} for bar in closed_hourly]
        level_price = scenario_model._number(getattr(level, "price", level))
        if level_price <= 0:
            raise ValueError("Level must be positive")
        result["reference_entry_price"] = hourly[-1]["close"]
        result["decision_available_at"] = cutoff
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, OSError, IndexError) as exc:
        return {**result, "status": "abstain", "reason": "invalid_closed_bar_input", "detail": str(exc)}

    try:
        direction_advice = _direction_module().direction_advice_from_ohlc(
            daily, level_price, model_path=direction_model_path, daily_close_confirmed=True,
        )
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, OSError, ImportError) as exc:
        return {**result, "status": "abstain", "reason": "direction_model_unavailable", "detail": str(exc)}
    result["direction_advice"] = direction_advice
    result["prerequisites"] = direction_advice.get("prerequisites")
    if isinstance(result["prerequisites"], Mapping):
        result["evidence"] = result["prerequisites"].get("evidence", result["evidence"])
    direction = direction_advice.get("direction")
    if direction_advice.get("status") != "research_only" or direction not in ("long", "short"):
        return {**result, "status": "abstain", "reason": "direction_unavailable"}
    result["predicted_direction"] = direction
    entry_advice = scenario_model.entry_timing_advice(
        hourly, level_price, direction, as_of=cutoff, interval="1h",
        daily_signal_open_time=gate["daily_signal_open_time"],
        model_path=scenario_model.MODEL_PATH if entry_model_path is None else entry_model_path,
    )
    result["entry_timing"] = entry_advice
    result["readiness"] = {
        "status": entry_advice.get("status"),
        "entry_readiness_rank": entry_advice.get("entry_readiness_rank"),
        "compared_states": entry_advice.get("compared_states"),
        "reason": entry_advice.get("reason"),
        "demonstrated_entry_pattern": entry_advice.get("demonstrated_entry_pattern"),
        "entry_permission": False,
    }
    if structural_stop is None:
        return {**result, "status": "needs_structural_stop", "reason": "supply_stop_beyond_relevant_structure"}
    try:
        result["take_profit_targets"] = calculate_take_profit_targets(
            direction=direction, entry=result["reference_entry_price"], stop=structural_stop,
        )
    except (ValueError, TypeError, OverflowError) as exc:
        return {**result, "status": "invalid_structural_stop", "reason": "invalid_supplied_risk_geometry", "detail": str(exc)}
    if entry_advice.get("status") != "research_only":
        return {**result, "status": "entry_timing_unavailable", "reason": entry_advice.get("reason", "entry_model_unavailable")}
    return result
