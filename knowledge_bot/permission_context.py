"""Layer 5: final permission / hard-gate context.

This layer is deliberately conservative. It does not discover a new setup and it
does not turn a passed checklist into an executable trade signal. It converts the
best Layer 4 entry candidate into the signed hard-gate validator input, then
keeps the remaining homework / discipline checks explicit for human review.

Source contracts:
  _knowledge_base/structured/consolidation/detector_spec_draft/specs/hard_gates_and_permission.md
  _knowledge_base/structured/consolidation/feature_contracts_validation/contracts/hard_gates_and_permission.md
  _knowledge_base/structured/consolidation/signed_canonical_rulebook/signed_canonical_rulebook.md
  _knowledge_base/structured/consolidation/updated_refined_checklist_retrieval/updated_refined_scenario_checklist_draft.md
  _knowledge_base/detector_specs/risk_stop_take_spec.md
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from detector_prototype import (
    MIN_TARGET_R,
    detect_breakout_failure,
    detect_rebound_model,
    validate_hard_gate,
    validate_market_mechanics,
    validate_tail_bars,
    validate_v_u_formation,
    validate_workflow_review,
)
from entry_context import EntryParams, build_entry_context
from level_discovery import DiscoveryParams, Level, discover_levels
from scn002_strict_kb_backtest import Bar, load_history


SOURCE_RULE_IDS = [
    "CRD-001-core-trade-framework",
    "CRD-010-no-trade-filters",
    "CRD-011-workflow-homework-review",
    "CRD-012A-market-participants",
    "CRD-012B-market-mechanics",
    "CRD-012C-psychology-discipline",
    "CRD-013-prerequisites-quality",
    "CRD-014-formations-momentum",
    "CRD-015-rebound-models",
    "CRD-004C-breakout-failure",
    "CRD-007A-global-local-trend",
    "CRD-007B-timeframe-workflow",
    "CRD-007C-context-conflicts",
    "CRD-008-entry-models",
    "CRD-009A-stop-placement",
    "CRD-009B-atr-room",
    "CRD-009C-target-exit",
    "CRD-009D-position-risk",
]

SOURCE_CHECKLIST_ID = "RSCD-000-hard-gates"

ADVISOR_STATUS_TEXT = {
    "blocked": "hard gate failed; review-only no-trade output",
    "needs_more_context": "level, direction, stop, target room, or scenario family is missing",
    "watchlist_only": "context has promise, but no executable Layer 4 trigger is allowed yet",
    "human_review_candidate": "machine gates pass, but human checklist review is still required",
}


@dataclass
class PermissionParams:
    execution_lookback_bars: int = EntryParams.execution_lookback_bars
    trend_swings: int = EntryParams.trend_swings
    global_lookback_bars: int = EntryParams.global_lookback_bars


def risk_validation(candidate: dict[str, Any] | None) -> dict[str, Any]:
    if not candidate:
        return {}
    risk = candidate.get("risk") or {}
    return risk.get("risk_validation") or {}


def room_to_target_r(candidate: dict[str, Any] | None) -> float | None:
    value = risk_validation(candidate).get("room_to_next_level_r")
    return float(value) if value is not None else None


def collect_hard_rejects(candidate: dict[str, Any] | None) -> list[str]:
    if not candidate:
        return []
    rejects: list[str] = []
    for key in ("entry_detector", "risk", "tbx_validation"):
        output = candidate.get(key) or {}
        rejects.extend(str(item) for item in output.get("hard_rejects", []))
    return sorted(set(rejects))


def string_items(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


def object_list(manual_context: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = manual_context.get(key, [])
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"manual_context.{key} must be a list")
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"manual_context.{key}[{index}] must be an object")
        rows.append(item)
    return rows


def checklist_answers(manual_context: dict[str, Any]) -> dict[str, Any]:
    value = manual_context.get("checklist_answers", {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("manual_context.checklist_answers must be an object")
    return value


def answer_status(manual_context: dict[str, Any], key: str, pass_text: str,
                  missing_text: str, fail_text: str | None = None) -> tuple[str, str]:
    answers = checklist_answers(manual_context)
    value = answers.get(key)
    if value is True:
        return "pass", pass_text
    if value is False:
        return "block", fail_text or missing_text
    return "manual_review", missing_text


def validate_optional_context(symbol: str | None, manual_context: dict[str, Any] | None) -> dict[str, list[dict[str, Any]]]:
    manual_context = manual_context or {}
    return {
        "market_mechanics": [
            validate_market_mechanics(raw, symbol) for raw in object_list(manual_context, "market_mechanics")
        ],
        "formations": [
            validate_v_u_formation(raw, symbol) for raw in object_list(manual_context, "formations")
        ],
        "tail_bars": [
            validate_tail_bars(raw, symbol) for raw in object_list(manual_context, "tail_bars")
        ],
        "breakout_failures": [
            detect_breakout_failure(raw, symbol) for raw in object_list(manual_context, "breakout_failures")
        ],
        "rebounds": [
            detect_rebound_model(raw, symbol) for raw in object_list(manual_context, "rebounds")
        ],
        "workflow_reviews": [
            validate_workflow_review(raw, symbol) for raw in object_list(manual_context, "workflow_reviews")
        ],
    }


def context_hard_rejects(context_validations: dict[str, list[dict[str, Any]]]) -> list[str]:
    rejects: list[str] = []
    for group_key, outputs in context_validations.items():
        for output in outputs:
            rejects.extend(f"{group_key}:{item}" for item in output.get("hard_rejects", []))
    return sorted(set(rejects))


def context_manual_review(context_validations: dict[str, list[dict[str, Any]]]) -> list[str]:
    items: set[str] = set()
    for group_key, outputs in context_validations.items():
        for output in outputs:
            for item in output.get("manual_review_needed", []):
                items.add(f"{group_key}:{item}")
    return sorted(items)


def allowed_scenario_family(family: str | None) -> str:
    value = str(family or "").lower()
    if value in {"breakout", "false_breakout", "rebound", "continuation"}:
        return value
    return ""


def build_hard_gate_input(entry_report: dict[str, Any], manual_context: dict[str, Any],
                          context_validations: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    level = entry_report.get("nearest_level") or {}
    scenario = entry_report.get("scenario") or {}
    best = entry_report.get("best_entry") or None
    candidate_rejects = collect_hard_rejects(best)

    no_trade_gates: list[str] = []
    if best and best.get("status") == "reject":
        no_trade_gates.extend(candidate_rejects or ["entry_candidate_rejected"])
    if level.get("kb_hard_rejects"):
        no_trade_gates.extend(str(item) for item in level.get("kb_hard_rejects") or [])
    no_trade_gates.extend(string_items(manual_context.get("no_trade_gates")))
    no_trade_gates.extend(context_hard_rejects(context_validations))

    tbx_rejects = (best or {}).get("tbx_validation", {}).get("hard_rejects", [])

    return {
        "scenario_family": allowed_scenario_family(scenario.get("family")),
        "direction": (best or {}).get("direction") or scenario.get("direction") or "unknown",
        "level_price": level.get("price"),
        "entry_price": (best or {}).get("entry_price"),
        "stop_price": (best or {}).get("stop_price"),
        "next_level_price": (best or {}).get("target_price"),
        "room_to_target_r": room_to_target_r(best),
        "has_level": bool(level) and level.get("kb_status") == "pass",
        "has_stop_before_entry": bool(best and best.get("stop_price") is not None),
        "conflicting_daily_execution": "entry_against_daily_scenario" in tbx_rejects,
        "no_trade_gates": sorted(set(no_trade_gates)),
        "discipline_violations": string_items(manual_context.get("discipline_violations")),
    }


def checklist_item(item_id: str, text: str, status: str, evidence: str,
                   source_rules: list[str], checklist_id: str = SOURCE_CHECKLIST_ID) -> dict[str, Any]:
    return {
        "item_id": item_id,
        "checklist_id": checklist_id,
        "text": text,
        "status": status,
        "evidence": evidence,
        "source_rule_ids": source_rules,
    }


def build_manual_checklist(entry_report: dict[str, Any], hard_gate: dict[str, Any],
                           manual_context: dict[str, Any],
                           context_validations: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    best = entry_report.get("best_entry") or None
    scenario = entry_report.get("scenario") or {}
    level = entry_report.get("nearest_level") or {}
    room = hard_gate.get("permission_features", {}).get("room_to_target_r")
    tbx = (best or {}).get("tbx_validation") or {}
    risk = (best or {}).get("risk") or {}
    tbx_rejects = tbx.get("hard_rejects", [])
    risk_status = risk.get("status")
    scenario_timing_status, scenario_timing_reason = answer_status(
        manual_context,
        "scenario_written_before_touch",
        "manual context confirms the plan was written before touch/breakout",
        "market data alone cannot prove homework timing or whether the plan was written before the move",
        "scenario was not written before the move",
    )
    factors_status, factors_reason = answer_status(
        manual_context,
        "factors_against_recorded",
        "manual context confirms pro/contra factors were written before entry",
        "Layer 1-4 expose evidence and rejects, but cannot prove the trader wrote both before entry",
        "scenario lacks written factors against",
    )
    invalidation_status, invalidation_reason = answer_status(
        manual_context,
        "invalidation_recorded",
        "manual context confirms invalidation was written before entry",
        "the adapter can expose stop/invalidation price, but cannot prove the plan was written as a rule",
        "scenario invalidation was not written before entry",
    )

    market_outputs = context_validations["market_mechanics"]
    formations = context_validations["formations"]
    tail_bars = context_validations["tail_bars"]
    breakout_failures = context_validations["breakout_failures"]
    rebounds = context_validations["rebounds"]
    workflow_reviews = context_validations["workflow_reviews"]
    discipline_violations = string_items(manual_context.get("discipline_violations"))
    discipline_status, discipline_reason = answer_status(
        manual_context,
        "no_discipline_violations",
        "manual context confirms no revenge trade, impulsive flip, or chase",
        "discipline state must be supplied explicitly",
        "manual context reports a discipline violation",
    )
    if discipline_violations:
        discipline_status = "block"
        discipline_reason = f"discipline_violations={discipline_violations}"

    rows = [
        checklist_item(
            "RSCD-000-001",
            "Scenario was written on a closed bar before level touch/breakout",
            scenario_timing_status,
            scenario_timing_reason,
            ["CRD-011-workflow-homework-review", "CRD-001-core-trade-framework", "CRD-010-no-trade-filters"],
        ),
        checklist_item(
            "RSCD-000-002",
            "Concrete working level exists and is explainable",
            "pass" if level and level.get("kb_status") == "pass" else "block",
            f"nearest_level={level.get('price')} kb_status={level.get('kb_status')}",
            ["CRD-002A-level-construction", "CRD-002B-level-strength", "CRD-002C-level-damage", "CRD-002D-level-context"],
        ),
        checklist_item(
            "RSCD-000-003",
            "Direction is separated from entry trigger",
            "pass" if allowed_scenario_family(scenario.get("family")) and scenario.get("direction") else "block",
            f"scenario_family={scenario.get('family')} direction={scenario.get('direction')} best_model={(best or {}).get('model')}",
            ["CRD-001-core-trade-framework", "CRD-010-no-trade-filters"],
        ),
        checklist_item(
            "RSCD-000-004",
            "Global/local trend context is resolved around the nearest level",
            "pass" if bool(scenario.get("valid")) else "manual_review",
            f"scenario_valid={scenario.get('valid')} approach={entry_report.get('approach_summary')}",
            ["CRD-007A-global-local-trend", "CRD-007B-timeframe-workflow", "CRD-007C-context-conflicts"],
        ),
        checklist_item(
            "RSCD-000-005",
            "Several pro-scenario factors and contradictions were written before entry",
            factors_status,
            factors_reason,
            ["CRD-011-workflow-homework-review", "CRD-001-core-trade-framework", "CRD-010-no-trade-filters"],
        ),
        checklist_item(
            "RSCD-000-006",
            "Executable H1/M15/M5 entry exists and does not contradict daily scenario",
            "pass" if best and best.get("status") == "trigger" and "entry_against_daily_scenario" not in tbx_rejects else "block",
            f"best_status={(best or {}).get('status')} tbx_rejects={tbx_rejects}",
            ["CRD-007B-timeframe-workflow", "CRD-007A-global-local-trend", "CRD-008-entry-models"],
        ),
        checklist_item(
            "RSCD-000-007",
            "Stop is known before entry and structurally justified",
            "pass" if best and best.get("stop_price") is not None and risk_status != "reject" else "block",
            f"entry={(best or {}).get('entry_price')} stop={(best or {}).get('stop_price')} risk_status={risk_status}",
            ["CRD-009A-stop-placement", "CRD-009B-atr-room", "CRD-009D-position-risk"],
        ),
        checklist_item(
            "RSCD-000-008",
            "At least 3R exists before the nearest target/level",
            "pass" if room is not None and float(room) >= MIN_TARGET_R else "block",
            f"room_to_target_r={room}",
            ["CRD-009A-stop-placement", "CRD-009B-atr-room", "CRD-009C-target-exit", "CRD-009D-position-risk"],
        ),
        checklist_item(
            "RSCD-000-009",
            "Scenario invalidation is written before taking the trade",
            invalidation_status,
            invalidation_reason,
            ["CRD-001-core-trade-framework", "CRD-010-no-trade-filters"],
        ),
    ]

    rows.append(checklist_item(
        "RSCD-013-001",
        "Participant pressure / fuel source is named",
        "pass" if market_outputs and all(row.get("status") != "reject" for row in market_outputs) else "manual_review" if not market_outputs else "block",
        "market_mechanics context validated" if market_outputs else "manual market_mechanics context not supplied",
        ["CRD-012A-market-participants", "CRD-012B-market-mechanics"],
        "RSCD-013-market_mechanics",
    ))
    rows.append(checklist_item(
        "RSCD-014-001",
        "No revenge trade, impulsive flip, or chase is present",
        discipline_status,
        discipline_reason,
        ["CRD-012C-psychology-discipline", "CRD-010-no-trade-filters"],
        "RSCD-014-psychology_discipline",
    ))
    rows.append(checklist_item(
        "RSCD-009-000",
        "V/U formation context, if relevant, is checked as modifier not standalone entry",
        "pass" if formations and all(row.get("status") != "reject" for row in formations) else "manual_review" if not formations else "block",
        "formation context validated" if formations else "formation context not supplied; leave V/U visual reading manual",
        ["CRD-014-formations-momentum", "CRD-013-prerequisites-quality"],
        "RSCD-009-v_u",
    ))
    rows.append(checklist_item(
        "RSCD-010-000",
        "Tail bars / two-sided limit context, if relevant, is checked near a level",
        "pass" if tail_bars and all(row.get("status") != "reject" for row in tail_bars) else "manual_review" if not tail_bars else "block",
        "tail-bar context validated" if tail_bars else "tail-bar context not supplied; leave two-sided limit reading manual",
        ["CRD-014-formations-momentum", "CRD-002A-level-construction", "CRD-002B-level-strength"],
        "RSCD-010-tail",
    ))
    breakout_failure_rejects = [item for output in breakout_failures for item in output.get("hard_rejects", [])]
    breakout_failure_warnings = [
        output for output in breakout_failures
        if output.get("status") == "warn" or output.get("reclassification_hint")
    ]
    rows.append(checklist_item(
        "FCD-007-000",
        "Breakout failure / failed fixation is checked before treating a breakout as continuation",
        "block" if breakout_failure_rejects else "manual_review" if breakout_failure_warnings else "pass" if breakout_failures else "manual_review",
        f"breakout_failure rejects={breakout_failure_rejects} warnings={len(breakout_failure_warnings)} supplied={len(breakout_failures)}",
        ["CRD-004C-breakout-failure", "CRD-004B-breakout-confirmation", "CRD-005-false-breakout"],
        "FCD-007-breakout_failure",
    ))
    if scenario.get("family") == "rebound" or rebounds:
        rows.append(checklist_item(
            "RSCD-015-000",
            "Rebound scenario has reaction/confirmation, stop, and room",
            "pass" if rebounds and all(row.get("status") != "reject" for row in rebounds) else "manual_review" if not rebounds else "block",
            "rebound context validated" if rebounds else "rebound context not supplied for rebound-family review",
            ["CRD-015-rebound-models", "CRD-002A-level-construction", "CRD-009A-stop-placement", "CRD-009B-atr-room"],
            "RSCD-015-rebound",
        ))
    rows.append(checklist_item(
        "FCD-015-000",
        "Workflow/review data quality keeps source refs and review queues explicit",
        "pass" if workflow_reviews and all(row.get("status") != "reject" for row in workflow_reviews) else "manual_review" if not workflow_reviews else "block",
        "workflow review context validated" if workflow_reviews else "workflow_review_data_quality context not supplied",
        ["CRD-011-workflow-homework-review", "CRD-001-core-trade-framework"],
        "FCD-015-workflow_review_data_quality",
    ))

    return rows


def advisor_status(entry_report: dict[str, Any], hard_gate: dict[str, Any], checklist: list[dict[str, Any]]) -> str:
    if hard_gate.get("status") == "reject" or any(item["status"] == "block" for item in checklist[:3]):
        return "blocked"
    if hard_gate.get("status") == "warn":
        return "needs_more_context"
    if hard_gate.get("missing_inputs"):
        return "needs_more_context"
    best = entry_report.get("best_entry") or None
    if not best or best.get("status") != "trigger":
        return "watchlist_only"
    return "human_review_candidate"


def build_permission_context(entry_report: dict[str, Any], manual_context: dict[str, Any] | None = None) -> dict[str, Any]:
    manual_context = manual_context or {}
    context_validations = validate_optional_context(entry_report.get("symbol"), manual_context)
    gate_input = build_hard_gate_input(entry_report, manual_context, context_validations)
    hard_gate = validate_hard_gate(gate_input, entry_report.get("symbol"))
    checklist = build_manual_checklist(entry_report, hard_gate, manual_context, context_validations)
    status = advisor_status(entry_report, hard_gate, checklist)
    unresolved = [item for item in checklist if item["status"] != "pass"]
    return {
        "symbol": entry_report.get("symbol"),
        "detector": "layer5_permission_context",
        "source_checklist_id": SOURCE_CHECKLIST_ID,
        "source_rule_ids": SOURCE_RULE_IDS,
        "advisor_status": status,
        "advisor_status_meaning": ADVISOR_STATUS_TEXT[status],
        "execution_allowed": False,
        "runtime_signal_allowed": False,
        "hard_gate_input": gate_input,
        "hard_gate": hard_gate,
        "optional_context_validations": context_validations,
        "optional_context_manual_review": context_manual_review(context_validations),
        "manual_checklist": checklist,
        "unresolved_checklist_items": unresolved,
        "entry_status": entry_report.get("status"),
        "best_entry": entry_report.get("best_entry"),
        "entry_context": entry_report,
    }


def print_report(report: dict[str, Any]) -> None:
    print("=" * 78)
    print(f"PERMISSION CONTEXT - {report['symbol']}")
    print("rules: hard_gates_and_permission + RSCD-000 + risk_stop_take")
    print("=" * 78)
    print(f"advisor_status: {report['advisor_status']} ({report['advisor_status_meaning']})")
    print("execution_allowed: false")
    hard_gate = report["hard_gate"]
    print(f"hard_gate: status={hard_gate['status']} direction={hard_gate['direction']}")
    if hard_gate.get("missing_inputs"):
        print(f"missing: {', '.join(hard_gate['missing_inputs'])}")
    if hard_gate.get("hard_rejects"):
        print(f"rejects: {', '.join(hard_gate['hard_rejects'])}")
    context_validations = report.get("optional_context_validations") or {}
    context_summary = {key: len(value) for key, value in context_validations.items() if value}
    print(f"optional_context: {context_summary if context_summary else 'not supplied'}")
    best = report.get("best_entry") or {}
    print("-" * 78)
    print(f"best_entry: model={best.get('model')} status={best.get('status')} direction={best.get('direction')}")
    print(f"entry={best.get('entry_price')} stop={best.get('stop_price')} target={best.get('target_price')}")
    print("-" * 78)
    print("checklist:")
    for item in report["manual_checklist"]:
        print(f"  {item['item_id']}: {item['status']} - {item['text']}")
    print("=" * 78)


def load_manual_context(path_arg: str | None) -> dict[str, Any]:
    if not path_arg:
        return {}
    path = Path(path_arg)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("manual context JSON must contain an object")
    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description="Layer 5: permission / hard-gate context")
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--context-interval", default="1d")
    ap.add_argument("--execution-interval", default="15m")
    ap.add_argument("--start", required=True, help="Start month in YYYY-MM format")
    ap.add_argument("--end", required=True, help="End month in YYYY-MM format")
    ap.add_argument("--higher-interval", default="1w")
    ap.add_argument("--breakout-direction", choices=["auto", "long", "short"], default="auto")
    ap.add_argument("--execution-lookback-bars", type=int, default=PermissionParams.execution_lookback_bars)
    ap.add_argument("--manual-context-json", help="Optional JSON object with checklist answers and supplied context detector inputs")
    ap.add_argument("--output-format", choices=["text", "json"], default="text")
    args = ap.parse_args()
    manual_context = load_manual_context(args.manual_context_json)

    print(f"Loading {args.symbol} {args.context_interval} {args.start}..{args.end} ...", file=sys.stderr)
    context_bars: list[Bar] = load_history(args.symbol, args.context_interval, args.start, args.end)
    if not context_bars:
        print("No context data.", file=sys.stderr)
        sys.exit(1)

    print(f"Loading {args.symbol} {args.execution_interval} {args.start}..{args.end} ...", file=sys.stderr)
    execution_bars: list[Bar] = load_history(args.symbol, args.execution_interval, args.start, args.end)
    if not execution_bars:
        print("No execution data.", file=sys.stderr)
        sys.exit(1)

    level_params = DiscoveryParams()
    higher_tf = args.higher_interval.strip()
    higher_levels: list[Level] | None = None
    if higher_tf and higher_tf != args.context_interval:
        print(f"Loading higher timeframe {args.symbol} {higher_tf} ...", file=sys.stderr)
        higher_bars = load_history(args.symbol, higher_tf, args.start, args.end)
        higher_levels = discover_levels(higher_bars, level_params) if higher_bars else []
    levels = discover_levels(context_bars, level_params, higher_levels, higher_tf)

    entry_report = build_entry_context(
        args.symbol,
        args.context_interval,
        args.execution_interval,
        context_bars,
        execution_bars,
        levels,
        args.breakout_direction,
        EntryParams(execution_lookback_bars=args.execution_lookback_bars),
    )
    report = build_permission_context(entry_report, manual_context)
    if args.output_format == "json":
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        print_report(report)


if __name__ == "__main__":
    main()