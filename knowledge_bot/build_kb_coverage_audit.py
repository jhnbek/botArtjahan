from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
VERSION = "kb_coverage_audit_v2"
OUTPUT_DIR = ROOT / "_knowledge_base" / "structured" / "consolidation" / "kb_coverage_audit"

SIGNED_RULEBOOK_PATH = ROOT / "_knowledge_base" / "structured" / "consolidation" / "signed_canonical_rulebook" / "signed_canonical_rulebook.md"
FEATURE_CONTRACTS_PATH = ROOT / "_knowledge_base" / "structured" / "consolidation" / "feature_contracts_validation" / "feature_contracts_validation.md"
RSCD_CHECKLIST_PATH = ROOT / "_knowledge_base" / "structured" / "consolidation" / "updated_refined_checklist_retrieval" / "updated_refined_scenario_checklist_draft.md"

SAFETY_FLAGS: dict[str, bool] = {
    "execution_allowed": False,
    "runtime_signal_allowed": False,
    "order_generation_allowed": False,
    "pnl_computation_allowed": False,
    "paper_trading_allowed": False,
    "live_trading_allowed": False,
    "backtest_harness_allowed": False,
}

EXPECTED_COUNTS = {
    "crd": 27,
    "fcd": 15,
    "rscd_checklists": 16,
    "rscd_items": 73,
}

STATUS_ORDER = {
    "automated": 0,
    "automated_partial": 1,
    "manual_context_supported": 2,
    "manual_review_only": 3,
    "represented_gap": 4,
}

USAGE_CHECK_ORDER = {
    "verified": 0,
    "partial": 1,
    "not_used": 2,
    "unverified": 3,
}

KNOWLEDGE_USAGE_ORDER = {
    "fully_used": 0,
    "partially_used": 1,
    "manual_context_supported": 2,
    "manual_review_only": 3,
    "not_used": 4,
    "unverified": 5,
}


