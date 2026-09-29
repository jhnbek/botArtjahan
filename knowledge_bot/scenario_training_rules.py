"""Versioned knowledge contract for the manually reviewed Dzhahan examples.

The contract records what can be learned from the existing annotations.  It
does not turn annotated, hindsight charts into price/outcome ground truth and
does not override the robot's causal OHLC detectors.  Source hashes pin the
specific local knowledge and algorithm versions used by an offline run.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any, Mapping


RULES_VERSION = "dzhahan-409-knowledge-contract-2026-09-27-v2"
ALLOWED_LEVEL_TYPES = ("limit", "mirror", "paranormal", "inflection")
EXCLUDED_LEVEL_TYPES = ("gap", "consolidation", "false_breakout")
PARANORMAL_MIN_BODY_ATR = 1.6
ATR_PERIOD = 14
DAILY_HISTORY_MONTHS = 18
MINIMUM_LEVEL_SPACING_PERCENT = 1.5

# The user confirmed the original scenarios are correct. These are unresolved
# interpretations of those annotations by the extraction/review process, not
# claims that the author's examples are wrong. Retain the original images.
DIRECTION_INTERPRETATION_REVIEW = {
    30: "Our D1/H1 direction interpretation needs alignment across the pair.",
    68: "Our reading of compression direction and approach needs verification.",
    83: "Our reading of compression direction and approach needs verification.",
    103: "Our reading of the breakout and entry directions needs verification.",
    160: "Our reading of the move direction across D1/H1 needs verification.",
}
DATE_REVIEW_IDS = (13, 78, 255, 404)
DUPLICATE_SCENARIO_GROUPS = ((281, 284),)
H1_DEPENDENT_DIRECTION_IDS = (276, 307)
ENTRY_INTERPRETATION_REVIEW = {
    54: "Our extracted entry appears before D1 close; resolve the alignment and enforce the user's closed-D1 rule.",
    36: "The H1 entry arrow or trigger has not yet been confidently located.",
    58: "The individual entry mark has not yet been confidently located.",
    156: "Our alignment of the D1-close arrow and H1 entry needs verification.",
    177: "Our interpretation of the one-ATR condition needs an aligned execution bar.",
    201: "Our alignment of the D1 signal arrow and time marker needs verification.",
    247: "The specific H1 entry bar has not yet been confidently located.",
    256: "Our reading identifies a take-profit label; locate the entry separately without relabelling that mark.",
    264: "Our interpretation of the one-ATR condition needs a verified entry-bar alignment.",
    278: "Our entry-arrow alignment appears before full-bar confirmation; verify both timestamps.",
    311: "Our reading of the end of chop needs a precisely located entry bar.",
    363: "Our interpretation of the two D1 arrows needs verified decision-time alignment.",
}


def _kb_item(identifier: str) -> str:
    return f"botArtjahan-main/knowledge/catalog/items/{identifier[:2]}/{identifier}.md"


SOURCE_REFERENCES = (
    {
        "key": "daily_context_before_entry",
        "path": _kb_item("037ad026d8fd06b9aaf0529d01429fd3c863de6787b2c3b1e5a0ced98181bc32"),
        "kind": "imported_knowledge",
        "rule": "Understand D1, the level and direction first; entry then locates a technical stop and reward/risk.",
    },
    {
        "key": "hourly_context",
        "path": _kb_item("05afb040e9f6b2f0cbed178b13fd8d847f780495b7cff015c5a5108e0a67d29e"),
        "kind": "imported_knowledge",
        "rule": "H1 checks direction, level behavior and accumulation before finer entry timing.",
    },
    {
        "key": "compression_precondition",
        "path": _kb_item("2b225cbe667369dd5bef31540b38c9e967f8c91844ea5e93466ab2a1a3eecf18"),
        "kind": "imported_knowledge",
        "rule": "Compression describes lows/closes progressing toward a level, not merely a triangle name.",
    },
    {
        "key": "breakout_in_trend",
        "path": _kb_item("26e989fe49e85959dfd86565d015c80fba43161c2026480541d379ff2f366c9e"),
        "kind": "imported_knowledge",
        "rule": "A close retest alone does not justify a breakout against the broader directional context.",
    },
    {
        "key": "full_bar_confirmation",
        "path": _kb_item("22b20cc5cb230bf8f99e362fdb15610d34939e95312500b77f86f3727771c3e0"),
        "kind": "imported_knowledge",
        "rule": "The confirmation-entry example waits for pressure to break and a full bar beyond the level, with a structural stop.",
    },
    {
        "key": "return_before_false_breakout_entry",
        "path": _kb_item("4d062d983af4b6a1ec5b9efc591c9d200f061e18ae52cb02c6ca0f6435a88ac5"),
        "kind": "imported_knowledge",
        "rule": "Wait for the penetration and return; the extreme provides a technical stop reference.",
    },
    {
        "key": "technical_stop_not_fixed_atr",
        "path": _kb_item("da3ff5944a62fe8b444c90914620bd8b494cfbb389a067b034c28a56f384c7ca"),
        "kind": "imported_knowledge",
        "rule": "For LP place the stop beyond its wick; 10% ATR is a reference, not a mandatory stop distance.",
    },
    {
        "key": "stop_and_position_geometry",
        "path": _kb_item("1f5dab9eb37bf772adee712f5685afe4b49895fbf6ff8a9faf60d843bd35c6ff"),
        "kind": "imported_knowledge",
        "rule": "A technical stop is structural; position size depends on specified money risk divided by stop distance.",
    },
    {
        "key": "target_as_reward_risk",
        "path": _kb_item("20dd84d9d616d4e3ef8bb32233b99dae524e12defb223fd74ed90975004dab41"),
        "kind": "imported_knowledge",
        "rule": "The worked example computes 3R from entry and stop; it is not an observed target label for another chart.",
    },
    {
        "key": "channel_room_for_risk",
        "path": _kb_item("22e220cec1a111eef8c30e4f28d7209abfba796094ebf63b67d8108758f2a986"),
        "kind": "imported_knowledge",
        "rule": "A channel entry needs room to its next boundary; the example requires 4 stops to preserve about 3R after execution.",
    },
    {
        "key": "lp_two_bar_vs_chop_reference",
        "path": _kb_item("8c222c9ef83c1ec4a8608146b06f5cbdb3080130fef3451b6d7ebc9cdc030b04"),
        "kind": "imported_knowledge",
        "rule": "Distinguish a close beyond then return from repeated ineffective crossings and wick contacts.",
    },
    {
        "key": "current_level_methodology",
        "path": "botArtjahan/_knowledge_base/manual_reviews/confirmed_methodology_20260925/README.md",
        "kind": "project_methodology",
        "rule": "Use density, reaction, anchor quality, confluence, progressive body strength and 1.5% spacing.",
    },
    {
        "key": "paranormal_user_override",
        "path": "botArtjahan/_knowledge_base/manual_reviews/paranormal_body_1_6_20260925/README.md",
        "kind": "user_override",
        "rule": "Paranormal requires real body >=1.6 prior ATR, not total high-low and not the historical 2.5 threshold.",
    },
    {
        "key": "excluded_level_origins",
        "path": "botArtjahan/_knowledge_base/manual_reviews/bar_rules_20260925/README.md",
        "kind": "user_override",
        "rule": "Gap, consolidation and false-breakout origins are not working level types; LP is entry context only.",
    },
    {
        "key": "current_lp_and_contact_code",
        "path": "botArtjahan/knowledge_bot/level_structure.py",
        "kind": "current_code",
        "rule": "A repeated same-side wick return on adjacent bars is chop. LP1/LP2 do not vote for level price/strength.",
    },
    {
        "key": "progressive_strength_code",
        "path": "botArtjahan/knowledge_bot/level_evidence_strength.py",
        "kind": "current_code",
        "rule": "The continuous body score is 2.5*r/(r+1.5), separately from the >=1.6 paranormal classification.",
    },
    {
        "key": "causal_origin_code",
        "path": "botArtjahan/knowledge_bot/level_origin_context.py",
        "kind": "current_code",
        "rule": "A wick sweeping an earlier level does not become a new origin merely by being a further extreme.",
    },
)


def source_fingerprints(workspace: Path | str | None = None) -> list[dict[str, Any]]:
    """Read source bytes without modifying the reference database.

    Missing sources raise FileNotFoundError: a new training manifest must not
    silently claim that an unavailable knowledge source was checked.

    ``workspace`` is the repository root. Historical references retain the old
    checkout prefix as ``original_path``; ``path`` resolves within this checkout
    even after it has been downloaded or renamed on another computer.
    """
    root = Path(workspace).resolve() if workspace is not None else Path(__file__).resolve().parents[1]
    result = []
    for source in SOURCE_REFERENCES:
        original_path = source["path"]
        prefix, separator, remainder = original_path.partition("/")
        local_path = remainder if separator and prefix in ("botArtjahan", "botArtjahan-main") else original_path
        data = (root / local_path).read_bytes()
        result.append({
            **source, "path": local_path, "original_path": original_path,
            "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
        })
    return result


def build_training_contract(workspace: Path | str | None = None) -> dict[str, Any]:
    """Return JSON-compatible constraints and provenance for a training run."""
    return {
        "version": RULES_VERSION,
        "source_precedence": ["latest_explicit_user_corrections", "current_project_methodology", "imported_knowledge"],
        "sources": source_fingerprints(workspace),
        "allowed_level_types": list(ALLOWED_LEVEL_TYPES),
        "excluded_level_types": list(EXCLUDED_LEVEL_TYPES),
        "level_confluence": "One price is one level; independent allowed types strengthen it without duplicating the line.",
        "atr": {"period": ATR_PERIOD, "signal_bar_included": False, "paranormal_min_body_atr": PARANORMAL_MIN_BODY_ATR},
        "daily_history_months": DAILY_HISTORY_MONTHS,
        "minimum_level_spacing_percent": MINIMUM_LEVEL_SPACING_PERCENT,
        "false_breakouts": {
            "one_bar": "Open and close on the original side; wick penetrates beyond the permitted micro-luft.",
            "two_bar": "First bar closes beyond the level, next bar returns and closes on the original side.",
            "chop": "Adjacent repeated same-side wick-return bars are chop, not multiple LP1 entries.",
            "confirms_level": False,
        },
        "label_source": "interpretation_of_user_confirmed_correct_annotations_not_verified_market_outcome",
        "annotation_authority": "Original user scenarios are accepted as correct; unresolved flags concern our extraction or interpretation, not the author's labels.",
        "training_target": "Rank the user-annotated H1 entry state above earlier unselected states of the same scenario.",
        "direction_role": "Long/short direction is a separate learning target from pre-entry prerequisites; the entry ranker consumes direction context. Training status is recorded in each component's own report.",
        "direction_rule": "Compare the prerequisites for long and short available at decision time; the side with more supporting prerequisites is preferred. Equal or unresolved evidence does not justify inventing a direction.",
        "daily_close_required": True,
        "daily_close_rule_source": "Latest user clarification: a daily false breakout is not confirmed until the D1 bar closes; all TVH candidates must wait for D1 close.",
        "comparison_label_meaning": "Earlier unselected states are not labelled losing trades and do not prove a later entry was profitable.",
        "unavailable_numeric_targets": ["level_price", "entry_price", "stop_price", "take_profit_price", "realized_return"],
        "unknown_numeric_target_policy": "Keep null; do not derive targets from profitable outcome pixels or assume fixed ATR multiples.",
        "risk_policy": {
            "stop": "Use the supplied technical stop beyond the relevant structure; a false-breakout stop lies beyond its extreme.",
            "atr_reference": "10% ATR is a knowledge-base reference, not a universal fixed stop or an inferred chart label.",
            "take_profit_multiples": [3.0, 4.0],
            "take_profit_source": "Latest user instruction, supported by knowledge-base reward/risk examples.",
            "take_profit_calculation": "Entry plus/minus 3 or 4 times the distance to a real supplied structural stop; calculated targets are not observed outcomes.",
            "price_inputs_required": ["entry", "structural_stop"],
            "channel_room_source_key": "channel_room_for_risk",
            "fees_and_slippage_included": False,
        },
        "feature_cutoff": "Only observations available before the annotated decision; result descriptions and future chart pixels are forbidden live predictive features.",
        "split_policy": "Keep both timeframes, repeated dates/instruments and duplicate scenarios together; chronological validation needs verified dates.",
        "known_duplicate_groups": [list(group) for group in DUPLICATE_SCENARIO_GROUPS],
        "outcome_validation_available": False,
        "automatic_order_execution_allowed": False,
    }


def scenario_training_policy(row: Mapping[str, Any]) -> dict[str, Any]:
    """Expose interpretation uncertainty separately from absent numeric labels."""
    identifier = int(row["scenario_id"])
    reasons = []
    if row.get("direction") not in ("long", "short"):
        reasons.append("direction_not_labelled")
    if identifier in DIRECTION_INTERPRETATION_REVIEW:
        reasons.append("direction_or_approach_interpretation_requires_review")
    missing_hourly = not any("1H_" in str(path) for path in row.get("image_files", ()))
    entry_reasons = []
    if row.get("direction") not in ("long", "short"):
        entry_reasons.append("scenario_direction_unknown")
    if identifier in (30, 103, 160):
        entry_reasons.append("direction_interpretation_requires_review")
    if missing_hourly:
        entry_reasons.append("missing_hourly_image")
    if identifier in ENTRY_INTERPRETATION_REVIEW:
        entry_reasons.append("entry_bar_or_decision_time_requires_review")
    return {
        "scenario_id": identifier,
        "eligible": not entry_reasons,
        "reasons": entry_reasons,
        "entry_ranking_eligible": not entry_reasons,
        "entry_review_detail": ENTRY_INTERPRETATION_REVIEW.get(identifier),
        "entry_coordinates_must_be_verified": True,
        "approach_interpretation_requires_review": identifier in (68, 83),
        "direction_training_eligible": not reasons,
        "direction_exclusion_reasons": reasons,
        "interpretation_review_detail": DIRECTION_INTERPRETATION_REVIEW.get(identifier),
        "hourly_image_present": not missing_hourly,
        "date_requires_review": identifier in DATE_REVIEW_IDS,
        "daily_only_direction_eligible": not reasons and identifier not in H1_DEPENDENT_DIRECTION_IDS,
        "known_duplicate_group": next((list(group) for group in DUPLICATE_SCENARIO_GROUPS if identifier in group), []),
        "numeric_trade_labels_available": False,
        "profitability_label_available": False,
    }


def _validate_positive_numbers(values: Mapping[str, Any]) -> None:
    for name, value in values.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be a finite positive number")


def assess_risk_geometry(
    *, direction: str, entry: float, stop: float, target: float,
    prior_atr: float | None = None, minimum_reward_risk: float = 3.0,
) -> dict[str, Any]:
    """Check explicitly supplied prices, not predict a stop or take profit.

    Passing geometry is not confirmation of technical stop placement, a valid
    scenario, execution costs or a tradable opportunity. No price is imputed.
    The 3R default is the project's explicit screening reference, not a learned
    target. It may be tightened by a caller's selected scenario rules.
    """
    if direction not in ("long", "short"):
        raise ValueError("direction must be long or short")
    values = {"entry": entry, "stop": stop, "target": target, "minimum_reward_risk": minimum_reward_risk}
    if prior_atr is not None:
        values["prior_atr"] = prior_atr
    _validate_positive_numbers(values)
    sign = 1 if direction == "long" else -1
    risk = sign * (entry - stop)
    reward = sign * (target - entry)
    if risk <= 0 or reward <= 0:
        raise ValueError("stop must be adverse to entry and target favorable to entry")
    ratio = reward / risk
    return {
        "direction": direction,
        "entry": entry,
        "stop": stop,
        "target": target,
        "risk_distance": risk,
        "reward_distance": reward,
        "reward_risk": ratio,
        "minimum_reward_risk": minimum_reward_risk,
        "meets_supplied_geometry": ratio >= minimum_reward_risk or math.isclose(ratio, minimum_reward_risk, rel_tol=1e-12),
        "stop_atr": risk / prior_atr if prior_atr is not None else None,
        "technical_stop_verified": False,
        "fees_and_slippage_included": False,
        "prices_source": "caller_supplied_not_model_predicted",
        "automatic_order_execution_allowed": False,
    }


def calculate_take_profit_targets(
    *, direction: str, entry: float, stop: float, prior_atr: float | None = None,
) -> dict[str, dict[str, Any]]:
    """Calculate the user's 3R/4R prices from an actual entry and structural stop.

    The caller supplies the stop beyond the relevant structure (the wick extreme
    for LP). This arithmetic cannot verify that placement or infer it from ATR.
    No market outcome, execution cost or permission to place an order is implied.
    An impossible nonpositive target is rejected, including on short positions.
    """
    if direction not in ("long", "short"):
        raise ValueError("direction must be long or short")
    _validate_positive_numbers({"entry": entry, "stop": stop})
    sign = 1 if direction == "long" else -1
    risk = sign * (entry - stop)
    if risk <= 0:
        raise ValueError("stop must be adverse to entry")
    result = {}
    for multiple in (3.0, 4.0):
        geometry = assess_risk_geometry(
            direction=direction, entry=entry, stop=stop,
            target=entry + sign * multiple * risk,
            prior_atr=prior_atr, minimum_reward_risk=multiple,
        )
        result[f"{multiple:g}R"] = {
            **geometry,
            "prices_source": "entry_and_stop_caller_supplied_target_calculated",
            "target_multiple": multiple,
            "target_source": "user_requested_reward_risk_not_observed_outcome",
        }
    return result
