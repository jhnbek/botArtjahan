"""Casebook CLI: accumulate reviewed cases from chart review packets.

Subcommands (all read/append only):

* ``append``  - store a light chart-review packet as a frozen decision snapshot
* ``outcome`` - attach a factual post-decision outcome to an existing case
* ``label``   - attach a human review label (valid / invalid / uncertain)
* ``show``    - print the merged view of a case
* ``report``  - descriptive, non-promotable summary of the casebook

This tool never computes a winrate that feeds back into decisions, never
generates orders, and never runs a backtest. Decision snapshots, outcomes, and
labels are stored in separate append-only logs to prevent hindsight contamination.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import casebook_store as store

try:
    from situation_analyzer import configure_stdio
except ModuleNotFoundError:
    from knowledge_bot.situation_analyzer import configure_stdio


def taxonomy_ids(matches: Any) -> list[str]:
    if not isinstance(matches, list):
        return []
    return [str(item.get("id")) for item in matches if isinstance(item, dict) and item.get("id")]


def example_ids(examples: Any) -> list[str]:
    if not isinstance(examples, list):
        return []
    return [str(item.get("candidate_id")) for item in examples if isinstance(item, dict) and item.get("candidate_id")]


def snapshot_from_packet(packet: dict[str, Any]) -> dict[str, Any]:
    """Build a decision-time snapshot. Excludes any descriptive outcome on purpose."""
    inputs = packet.get("inputs") or {}
    context = packet.get("context") or {}
    ohlc = context.get("ohlc") or {}
    analyzer = packet.get("analyzer_summary") or {}
    completeness = analyzer.get("trade_contract_completeness") or {}
    verdict = analyzer.get("verdict") or {}
    review_state = packet.get("review_state") or {}
    return {
        "instrument": inputs.get("instrument") or context.get("instrument"),
        "venue": inputs.get("venue") or context.get("venue"),
        "timeframe": inputs.get("timeframe") or context.get("timeframe"),
        "direction": inputs.get("direction") or context.get("direction"),
        "level": inputs.get("level"),
        "entry": inputs.get("entry"),
        "stop": inputs.get("stop"),
        "target": inputs.get("target"),
        "trigger": inputs.get("trigger") or context.get("trigger"),
        "date_session": context.get("date_session"),
        "review_state": review_state.get("state"),
        "analyzer_verdict": verdict.get("state"),
        "hard_gate_satisfied": completeness.get("hard_gate_satisfied"),
        "missing_fields": completeness.get("missing_fields") or [],
        "taxonomy": taxonomy_ids(analyzer.get("taxonomy_matches")),
        "ohlc_source": ohlc.get("source_path"),
        "ohlc_window": {
            "start": ohlc.get("start_time"),
            "end": ohlc.get("end_time"),
            "bar_count": ohlc.get("bar_count"),
        },
        "decision_time": packet.get("generated_at"),
    }


def packet_outcome(packet: dict[str, Any]) -> dict[str, Any] | None:
    ohlc = (packet.get("context") or {}).get("ohlc") or {}
    outcome = ohlc.get("descriptive_outcome")
    return outcome if isinstance(outcome, dict) else None


def load_packet(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("packet file must contain a JSON object")
    return payload


def cmd_append(args: argparse.Namespace) -> int:
    packet_path = args.packet if args.packet.is_absolute() else store.ROOT / args.packet
    packet = load_packet(packet_path)
    snapshot = snapshot_from_packet(packet)
    analyzer = packet.get("analyzer_summary") or {}
    evidence = {
        "supporting_example_ids": example_ids(analyzer.get("supporting_examples")),
        "guardrail_example_ids": example_ids(analyzer.get("guardrail_examples")),
    }
    provenance = {
        "packet_path": packet_path.as_posix(),
        "dataset_id": packet.get("dataset_id"),
        "code_version": packet.get("dataset_id"),
    }
    read_only_flags = {
        key: packet.get(key)
        for key in (
            "execution_allowed",
            "runtime_signal_allowed",
            "order_generation_allowed",
            "pnl_computation_allowed",
            "aggregate_winrate_allowed",
            "paper_trading_allowed",
            "live_trading_allowed",
            "backtest_harness_allowed",
        )
        if key in packet
    }
    case, created = store.append_case(snapshot, args.source, args.mode, evidence, provenance, read_only_flags)

    outcome_recorded = None
    if args.record_packet_outcome and created:
        outcome = packet_outcome(packet)
        if outcome:
            outcome_recorded = store.append_outcome(
                case["case_id"],
                outcome.get("outcome"),
                mfe_r=outcome.get("mfe_r"),
                mae_r=outcome.get("mae_r"),
                bars_to_resolution=outcome.get("bars_to_resolution"),
                ohlc_source=outcome.get("source_path") or snapshot.get("ohlc_source"),
                resolution_time=outcome.get("resolution_time"),
                post_decision=None,
                provenance={"source": "same_packet_window", "note": "outcome computed from the same OHLC window as the decision"},
            )

    print(json.dumps({
        "case_id": case["case_id"],
        "created": created,
        "idempotency_key": case["idempotency_key"],
        "review_state": snapshot.get("review_state"),
        "analyzer_verdict": snapshot.get("analyzer_verdict"),
        "outcome_recorded": None if outcome_recorded is None else outcome_recorded["outcome"],
        "cases_path": store.CASES_PATH.as_posix(),
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_outcome(args: argparse.Namespace) -> int:
    if args.from_ohlc:
        if not (args.direction and args.entry is not None and args.stop is not None and args.target is not None):
            raise SystemExit("--from-ohlc requires --direction, --entry, --stop, and --target")
        ohlc_path = args.from_ohlc if args.from_ohlc.is_absolute() else store.ROOT / args.from_ohlc
        try:
            from ohlc_situation_adapter import load_bars
            from outcome_descriptor import describe_outcome
        except ModuleNotFoundError:
            from knowledge_bot.ohlc_situation_adapter import load_bars
            from knowledge_bot.outcome_descriptor import describe_outcome
        bars = load_bars(ohlc_path)
        report = describe_outcome(bars, args.direction, args.entry, args.stop, args.target)
        record = store.append_outcome(
            args.case_id,
            report["outcome"],
            mfe_r=report.get("mfe_r"),
            mae_r=report.get("mae_r"),
            bars_to_resolution=report.get("bars_to_resolution"),
            ohlc_source=ohlc_path.as_posix(),
            resolution_time=report.get("resolution_time"),
            post_decision=args.post_decision,
            provenance={"source": "outcome_descriptor", "note": "computed from a separately supplied OHLC window"},
        )
    else:
        if not args.outcome:
            raise SystemExit("provide either --from-ohlc (with prices) or --outcome")
        record = store.append_outcome(
            args.case_id,
            args.outcome,
            mfe_r=args.mfe_r,
            mae_r=args.mae_r,
            bars_to_resolution=args.bars_to_resolution,
            ohlc_source=args.ohlc_source,
            post_decision=args.post_decision,
            provenance={"source": "manual", "note": args.note or ""},
        )
    print(json.dumps({
        "case_id": record["case_id"],
        "outcome_id": record["outcome_id"],
        "outcome": record["outcome"],
        "mfe_r": record["mfe_r"],
        "mae_r": record["mae_r"],
        "outcomes_path": store.OUTCOMES_PATH.as_posix(),
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_label(args: argparse.Namespace) -> int:
    judged_blind = None
    if args.judged_blind_to_outcome:
        judged_blind = True
    elif args.judged_with_outcome_known:
        judged_blind = False
    record = store.append_label(
        args.case_id,
        args.label,
        args.labeler,
        args.rationale or "",
        decision_quality=args.decision_quality,
        market_behavior=args.market_behavior,
        judged_blind_to_outcome=judged_blind,
        premise_audit=args.premise_audit or "",
    )
    print(json.dumps({
        "case_id": record["case_id"],
        "label_id": record["label_id"],
        "label": record["label"],
        "decision_quality": record["decision_quality"],
        "market_behavior": record["market_behavior"],
        "learning_class": record["learning_class"],
        "judged_blind_to_outcome": record["judged_blind_to_outcome"],
        "blind_judgement_trustworthy": record["blind_judgement_trustworthy"],
        "labeler": record["labeler"],
        "labels_path": store.LABELS_PATH.as_posix(),
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    view = store.materialize_case(args.case_id)
    if view is None:
        raise SystemExit(f"unknown case_id: {args.case_id}")
    print(json.dumps(view, ensure_ascii=False, indent=2))
    return 0


def cmd_pending(args: argparse.Namespace) -> int:
    cases = store.pending_confirmation_cases()
    items = []
    for case in cases:
        items.append({
            "case_id": case.get("case_id"),
            "mode": case.get("mode"),
            "bot_analysis": store.bot_self_analysis(case),
            "question": "Бот сделал этот анализ. Он верный? Подтвердите (confirm --agree) или поправьте (confirm --correct-...).",
        })
        if args.limit and len(items) >= args.limit:
            break
    print(json.dumps({
        "pending_count": len(cases),
        "shown": len(items),
        "cases": items,
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_confirm(args: argparse.Namespace) -> int:
    if args.agree and args.correct:
        raise SystemExit("use either --agree or --correct, not both")
    corrections: dict[str, Any] = {}
    if args.correct_direction is not None:
        corrections["direction"] = args.correct_direction
    if args.correct_review_state is not None:
        corrections["review_state"] = args.correct_review_state
    if args.correct_verdict is not None:
        corrections["analyzer_verdict"] = args.correct_verdict
    if args.correct_level is not None:
        corrections["level"] = args.correct_level
    if args.correct_entry is not None:
        corrections["entry"] = args.correct_entry
    if args.correct_stop is not None:
        corrections["stop"] = args.correct_stop
    if args.correct_target is not None:
        corrections["target"] = args.correct_target
    if args.correct_trigger is not None:
        corrections["trigger"] = args.correct_trigger

    if args.agree:
        agrees = True
    elif args.correct or corrections:
        agrees = False
    else:
        raise SystemExit("provide --agree, or --correct with at least one --correct-* value")

    judged_blind = None
    if args.judged_blind_to_outcome:
        judged_blind = True
    elif args.judged_with_outcome_known:
        judged_blind = False

    record = store.append_confirmation(
        args.case_id,
        agrees,
        args.confirmed_by,
        corrections=corrections,
        market_behavior=args.market_behavior,
        judged_blind_to_outcome=judged_blind,
        rationale=args.rationale or "",
        premise_audit=args.premise_audit or "",
    )
    print(json.dumps({
        "case_id": record["case_id"],
        "label_id": record["label_id"],
        "agrees_with_bot": record["agrees_with_bot"],
        "bot_analysis": record["bot_analysis"],
        "corrections": record["corrections"],
        "decision_quality": record["decision_quality"],
        "market_behavior": record["market_behavior"],
        "learning_class": record["learning_class"],
        "blind_judgement_trustworthy": record["blind_judgement_trustworthy"],
        "confirmed_by": record["labeler"],
        "labels_path": store.LABELS_PATH.as_posix(),
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    cases = store.load_cases()
    outcomes = store.load_outcomes()
    labels = store.load_labels()

    def tally(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in rows:
            value = str(row.get(key))
            counts[value] = counts.get(value, 0) + 1
        return counts

    latest_label_by_case: dict[str, dict[str, Any]] = {}
    for row in labels:
        latest_label_by_case[row["case_id"]] = row
    label_counts: dict[str, int] = {}
    decision_quality_counts: dict[str, int] = {}
    market_behavior_counts: dict[str, int] = {}
    learning_class_counts: dict[str, int] = {}
    blind_trustworthy_count = 0
    hindsight_risk_count = 0
    agreed_with_bot_count = 0
    corrected_bot_count = 0
    for row in latest_label_by_case.values():
        label_counts[row.get("label")] = label_counts.get(row.get("label"), 0) + 1
        if row.get("decision_quality"):
            decision_quality_counts[row["decision_quality"]] = decision_quality_counts.get(row["decision_quality"], 0) + 1
        if row.get("market_behavior"):
            market_behavior_counts[row["market_behavior"]] = market_behavior_counts.get(row["market_behavior"], 0) + 1
        if row.get("learning_class"):
            learning_class_counts[row["learning_class"]] = learning_class_counts.get(row["learning_class"], 0) + 1
        if row.get("blind_judgement_trustworthy"):
            blind_trustworthy_count += 1
        else:
            hindsight_risk_count += 1
        if row.get("record_type") == "confirmation":
            if row.get("agrees_with_bot"):
                agreed_with_bot_count += 1
            else:
                corrected_bot_count += 1
    pending_cases = store.pending_confirmation_cases()

    report = {
        "dataset_id": "live_casebook_descriptive_report_v1",
        "non_promotable": True,
        "note": "Descriptive counts only. Not a winrate, not an edge, not a trade signal.",
        "case_count": len(cases),
        "outcome_event_count": len(outcomes),
        "label_event_count": len(labels),
        "labeled_case_count": len(latest_label_by_case),
        "human_confirmation": {
            "pending_confirmation_count": len(pending_cases),
            "agreed_with_bot_count": agreed_with_bot_count,
            "corrected_bot_count": corrected_bot_count,
            "note": "Cases the bot analysed that a human has not yet confirmed or corrected are pending; nothing is auto-confirmed.",
        },
        "cases_by_mode": tally(cases, "mode"),
        "cases_by_review_state": {
            state: count
            for state, count in sorted(
                tally([{"state": (c.get("decision_snapshot") or {}).get("review_state")} for c in cases], "state").items()
            )
        },
        "outcomes_by_type": tally(outcomes, "outcome"),
        "labels_by_value": label_counts,
        "decision_quality_counts": decision_quality_counts,
        "market_behavior_counts": market_behavior_counts,
        "learning_class_counts": learning_class_counts,
        "valid_scenario_adverse_market_count": learning_class_counts.get("valid_scenario_adverse_market", 0),
        "hindsight_guard": {
            "blind_judgement_trustworthy_count": blind_trustworthy_count,
            "hindsight_risk_count": hindsight_risk_count,
            "note": "decision_quality is only trusted when judged from the frozen snapshot, blind to the outcome; hindsight_risk labels were made knowing the result and should be re-reviewed blind.",
        },
        "learning_class_legend": {
            "confirmed_setup": "correct scenario, market followed - reinforce the pattern",
            "valid_scenario_adverse_market": "correct scenario, market moved against it - keep the decision positive, study the failure mode",
            "valid_scenario_unresolved": "correct scenario, market inconclusive",
            "lucky_or_misjudged": "flawed scenario that happened to work - do not reinforce the decision",
            "avoidable_error": "flawed scenario and adverse market - clear negative example",
            "flawed_scenario_unresolved": "flawed scenario, market inconclusive",
            "needs_more_review": "decision quality still uncertain",
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Live casebook accumulation (read/append only).")
    sub = parser.add_subparsers(dest="command", required=True)

    append = sub.add_parser("append", help="store a chart review packet as a decision snapshot")
    append.add_argument("--packet", type=Path, required=True)
    append.add_argument("--mode", choices=sorted(store.VALID_MODES), default="analysis")
    append.add_argument("--source", default="chart_review_packet_light")
    append.add_argument("--record-packet-outcome", action="store_true",
                        help="also record the packet's same-window outcome as an outcome event")
    append.set_defaults(func=cmd_append)

    outcome = sub.add_parser("outcome", help="attach a factual outcome to an existing case")
    outcome.add_argument("--case-id", required=True)
    outcome.add_argument("--from-ohlc", type=Path, help="OHLC file to derive the outcome from")
    outcome.add_argument("--direction", choices=["long", "short"])
    outcome.add_argument("--entry", type=float)
    outcome.add_argument("--stop", type=float)
    outcome.add_argument("--target", type=float)
    outcome.add_argument("--outcome", help="manual outcome label when not using --from-ohlc")
    outcome.add_argument("--mfe-r", type=float)
    outcome.add_argument("--mae-r", type=float)
    outcome.add_argument("--bars-to-resolution", type=int)
    outcome.add_argument("--ohlc-source")
    outcome.add_argument("--post-decision", action="store_true",
                        help="mark that the OHLC window is strictly after the decision time")
    outcome.add_argument("--note")
    outcome.set_defaults(func=cmd_outcome)

    label = sub.add_parser("label", help="attach a human review label")
    label.add_argument("--case-id", required=True)
    label.add_argument("--label", choices=sorted(store.VALID_LABELS), required=True)
    label.add_argument("--decision-quality", choices=sorted(store.VALID_DECISION_QUALITY),
                       help="was the scenario/reasoning correct at decision time; defaults from --label")
    label.add_argument("--market-behavior", choices=sorted(store.VALID_MARKET_BEHAVIOR),
                       help="did price follow the expected path; enables learning_class derivation")
    label.add_argument("--judged-blind-to-outcome", action="store_true",
                       help="decision_quality was judged from the frozen snapshot WITHOUT knowing the outcome (recommended)")
    label.add_argument("--judged-with-outcome-known", action="store_true",
                       help="decision_quality was judged AFTER seeing the outcome (flagged as hindsight risk)")
    label.add_argument("--premise-audit",
                       help="which decision-time premises actually held vs were misread, checked on the snapshot alone")
    label.add_argument("--labeler", required=True, help="human identifier; 'auto' is rejected")
    label.add_argument("--rationale")
    label.set_defaults(func=cmd_label)

    show = sub.add_parser("show", help="print the merged view of a case")
    show.add_argument("--case-id", required=True)
    show.set_defaults(func=cmd_show)

    pending = sub.add_parser("pending", help="list cases awaiting your confirmation of the bot's analysis")
    pending.add_argument("--limit", type=int, default=0, help="max cases to show (0 = all)")
    pending.set_defaults(func=cmd_pending)

    confirm = sub.add_parser("confirm", help="confirm or correct the bot's analysis of a case")
    confirm.add_argument("--case-id", required=True)
    confirm.add_argument("--confirmed-by", required=True, help="human identifier; 'auto' is rejected")
    confirm.add_argument("--agree", action="store_true", help="the bot's analysis is correct as-is")
    confirm.add_argument("--correct", action="store_true", help="the bot was wrong; supply corrected --correct-* values")
    confirm.add_argument("--correct-direction", choices=["long", "short"])
    confirm.add_argument("--correct-review-state")
    confirm.add_argument("--correct-verdict")
    confirm.add_argument("--correct-level", type=float)
    confirm.add_argument("--correct-entry", type=float)
    confirm.add_argument("--correct-stop", type=float)
    confirm.add_argument("--correct-target", type=float)
    confirm.add_argument("--correct-trigger")
    confirm.add_argument("--market-behavior", choices=sorted(store.VALID_MARKET_BEHAVIOR),
                         help="optional: did price follow the expected path (enables learning_class)")
    confirm.add_argument("--judged-blind-to-outcome", action="store_true",
                         help="you judged correctness from the snapshot WITHOUT knowing the outcome (recommended)")
    confirm.add_argument("--judged-with-outcome-known", action="store_true",
                         help="you judged after seeing the outcome (flagged as hindsight risk)")
    confirm.add_argument("--premise-audit", help="which decision-time premises actually held vs were misread")
    confirm.add_argument("--rationale")
    confirm.set_defaults(func=cmd_confirm)

    report = sub.add_parser("report", help="descriptive, non-promotable casebook summary")
    report.set_defaults(func=cmd_report)

    return parser.parse_args()


def main() -> int:
    configure_stdio()
    args = parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
