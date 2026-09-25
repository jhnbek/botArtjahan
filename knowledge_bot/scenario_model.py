"""Research-only entry timing ranker trained from annotated H1 examples.

The score compares states BEFORE entry; it is neither a profit probability nor
an instruction to trade. Existing level, entry and risk rules stay authoritative.
No image, caption, outcome, symbol or scenario ID is an inference input.
"""
from __future__ import annotations

import importlib
import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


MODEL_PATH = (Path(__file__).resolve().parents[1] / "_knowledge_base" /
              "manual_reviews/scenarios_dzhahan_20260925/training/scenario_model.json")
MODEL_KIND = "entry_pairwise_ranker"
MIN_CLOSED_BARS = 16  # fourteen prior TR values, each with its previous close


def _feature_module():
    if __package__:
        return importlib.import_module(".scenario_image_features", __package__)
    return importlib.import_module("scenario_image_features")


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Expected a finite numeric value")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("Expected a finite numeric value")
    return value


def validate_model(artifact: Mapping[str, Any]) -> dict[str, Any]:
    """Reject stale, corrupt or incompatible artifacts before inference."""
    if not isinstance(artifact, Mapping):
        raise ValueError("Model must be a JSON object")
    if artifact.get("schema_version") != 1 or artifact.get("model_kind") != MODEL_KIND:
        raise ValueError("Unsupported model schema or task")
    names = artifact.get("feature_names")
    expected = list(_feature_module().FEATURE_NAMES)
    if not isinstance(names, list) or names != expected:
        raise ValueError("Unknown, reordered or missing feature schema")
    result = dict(artifact)
    for key in ("mean", "scale", "weights"):
        values = artifact.get(key)
        if not isinstance(values, list) or len(values) != len(names):
            raise ValueError(f"Invalid {key} shape")
        result[key] = [_number(value) for value in values]
    if any(value <= 0 for value in result["scale"]):
        raise ValueError("Feature scales must be positive")
    result["bias"] = _number(artifact.get("bias"))
    if artifact.get("classes") is not None or artifact.get("threshold") is not None:
        raise ValueError("Entry ranker must not be a direction/profit classifier")
    return result


def load_model(path: Path | str = MODEL_PATH) -> dict[str, Any]:
    path = Path(path)
    if path.stat().st_size > 2_000_000:
        raise ValueError("Unexpectedly large model artifact")
    return validate_model(json.loads(path.read_text(encoding="utf-8")))


def features_for_entry(bars: Sequence[Mapping[str, float]], level: float,
                       direction: str) -> dict[str, float]:
    """Canonicalize known trade direction so long and short entries can share weights.

    Direction comes from the existing scenario rules, never from this model.
    The caller supplies closed bars ending strictly BEFORE the proposed entry.
    """
    if direction not in {"long", "short"}:
        raise ValueError("Entry direction must be long or short")
    if len(bars) < MIN_CLOSED_BARS:
        raise ValueError("At least sixteen closed bars are required")
    level = _number(level)
    normalized = []
    for bar in bars:
        o, h, l, c = (_number(bar[key]) for key in ("open", "high", "low", "close"))
        if h < max(o, c) or l > min(o, c):
            raise ValueError("Invalid OHLC ordering")
        normalized.append(dict(open=o, high=h, low=l, close=c) if direction == "long"
                          else dict(open=-o, high=-l, low=-h, close=-c))
    features = _feature_module().features_from_ohlc(normalized, level if direction == "long" else -level)
    return {key: _number(value) for key, value in features.items()}


def score_features(features: Mapping[str, float], artifact: Mapping[str, Any]) -> float:
    """Raw linear ranking score; there is deliberately no probability conversion."""
    model = validate_model(artifact)
    if set(features) != set(model["feature_names"]):
        raise ValueError("Unknown or missing inference features")
    score = model["bias"] + math.fsum(
        ((_number(features[name]) - mean) / scale) * weight
        for name, mean, scale, weight in zip(model["feature_names"], model["mean"],
                                            model["scale"], model["weights"])
    )
    if not math.isfinite(score):
        raise ValueError("Non-finite ranking score")
    return score