CRD_COVERAGE: dict[str, dict[str, Any]] = {
    "CRD-001-core-trade-framework": {
        "status": "automated_partial",
        "layer": "Layer 5/6 permission gates and RSCD matrix",
        "implementation_refs": ["knowledge_bot/permission_context.py", "knowledge_bot/chart_review_packet.py"],
        "evidence": "core framework is represented as hard gates and scenario checklist; pre-scenario authorship remains manual",
        "remaining_gap": "written-before-touch, cancellation plan, and scenario-vs-result discipline need manual checklist answers",
    },
    "CRD-002A-level-construction": {
        "status": "automated",
        "layer": "Layer 1 level discovery",
        "implementation_refs": ["knowledge_bot/level_discovery.py"],
        "evidence": "levels are discovered from OHLC pivots/touches and exported with evidence metrics",
        "remaining_gap": "drawn screenshot levels are used only after OHLC validation; visual coordinate accuracy still depends on screenshot/vision quality",
    },
    "CRD-002B-level-strength": {
        "status": "automated",
        "layer": "Layer 1 + canonical detector",
        "implementation_refs": ["knowledge_bot/level_discovery.py", "knowledge_bot/detector_prototype.py"],
        "evidence": "level strength is validated through touch/reaction/quality features",
        "remaining_gap": "visual equality and trader-drawn refinements can still require manual review",
    },
    "CRD-002C-level-damage": {
        "status": "automated_partial",
        "layer": "Layer 1 level diagnostics",
        "implementation_refs": ["knowledge_bot/level_discovery.py", "knowledge_bot/chart_review_packet.py"],
        "evidence": "damage-like evidence is exposed via side switches, active-after-touch, and reaction metrics",
        "remaining_gap": "semantic interpretation of damaged vs still-working level may need manual confirmation",
    },
    "CRD-002D-level-context": {
        "status": "automated_partial",
        "layer": "Layer 1/2 level and trend context",
        "implementation_refs": ["knowledge_bot/level_discovery.py", "knowledge_bot/trend_direction.py"],
        "evidence": "nearest working level and local zone context are calculated",
        "remaining_gap": "multi-level story and trader-drawn level priority are not fully automated",
    },
    "CRD-003-bsu-bpu-limit-player": {
        "status": "automated",
        "layer": "Layer 4 entry context",
        "implementation_refs": ["knowledge_bot/entry_context.py", "knowledge_bot/detector_prototype.py"],
        "evidence": "BSU/BPU candidate is built and passed through canonical validation",
        "remaining_gap": "limit-player narrative remains review evidence, not execution permission",
    },
    "CRD-004A-breakout-preconditions": {
        "status": "automated",
        "layer": "Layer 3 approach context",
        "implementation_refs": ["knowledge_bot/approach_context.py", "knowledge_bot/detector_prototype.py"],
        "evidence": "breakout precondition features are calculated and mapped to RSCD-003",
        "remaining_gap": "visual compression/base quality can require manual review when OHLC evidence is weak",
    },
    "CRD-004B-breakout-confirmation": {
        "status": "automated",
        "layer": "Layer 4 fixation entry candidate",
        "implementation_refs": ["knowledge_bot/entry_context.py", "knowledge_bot/chart_review_packet.py"],
        "evidence": "fixation/return candidate validates breakout confirmation items",
        "remaining_gap": "confirmation stays review-only and does not become a signal",
    },
    "CRD-004C-breakout-failure": {
        "status": "automated_partial",
        "layer": "Layer 6 auto context + Layer 5 optional validators",
        "implementation_refs": ["knowledge_bot/chart_review_packet.py", "knowledge_bot/permission_context.py", "knowledge_bot/detector_prototype.py"],
        "evidence": "Layer 6 generates breakout-failure review context from execution OHLC and sends it through the canonical validator",
        "remaining_gap": "chasing, stop-widening, and final false-breakout reclassification still need visual/manual review",
    },
    "CRD-005-false-breakout": {
        "status": "automated",
        "layer": "Layer 3/4 false-breakout context and entry candidate",
        "implementation_refs": ["knowledge_bot/approach_context.py", "knowledge_bot/entry_context.py"],
        "evidence": "false-breakout return/reversal features are mapped to RSCD-004",
        "remaining_gap": "candidate remains advisory; no runtime signal is emitted",
    },
    "CRD-006-retest-room-atr": {
        "status": "automated",
        "layer": "Layer 3 retest and Layer 4 risk",
        "implementation_refs": ["knowledge_bot/approach_context.py", "knowledge_bot/entry_context.py"],
        "evidence": "near/far retest classification and ATR room are exposed",
        "remaining_gap": "manual review may be needed for ambiguous visual retests",
    },
    "CRD-007A-global-local-trend": {
        "status": "automated",
        "layer": "Layer 2 trend direction",
        "implementation_refs": ["knowledge_bot/trend_direction.py"],
        "evidence": "global trend, local zone, and nearest working level are calculated",
        "remaining_gap": "trend is OHLC-derived and can disagree with trader markup in borderline cases",
    },
    "CRD-007B-timeframe-workflow": {
        "status": "automated",
        "layer": "Layer 8 multi-timeframe review packet",
        "implementation_refs": ["knowledge_bot/multitimeframe_trade_review_packet.py", "knowledge_bot/validate_layer8_multitimeframe_packets.py"],
        "evidence": "higher/setup/execution timeframe decisions are separated in one read-only packet with alignment checks",
        "remaining_gap": "workflow remains review-only and may still require trader intent or chart markup in ambiguous contexts",
    },
    "CRD-007C-context-conflicts": {
        "status": "automated_partial",
        "layer": "Layer 5 hard gates",
        "implementation_refs": ["knowledge_bot/permission_context.py", "knowledge_bot/chart_review_packet.py"],
        "evidence": "context conflicts surface as blockers/manual queue items",
        "remaining_gap": "human scenario intent is still needed for some conflict judgments",
    },
    "CRD-008-entry-models": {
        "status": "automated",
        "layer": "Layer 4 entry context",
        "implementation_refs": ["knowledge_bot/entry_context.py"],
        "evidence": "fixation, BSU/BPU, impulse, and false-breakout entry candidates are generated and ranked for review",
        "remaining_gap": "entry models remain read-only candidate reviews",
    },
    "CRD-009A-stop-placement": {
        "status": "automated_partial",
        "layer": "Layer 4 risk validation",
        "implementation_refs": ["knowledge_bot/entry_context.py", "knowledge_bot/detector_prototype.py"],
        "evidence": "technical stop features are validated for best entry candidate",
        "remaining_gap": "final stop rationale and later stop movement plan need manual confirmation",
    },
    "CRD-009B-atr-room": {
        "status": "automated",
        "layer": "Layer 4 risk validation",
        "implementation_refs": ["knowledge_bot/entry_context.py", "knowledge_bot/detector_prototype.py"],
        "evidence": "ATR room and stop-distance checks are calculated",
        "remaining_gap": "ATR checks are only review evidence, not permission to trade",
    },
    "CRD-009C-target-exit": {
        "status": "automated_partial",
        "layer": "Layer 4/6 risk and checklist matrix",
        "implementation_refs": ["knowledge_bot/entry_context.py", "knowledge_bot/chart_review_packet.py"],
        "evidence": "room-to-next-level R is checked for 3R+ requirement",
        "remaining_gap": "dynamic exit management and post-entry target handling are not automated",
    },
    "CRD-009D-position-risk": {
        "status": "manual_review_only",
        "layer": "Layer 6 checklist matrix",
        "implementation_refs": ["knowledge_bot/chart_review_packet.py"],
        "evidence": "risk-size discipline is represented in RSCD manual answers",
        "remaining_gap": "account size, risk percent, and position sizing are intentionally not computed",
    },
    "CRD-010-no-trade-filters": {
        "status": "automated_partial",
        "layer": "Layer 5 hard gates",
        "implementation_refs": ["knowledge_bot/permission_context.py"],
        "evidence": "no-trade filters can block review status through hard gates and supplied manual context",
        "remaining_gap": "news/session/trader-state filters need supplied context",
    },
    "CRD-011-workflow-homework-review": {
        "status": "manual_review_only",
        "layer": "Layer 5/6 manual checklist",
        "implementation_refs": ["knowledge_bot/permission_context.py", "knowledge_bot/chart_review_packet.py"],
        "evidence": "workflow/homework questions are preserved in RSCD-000/001/012",
        "remaining_gap": "journal chronology, screenshot-before-scenario, and result-in-R need human/journal evidence",
    },
    "CRD-012A-market-participants": {
        "status": "automated_partial",
        "layer": "Layer 6 auto context + Layer 5 optional validators",
        "implementation_refs": ["knowledge_bot/chart_review_packet.py", "knowledge_bot/permission_context.py", "knowledge_bot/detector_prototype.py"],
        "evidence": "failed breakout context can derive trapped-side pressure; participant intent remains review evidence",
        "remaining_gap": "large-player intent and non-breakout participant reasoning remain manual",
    },
    "CRD-012B-market-mechanics": {
        "status": "automated_partial",
        "layer": "Layer 6 auto context + Layer 5 optional validators",
        "implementation_refs": ["knowledge_bot/chart_review_packet.py", "knowledge_bot/permission_context.py", "knowledge_bot/detector_prototype.py"],
        "evidence": "Layer 6 derives market-mechanics context from failed breakout trapped-side evidence",
        "remaining_gap": "mechanics outside failed breakout/reclaim context remain manual",
    },
    "CRD-012C-psychology-discipline": {
        "status": "manual_review_only",
        "layer": "Layer 5/6 manual checklist",
        "implementation_refs": ["knowledge_bot/permission_context.py", "knowledge_bot/chart_review_packet.py"],
        "evidence": "discipline violations and psychology checks are represented as manual gates",
        "remaining_gap": "psychological state cannot be inferred from market bars",
    },
    "CRD-013-prerequisites-quality": {
        "status": "automated_partial",
        "layer": "Layer 3/5 precondition checks",
        "implementation_refs": ["knowledge_bot/approach_context.py", "knowledge_bot/permission_context.py"],
        "evidence": "setup prerequisites are checked across approach features and hard gates",
        "remaining_gap": "qualitative setup cleanliness remains manual when machine evidence is borderline",
    },
    "CRD-014-formations-momentum": {
        "status": "automated_partial",
        "layer": "Layer 6 auto tail-bar context + Layer 5 optional validators",
        "implementation_refs": ["knowledge_bot/chart_review_packet.py", "knowledge_bot/permission_context.py", "knowledge_bot/detector_prototype.py"],
        "evidence": "Layer 6 generates conservative V-formation and tail-bar review context from OHLC near a working level; rounded U formations remain supplied context",
        "remaining_gap": "automatic rounded U formation extraction is not implemented",
    },
    "CRD-015-rebound-models": {
        "status": "automated_partial",
        "layer": "Layer 6 auto context + Layer 5 optional validators",
        "implementation_refs": ["knowledge_bot/chart_review_packet.py", "knowledge_bot/permission_context.py", "knowledge_bot/detector_prototype.py"],
        "evidence": "Layer 6 generates conservative rebound review context from compact OHLC reaction to a working level",
        "remaining_gap": "rebound is still a review context, not an automatically promoted standalone scenario family",
    },
}

