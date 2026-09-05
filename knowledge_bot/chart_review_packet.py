"""Layer 6: chart review packet orchestration.

This module is deliberately read-only. It does not discover a new setup, run a
backtest, or enable execution. It combines the current Layer 1-5 outputs into a
single review packet and expands the signed RSCD checklist draft into an explicit
matrix so missing visual/manual context stays visible.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from entry_context import EntryParams, build_entry_context
from level_discovery import DiscoveryParams, Level, build_drawn_level_candidate, build_report as build_level_report, discover_levels
from ohlc_situation_adapter import build_context as build_ohlc_context, load_bars as load_ohlc_bars
from permission_context import build_permission_context, load_manual_context
from runtime_root import data_root
from scn002_strict_kb_backtest import Bar, atr_at, load_history
from situation_analyzer import analyze as analyze_situation, configure_stdio
from build_visual_review_inventory_report import BASE, OBSERVATIONS, compact, read_jsonl


ROOT = data_root(__file__)
CHECKLIST_DRAFT_PATH = ROOT / "_knowledge_base" / "structured" / "consolidation" / "updated_refined_checklist_retrieval" / "updated_refined_scenario_checklist_draft.md"

SOURCE_ARTIFACTS = [
    "_knowledge_base/structured/consolidation/signed_canonical_rulebook/signed_canonical_rulebook.md",
    "_knowledge_base/structured/consolidation/feature_contracts_validation/feature_contracts_validation.md",
    "_knowledge_base/structured/consolidation/updated_refined_checklist_retrieval/updated_refined_scenario_checklist_draft.md",
]
LIGHT_PACKET_DEFAULT_JSON = BASE / "chart_review_packet_light_last.json"
LIGHT_PACKET_DEFAULT_MARKDOWN = BASE / "chart_review_packet_light_last.md"

SUPPORTED_MANUAL_CONTEXT_KEYS = [
    "checklist_answers",
    "discipline_violations",
    "no_trade_gates",
    "market_mechanics",
    "formations",
    "tail_bars",
    "breakout_failures",
    "rebounds",
    "workflow_reviews",
    "screenshot_refs",
    "drawn_levels",
    "chart_notes",
    "vision_metadata",
    "vision_warnings",
]


@dataclass
class ChartReviewParams:
    execution_lookback_bars: int = EntryParams.execution_lookback_bars


def list_items(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def enrich_manual_context_with_vision(manual_context: dict[str, Any] | None,
                                      screenshot_path: str | Path,
                                      *,
                                      model: str | None = None,
                                      expected_symbol: str | None = None,
                                      expected_timeframes: list[str] | None = None,
                                      run_vision: bool = True) -> dict[str, Any]:
    merged = dict(manual_context or {})
    path = Path(screenshot_path)
    path_text = str(path)
    screenshot_refs = [str(item) for item in list_items(merged.get("screenshot_refs"))]
    if path_text not in screenshot_refs:
        screenshot_refs.append(path_text)
    merged["screenshot_refs"] = screenshot_refs

    existing_metadata = merged.get("vision_metadata")
    vision_metadata = dict(existing_metadata) if isinstance(existing_metadata, dict) else {}
    vision_metadata.update({
        "screenshot_path": path_text,
        "vision_attempted": bool(run_vision),
        "metadata_only": True,
    })
    warnings = [str(item) for item in list_items(merged.get("vision_warnings"))]

    if not path.exists():
        vision_metadata["error"] = f"screenshot_not_found:{path_text}"
        warnings.append("screenshot_not_found")
        merged["vision_metadata"] = vision_metadata
        merged["vision_warnings"] = sorted(set(warnings))
        return merged

    if run_vision:
        try:
            from chart_vision import extract_metadata

            result = extract_metadata(path, model=model)
            vision_metadata.update(result.to_dict())
            if result.drawn_levels and not list_items(merged.get("drawn_levels")):
                merged["drawn_levels"] = result.drawn_levels
            if result.notes and not merged.get("chart_notes") and not merged.get("notes"):
                merged["chart_notes"] = result.notes
            if result.ticker and expected_symbol and result.ticker.upper() != expected_symbol.upper():
                warnings.append(f"vision_ticker_mismatch:{result.ticker}!={expected_symbol}")
            expected = {timeframe for timeframe in expected_timeframes or [] if timeframe}
            if result.timeframe and expected and result.timeframe not in expected:
                warnings.append(f"vision_timeframe_not_in_pipeline:{result.timeframe}")
            if result.confidence < 0.75:
                warnings.append(f"vision_low_confidence:{result.confidence:.2f}")
        except Exception as exc:
            vision_metadata["error"] = f"{type(exc).__name__}: {exc}"
            warnings.append("vision_extraction_failed")

    merged["vision_metadata"] = vision_metadata
    if warnings:
        merged["vision_warnings"] = sorted(set(warnings))
    return merged


def checklist_answers(manual_context: dict[str, Any]) -> dict[str, Any]:
    value = manual_context.get("checklist_answers") or {}
    return value if isinstance(value, dict) else {}


def answer_state(manual_context: dict[str, Any], key: str, pass_text: str,
                 missing_text: str, fail_text: str | None = None) -> tuple[str, str]:
    value = checklist_answers(manual_context).get(key)
    if value is True:
        return "pass", pass_text
    if value is False:
        return "block", fail_text or missing_text
    return "manual_review", missing_text


def parse_rscd_items(path: Path = CHECKLIST_DRAFT_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    section_re = re.compile(r"^##\s+(RSCD-\d{3}[-\w]*)\s*(.*)$")
    item_re = re.compile(r"^- \[ \]\s+`([^`]+)`\s+(.*)$")
    rows: list[dict[str, Any]] = []
    checklist_id = ""
    checklist_title = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        section = section_re.match(line)
        if section:
            checklist_id = section.group(1)
            checklist_title = section.group(2).strip()
            continue
        item = item_re.match(line)
        if item and checklist_id:
            rows.append({
                "checklist_id": checklist_id,
                "checklist_title": checklist_title,
                "item_id": item.group(1),
                "text": item.group(2).strip(),
                "status": "manual_review",
                "evidence": "not yet linked to a layer output or supplied manual context",
                "source_rule_ids": [],
            })
    return rows


def by_item_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row["item_id"]): row for row in rows}


def set_item(rows_by_id: dict[str, dict[str, Any]], item_id: str, status: str,
             evidence: str, source_rule_ids: list[str] | None = None) -> None:
    row = rows_by_id.get(item_id)
    if row is None:
        return
    row["status"] = status
    row["evidence"] = evidence
    if source_rule_ids is not None:
        row["source_rule_ids"] = source_rule_ids


def detector_status(output: dict[str, Any] | None, pass_statuses: set[str] | None = None) -> tuple[str, str]:
    if not output:
        return "manual_review", "detector output is missing"
    status = str(output.get("status") or "")
    if status == "reject" or output.get("hard_rejects"):
        return "block", f"status={status} hard_rejects={output.get('hard_rejects', [])}"
    allowed = pass_statuses or {"pass", "setup", "trigger", "context_ready"}
    if status in allowed:
        return "pass", f"status={status}"
    return "manual_review", f"status={status or 'unknown'}"


def optional_context_state(outputs: list[dict[str, Any]], label: str) -> tuple[str, str]:
    if not outputs:
        return "manual_review", f"{label} context not supplied"
    hard_rejects = [item for output in outputs for item in output.get("hard_rejects", [])]
    if any(output.get("status") == "reject" for output in outputs) or hard_rejects:
        return "block", f"{label} rejected: {hard_rejects}"
    manual = [item for output in outputs for item in output.get("manual_review_needed", [])]
    evidence = f"{label} supplied to canonical validator"
    if manual:
        evidence += f"; manual remains={sorted(set(manual))}"
    return "pass", evidence


def first_candidate(entry_report: dict[str, Any], model: str) -> dict[str, Any] | None:
    for candidate in entry_report.get("entry_candidates", []) or []:
        if candidate.get("model") == model:
            return candidate
    return None


def candidate_detector(candidate: dict[str, Any] | None) -> dict[str, Any] | None:
    return (candidate or {}).get("entry_detector") or None


def risk_features(best: dict[str, Any] | None) -> dict[str, Any]:
    risk = (best or {}).get("risk") or {}
    return risk.get("risk_validation") or {}


def build_checklist_matrix(permission_report: dict[str, Any], manual_context: dict[str, Any]) -> dict[str, Any]:
    rows = parse_rscd_items()
    rows_by_id = by_item_id(rows)
    entry_report = permission_report.get("entry_context") or {}
    approach = entry_report.get("approach") or {}
    permission_items = {item["item_id"]: item for item in permission_report.get("manual_checklist", [])}

    for item_id, item in permission_items.items():
        set_item(
            rows_by_id,
            item_id,
            item.get("status", "manual_review"),
            item.get("evidence", "from Layer 5 permission context"),
            item.get("source_rule_ids", []),
        )

    scenario = entry_report.get("scenario") or {}
    nearest_level = entry_report.get("nearest_level") or {}
    best = entry_report.get("best_entry") or {}
    retest = approach.get("retest") or {}
    breakout = approach.get("breakout") or {}
    false_breakout = approach.get("false_breakout_reversal") or {}
    breakout_features = breakout.get("breakout_features") or {}
    false_breakout_features = false_breakout.get("false_breakout_features") or {}
    retest_features = retest.get("retest_features") or {}
    optional = permission_report.get("optional_context_validations") or {}

    has_best = bool(best)
    has_level = bool(nearest_level)
    scenario_complete = bool(has_level and scenario.get("family") and scenario.get("direction") and has_best)
    set_item(rows_by_id, "RSCD-001-001", "pass" if scenario_complete else "manual_review",
             f"symbol={entry_report.get('symbol')} level={nearest_level.get('price')} family={scenario.get('family')} direction={scenario.get('direction')} model={best.get('model')}")
    screenshot_count = len(list_items(manual_context.get("screenshot_refs")))
    for item_id, key in [
        ("RSCD-001-002", "screenshot_before_scenario"),
        ("RSCD-001-003", "no_opposite_entry_after_scenario"),
        ("RSCD-001-004", "separate_scenario_and_trade_result"),
    ]:
        status, evidence = answer_state(manual_context, key, f"manual answer {key}=true", f"manual answer {key} not supplied", f"manual answer {key}=false")
        if item_id == "RSCD-001-002" and status == "manual_review" and screenshot_count:
            evidence = f"screenshot_refs={screenshot_count}; before-scenario timing not confirmed"
        set_item(rows_by_id, item_id, status, evidence)

    trend_summary = approach.get("trend_summary") or {}
    global_known = trend_summary.get("global_trend") not in {None, "", "unknown"}
    set_item(rows_by_id, "RSCD-002-001", "pass" if global_known else "manual_review",
             f"global_trend={trend_summary.get('global_trend')} status={trend_summary.get('status')}")
    set_item(rows_by_id, "RSCD-002-002", "pass" if scenario.get("valid") else "manual_review",
             f"scenario_valid={scenario.get('valid')} local_zone={trend_summary.get('local_zone')}")
    set_item(rows_by_id, "RSCD-002-003", "pass" if breakout.get("status") == "setup" else "manual_review" if breakout.get("status") != "reject" else "block",
             f"breakout_status={breakout.get('status')} trend_aligned={breakout_features.get('trend_aligned')}")
    set_item(rows_by_id, "RSCD-002-004", "pass" if scenario.get("family") == "false_breakout" else "manual_review",
             f"scenario_family={scenario.get('family')} false_breakout_status={false_breakout.get('status')}")

    retest_class = str(retest.get("classification") or retest_features.get("classification") or "")
    set_item(rows_by_id, "RSCD-003-001", "pass" if "near" in retest_class else "manual_review",
             f"retest_classification={retest_class or 'missing'}")
    set_item(rows_by_id, "RSCD-003-002", "pass" if breakout_features.get("close_near_level") or breakout_features.get("close_at_extreme") else "manual_review",
             f"close_near={breakout_features.get('close_near_level')} close_at_extreme={breakout_features.get('close_at_extreme')}")
    set_item(rows_by_id, "RSCD-003-003", "pass" if breakout_features.get("compression") or breakout_features.get("consolidation_near_level") or breakout_features.get("volatility_fade") else "manual_review",
             f"compression={breakout_features.get('compression')} consolidation={breakout_features.get('consolidation_near_level')} volatility_fade={breakout_features.get('volatility_fade')}")
    set_item(rows_by_id, "RSCD-003-004", "pass" if breakout_features.get("no_reaction_to_lp") or breakout_features.get("local_false_breakout_in_direction") else "manual_review",
             f"no_reaction_to_lp={breakout_features.get('no_reaction_to_lp')} local_lp={breakout_features.get('local_false_breakout_in_direction')}")
    breakout_status, breakout_evidence = detector_status(breakout, {"setup", "trigger"})
    set_item(rows_by_id, "RSCD-003-005", breakout_status, breakout_evidence)

    false_breakout_status, false_breakout_evidence = detector_status(false_breakout, {"setup", "trigger"})
    for item_id, feature in [
        ("RSCD-004-001", "sharp_approach"),
        ("RSCD-004-002", "atr_consumed_before_level"),
        ("RSCD-004-003", "far_retest"),
        ("RSCD-004-004", "returned_beyond_level"),
        ("RSCD-004-005", "full_bar_beyond_after_sweep"),
    ]:
        if item_id == "RSCD-004-005":
            status = "block" if false_breakout.get("status") == "reject" else "pass" if false_breakout.get("status") in {"setup", "trigger"} else "manual_review"
            evidence = false_breakout_evidence
        else:
            value = false_breakout_features.get(feature)
            status = "pass" if value else "manual_review"
            evidence = f"{feature}={value}"
        set_item(rows_by_id, item_id, status, evidence)

    set_item(rows_by_id, "RSCD-005-001", "pass" if retest_class else "manual_review", f"classification={retest_class or 'missing'} bars_since={retest_features.get('bars_since_contact')}")
    set_item(rows_by_id, "RSCD-005-002", "pass" if "near" in retest_class else "manual_review", f"classification={retest_class or 'missing'}")
    set_item(rows_by_id, "RSCD-005-003", "pass" if "far" in retest_class else "manual_review", f"classification={retest_class or 'missing'}")
    set_item(rows_by_id, "RSCD-005-004", "pass" if retest_features.get("far_to_near_retest_exception") else "manual_review", f"far_to_near={retest_features.get('far_to_near_retest_exception')}")

    fixation = first_candidate(entry_report, "fixation_return")
    fixation_status, fixation_evidence = detector_status(candidate_detector(fixation), {"trigger"})
    for item_id in ["RSCD-006-001", "RSCD-006-002", "RSCD-006-003", "RSCD-006-004", "RSCD-006-005"]:
        set_item(rows_by_id, item_id, fixation_status, fixation_evidence)

    bsu_bpu = first_candidate(entry_report, "bsu_bpu_limit")
    bsu_status, bsu_evidence = detector_status(candidate_detector(bsu_bpu), {"trigger"})
    for item_id in ["RSCD-007-001", "RSCD-007-002", "RSCD-007-003", "RSCD-007-004", "RSCD-007-005"]:
        set_item(rows_by_id, item_id, bsu_status, bsu_evidence)

    tbx = (best or {}).get("tbx_validation") or {}
    tbx_status, tbx_evidence = detector_status(tbx, {"pass"})
    for item_id in ["RSCD-008-001", "RSCD-008-002", "RSCD-008-003", "RSCD-008-004", "RSCD-008-005"]:
        set_item(rows_by_id, item_id, tbx_status, tbx_evidence)

    formation_status, formation_evidence = optional_context_state(optional.get("formations", []), "formations")
    for item_id in ["RSCD-009-001", "RSCD-009-002", "RSCD-009-003", "RSCD-009-004", "RSCD-009-005"]:
        set_item(rows_by_id, item_id, formation_status, formation_evidence)

    tail_status, tail_evidence = optional_context_state(optional.get("tail_bars", []), "tail_bars")
    for item_id in ["RSCD-010-001", "RSCD-010-002", "RSCD-010-003", "RSCD-010-004"]:
        set_item(rows_by_id, item_id, tail_status, tail_evidence)

    risk = risk_features(best)
    risk_status = str(((best or {}).get("risk") or {}).get("status") or "")
    set_item(rows_by_id, "RSCD-011-001", "pass" if risk.get("calculated_stop_abs") is not None else "manual_review", f"calculated_stop_abs={risk.get('calculated_stop_abs')}")
    set_item(rows_by_id, "RSCD-011-002", "block" if risk_status == "reject" else "pass" if risk_status in {"pass", "warn"} else "manual_review", f"risk_status={risk_status} technical_stop_atr={risk.get('technical_stop_atr')}")
    set_item(rows_by_id, "RSCD-011-003", "pass" if (risk.get("room_to_next_level_r") or 0) >= 3 else "block" if risk.get("room_to_next_level_r") is not None else "manual_review", f"room_to_next_level_r={risk.get('room_to_next_level_r')}")
    status, evidence = answer_state(manual_context, "stop_moves_only_after_new_structure", "manual answer confirms structural stop management", "stop-management plan not supplied", "manual answer rejects structural stop management")
    set_item(rows_by_id, "RSCD-011-004", status, evidence)

    for item_id, key in [
        ("RSCD-012-001", "review_order_d1_h1_m5"),
        ("RSCD-012-002", "participant_pain_recorded"),
        ("RSCD-012-003", "scenario_vs_entry_error_separated"),
        ("RSCD-012-004", "result_recorded_in_r"),
    ]:
        status, evidence = answer_state(manual_context, key, f"manual answer {key}=true", f"manual answer {key} not supplied", f"manual answer {key}=false")
        set_item(rows_by_id, item_id, status, evidence)

    mechanics_status, mechanics_evidence = optional_context_state(optional.get("market_mechanics", []), "market_mechanics")
    for item_id in ["RSCD-013-001", "RSCD-013-002", "RSCD-013-003"]:
        set_item(rows_by_id, item_id, mechanics_status, mechanics_evidence)

    for item_id, key in [
        ("RSCD-014-002", "no_impulsive_reentry_after_stop_or_miss"),
        ("RSCD-014-003", "risk_size_matches_plan"),
    ]:
        status, evidence = answer_state(manual_context, key, f"manual answer {key}=true", f"manual answer {key} not supplied", f"manual answer {key}=false")
        set_item(rows_by_id, item_id, status, evidence)

    rebound_status, rebound_evidence = optional_context_state(optional.get("rebounds", []), "rebounds")
    if scenario.get("family") != "rebound" and not optional.get("rebounds"):
        rebound_evidence = f"rebound context not supplied; scenario_family={scenario.get('family')}"
    for item_id in ["RSCD-015-001", "RSCD-015-002", "RSCD-015-003", "RSCD-015-004"]:
        set_item(rows_by_id, item_id, rebound_status, rebound_evidence)

    counts: dict[str, int] = {}
    group_counts: dict[str, dict[str, int]] = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
        group = group_counts.setdefault(row["checklist_id"], {})
        group[row["status"]] = group.get(row["status"], 0) + 1
    return {
        "source_checklist": CHECKLIST_DRAFT_PATH.relative_to(ROOT).as_posix() if CHECKLIST_DRAFT_PATH.exists() else None,
        "items": rows,
        "summary": {
            "item_count": len(rows),
            "status_counts": counts,
            "group_status_counts": group_counts,
        },
        "manual_review_queue": [row for row in rows if row["status"] == "manual_review"],
        "blockers": [row for row in rows if row["status"] == "block"],
    }


def chart_context(manual_context: dict[str, Any]) -> dict[str, Any]:
    return {
        "screenshot_refs": list_items(manual_context.get("screenshot_refs")),
        "drawn_levels": list_items(manual_context.get("drawn_levels")),
        "chart_notes": manual_context.get("chart_notes") or manual_context.get("notes"),
        "vision_metadata": manual_context.get("vision_metadata"),
        "vision_warnings": list_items(manual_context.get("vision_warnings")),
    }


def drawn_level_price(raw_item: Any) -> float | None:
    if isinstance(raw_item, bool):
        return None
    if isinstance(raw_item, (int, float)):
        return float(raw_item)
    if isinstance(raw_item, dict):
        value = raw_item.get("price", raw_item.get("level_price"))
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value)
    return None


def levels_with_drawn_candidates(levels: list[Level], manual_context: dict[str, Any],
                                 context_bars: list[Bar]) -> list[Level]:
    raw_drawn_levels = list_items(manual_context.get("drawn_levels"))
    if not raw_drawn_levels or not context_bars:
        return levels

    params = DiscoveryParams()
    last_atr = atr_at(context_bars, len(context_bars) - 1, params.atr_period) or 0.0
    if last_atr <= 0:
        return levels
    duplicate_tolerance = params.cluster_luft_atr * last_atr
    current_price = context_bars[-1].close

    drawn_prices: list[float] = []
    for raw_item in raw_drawn_levels:
        price = drawn_level_price(raw_item)
        if price is None:
            continue
        if any(abs(existing - price) <= duplicate_tolerance for existing in drawn_prices):
            continue
        drawn_prices.append(price)
    if not drawn_prices:
        return levels

    candidate_prices = [level.price for level in levels] + drawn_prices
    nearest_above = min((price for price in candidate_prices if price >= current_price), key=lambda price: price, default=None)
    nearest_below = max((price for price in candidate_prices if price < current_price), key=lambda price: price, default=None)

    drawn_candidates: list[Level] = []
    for price in drawn_prices:
        if any(abs(level.price - price) <= duplicate_tolerance for level in levels):
            continue
        nearest_level = (
            (nearest_above is not None and abs(price - nearest_above) <= duplicate_tolerance)
            or (nearest_below is not None and abs(price - nearest_below) <= duplicate_tolerance)
        )
        candidate = build_drawn_level_candidate(context_bars, price, params, nearest_level=nearest_level)
        if candidate is not None:
            drawn_candidates.append(candidate)
    if not drawn_candidates:
        return levels
    return [*levels, *drawn_candidates]


def candle_dict(bar: Bar) -> dict[str, float | str]:
    return {
        "time": bar_time(bar),
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
    }


def bar_time(bar: Bar) -> str:
    return datetime.fromtimestamp(bar.open_time / 1000, tz=timezone.utc).isoformat()


def closes_beyond_level(candle: dict[str, Any], direction: str, level_price: float) -> bool:
    close = float(candle["close"])
    return close > level_price if direction == "long" else close < level_price


def no_impulse_after_breakout(candles: list[dict[str, Any]], direction: str, breakout_index: int) -> bool:
    if breakout_index + 1 >= len(candles):
        return True
    breakout = candles[breakout_index]
    after = candles[breakout_index + 1:]
    if direction == "long":
        return max(float(candle["high"]) for candle in after) <= float(breakout["high"])
    return min(float(candle["low"]) for candle in after) >= float(breakout["low"])


def breakout_failure_direction(entry_report: dict[str, Any]) -> str:
    approach = entry_report.get("approach") or {}
    directions = approach.get("directions") or {}
    direction = str(directions.get("breakout") or "")
    if direction in {"long", "short"}:
        return direction
    scenario = entry_report.get("scenario") or {}
    scenario_direction = str(scenario.get("direction") or "")
    if scenario.get("family") == "false_breakout" and scenario_direction in {"long", "short"}:
        return "short" if scenario_direction == "long" else "long"
    return scenario_direction if scenario_direction in {"long", "short"} else ""


def build_auto_breakout_failure_context(entry_report: dict[str, Any], execution_bars: list[Bar],
                                        execution_timeframe: str,
                                        params: ChartReviewParams) -> dict[str, list[dict[str, Any]]]:
    approach = entry_report.get("approach") or {}
    level = approach.get("nearest_level") or entry_report.get("nearest_level") or {}
    direction = breakout_failure_direction(entry_report)
    level_price = level.get("price")
    if direction not in {"long", "short"} or level_price is None:
        return {}

    candles = [candle_dict(bar) for bar in review_execution_window(execution_bars, params)]
    if len(candles) < 2:
        return {}

    breakout_index = next(
        (index for index in range(len(candles) - 2, -1, -1) if closes_beyond_level(candles[index], direction, float(level_price))),
        None,
    )
    if breakout_index is None or closes_beyond_level(candles[-1], direction, float(level_price)):
        return {}

    second_fixation_bar = any(
        closes_beyond_level(candle, direction, float(level_price))
        for candle in candles[breakout_index + 1:-1]
    )
    return {
        "breakout_failures": [{
            "timeframe": execution_timeframe,
            "direction": direction,
            "level_price": float(level_price),
            "atr": float(entry_report.get("atr") or 0.0),
            "candles": candles,
            "breakout_candle_index": breakout_index,
            "second_fixation_bar": second_fixation_bar,
            "no_impulse_after_break": no_impulse_after_breakout(candles, direction, breakout_index),
            "source": "layer6_auto_breakout_failure_context",
        }]
    }


def build_auto_market_mechanics_context(breakout_failure_context: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    failures = breakout_failure_context.get("breakout_failures") or []
    if not failures:
        return {}
    failure = failures[0]
    direction = str(failure.get("direction") or "")
    level_price = failure.get("level_price")
    atr = float(failure.get("atr") or 0.0)
    candles = failure.get("candles") or []
    breakout_index = failure.get("breakout_candle_index")
    if atr <= 0 or level_price is None or breakout_index is None or not candles:
        return {}
    breakout_close = float(candles[int(breakout_index)]["close"])
    if abs(breakout_close - float(level_price)) / atr < 0.25:
        return {}
    if direction == "long":
        pressure_side = "sellers"
        trapped_side = "buyers"
        pressure_source = "failed long breakout trapped buyers above the level"
    elif direction == "short":
        pressure_side = "buyers"
        trapped_side = "sellers"
        pressure_source = "failed short breakdown trapped sellers below the level"
    else:
        return {}

    return {
        "market_mechanics": [{
            "pressure_side": pressure_side,
            "trapped_side": trapped_side,
            "forced_exit_probability": None,
            "range_accumulation_duration": 0,
            "volume_expansion": False,
            "failed_reaction": True,
            "expected_pressure_source": pressure_source,
            "order_book_claim": False,
            "imbalance_exhausted": False,
            "level_price": level_price,
            "source": "layer6_auto_market_mechanics_from_breakout_failure",
        }]
    }


def candle_contacts_price(candle: dict[str, Any], level_price: float, tolerance: float) -> bool:
    return (
        float(candle["low"]) <= level_price <= float(candle["high"])
        or abs(float(candle["close"]) - level_price) <= tolerance
        or abs(float(candle["open"]) - level_price) <= tolerance
    )


def compact_rebound_stop(candles: list[dict[str, Any]], direction: str, atr: float,
                         contact_index: int, latest_index: int) -> tuple[float | None, float | None]:
    if atr <= 0 or contact_index < 0 or latest_index < contact_index:
        return None, None
    reaction = candles[contact_index:latest_index + 1]
    buffer = 0.01 * atr
    entry_price = float(candles[latest_index]["close"])
    if direction == "long":
        stop_price = min(float(candle["low"]) for candle in reaction) - buffer
    else:
        stop_price = max(float(candle["high"]) for candle in reaction) + buffer
    stop_size_atr = abs(entry_price - stop_price) / atr
    return stop_price, stop_size_atr


def build_auto_rebound_context(entry_report: dict[str, Any], execution_bars: list[Bar],
                               execution_timeframe: str,
                               params: ChartReviewParams) -> dict[str, list[dict[str, Any]]]:
    level = entry_report.get("nearest_level") or {}
    level_price = level.get("price")
    atr = entry_report.get("atr")
    if level_price is None or atr is None or float(atr) <= 0:
        return {}

    candles = [candle_dict(bar) for bar in review_execution_window(execution_bars, params)]
    if len(candles) < 3:
        return {}

    atr_value = float(atr)
    level_value = float(level_price)
    tolerance = max(0.02 * atr_value, 1e-12)
    recent_start = max(0, len(candles) - 12)
    contact_indices = [
        index for index in range(recent_start, len(candles))
        if candle_contacts_price(candles[index], level_value, tolerance)
    ]
    if not contact_indices:
        return {}

    contact_index = contact_indices[-1]
    latest = candles[-1]
    away_threshold = max(0.05 * atr_value, tolerance)
    if float(latest["close"]) >= level_value + away_threshold:
        direction = "long"
    elif float(latest["close"]) <= level_value - away_threshold:
        direction = "short"
    else:
        return {}

    reaction_closes = [
        candle for candle in candles[contact_index:]
        if closes_beyond_level(candle, direction, level_value)
    ]
    if not reaction_closes:
        return {}

    stop_price, stop_size_atr = compact_rebound_stop(candles, direction, atr_value, contact_index, len(candles) - 1)
    if stop_price is None or stop_size_atr is None or stop_size_atr > 0.13:
        return {}

    return {
        "rebounds": [{
            "timeframe": execution_timeframe,
            "direction": direction,
            "level_price": level_value,
            "atr": atr_value,
            "entry_price": float(latest["close"]),
            "stop_price": stop_price,
            "reaction_bar_count": len(reaction_closes),
            "reaction_confirmed": True,
            "confirmation_after_rebound": len(reaction_closes) >= 2,
            "first_impulse_acceptable_stop": True,
            "level_valid": level.get("kb_status") == "pass",
            "level_strong": float(level.get("kb_score") or 0.0) >= 2.0,
            "source": "layer6_auto_rebound_context",
        }]
    }


def close_near_price(candle: dict[str, Any], price: float, tolerance: float) -> bool:
    return abs(float(candle["close"]) - price) <= tolerance


def build_auto_formation_context(entry_report: dict[str, Any], execution_bars: list[Bar],
                                 execution_timeframe: str,
                                 params: ChartReviewParams) -> dict[str, list[dict[str, Any]]]:
    level = entry_report.get("nearest_level") or {}
    level_price = level.get("price")
    atr = entry_report.get("atr")
    if level_price is None or atr is None or float(atr) <= 0:
        return {}

    candles = [candle_dict(bar) for bar in review_execution_window(execution_bars, params)]
    if len(candles) < 5:
        return {}

    atr_value = float(atr)
    level_value = float(level_price)
    tolerance = max(0.08 * atr_value, 1e-12)
    distance_threshold = max(0.35 * atr_value, tolerance)
    recent_start = max(0, len(candles) - 12)
    latest_index = len(candles) - 1
    pivot_range = range(recent_start + 1, latest_index)
    if not pivot_range:
        return {}

    candidates: list[tuple[float, int, str]] = []
    downside_pivot = min(pivot_range, key=lambda index: float(candles[index]["low"]))
    downside_extreme = float(candles[downside_pivot]["low"])
    downside_distance = level_value - downside_extreme
    if downside_distance >= distance_threshold:
        candidates.append((downside_distance, downside_pivot, "downside_v"))

    upside_pivot = max(pivot_range, key=lambda index: float(candles[index]["high"]))
    upside_extreme = float(candles[upside_pivot]["high"])
    upside_distance = upside_extreme - level_value
    if upside_distance >= distance_threshold:
        candidates.append((upside_distance, upside_pivot, "upside_v"))

    for _distance, pivot_index, shape in sorted(candidates, reverse=True):
        pre_start = max(recent_start, pivot_index - 3)
        pre_origin = any(
            candle_contacts_price(candles[index], level_value, tolerance) or close_near_price(candles[index], level_value, tolerance)
            for index in range(pre_start, pivot_index)
        )
        if not pre_origin or latest_index - pivot_index > 3:
            continue
        latest = candles[latest_index]
        if shape == "downside_v":
            pivot_extreme = float(candles[pivot_index]["low"])
            returned_to_origin = float(latest["close"]) >= level_value - tolerance
            sharp_return = float(latest["close"]) - pivot_extreme >= distance_threshold
        else:
            pivot_extreme = float(candles[pivot_index]["high"])
            returned_to_origin = float(latest["close"]) <= level_value + tolerance
            sharp_return = pivot_extreme - float(latest["close"]) >= distance_threshold
        if not returned_to_origin or not sharp_return:
            continue
        return {
            "formations": [{
                "timeframe": execution_timeframe,
                "formation_type": "v",
                "level_price": level_value,
                "accumulation_near_level": False,
                "sharp_move_out": True,
                "sharp_return": True,
                "returned_to_origin": True,
                "atr_consumed": True,
                "near_retest_after_first_failure": False,
                "contaminated_zone": False,
                "source": "layer6_auto_v_formation_context",
                "pivot_time": candles[pivot_index].get("time"),
                "shape": shape,
            }]
        }
    return {}


def candle_tail_profile(candle: dict[str, Any]) -> dict[str, float]:
    open_price = float(candle["open"])
    high = float(candle["high"])
    low = float(candle["low"])
    close = float(candle["close"])
    range_size = high - low
    body_size = abs(close - open_price)
    upper_tail = high - max(open_price, close)
    lower_tail = min(open_price, close) - low
    return {
        "range_size": range_size,
        "body_size": body_size,
        "upper_tail": upper_tail,
        "lower_tail": lower_tail,
    }


def is_two_sided_tail_bar(candle: dict[str, Any], level_price: float, atr: float) -> bool:
    profile = candle_tail_profile(candle)
    range_size = profile["range_size"]
    if range_size <= 0 or atr <= 0:
        return False
    tolerance = max(0.05 * atr, 1e-12)
    return (
        candle_contacts_price(candle, level_price, tolerance)
        and range_size >= 0.08 * atr
        and profile["body_size"] <= 0.35 * range_size
        and profile["upper_tail"] >= 0.20 * range_size
        and profile["lower_tail"] >= 0.20 * range_size
    )


def prior_move_for_tail_cluster(candles: list[dict[str, Any]], first_tail_index: int, atr: float) -> str:
    if first_tail_index <= 0 or atr <= 0:
        return "unknown"
    lookback_index = max(0, first_tail_index - 4)
    before_cluster_index = first_tail_index - 1
    delta = float(candles[before_cluster_index]["close"]) - float(candles[lookback_index]["close"])
    threshold = max(0.05 * atr, 1e-12)
    if delta >= threshold:
        return "up"
    if delta <= -threshold:
        return "down"
    return "unknown"


def build_auto_tail_bar_context(entry_report: dict[str, Any], execution_bars: list[Bar],
                                execution_timeframe: str,
                                params: ChartReviewParams) -> dict[str, list[dict[str, Any]]]:
    level = entry_report.get("nearest_level") or {}
    level_price = level.get("price")
    atr = entry_report.get("atr")
    if level_price is None or atr is None or float(atr) <= 0:
        return {}

    candles = [candle_dict(bar) for bar in review_execution_window(execution_bars, params)]
    if len(candles) < 4:
        return {}

    atr_value = float(atr)
    level_value = float(level_price)
    recent_start = max(0, len(candles) - 16)
    runs: list[list[int]] = []
    current_run: list[int] = []
    for index in range(recent_start, len(candles)):
        if is_two_sided_tail_bar(candles[index], level_value, atr_value):
            current_run.append(index)
            continue
        if len(current_run) >= 2:
            runs.append(current_run)
        current_run = []
    if len(current_run) >= 2:
        runs.append(current_run)
    if not runs:
        return {}

    tail_indices = runs[-1]
    if len(tail_indices) > 3:
        return {}
    if tail_indices[-1] < len(candles) - 4:
        return {}
    prior_move = prior_move_for_tail_cluster(candles, tail_indices[0], atr_value)
    if prior_move == "unknown":
        return {}

    return {
        "tail_bars": [{
            "timeframe": execution_timeframe,
            "prior_move": prior_move,
            "tail_bar_count": len(tail_indices),
            "small_bodies": True,
            "both_sided_tails": True,
            "near_level": True,
            "level_price": level_value,
            "contaminated_zone": False,
            "long_tail_level_basis": level.get("kb_status") == "pass",
            "no_tail_toward_level_close": False,
            "source": "layer6_auto_tail_bar_context",
        }]
    }


def combine_auto_contexts(*contexts: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    combined: dict[str, Any] = {}
    for context in contexts:
        combined = merge_auto_context(combined, context)
    return combined


def merge_auto_context(manual_context: dict[str, Any], auto_context: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    merged = dict(manual_context)
    for key, generated in auto_context.items():
        if not generated:
            continue
        existing = merged.get(key, [])
        if existing is None:
            existing = []
        if not isinstance(existing, list):
            continue
        merged[key] = [*existing, *generated]
    return merged


def review_execution_window(execution_bars: list[Bar], params: ChartReviewParams) -> list[Bar]:
    if params.execution_lookback_bars <= 0:
        return execution_bars
    return execution_bars[-params.execution_lookback_bars:]


def bar_index_by_time(bars: list[Bar], value: str | None) -> int | None:
    if not value:
        return None
    for index, bar in enumerate(bars):
        if bar_time(bar) == value:
            return index
    return None


def structure_window_from_entry(best: dict[str, Any]) -> dict[str, Any]:
    detector = best.get("entry_detector") or {}
    if not isinstance(detector, dict):
        return {}
    explicit = detector.get("structure_window")
    if isinstance(explicit, dict):
        return {
            "kind": explicit.get("kind"),
            "start_time": explicit.get("start_time"),
            "end_time": explicit.get("end_time"),
            "trigger_time": explicit.get("trigger_time") or explicit.get("end_time"),
            "bar_count": explicit.get("bar_count"),
        }
    features = detector.get("bsu_bpu_features")
    if isinstance(features, dict) and (features.get("bpu1_time") or features.get("bpu2_time")):
        return {
            "kind": "bpu_structure",
            "start_time": features.get("bpu1_time"),
            "end_time": features.get("bpu2_time"),
            "trigger_time": features.get("bpu2_time"),
        }
    features = detector.get("fixation_features")
    if isinstance(features, dict) and (features.get("return_attempt_time") or features.get("full_bar_2_time")):
        return {
            "kind": "return_attempt_tail",
            "start_time": features.get("return_attempt_time"),
            "end_time": features.get("full_bar_2_time"),
            "trigger_time": features.get("full_bar_2_time") or features.get("trigger_time"),
        }
    return {}


def trigger_time_from_entry(best: dict[str, Any]) -> str | None:
    return structure_window_from_entry(best).get("trigger_time")


def structure_times_from_entry(best: dict[str, Any]) -> tuple[str | None, str | None, str | None, Any]:
    window = structure_window_from_entry(best)
    return window.get("start_time"), window.get("end_time"), window.get("kind"), window.get("bar_count")


def build_review_structure_audit(entry_report: dict[str, Any], execution_bars: list[Bar],
                                 params: ChartReviewParams) -> dict[str, Any]:
    best = entry_report.get("best_entry") or {}
    bars = review_execution_window(execution_bars, params)
    direction = str(best.get("direction") or "")
    stop_price = best.get("stop_price")
    structure_start_time, structure_end_time, structure_kind, structure_bar_count = structure_times_from_entry(best)
    trigger_time = trigger_time_from_entry(best)
    audit: dict[str, Any] = {
        "audit_type": "review_window_structure_only",
        "review_only": True,
        "does_not_compute_outcome_or_pnl": True,
        "model": best.get("model"),
        "direction": direction or None,
        "entry_price": best.get("entry_price"),
        "stop_price": stop_price,
        "trigger_time": trigger_time,
        "structure_kind": structure_kind,
        "structure_window_start_time": structure_start_time,
        "structure_window_end_time": structure_end_time,
        "structure_window_bar_count": structure_bar_count,
        "status": "manual_review_required",
        "blockers": [],
        "manual_review": [],
    }
    if not best or direction not in {"long", "short"} or stop_price is None or not bars:
        audit["manual_review"].append("entry_or_stop_missing")
        return audit

    structure_start_index = bar_index_by_time(bars, structure_start_time)
    structure_end_index = bar_index_by_time(bars, structure_end_time)
    if structure_start_index is not None and structure_end_index is not None and structure_start_index <= structure_end_index:
        structure_bars = bars[structure_start_index:structure_end_index + 1]
        if direction == "short":
            protected = float(stop_price) > max(bar.high for bar in structure_bars)
            extreme = max(bar.high for bar in structure_bars)
        else:
            protected = float(stop_price) < min(bar.low for bar in structure_bars)
            extreme = min(bar.low for bar in structure_bars)
        audit["trigger_structure_stop_protected"] = protected
        audit["trigger_structure_extreme"] = extreme
        if not protected:
            audit["blockers"].append("planned_stop_not_beyond_trigger_structure")
    else:
        audit["manual_review"].append("structure_window_not_found_for_entry_model")

    trigger_index = bar_index_by_time(bars, trigger_time)
    if trigger_index is None:
        audit["manual_review"].append("trigger_time_not_found_in_review_window")
        return audit

    review_bars = bars[trigger_index + 1:]
    audit["post_trigger_review_bar_count"] = len(review_bars)
    if review_bars:
        if direction == "short":
            adverse_bar = max(review_bars, key=lambda bar: bar.high)
            adverse_extreme = adverse_bar.high
            crossed = adverse_extreme >= float(stop_price)
        else:
            adverse_bar = min(review_bars, key=lambda bar: bar.low)
            adverse_extreme = adverse_bar.low
            crossed = adverse_extreme <= float(stop_price)
        audit["post_trigger_adverse_extreme"] = adverse_extreme
        audit["post_trigger_adverse_extreme_time"] = bar_time(adverse_bar)
        audit["planned_stop_crossed_in_review_window"] = crossed
        if crossed:
            audit["blockers"].append("planned_stop_crossed_in_review_window")
    else:
        audit["planned_stop_crossed_in_review_window"] = False

    if audit["blockers"]:
        audit["status"] = "structural_conflict"
    elif audit["manual_review"]:
        audit["status"] = "manual_review_required"
    else:
        audit["status"] = "pass"
    return audit


def build_chart_review_packet(symbol: str, context_timeframe: str, execution_timeframe: str,
                              context_bars: list[Bar], execution_bars: list[Bar], levels: list[Level],
                              higher_timeframe: str, breakout_direction_arg: str,
                              manual_context: dict[str, Any] | None = None,
                              params: ChartReviewParams | None = None) -> dict[str, Any]:
    manual_context = manual_context or {}
    params = params or ChartReviewParams()
    review_levels = levels_with_drawn_candidates(levels, manual_context, context_bars)
    level_report = build_level_report(symbol, context_timeframe, higher_timeframe, context_bars[-1].close, review_levels)
    entry_report = build_entry_context(
        symbol,
        context_timeframe,
        execution_timeframe,
        context_bars,
        execution_bars,
        review_levels,
        breakout_direction_arg,
        EntryParams(execution_lookback_bars=params.execution_lookback_bars),
    )
    auto_breakout_failure_context = build_auto_breakout_failure_context(entry_report, execution_bars, execution_timeframe, params)
    auto_context = combine_auto_contexts(
        auto_breakout_failure_context,
        build_auto_market_mechanics_context(auto_breakout_failure_context),
        build_auto_formation_context(entry_report, execution_bars, execution_timeframe, params),
        build_auto_tail_bar_context(entry_report, execution_bars, execution_timeframe, params),
        build_auto_rebound_context(entry_report, execution_bars, execution_timeframe, params),
    )
    review_context = merge_auto_context(manual_context, auto_context)
    permission_report = build_permission_context(entry_report, review_context)
    checklist_matrix = build_checklist_matrix(permission_report, review_context)
    review_structure_audit = build_review_structure_audit(entry_report, execution_bars, params)
    audit_blockers = [
        {
            "item_id": f"REVIEW-AUDIT-{index:03d}",
            "text": "Review window shows a structural conflict with the planned stop zone",
            "status": "block",
            "evidence": blocker,
            "source": "review_window_structure_only",
            "source_rule_ids": ["CRD-009A-stop-placement"],
        }
        for index, blocker in enumerate(review_structure_audit.get("blockers", []), start=1)
    ]
    status_counts = checklist_matrix["summary"]["status_counts"]
    hard_gate = permission_report.get("hard_gate") or {}
    if hard_gate.get("status") == "reject" or status_counts.get("block", 0) or audit_blockers:
        review_status = "blocked_review_only"
    elif status_counts.get("manual_review", 0):
        review_status = "manual_review_required"
    else:
        review_status = "checklist_complete_review_only"
    return {
        "symbol": symbol,
        "detector": "layer6_chart_review_packet",
        "review_status": review_status,
        "execution_allowed": False,
        "runtime_signal_allowed": False,
        "order_generation_allowed": False,
        "pnl_computation_allowed": False,
        "paper_trading_allowed": False,
        "live_trading_allowed": False,
        "backtest_harness_allowed": False,
        "source_artifacts": SOURCE_ARTIFACTS,
        "supported_manual_context_keys": SUPPORTED_MANUAL_CONTEXT_KEYS,
        "timeframes": {
            "context": context_timeframe,
            "execution": execution_timeframe,
            "higher": higher_timeframe,
        },
        "chart_context": chart_context(manual_context),
        "auto_context": auto_context,
        "level_summary": level_report.get("summary"),
        "permission_summary": {
            "advisor_status": permission_report.get("advisor_status"),
            "hard_gate_status": hard_gate.get("status"),
            "hard_rejects": hard_gate.get("hard_rejects", []),
            "missing_inputs": hard_gate.get("missing_inputs", []),
            "best_entry_model": (permission_report.get("best_entry") or {}).get("model"),
            "best_entry_status": (permission_report.get("best_entry") or {}).get("status"),
        },
        "checklist_matrix": checklist_matrix,
        "manual_review_queue": checklist_matrix["manual_review_queue"] + permission_report.get("unresolved_checklist_items", []),
        "review_structure_audit": review_structure_audit,
        "blockers": checklist_matrix["blockers"] + audit_blockers,
        "layer_reports": {
            "levels": level_report,
            "entry": entry_report,
            "permission": permission_report,
        },
    }


def build_chart_review_packet_from_data_source(symbol: str, context_timeframe: str, execution_timeframe: str,
                                               start: str, end: str,
                                               higher_timeframe: str = "1w",
                                               breakout_direction_arg: str = "auto",
                                               manual_context: dict[str, Any] | None = None,
                                               params: ChartReviewParams | None = None) -> dict[str, Any]:
    context_bars = load_history(symbol, context_timeframe, start, end)
    if not context_bars:
        raise ValueError(f"No context data for {symbol} {context_timeframe} {start}..{end}")
    execution_bars = load_history(symbol, execution_timeframe, start, end)
    if not execution_bars:
        raise ValueError(f"No execution data for {symbol} {execution_timeframe} {start}..{end}")

    level_params = DiscoveryParams()
    higher_tf = higher_timeframe.strip()
    higher_levels: list[Level] | None = None
    if higher_tf and higher_tf != context_timeframe:
        higher_bars = load_history(symbol, higher_tf, start, end)
        higher_levels = discover_levels(higher_bars, level_params) if higher_bars else []
    levels = discover_levels(context_bars, level_params, higher_levels, higher_tf)
    return build_chart_review_packet(
        symbol,
        context_timeframe,
        execution_timeframe,
        context_bars,
        execution_bars,
        levels,
        higher_tf,
        breakout_direction_arg,
        manual_context,
        params,
    )


def ohlc_time_to_ms(value: Any) -> int:
    text = str(value).strip()
    if not text:
        raise ValueError("empty OHLC timestamp")
    if re.fullmatch(r"\d{13}", text):
        return int(text)
    if re.fullmatch(r"\d{10}", text):
        return int(text) * 1000
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.astimezone(timezone.utc).timestamp() * 1000)


def detector_bars_from_ohlc(ohlc_bars: list[Any]) -> list[Bar]:
    return [
        Bar(
            open_time=ohlc_time_to_ms(bar.open_time),
            open=float(bar.open),
            high=float(bar.high),
            low=float(bar.low),
            close=float(bar.close),
            volume=0.0 if bar.volume is None else float(bar.volume),
        )
        for bar in ohlc_bars
    ]


def build_kb_chart_review_packet_from_ohlc_bars(symbol: str, timeframe: str,
                                               ohlc_bars: list[Any],
                                               *,
                                               context_timeframe: str | None = None,
                                               context_ohlc_bars: list[Any] | None = None,
                                               higher_timeframe: str = "",
                                               higher_ohlc_bars: list[Any] | None = None,
                                               breakout_direction_arg: str = "auto",
                                               manual_context: dict[str, Any] | None = None,
                                               params: ChartReviewParams | None = None) -> dict[str, Any]:
    execution_bars = detector_bars_from_ohlc(ohlc_bars)
    if not execution_bars:
        raise ValueError("OHLC input has no bars")
    context_tf = context_timeframe or timeframe
    context_bars = detector_bars_from_ohlc(context_ohlc_bars) if context_ohlc_bars else execution_bars
    if not context_bars:
        raise ValueError("context OHLC input has no bars")
    level_params = DiscoveryParams()
    higher_bars = detector_bars_from_ohlc(higher_ohlc_bars) if higher_ohlc_bars else []
    higher_levels = discover_levels(higher_bars, level_params) if higher_bars else None
    higher_tf = higher_timeframe if higher_levels is not None else ""
    levels = discover_levels(context_bars, level_params, higher_levels, higher_tf)
    return build_chart_review_packet(
        symbol,
        context_tf,
        timeframe,
        context_bars,
        execution_bars,
        levels,
        higher_tf,
        breakout_direction_arg,
        manual_context or {},
        params or ChartReviewParams(),
    )


def compact_kb_packet(packet: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "ok",
        "detector": packet.get("detector"),
        "review_status": packet.get("review_status"),
        "timeframes": packet.get("timeframes"),
        "level_summary": packet.get("level_summary"),
        "permission_summary": packet.get("permission_summary"),
        "manual_review_queue": packet.get("manual_review_queue") or [],
        "blockers": packet.get("blockers") or [],
        "layer_reports": packet.get("layer_reports") or {},
    }


def build_live_kb_chart_review_packet(args: argparse.Namespace) -> dict[str, Any]:
    packet = build_light_chart_review_packet(args)
    packet["dataset_id"] = "live_kb_chart_review_packet_v1"
    root = ROOT
    ohlc_path = args.ohlc_file if args.ohlc_file.is_absolute() else root / args.ohlc_file
    try:
        ohlc_bars = load_ohlc_bars(ohlc_path)
        context_ohlc_bars = None
        context_path = getattr(args, "context_ohlc_file", None)
        if context_path is not None:
            context_path = context_path if context_path.is_absolute() else root / context_path
            context_ohlc_bars = load_ohlc_bars(context_path)
        higher_ohlc_bars = None
        higher_path = getattr(args, "higher_ohlc_file", None)
        if higher_path is not None:
            higher_path = higher_path if higher_path.is_absolute() else root / higher_path
            higher_ohlc_bars = load_ohlc_bars(higher_path)
        kb_packet = build_kb_chart_review_packet_from_ohlc_bars(
            args.instrument or args.symbol,
            args.timeframe,
            ohlc_bars,
            context_timeframe=getattr(args, "context_timeframe", None),
            context_ohlc_bars=context_ohlc_bars,
            higher_timeframe=getattr(args, "higher_timeframe", "") or "",
            higher_ohlc_bars=higher_ohlc_bars,
            breakout_direction_arg="auto",
            manual_context={},
        )
        packet["kb_analysis"] = compact_kb_packet(kb_packet)
    except Exception as exc:  # noqa: BLE001 - live charting falls back to light review
        packet["kb_analysis"] = {
            "status": "fallback_light",
            "error": f"{type(exc).__name__}: {exc}",
        }
    return packet


def read_jsonl_if_exists(path: Path) -> list[dict[str, Any]]:
    return read_jsonl(path) if path.exists() else []


def compact_examples(examples: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    return [
        {
            "candidate_id": item.get("candidate_id"),
            "score": item.get("score"),
            "review_class": item.get("review_class"),
            "lecture_title": item.get("lecture_title"),
            "timecode": item.get("timecode"),
            "taxonomy": item.get("taxonomy"),
            "summary": item.get("summary"),
            "scenario": item.get("scenario"),
        }
        for item in examples[:limit]
    ]


def derive_light_review_state(context: dict[str, Any], analyzer_report: dict[str, Any]) -> dict[str, Any]:
    ohlc = context.get("ohlc") or {}
    outcome = ohlc.get("descriptive_outcome") or {}
    analyzer_state = ((analyzer_report.get("verdict") or {}).get("state") or "").strip()
    missing_fields = (analyzer_report.get("trade_contract_completeness") or {}).get("missing_fields") or []
    verification = ohlc.get("ohlc_verification_status")

    if verification == "ohlc_window_loaded_but_entry_not_touched" or outcome.get("outcome") == "entry_not_reached":
        return {
            "state": "entry_not_confirmed",
            "reason": "OHLC window loaded, but the stated entry was not reached.",
        }
    if outcome.get("outcome") == "hit_target_first":
        return {
            "state": "post_entry_target_first",
            "reason": "Entry was reached and the target was touched before the stop inside the supplied OHLC window.",
        }
    if outcome.get("outcome") == "hit_stop_first":
        return {
            "state": "post_entry_stop_first",
            "reason": "Entry was reached and the stop was touched before the target inside the supplied OHLC window.",
        }
    if outcome.get("outcome") == "ambiguous_same_bar":
        return {
            "state": "manual_review_needed",
            "reason": "Stop and target were touched inside the same OHLC bar, so intrabar order is unknown.",
        }
    if outcome.get("outcome") == "none_reached":
        return {
            "state": "setup_observable",
            "reason": "Entry was reached, but neither stop nor target resolved inside the supplied OHLC window.",
        }
    if missing_fields:
        return {
            "state": "setup_incomplete",
            "reason": "Analyzer still has missing trade-contract evidence: " + ", ".join(missing_fields),
        }
    if analyzer_state == "possible_setup":
        return {
            "state": "setup_observable",
            "reason": "Analyzer hard gate is satisfied, but packet remains read-only and non-executable.",
        }
    return {
        "state": "manual_review_needed",
        "reason": f"Analyzer verdict is {analyzer_state or 'unknown'}; no descriptive outcome was attached.",
    }


def build_light_chart_review_packet(args: argparse.Namespace) -> dict[str, Any]:
    root = ROOT
    ohlc_path = args.ohlc_file if args.ohlc_file.is_absolute() else root / args.ohlc_file
    bars = load_ohlc_bars(ohlc_path)
    should_describe = (
        not args.no_describe_outcome
        and args.direction is not None
        and args.entry is not None
        and args.stop is not None
        and args.target is not None
    )
    adapter_args = argparse.Namespace(
        root=root,
        ohlc_file=ohlc_path,
        instrument=args.instrument or args.symbol,
        venue=args.venue,
        timeframe=args.timeframe,
        direction=args.direction,
        date_session=args.date_session,
        level=args.level,
        entry=args.entry,
        stop=args.stop,
        target=args.target,
        trigger=args.trigger,
        atr_period=args.atr_period,
        describe_outcome=should_describe,
    )
    context = build_ohlc_context(adapter_args, bars)
    rows = read_jsonl_if_exists(root / OBSERVATIONS)
    analyzer_report = analyze_situation(context, rows, top_k=max(1, args.top_k))
    review_state = derive_light_review_state(context, analyzer_report)
    return {
        "dataset_id": "light_chart_review_packet_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "review_state": review_state,
        "execution_allowed": False,
        "runtime_signal_allowed": False,
        "order_generation_allowed": False,
        "pnl_computation_allowed": False,
        "aggregate_winrate_allowed": False,
        "paper_trading_allowed": False,
        "live_trading_allowed": False,
        "backtest_harness_allowed": False,
        "inputs": {
            "ohlc_file": ohlc_path.as_posix(),
            "instrument": adapter_args.instrument,
            "venue": args.venue,
            "timeframe": args.timeframe,
            "direction": args.direction,
            "level": args.level,
            "entry": args.entry,
            "stop": args.stop,
            "target": args.target,
            "trigger": args.trigger,
            "describe_outcome": should_describe,
        },
        "context": context,
        "analyzer_summary": {
            "verdict": analyzer_report.get("verdict"),
            "trade_contract_completeness": analyzer_report.get("trade_contract_completeness"),
            "taxonomy_matches": analyzer_report.get("taxonomy_matches"),
            "supporting_examples": compact_examples(analyzer_report.get("supporting_examples") or []),
            "guardrail_examples": compact_examples(analyzer_report.get("guardrail_examples") or []),
        },
        "analyzer_report": analyzer_report,
        "safety_note": "Read-only chart review packet: factual context, retrieval, and single-window outcome description only; no PnL, no winrate, no orders, no backtest execution.",
    }


def render_example_lines(examples: list[dict[str, Any]]) -> list[str]:
    if not examples:
        return ["- None"]
    lines: list[str] = []
    for item in examples:
        title = compact(item.get("lecture_title"), 80)
        summary = compact(item.get("summary") or item.get("scenario"), 180)
        lines.append(f"- `{item.get('candidate_id')}` score=`{item.get('score')}` class=`{item.get('review_class')}` {title}: {summary}")
    return lines


def render_light_chart_review_markdown(packet: dict[str, Any]) -> str:
    context = packet["context"]
    ohlc = context["ohlc"]
    analyzer = packet["analyzer_summary"]
    completeness = analyzer["trade_contract_completeness"] or {}
    outcome = ohlc.get("descriptive_outcome") or {}
    missing = ", ".join(f"`{field}`" for field in completeness.get("missing_fields") or []) or "none"
    present = ", ".join(f"`{field}`" for field in completeness.get("present_fields") or []) or "none"
    lines = [
        "# Light Chart Review Packet",
        "",
        f"Generated: `{packet['generated_at']}`",
        "",
        f"Review state: **`{packet['review_state']['state']}`** - {packet['review_state']['reason']}",
        f"Analyzer verdict: **`{(analyzer['verdict'] or {}).get('state')}`** - {(analyzer['verdict'] or {}).get('reason')}",
        "",
        "## Situation",
        "",
        f"- Instrument: `{context.get('instrument', 'unknown')}`",
        f"- Venue: `{context.get('venue', 'unknown')}`",
        f"- Timeframe: `{context.get('timeframe', 'unknown')}`",
        f"- Direction: `{context.get('direction', 'unknown')}`",
        f"- Level / Entry / Stop / Target: `{context.get('level_area')}` / `{context.get('entry')}` / `{context.get('stop')}` / `{context.get('target')}`",
        f"- Trigger: `{context.get('trigger', 'unknown')}`",
        "",
        "## Trade Contract",
        "",
        f"- Present fields: {present}",
        f"- Missing fields: {missing}",
        f"- Hard gate satisfied: `{completeness.get('hard_gate_satisfied')}`",
        "",
        "## OHLC Checks",
        "",
        f"- Source: `{ohlc['source_path']}`",
        f"- Bars: `{ohlc['bar_count']}` from `{ohlc['start_time']}` to `{ohlc['end_time']}`",
        f"- ATR{ohlc['atr_period']}: `{ohlc['atr']}`",
        f"- Close vs level: `{ohlc['close_position_vs_level']}`",
        f"- Entry / Stop / Target touched: `{bool(ohlc['entry_touch'])}` / `{bool(ohlc['stop_touch'])}` / `{bool(ohlc['target_touch'])}`",
        f"- OHLC verification status: `{ohlc['ohlc_verification_status']}`",
        "",
    ]
    if outcome:
        lines.extend([
            "## Descriptive Outcome",
            "",
            f"- Outcome: `{outcome['outcome']}`",
            f"- Bars to resolution: `{outcome['bars_to_resolution']}`",
            f"- MFE / MAE: `{outcome['mfe_r']}R` / `{outcome['mae_r']}R`",
            f"- Entry time: `{outcome['entry_time']}`",
            f"- Resolution time: `{outcome['resolution_time']}`",
            "",
        ])
    lines.extend([
        "## Supporting Examples",
        "",
        *render_example_lines(analyzer.get("supporting_examples") or []),
        "",
        "## Guardrail Examples",
        "",
        *render_example_lines(analyzer.get("guardrail_examples") or []),
        "",
        "## Analyzer Input Text",
        "",
        context["text"],
        "",
        "Safety: read-only packet; no PnL, no aggregate winrate, no orders, no backtest execution.",
        "",
    ])
    return "\n".join(lines)


def write_light_packet(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")


def run_light_mode(args: argparse.Namespace) -> None:
    packet = build_light_chart_review_packet(args)
    output_path = args.packet_output or LIGHT_PACKET_DEFAULT_JSON
    output_path = output_path if output_path.is_absolute() else ROOT / output_path
    write_light_packet(output_path, json.dumps(packet, ensure_ascii=False, indent=2) + "\n")

    markdown_path = args.markdown_output
    if markdown_path is not None:
        markdown_path = markdown_path if markdown_path.is_absolute() else ROOT / markdown_path
        write_light_packet(markdown_path, render_light_chart_review_markdown(packet))

    print(json.dumps({
        "packet_output": output_path.as_posix(),
        "markdown_output": None if markdown_path is None else markdown_path.as_posix(),
        "review_state": packet["review_state"],
        "analyzer_verdict": (packet["analyzer_summary"].get("verdict") or {}).get("state"),
        "outcome": ((packet["context"].get("ohlc") or {}).get("descriptive_outcome") or {}).get("outcome"),
        "mfe_r": ((packet["context"].get("ohlc") or {}).get("descriptive_outcome") or {}).get("mfe_r"),
        "mae_r": ((packet["context"].get("ohlc") or {}).get("descriptive_outcome") or {}).get("mae_r"),
    }, ensure_ascii=False, indent=2))


def print_report(packet: dict[str, Any]) -> None:
    matrix = packet["checklist_matrix"]
    counts = matrix["summary"]["status_counts"]
    permission = packet["permission_summary"]
    print("=" * 78)
    print(f"CHART REVIEW PACKET - {packet['symbol']}")
    print("rules: signed canonical rulebook + 15 feature contracts + RSCD matrix")
    print("=" * 78)
    print(f"review_status: {packet['review_status']}")
    print("execution_allowed: false")
    print(f"checklist: pass={counts.get('pass', 0)} manual={counts.get('manual_review', 0)} block={counts.get('block', 0)} total={matrix['summary']['item_count']}")
    print(f"levels: {packet['level_summary']}")
    print(f"hard_gate: {permission.get('hard_gate_status')} advisor={permission.get('advisor_status')}")
    if permission.get("hard_rejects"):
        print(f"hard_rejects: {', '.join(permission['hard_rejects'])}")
    print(f"best_entry: model={permission.get('best_entry_model')} status={permission.get('best_entry_status')}")
    print("-" * 78)
    group_counts = matrix["summary"]["group_status_counts"]
    for checklist_id in sorted(group_counts):
        group = group_counts[checklist_id]
        print(f"{checklist_id}: pass={group.get('pass', 0)} manual={group.get('manual_review', 0)} block={group.get('block', 0)}")
    if packet["blockers"]:
        print("-" * 78)
        print("blockers:")
        for item in packet["blockers"][:12]:
            print(f"  {item['item_id']}: {item['text']} -- {item['evidence']}")
    if matrix["manual_review_queue"]:
        print("-" * 78)
        print("manual review queue:")
        for item in matrix["manual_review_queue"][:16]:
            print(f"  {item['item_id']}: {item['text']}")
    print("=" * 78)


def main() -> None:
    configure_stdio()
    ap = argparse.ArgumentParser(description="Layer 6: read-only chart review packet")
    ap.add_argument("--symbol")
    ap.add_argument("--context-interval", default="1d")
    ap.add_argument("--execution-interval", default="15m")
    ap.add_argument("--start", help="Start month in YYYY-MM format")
    ap.add_argument("--end", help="End month in YYYY-MM format")
    ap.add_argument("--higher-interval", default="1w")
    ap.add_argument("--breakout-direction", choices=["auto", "long", "short"], default="auto")
    ap.add_argument("--execution-lookback-bars", type=int, default=ChartReviewParams.execution_lookback_bars)
    ap.add_argument("--manual-context-json", help="Optional JSON object with chart/manual context")
    ap.add_argument("--screenshot", help="Optional chart screenshot path; stored as review context and optionally read by chart_vision")
    ap.add_argument("--vision-model", help="Optional OpenAI vision model override")
    ap.add_argument("--skip-vision", action="store_true", help="Attach screenshot ref but do not call the vision API")
    ap.add_argument("--output-format", choices=["text", "json"], default="text")
    ap.add_argument("--ohlc-file", type=Path, help="Light mode: OHLC CSV/JSON file for situation+analyzer+outcome packet")
    ap.add_argument("--instrument", help="Light mode instrument/ticker; defaults to --symbol when omitted")
    ap.add_argument("--venue", help="Light mode venue/exchange")
    ap.add_argument("--timeframe", help="Light mode OHLC timeframe")
    ap.add_argument("--direction", choices=["long", "short"], help="Light mode trade direction")
    ap.add_argument("--date-session", help="Light mode date/session label")
    ap.add_argument("--level", type=float, help="Light mode level/zone price")
    ap.add_argument("--entry", type=float, help="Light mode entry price")
    ap.add_argument("--stop", type=float, help="Light mode stop price")
    ap.add_argument("--target", type=float, help="Light mode target price")
    ap.add_argument("--trigger", help="Light mode trigger description")
    ap.add_argument("--atr-period", type=int, default=14, help="Light mode ATR period")
    ap.add_argument("--top-k", type=int, default=7, help="Light mode retrieved examples per side")
    ap.add_argument("--no-describe-outcome", action="store_true", help="Light mode: skip factual post-entry outcome description")
    ap.add_argument("--packet-output", type=Path, help="Light mode JSON packet output path")
    ap.add_argument("--markdown-output", type=Path, help="Light mode markdown packet output path")
    args = ap.parse_args()

    if args.ohlc_file:
        run_light_mode(args)
        return

    if not args.symbol or not args.start or not args.end:
        ap.error("heavy mode requires --symbol, --start, and --end; light mode requires --ohlc-file")

    manual_context = load_manual_context(args.manual_context_json)
    if args.screenshot:
        manual_context = enrich_manual_context_with_vision(
            manual_context,
            args.screenshot,
            model=args.vision_model,
            expected_symbol=args.symbol,
            expected_timeframes=[args.context_interval, args.execution_interval, args.higher_interval],
            run_vision=not args.skip_vision,
        )
    print(f"Loading {args.symbol} {args.context_interval}/{args.execution_interval} {args.start}..{args.end} ...", file=sys.stderr)
    try:
        packet = build_chart_review_packet_from_data_source(
            args.symbol,
            args.context_interval,
            args.execution_interval,
            args.start,
            args.end,
            args.higher_interval,
            args.breakout_direction,
            manual_context,
            ChartReviewParams(execution_lookback_bars=args.execution_lookback_bars),
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    if args.output_format == "json":
        json.dump(packet, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        print_report(packet)


if __name__ == "__main__":
    main()