def _utc_time(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        stamp = _number(value)
        parsed = datetime.fromtimestamp(stamp / 1000 if abs(stamp) > 100_000_000_000 else stamp,
                                        tz=timezone.utc)
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError("Missing or invalid timestamp")
    if parsed.tzinfo is None:
        raise ValueError("Timestamp needs an explicit timezone")
    return parsed.astimezone(timezone.utc)


def _bar_mapping(bar: Any) -> dict[str, Any]:
    if isinstance(bar, Mapping):
        return dict(bar)
    return {key: getattr(bar, key) for key in ("open_time", "open", "high", "low", "close")}


def _bar_open_time(bar: Mapping[str, Any]) -> datetime:
    raw_time = next((bar[key] for key in ("open_time_ms", "open_time", "time", "timestamp")
                     if bar.get(key) is not None), None)
    return _utc_time(raw_time)


def daily_confirmation_gate(daily_signal_open_time: Any, as_of: Any) -> dict[str, Any]:
    """The actual D1 signal is usable only once that UTC daily bar has closed."""
    if daily_signal_open_time is None or as_of is None:
        return {"confirmed": False, "status": "daily_confirmation_missing"}
    try:
        opening, cutoff = _utc_time(daily_signal_open_time), _utc_time(as_of)
        closing = opening + timedelta(days=1)
        return {"confirmed": cutoff >= closing,
                "status": "confirmed" if cutoff >= closing else "waiting_for_daily_close",
                "daily_signal_open_time": opening.isoformat(),
                "daily_signal_close_time": closing.isoformat(), "as_of": cutoff.isoformat()}
    except (ValueError, TypeError, OverflowError, OSError):
        return {"confirmed": False, "status": "invalid_daily_confirmation"}


def daily_gate_for_context(context_bars: Sequence[Any], execution_bars: Sequence[Any],
                           context_timeframe: str, execution_timeframe: str,
                           *, as_of: Any = None) -> dict[str, Any]:
    """Gate the latest D1 used by approach analysis, without substituting an older bar."""
    if str(context_timeframe).lower() not in {"1d", "d1", "d"} or not context_bars:
        return {"confirmed": False, "status": "daily_confirmation_missing"}
    try:
        value = str(execution_timeframe).lower()
        match = re.fullmatch(r"(?:(\d+)([mhdw])|([mhdw])(\d+))", value)
        if not match:
            raise ValueError("Unknown execution interval")
        amount = int(match.group(1) or match.group(4))
        unit = match.group(2) or match.group(3)
        if amount <= 0:
            raise ValueError("Execution interval must be positive")
        duration = timedelta(seconds=amount * {"m": 60, "h": 3600, "d": 86400, "w": 604800}[unit])
        now = datetime.now(timezone.utc)
        cutoff = now if as_of is None else min(_utc_time(as_of), now)
        closes = []
        previous = None
        for bar in execution_bars:
            opening = _bar_open_time(_bar_mapping(bar))
            if previous is not None and opening <= previous:
                raise ValueError("Unordered execution bars")
            previous = opening
            if opening + duration <= cutoff:
                closes.append(opening + duration)
        last_execution_close = closes[-1] if closes else None
        return daily_confirmation_gate(_bar_open_time(_bar_mapping(context_bars[-1])),
                                       last_execution_close)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, OSError):
        return {"confirmed": False, "status": "invalid_daily_confirmation"}


def closed_hourly_bars(bars: Sequence[Any], as_of: Any) -> list[dict[str, Any]]:
    """Keep a chronological prefix whose H1 bars have closed by the cutoff.

    Timestamps denote bar OPEN. A close supplied by an unfinished bar is never
    used. Sorting is intentionally not used to hide mixed/duplicate data.
    """
    cutoff = _utc_time(as_of)
    selected = []
    previous = None
    for original in bars:
        bar = _bar_mapping(original)
        opening = _bar_open_time(bar)
        if previous is not None and opening <= previous:
            raise ValueError("Bar timestamps must be unique and ascending")
        previous = opening
        if opening + timedelta(hours=1) <= cutoff:
            selected.append({**bar, "open_time": opening.isoformat(),
                             "close_time": (opening + timedelta(hours=1)).isoformat()})
    return selected


def _base_result() -> dict[str, Any]:
    return {
        "status": "research_only", "model_kind": MODEL_KIND,
        "action": "no_order", "suggested_next_step": "review_kb_entry_conditions",
        "trained_on_successful_examples_only": True, "evaluated_profit": False,
        "score_is_probability": False, "changes_entry_or_risk_rules": False,
        "entry_readiness_rank": None,
    }


def entry_timing_advice(execution_bars: Sequence[Any], level: float, direction: str,
                        *, as_of: Any = None, interval: str = "1h",
                        daily_signal_open_time: Any = None,
                        model_path: Path | str = MODEL_PATH) -> dict[str, Any]:
    """Rank latest closed H1 state against up to six earlier available states.

    Rank 1 means the largest model score in this local comparison, not an entry
    permission. All input prices, levels, stops and existing signals are kept.
    """
    result = _base_result()
    if str(interval).lower() not in {"1h", "h1", "60m", "60"}:
        return {**result, "status": "unavailable", "reason": "model_trained_for_h1_only"}
    path = Path(model_path)
    if not path.is_file():
        return {**result, "status": "unavailable", "reason": "model_not_found"}
    try:
        model = load_model(path)
        now = datetime.now(timezone.utc)
        cutoff = now if as_of is None else min(_utc_time(as_of), now)
        closed = closed_hourly_bars(execution_bars, cutoff)
        daily_gate = daily_confirmation_gate(daily_signal_open_time,
                                             closed[-1]["close_time"] if closed else None)
        result["daily_confirmation"] = daily_gate
        if not daily_gate["confirmed"]:
            return {**result, "status": "waiting_for_daily_close" if daily_gate["status"] == "waiting_for_daily_close" else "abstain",
                    "reason": daily_gate["status"]}
        if len(closed) < MIN_CLOSED_BARS:
            return {**result, "status": "abstain", "reason": "insufficient_closed_history"}
        scores = []
        for end in range(max(MIN_CLOSED_BARS, len(closed)-6), len(closed)+1):
            values = features_for_entry(closed[:end], level, direction)
            scores.append({"as_of": closed[end-1]["close_time"],
                           "raw_entry_score": score_features(values, model)})
        latest = scores[-1]["raw_entry_score"]
        rank = 1 + sum(row["raw_entry_score"] > latest + 1e-12 for row in scores[:-1])
        return {**result, "entry_readiness_rank": rank, "compared_states": len(scores),
                "raw_entry_score": latest, "recent_state_scores": scores,
                "direction_supplied_by_rules": direction,
                "latest_closed_bar": closed[-1]["open_time"],
                "decision_available_at": closed[-1]["close_time"],
                "closed_bars_used": len(closed), "timeframe": "1h",
                "feature_schema_version": _feature_module().FEATURE_SCHEMA_VERSION,
                "model_path": str(path),
                "interpretation": "Relative timing resemblance among recent states; not profit or entry probability"}
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError, ImportError) as exc:
        return {**result, "status": "abstain", "reason": "invalid_model_or_input",
                "detail": str(exc)}