FCD_COVERAGE: dict[str, dict[str, Any]] = {
    "FCD-001": {"status": "automated", "layer": "Layer 5", "implementation_refs": ["knowledge_bot/permission_context.py"], "evidence": "hard gates and permission are built from entry context plus manual gates", "remaining_gap": "manual-only gates still require supplied answers"},
    "FCD-002": {"status": "automated", "layer": "Layer 1/6", "implementation_refs": ["knowledge_bot/level_discovery.py", "knowledge_bot/chart_review_packet.py"], "evidence": "level strength features are computed and validated, including supplied drawn levels after OHLC validation", "remaining_gap": "visual coordinate accuracy still depends on screenshot/vision quality"},
    "FCD-003": {"status": "automated", "layer": "Layer 8", "implementation_refs": ["knowledge_bot/multitimeframe_trade_review_packet.py", "knowledge_bot/validate_layer8_multitimeframe_packets.py"], "evidence": "higher/setup/execution timeframe decisions and alignment blockers are generated in one read-only packet", "remaining_gap": "workflow remains review-only and may need trader intent for ambiguous conflicts"},
    "FCD-004": {"status": "automated_partial", "layer": "Layer 6/5", "implementation_refs": ["knowledge_bot/chart_review_packet.py", "knowledge_bot/permission_context.py", "knowledge_bot/detector_prototype.py"], "evidence": "market mechanics can be generated from failed breakout trapped-side context or supplied manually", "remaining_gap": "non-breakout mechanics remain manual"},
    "FCD-005": {"status": "automated", "layer": "Layer 3", "implementation_refs": ["knowledge_bot/approach_context.py"], "evidence": "breakout preconditions are detected from approach features", "remaining_gap": "ambiguous bases can require manual review"},
    "FCD-006": {"status": "automated", "layer": "Layer 4", "implementation_refs": ["knowledge_bot/entry_context.py"], "evidence": "fixation/return confirmation candidate is built", "remaining_gap": "review-only, no signal"},
    "FCD-007": {"status": "automated_partial", "layer": "Layer 6/5", "implementation_refs": ["knowledge_bot/chart_review_packet.py", "knowledge_bot/permission_context.py", "knowledge_bot/detector_prototype.py"], "evidence": "Layer 6 auto-generates breakout failure context from execution OHLC and Layer 5 validates it", "remaining_gap": "intent violations such as chasing after failure or stop widening still require manual evidence"},
    "FCD-008": {"status": "automated", "layer": "Layer 3/4", "implementation_refs": ["knowledge_bot/approach_context.py", "knowledge_bot/entry_context.py"], "evidence": "false-breakout reversal context and entry candidate are calculated", "remaining_gap": "review-only, no signal"},
    "FCD-009": {"status": "automated", "layer": "Layer 3/4", "implementation_refs": ["knowledge_bot/approach_context.py", "knowledge_bot/entry_context.py"], "evidence": "retest and ATR room checks are exposed", "remaining_gap": "visual ambiguity stays manual"},
    "FCD-010": {"status": "automated", "layer": "Layer 4", "implementation_refs": ["knowledge_bot/entry_context.py"], "evidence": "BSU/BPU limit-player candidate is validated", "remaining_gap": "limit-player story remains review evidence"},
    "FCD-011": {"status": "automated", "layer": "Layer 4", "implementation_refs": ["knowledge_bot/entry_context.py"], "evidence": "TBX candidates are validated against canonical contract", "remaining_gap": "no order generation"},
    "FCD-012": {"status": "automated_partial", "layer": "Layer 4/6", "implementation_refs": ["knowledge_bot/entry_context.py", "knowledge_bot/chart_review_packet.py"], "evidence": "technical stop and 3R room are checked", "remaining_gap": "position sizing and post-entry management are manual/not computed"},
    "FCD-013": {"status": "automated_partial", "layer": "Layer 6/5", "implementation_refs": ["knowledge_bot/chart_review_packet.py", "knowledge_bot/permission_context.py", "knowledge_bot/detector_prototype.py"], "evidence": "Layer 6 auto-generates V-formation and tail-bar context near a working level; rounded U formations remain supplied context", "remaining_gap": "no automatic rounded U extraction"},
    "FCD-014": {"status": "automated_partial", "layer": "Layer 6/5", "implementation_refs": ["knowledge_bot/chart_review_packet.py", "knowledge_bot/permission_context.py", "knowledge_bot/detector_prototype.py"], "evidence": "Layer 6 auto-generates rebound context from compact reaction to a working level and Layer 5 validates it", "remaining_gap": "not automatically promoted into a standalone scenario family"},
    "FCD-015": {"status": "manual_context_supported", "layer": "Layer 5/6", "implementation_refs": ["knowledge_bot/permission_context.py", "knowledge_bot/chart_review_packet.py"], "evidence": "workflow review can be supplied and checklist answers are preserved", "remaining_gap": "journal/data-quality facts need external evidence"},
}

RSCD_GROUP_COVERAGE: dict[str, dict[str, Any]] = {
    "RSCD-000-hard-gates": {"status": "automated_partial", "layer": "Layer 5", "implementation_refs": ["knowledge_bot/permission_context.py"], "evidence": "hard gates are built, with manual chronology/risk-plan items preserved", "remaining_gap": "pre-scenario authorship and cancellation plan need manual answers"},
    "RSCD-001-scenario": {"status": "manual_context_supported", "layer": "Layer 6", "implementation_refs": ["knowledge_bot/chart_review_packet.py"], "evidence": "scenario completeness is partly inferred; screenshot/opposite-entry/results need manual answers", "remaining_gap": "journal/screenshot timing not automatic"},
    "RSCD-002-trend": {"status": "automated", "layer": "Layer 2/6", "implementation_refs": ["knowledge_bot/trend_direction.py", "knowledge_bot/chart_review_packet.py"], "evidence": "global/local trend items are linked to layer outputs", "remaining_gap": "borderline visual trend still needs review"},
    "RSCD-003-breakout": {"status": "automated", "layer": "Layer 3/6", "implementation_refs": ["knowledge_bot/approach_context.py", "knowledge_bot/chart_review_packet.py"], "evidence": "breakout items are linked to approach detector outputs", "remaining_gap": "weak OHLC evidence remains manual"},
    "RSCD-004-false_breakout": {"status": "automated", "layer": "Layer 3/6", "implementation_refs": ["knowledge_bot/approach_context.py", "knowledge_bot/chart_review_packet.py"], "evidence": "false-breakout items are linked to detector outputs", "remaining_gap": "review-only, no signal"},
    "RSCD-005-retest": {"status": "automated", "layer": "Layer 3/6", "implementation_refs": ["knowledge_bot/approach_context.py", "knowledge_bot/chart_review_packet.py"], "evidence": "near/far retest classification is linked", "remaining_gap": "visual ambiguity remains manual"},
    "RSCD-006-fixation": {"status": "automated", "layer": "Layer 4/6", "implementation_refs": ["knowledge_bot/entry_context.py", "knowledge_bot/chart_review_packet.py"], "evidence": "fixation return candidate drives checklist status", "remaining_gap": "candidate only"},
    "RSCD-007-bsu_bpu": {"status": "automated", "layer": "Layer 4/6", "implementation_refs": ["knowledge_bot/entry_context.py", "knowledge_bot/chart_review_packet.py"], "evidence": "BSU/BPU candidate drives checklist status", "remaining_gap": "candidate only"},
    "RSCD-008-tbx": {"status": "automated", "layer": "Layer 4/6", "implementation_refs": ["knowledge_bot/entry_context.py", "knowledge_bot/chart_review_packet.py"], "evidence": "best entry TBX validation drives checklist status", "remaining_gap": "no order generation"},
    "RSCD-009-v_u": {"status": "automated_partial", "layer": "Layer 6/5", "implementation_refs": ["knowledge_bot/chart_review_packet.py", "knowledge_bot/permission_context.py", "knowledge_bot/detector_prototype.py"], "evidence": "V-formation validator output is mapped from generated or supplied context", "remaining_gap": "rounded U formations and visual quality remain review context"},
    "RSCD-010-tail": {"status": "automated_partial", "layer": "Layer 6/5", "implementation_refs": ["knowledge_bot/chart_review_packet.py", "knowledge_bot/permission_context.py", "knowledge_bot/detector_prototype.py"], "evidence": "tail-bar validator output is mapped from generated or supplied context", "remaining_gap": "two-sided tail context remains review evidence, not a standalone entry trigger"},
    "RSCD-011-risk": {"status": "automated_partial", "layer": "Layer 4/6", "implementation_refs": ["knowledge_bot/entry_context.py", "knowledge_bot/chart_review_packet.py"], "evidence": "stop and 3R room are checked; stop-management plan remains manual", "remaining_gap": "position sizing/post-entry management not computed"},
    "RSCD-012-review": {"status": "manual_review_only", "layer": "Layer 6", "implementation_refs": ["knowledge_bot/chart_review_packet.py"], "evidence": "review workflow items are preserved as manual answers", "remaining_gap": "requires journal evidence"},
    "RSCD-013-market_mechanics": {"status": "automated_partial", "layer": "Layer 6/5", "implementation_refs": ["knowledge_bot/chart_review_packet.py", "knowledge_bot/permission_context.py", "knowledge_bot/detector_prototype.py"], "evidence": "market mechanics validator output is mapped from generated failed-breakout context or supplied context", "remaining_gap": "large-player intent and non-breakout mechanics remain manual"},
    "RSCD-014-psychology_discipline": {"status": "manual_review_only", "layer": "Layer 5/6", "implementation_refs": ["knowledge_bot/permission_context.py", "knowledge_bot/chart_review_packet.py"], "evidence": "discipline checks are preserved as manual gates", "remaining_gap": "requires trader/journal input"},
    "RSCD-015-rebound": {"status": "automated_partial", "layer": "Layer 6/5", "implementation_refs": ["knowledge_bot/chart_review_packet.py", "knowledge_bot/permission_context.py"], "evidence": "rebound validator output is mapped from generated or supplied context", "remaining_gap": "full rebound scenario promotion still requires visual/manual confirmation"},
}

EXPECTED_MARKERS_BY_ARTIFACT: dict[str, list[str]] = {
    "CRD-001-core-trade-framework": ["validate_hard_gate(", "build_checklist_matrix", "RSCD-000-001"],
    "CRD-002A-level-construction": ["def discover_levels(", "def level_evidence("],
    "CRD-002B-level-strength": ["validate_level_strength("],
    "CRD-002C-level-damage": ["close_side_switches", "active_after_last_touch"],
    "CRD-002D-level-context": ["nearest_working_level"],
    "CRD-003-bsu-bpu-limit-player": ["detect_bsu_bpu("],
    "CRD-004A-breakout-preconditions": ["detect_breakout_preconditions("],
    "CRD-004B-breakout-confirmation": ["detect_fixation("],
    "CRD-004C-breakout-failure": ["build_auto_breakout_failure_context", "detect_breakout_failure("],
    "CRD-005-false-breakout": ["detect_false_breakout_reversal("],
    "CRD-006-retest-room-atr": ["detect_retest(", "room_to_target_r"],
    "CRD-007A-global-local-trend": ["validate_trend_context("],
    "CRD-007B-timeframe-workflow": ["build_multitimeframe_trade_review_packet", "timeframe_decisions", "alignment_summary"],
    "CRD-007C-context-conflicts": ["conflicting_daily_execution", "hard_gate"],
    "CRD-008-entry-models": ["build_fixation_candidate", "build_bsu_bpu_candidate", "build_false_breakout_entry_candidate"],
    "CRD-009A-stop-placement": ["validate_risk(", "calculated_stop_abs"],
    "CRD-009B-atr-room": ["technical_stop_atr", "CALCULATED_STOP_ATR"],
    "CRD-009C-target-exit": ["room_to_next_level_r"],
    "CRD-009D-position-risk": ["risk_size_matches_plan"],
    "CRD-010-no-trade-filters": ["no_trade_gates"],
    "CRD-011-workflow-homework-review": ["screenshot_before_scenario", "validate_workflow_review("],
    "CRD-012A-market-participants": ["build_auto_market_mechanics_context", "participant_pain_recorded"],
    "CRD-012B-market-mechanics": ["build_auto_market_mechanics_context", "validate_market_mechanics("],
    "CRD-012C-psychology-discipline": ["discipline_violations"],
    "CRD-013-prerequisites-quality": ["build_hard_gate_input", "detect_breakout_preconditions("],
    "CRD-014-formations-momentum": ["build_auto_formation_context", "build_auto_tail_bar_context", "validate_v_u_formation(", "validate_tail_bars("],
    "CRD-015-rebound-models": ["build_auto_rebound_context", "detect_rebound_model("],
    "FCD-001": ["validate_hard_gate("],
    "FCD-002": ["build_drawn_level_candidate", "validate_level_strength("],
    "FCD-003": ["build_multitimeframe_trade_review_packet", "build_alignment_summary"],
    "FCD-004": ["build_auto_market_mechanics_context", "validate_market_mechanics("],
    "FCD-005": ["detect_breakout_preconditions("],
    "FCD-006": ["detect_fixation("],
    "FCD-007": ["build_auto_breakout_failure_context", "detect_breakout_failure("],
    "FCD-008": ["detect_false_breakout_reversal("],
    "FCD-009": ["detect_retest("],
    "FCD-010": ["detect_bsu_bpu("],
    "FCD-011": ["validate_tbx_entry_model("],
    "FCD-012": ["validate_risk("],
    "FCD-013": ["build_auto_formation_context", "build_auto_tail_bar_context", "validate_v_u_formation(", "validate_tail_bars("],
    "FCD-014": ["build_auto_rebound_context", "detect_rebound_model("],
    "FCD-015": ["validate_workflow_review("],
    "RSCD-000-hard-gates": ["RSCD-000-001", "RSCD-000-009"],
    "RSCD-001-scenario": ["RSCD-001-001", "screenshot_before_scenario"],
    "RSCD-002-trend": ["RSCD-002-001", "global_trend"],
    "RSCD-003-breakout": ["RSCD-003-001", "breakout_features"],
    "RSCD-004-false_breakout": ["RSCD-004-001", "false_breakout_features"],
    "RSCD-005-retest": ["RSCD-005-001", "retest_features"],
    "RSCD-006-fixation": ["RSCD-006-001", "fixation_return"],
    "RSCD-007-bsu_bpu": ["RSCD-007-001", "bsu_bpu_limit"],
    "RSCD-008-tbx": ["RSCD-008-001", "tbx_validation"],
    "RSCD-009-v_u": ["RSCD-009-001", "build_auto_formation_context", "formations"],
    "RSCD-010-tail": ["RSCD-010-001", "build_auto_tail_bar_context", "tail_bars"],
    "RSCD-011-risk": ["RSCD-011-001", "room_to_next_level_r"],
    "RSCD-012-review": ["RSCD-012-001", "result_recorded_in_r"],
    "RSCD-013-market_mechanics": ["RSCD-013-001", "build_auto_market_mechanics_context", "market_mechanics"],
    "RSCD-014-psychology_discipline": ["RSCD-014-001", "discipline_violations"],
    "RSCD-015-rebound": ["RSCD-015-001", "build_auto_rebound_context", "rebounds"],
}


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_text(path: Path, blockers: list[str]) -> str:
    if not path.exists():
        blockers.append(f"missing_source:{path.relative_to(ROOT).as_posix()}")
        return ""
    return path.read_text(encoding="utf-8")


def split_table_row(line: str) -> list[str]:
    return [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]


def parse_markdown_table_after_heading(text: str, heading: str) -> list[dict[str, str]]:
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.strip() == heading:
            start = index + 1
            break
    if start is None:
        return []

    table_lines: list[str] = []
    for line in lines[start:]:
        stripped = line.strip()
        if table_lines and not stripped:
            break
        if stripped.startswith("## "):
            break
        if stripped.startswith("|"):
            table_lines.append(stripped)
    if len(table_lines) < 2:
        return []

    headers = split_table_row(table_lines[0])
    rows: list[dict[str, str]] = []
    for line in table_lines[2:]:
        cells = split_table_row(line)
        if len(cells) != len(headers):
            continue
        rows.append(dict(zip(headers, cells)))
    return rows


def parse_rscd_items(text: str) -> list[dict[str, str]]:
    section_re = re.compile(r"^##\s+(RSCD-\d{3}[-\w]*)\s*(.*)$")
    item_re = re.compile(r"^- \[ \]\s+`([^`]+)`\s+(.*)$")
    rows: list[dict[str, str]] = []
    checklist_id = ""
    checklist_title = ""
    for line in text.splitlines():
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
            })
    return rows


def int_cell(value: Any) -> int:
    try:
        return int(str(value).strip() or "0")
    except ValueError:
        return 0


def status_sort_key(row: dict[str, Any]) -> tuple[int, str, str]:
    return (
        KNOWLEDGE_USAGE_ORDER.get(str(row.get("knowledge_usage_status")), 99),
        STATUS_ORDER.get(str(row.get("coverage_status")), 99),
        str(row.get("artifact_type")),
        str(row.get("artifact_id")),
    )


def normalize_refs(refs: Any) -> str:
    if not refs:
        return ""
    if isinstance(refs, list):
        return "; ".join(str(item) for item in refs)
    return str(refs)


def expected_markers_for_row(row: dict[str, Any]) -> list[str]:
    if row.get("artifact_type") == "RSCD_ITEM":
        return [str(row.get("artifact_id"))]
    markers = EXPECTED_MARKERS_BY_ARTIFACT.get(str(row.get("artifact_id")), [])
    return [str(marker) for marker in markers if str(marker)]


def read_implementation_files(refs: list[str]) -> tuple[dict[str, str], list[str]]:
    texts: dict[str, str] = {}
    missing: list[str] = []
    for ref in refs:
        path = ROOT / ref
        if not path.exists():
            missing.append(ref)
            continue
        texts[ref] = path.read_text(encoding="utf-8", errors="ignore")
    return texts, missing


def knowledge_usage_status(coverage_status: str, usage_check_status: str) -> str:
    if usage_check_status == "not_used":
        return "not_used"
    if usage_check_status == "unverified":
        return "unverified"
    if coverage_status == "automated" and usage_check_status == "verified":
        return "fully_used"
    if coverage_status in {"automated", "automated_partial"}:
        return "partially_used"
    if coverage_status == "manual_context_supported":
        return "manual_context_supported"
    if coverage_status == "manual_review_only":
        return "manual_review_only"
    return "unverified"


def verify_row_usage(row: dict[str, Any]) -> dict[str, Any]:
    refs = [str(ref) for ref in row.get("implementation_refs") or []]
    expected_markers = expected_markers_for_row(row)
    texts, missing_files = read_implementation_files(refs)
    marker_hits: dict[str, list[str]] = {}
    for marker in expected_markers:
        marker_hits[marker] = [ref for ref, text in texts.items() if marker in text]
    found_markers = [marker for marker, hits in marker_hits.items() if hits]
    missing_markers = [marker for marker, hits in marker_hits.items() if not hits]

    if not refs or not texts:
        usage_check = "not_used"
        usage_reason = "no readable implementation refs"
    elif not expected_markers:
        usage_check = "unverified"
        usage_reason = "no expected code markers configured"
    elif not found_markers:
        usage_check = "not_used"
        usage_reason = "none of the expected code markers were found"
    elif missing_markers:
        usage_check = "partial"
        usage_reason = "some expected code markers are missing"
    else:
        usage_check = "verified"
        usage_reason = "all expected code markers were found"

    coverage_status = str(row.get("coverage_status") or "")
    return {
        "expected_code_markers": expected_markers,
        "found_code_markers": found_markers,
        "missing_code_markers": missing_markers,
        "implementation_files_found": sorted(texts),
        "implementation_files_missing": missing_files,
        "usage_check_status": usage_check,
        "usage_check_reason": usage_reason,
        "knowledge_usage_status": knowledge_usage_status(coverage_status, usage_check),
    }


def apply_usage_checks(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        row.update(verify_row_usage(row))


def count_by_field(rows: list[dict[str, Any]], field: str, order: dict[str, int]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = str(row.get(field) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: order.get(item[0], 99)))


def build_crd_rows(signed_rows: list[dict[str, str]], blockers: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in signed_rows:
        crd_id = source.get("Rule", "")
        seen.add(crd_id)
        coverage = CRD_COVERAGE.get(crd_id)
        if coverage is None:
            blockers.append(f"missing_crd_coverage:{crd_id}")
            coverage = {"status": "represented_gap", "layer": "none", "implementation_refs": [], "evidence": "no coverage mapping", "remaining_gap": "add audit mapping"}
        rows.append({
            "artifact_type": "CRD",
            "artifact_id": crd_id,
            "title": crd_id,
            "source_candidates": int_cell(source.get("Candidates")),
            "warning_candidates": int_cell(source.get("Warning Candidates")),
            "human_signoff": source.get("Human Signoff"),
            "source_execution_allowed": source.get("Execution"),
            "coverage_status": coverage["status"],
            "coverage_layer": coverage["layer"],
            "implementation_refs": coverage["implementation_refs"],
            "evidence": coverage["evidence"],
            "remaining_gap": coverage["remaining_gap"],
        })
    for crd_id in sorted(set(CRD_COVERAGE) - seen):
        blockers.append(f"coverage_for_unknown_crd:{crd_id}")
    return rows


def build_fcd_rows(crosswalk_rows: list[dict[str, str]], blockers: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in crosswalk_rows:
        fcd_id = source.get("Contract", "")
        seen.add(fcd_id)
        coverage = FCD_COVERAGE.get(fcd_id)
        if coverage is None:
            blockers.append(f"missing_fcd_coverage:{fcd_id}")
            coverage = {"status": "represented_gap", "layer": "none", "implementation_refs": [], "evidence": "no coverage mapping", "remaining_gap": "add audit mapping"}
        rows.append({
            "artifact_type": "FCD",
            "artifact_id": fcd_id,
            "title": source.get("Detector"),
            "seed_cases": int_cell(source.get("Seed Cases")),
            "fixtures": int_cell(source.get("Fixtures")),
            "generated_cases": int_cell(source.get("Generated Cases")),
            "retrieval_tests": int_cell(source.get("Retrieval Tests")),
            "coverage_state": source.get("Coverage State"),
            "coverage_status": coverage["status"],
            "coverage_layer": coverage["layer"],
            "implementation_refs": coverage["implementation_refs"],
            "evidence": coverage["evidence"],
            "remaining_gap": coverage["remaining_gap"],
        })
    for fcd_id in sorted(set(FCD_COVERAGE) - seen):
        blockers.append(f"coverage_for_unknown_fcd:{fcd_id}")
    return rows


def build_rscd_rows(checklist_rows: list[dict[str, str]], item_rows: list[dict[str, str]], blockers: list[str]) -> list[dict[str, Any]]:
    checklist_meta = {row.get("Checklist", ""): row for row in checklist_rows}
    items_by_checklist: dict[str, list[dict[str, str]]] = {}
    for item in item_rows:
        items_by_checklist.setdefault(item["checklist_id"], []).append(item)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for checklist_id, source in checklist_meta.items():
        seen.add(checklist_id)
        coverage = RSCD_GROUP_COVERAGE.get(checklist_id)
        if coverage is None:
            blockers.append(f"missing_rscd_coverage:{checklist_id}")
            coverage = {"status": "represented_gap", "layer": "none", "implementation_refs": [], "evidence": "no coverage mapping", "remaining_gap": "add audit mapping"}
        source_items = int_cell(source.get("Items"))
        parsed_items = len(items_by_checklist.get(checklist_id, []))
        if source_items != parsed_items:
            blockers.append(f"rscd_item_count_mismatch:{checklist_id}:{source_items}!={parsed_items}")
        rows.append({
            "artifact_type": "RSCD_GROUP",
            "artifact_id": checklist_id,
            "title": checklist_id,
            "source_items": source_items,
            "parsed_items": parsed_items,
            "rules": int_cell(source.get("Rules")),
            "source_candidates": int_cell(source.get("Candidates")),
            "source_execution_allowed": source.get("Execution"),
            "coverage_status": coverage["status"],
            "coverage_layer": coverage["layer"],
            "implementation_refs": coverage["implementation_refs"],
            "evidence": coverage["evidence"],
            "remaining_gap": coverage["remaining_gap"],
        })
    for checklist_id in sorted(set(RSCD_GROUP_COVERAGE) - seen):
        blockers.append(f"coverage_for_unknown_rscd:{checklist_id}")
    return rows


def build_rscd_item_rows(item_rows: list[dict[str, str]], blockers: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in item_rows:
        coverage = RSCD_GROUP_COVERAGE.get(item["checklist_id"])
        if coverage is None:
            blockers.append(f"missing_rscd_item_group_coverage:{item['checklist_id']}")
            coverage = {"status": "represented_gap", "layer": "none", "implementation_refs": [], "evidence": "no coverage mapping", "remaining_gap": "add audit mapping"}
        rows.append({
            "artifact_type": "RSCD_ITEM",
            "artifact_id": item["item_id"],
            "title": item["text"],
            "checklist_id": item["checklist_id"],
            "checklist_title": item["checklist_title"],
            "coverage_status": coverage["status"],
            "coverage_layer": coverage["layer"],
            "implementation_refs": coverage["implementation_refs"],
            "evidence": coverage["evidence"],
            "remaining_gap": coverage["remaining_gap"],
        })
    return rows


def count_by_status(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get("coverage_status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: STATUS_ORDER.get(item[0], 99)))


def table_cell(value: Any) -> str:
    if isinstance(value, list):
        value = "; ".join(str(item) for item in value)
    text = " ".join(str(value if value is not None else "").split()).replace("|", "\\|")
    return text or "-"


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) for row in rows)
    path.write_text(text + ("\n" if text else ""), encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "artifact_type",
        "artifact_id",
        "title",
        "coverage_status",
        "knowledge_usage_status",
        "usage_check_status",
        "coverage_layer",
        "implementation_refs",
        "implementation_files_found",
        "implementation_files_missing",
        "expected_code_markers",
        "found_code_markers",
        "missing_code_markers",
        "evidence",
        "remaining_gap",
        "usage_check_reason",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: normalize_refs(row.get(field)) for field in fieldnames})


def write_markdown_report(path: Path, status: dict[str, Any], matrix_rows: list[dict[str, Any]]) -> None:
    lines = [
        "# KB Coverage Audit",
        "",
        "## Verdict",
        "",
        f"- Generated: `{status['generated_at']}`",
        f"- Audit ready: `{str(status['kb_coverage_audit_ready']).lower()}`",
        f"- CRD signed cards: {status['counts']['crd']} / {EXPECTED_COUNTS['crd']}",
        f"- FCD contracts: {status['counts']['fcd']} / {EXPECTED_COUNTS['fcd']}",
        f"- RSCD checklists: {status['counts']['rscd_checklists']} / {EXPECTED_COUNTS['rscd_checklists']}",
        f"- RSCD items: {status['counts']['rscd_items']} / {EXPECTED_COUNTS['rscd_items']}",
        f"- Execution allowed: `{str(status['execution_allowed']).lower()}`",
        f"- Runtime signal allowed: `{str(status['runtime_signal_allowed']).lower()}`",
        f"- Backtest harness allowed: `{str(status['backtest_harness_allowed']).lower()}`",
        "",
        "This artifact checks knowledge coverage only. It does not produce signals, PnL, orders, paper trading, or live trading.",
        "",
        "## Status Counts",
        "",
    ]
    for group, counts in status["coverage_status_counts"].items():
        lines.append(f"- {group}: `{counts}`")
    lines.extend([
        "",
        "## Usage Verification Counts",
        "",
    ])
    for group, counts in status["knowledge_usage_counts"].items():
        lines.append(f"- {group}: `{counts}`")
    lines.append(f"- usage check status: `{status['usage_check_counts']}`")
    lines.append(f"- not used or unverified rows: {status['not_used_or_unverified_count']}")
    lines.append(f"- control queue rows: {status['control_queue_count']}")
    lines.extend([
        "",
        "## Remaining Engineering Gaps",
        "",
    ])
    for gap in status["remaining_engineering_gaps"]:
        lines.append(f"- {gap}")
    lines.extend([
        "",
        "## CRD Coverage",
        "",
        "| Rule | Coverage | Usage | Layer | Evidence | Remaining gap |",
        "| --- | --- | --- | --- | --- | --- |",
    ])
    for row in [row for row in matrix_rows if row["artifact_type"] == "CRD"]:
        lines.append("| " + " | ".join([
            table_cell(row.get("artifact_id")),
            table_cell(row.get("coverage_status")),
            table_cell(row.get("knowledge_usage_status")),
            table_cell(row.get("coverage_layer")),
            table_cell(row.get("evidence")),
            table_cell(row.get("remaining_gap")),
        ]) + " |")

    lines.extend([
        "",
        "## FCD Coverage",
        "",
        "| Contract | Detector | Coverage | Usage | Layer | Remaining gap |",
        "| --- | --- | --- | --- | --- | --- |",
    ])
    for row in [row for row in matrix_rows if row["artifact_type"] == "FCD"]:
        lines.append("| " + " | ".join([
            table_cell(row.get("artifact_id")),
            table_cell(row.get("title")),
            table_cell(row.get("coverage_status")),
            table_cell(row.get("knowledge_usage_status")),
            table_cell(row.get("coverage_layer")),
            table_cell(row.get("remaining_gap")),
        ]) + " |")

    lines.extend([
        "",
        "## RSCD Group Coverage",
        "",
        "| Checklist | Items | Coverage | Usage | Layer | Remaining gap |",
        "| --- | ---: | --- | --- | --- | --- |",
    ])
    for row in [row for row in matrix_rows if row["artifact_type"] == "RSCD_GROUP"]:
        lines.append("| " + " | ".join([
            table_cell(row.get("artifact_id")),
            table_cell(row.get("parsed_items")),
            table_cell(row.get("coverage_status")),
            table_cell(row.get("knowledge_usage_status")),
            table_cell(row.get("coverage_layer")),
            table_cell(row.get("remaining_gap")),
        ]) + " |")

    queue_rows = [
        row for row in matrix_rows
        if row.get("knowledge_usage_status") in {"not_used", "unverified"}
        or row.get("usage_check_status") == "partial"
    ]
    lines.extend([
        "",
        "## Usage Control Queue",
        "",
    ])
    if not queue_rows:
        lines.append("No rows are currently not-used, unverified, or marker-partial.")
    else:
        lines.extend([
            "| Artifact | Coverage | Usage | Marker check | Missing markers | Remaining gap |",
            "| --- | --- | --- | --- | --- | --- |",
        ])
        for row in sorted(queue_rows, key=status_sort_key):
            lines.append("| " + " | ".join([
                table_cell(f"{row.get('artifact_type')}:{row.get('artifact_id')}"),
                table_cell(row.get("coverage_status")),
                table_cell(row.get("knowledge_usage_status")),
                table_cell(row.get("usage_check_status")),
                table_cell(row.get("missing_code_markers")),
                table_cell(row.get("remaining_gap")),
            ]) + " |")

    if status["blockers"]:
        lines.extend(["", "## Blockers", ""])
        for blocker in status["blockers"]:
            lines.append(f"- `{blocker}`")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def build_audit(output_dir: Path) -> dict[str, Any]:
    blockers: list[str] = []
    signed_text = read_text(SIGNED_RULEBOOK_PATH, blockers)
    contracts_text = read_text(FEATURE_CONTRACTS_PATH, blockers)
    rscd_text = read_text(RSCD_CHECKLIST_PATH, blockers)

    signed_card_rows = parse_markdown_table_after_heading(signed_text, "## Signed Cards")
    signed_checklist_rows = parse_markdown_table_after_heading(signed_text, "## Signed Checklists")
    fcd_crosswalk_rows = parse_markdown_table_after_heading(contracts_text, "## Crosswalk")
    rscd_item_rows = parse_rscd_items(rscd_text)

    counts = {
        "crd": len(signed_card_rows),
        "fcd": len(fcd_crosswalk_rows),
        "rscd_checklists": len(signed_checklist_rows),
        "rscd_items": len(rscd_item_rows),
    }
    for key, expected in EXPECTED_COUNTS.items():
        if counts[key] != expected:
            blockers.append(f"count_mismatch:{key}:{counts[key]}!={expected}")

    crd_rows = build_crd_rows(signed_card_rows, blockers)
    fcd_rows = build_fcd_rows(fcd_crosswalk_rows, blockers)
    rscd_group_rows = build_rscd_rows(signed_checklist_rows, rscd_item_rows, blockers)
    rscd_item_matrix_rows = build_rscd_item_rows(rscd_item_rows, blockers)
    matrix_rows = [*crd_rows, *fcd_rows, *rscd_group_rows, *rscd_item_matrix_rows]
    apply_usage_checks(matrix_rows)
    control_queue_rows = [
        row for row in matrix_rows
        if row.get("knowledge_usage_status") != "fully_used"
    ]
    not_used_or_unverified_rows = [
        row for row in matrix_rows
        if row.get("knowledge_usage_status") in {"not_used", "unverified"}
    ]

    status = {
        "version": VERSION,
        "generated_at": utc_now(),
        "mode": "read_only_knowledge_coverage_audit",
        "source_artifacts": [
            SIGNED_RULEBOOK_PATH.relative_to(ROOT).as_posix(),
            FEATURE_CONTRACTS_PATH.relative_to(ROOT).as_posix(),
            RSCD_CHECKLIST_PATH.relative_to(ROOT).as_posix(),
        ],
        "counts": counts,
        "expected_counts": EXPECTED_COUNTS,
        "coverage_status_counts": {
            "crd": count_by_status(crd_rows),
            "fcd": count_by_status(fcd_rows),
            "rscd_groups": count_by_status(rscd_group_rows),
            "rscd_items": count_by_status(rscd_item_matrix_rows),
            "all_rows": count_by_status(matrix_rows),
        },
        "usage_check_counts": count_by_field(matrix_rows, "usage_check_status", USAGE_CHECK_ORDER),
        "knowledge_usage_counts": {
            "crd": count_by_field(crd_rows, "knowledge_usage_status", KNOWLEDGE_USAGE_ORDER),
            "fcd": count_by_field(fcd_rows, "knowledge_usage_status", KNOWLEDGE_USAGE_ORDER),
            "rscd_groups": count_by_field(rscd_group_rows, "knowledge_usage_status", KNOWLEDGE_USAGE_ORDER),
            "rscd_items": count_by_field(rscd_item_matrix_rows, "knowledge_usage_status", KNOWLEDGE_USAGE_ORDER),
            "all_rows": count_by_field(matrix_rows, "knowledge_usage_status", KNOWLEDGE_USAGE_ORDER),
        },
        "not_used_or_unverified_count": len(not_used_or_unverified_rows),
        "control_queue_count": len(control_queue_rows),
        "remaining_engineering_gaps": [
            "drawn screenshot levels are merged into candidate selection only after OHLC validation; screenshot coordinate accuracy remains review evidence",
            "historical data freshness, gap-checking, and versioned market-data manifests are outside this audit",
            "automatic extraction is pending for rounded U formations and non-breakout market mechanics",
            "journal facts such as screenshot-before-scenario, no opposite entry, and result recorded in R remain manual evidence",
            "PnL, outcome labels, backtest harness, paper trading, live trading, and order generation remain disabled",
        ],
        "blockers": blockers,
        "kb_coverage_audit_ready": not blockers,
        **SAFETY_FLAGS,
    }

    write_json(output_dir / "kb_coverage_audit_status.json", status)
    write_jsonl(output_dir / "kb_coverage_audit_matrix.jsonl", sorted(matrix_rows, key=status_sort_key))
    write_csv(output_dir / "kb_coverage_audit_matrix.csv", sorted(matrix_rows, key=status_sort_key))
    write_jsonl(output_dir / "kb_usage_control_queue.jsonl", sorted(control_queue_rows, key=status_sort_key))
    write_jsonl(output_dir / "kb_not_used_or_unverified.jsonl", sorted(not_used_or_unverified_rows, key=status_sort_key))
    write_markdown_report(output_dir / "kb_coverage_audit.md", status, matrix_rows)
    return status


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a read-only KB coverage audit matrix")
    parser.add_argument("--out-dir", default=str(OUTPUT_DIR), help="Output directory for audit artifacts")
    parser.add_argument("--strict-exit-code", action="store_true", help="Return non-zero if audit blockers are present")
    return parser


def main() -> int:
    configure_stdio()
    args = build_parser().parse_args()
    status = build_audit(Path(args.out_dir))
    print(json.dumps({
        "version": status["version"],
        "kb_coverage_audit_ready": status["kb_coverage_audit_ready"],
        "counts": status["counts"],
        "coverage_status_counts": status["coverage_status_counts"],
        "usage_check_counts": status["usage_check_counts"],
        "knowledge_usage_counts": status["knowledge_usage_counts"],
        "not_used_or_unverified_count": status["not_used_or_unverified_count"],
        "control_queue_count": status["control_queue_count"],
        "blockers": status["blockers"],
        "output_dir": str(Path(args.out_dir)),
        **SAFETY_FLAGS,
    }, ensure_ascii=False, indent=2))
    if args.strict_exit_code and status["blockers"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())