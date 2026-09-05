"""Append-only storage primitives for the live casebook.

The casebook keeps three separate append-only logs so that a frozen
decision-time snapshot is never contaminated by information that only became
available later:

* ``cases.jsonl``    - decision snapshots (what was known when the call was made)
* ``outcomes.jsonl`` - factual post-decision outcome events (what price did)
* ``labels.jsonl``   - human review labels (valid / invalid / uncertain)

This module is read/append only. It never computes aggregate winrate that feeds
back into decisions, never generates orders, and never runs a backtest. Outcome
and label events are stored as independent records keyed by ``case_id``.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime_root import data_root

ROOT = data_root(__file__)
CASEBOOK_DIR = ROOT / "_knowledge_base" / "live_casebook"
CASES_PATH = CASEBOOK_DIR / "cases.jsonl"
OUTCOMES_PATH = CASEBOOK_DIR / "outcomes.jsonl"
LABELS_PATH = CASEBOOK_DIR / "labels.jsonl"

SCHEMA_VERSION = "1"
VALID_MODES = {"analysis", "signal", "auto"}
VALID_LABELS = {"valid", "invalid", "uncertain"}
# Two independent axes so a sound decision is never conflated with the outcome.
# decision_quality: was the scenario/reasoning correct given what was known at
# decision time? market_behavior: did price actually follow the expected path?
VALID_DECISION_QUALITY = {"correct", "incorrect", "uncertain"}
VALID_MARKET_BEHAVIOR = {"as_expected", "contrary", "ambiguous"}
# Derived learning class. The key Gerchik case is a correct scenario where the
# market still moved against it: the decision stays a positive example while the
# outcome is recorded as adverse, so the bot learns the setup's failure mode
# without punishing sound reasoning.
LEARNING_CLASS_MATRIX = {
    ("correct", "as_expected"): "confirmed_setup",
    ("correct", "contrary"): "valid_scenario_adverse_market",
    ("correct", "ambiguous"): "valid_scenario_unresolved",
    ("incorrect", "as_expected"): "lucky_or_misjudged",
    ("incorrect", "contrary"): "avoidable_error",
    ("incorrect", "ambiguous"): "flawed_scenario_unresolved",
    ("uncertain", "as_expected"): "needs_more_review",
    ("uncertain", "contrary"): "needs_more_review",
    ("uncertain", "ambiguous"): "needs_more_review",
}
# Convenience mapping when only the legacy single label is supplied.
LABEL_TO_DECISION_QUALITY = {"valid": "correct", "invalid": "incorrect", "uncertain": "uncertain"}
IDEMPOTENCY_FIELDS = (
    "source",
    "mode",
    "instrument",
    "venue",
    "timeframe",
    "direction",
    "level",
    "entry",
    "stop",
    "target",
    "trigger",
    "ohlc_source",
    "ohlc_start",
    "ohlc_end",
    "ohlc_bar_count",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def idempotency_key(snapshot: dict[str, Any], source: str, mode: str) -> str:
    basis = {field: None for field in IDEMPOTENCY_FIELDS}
    basis["source"] = source
    basis["mode"] = mode
    for field in IDEMPOTENCY_FIELDS:
        if field in snapshot:
            basis[field] = snapshot[field]
    ohlc = snapshot.get("ohlc_window") or {}
    basis["ohlc_source"] = snapshot.get("ohlc_source")
    basis["ohlc_start"] = ohlc.get("start")
    basis["ohlc_end"] = ohlc.get("end")
    basis["ohlc_bar_count"] = ohlc.get("bar_count")
    return content_hash(basis)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_cases() -> list[dict[str, Any]]:
    return read_jsonl(CASES_PATH)


def load_outcomes() -> list[dict[str, Any]]:
    return read_jsonl(OUTCOMES_PATH)


def load_labels() -> list[dict[str, Any]]:
    return read_jsonl(LABELS_PATH)


def find_case_by_idempotency(key: str) -> dict[str, Any] | None:
    for case in load_cases():
        if case.get("idempotency_key") == key:
            return case
    return None


def get_case(case_id: str) -> dict[str, Any] | None:
    for case in load_cases():
        if case.get("case_id") == case_id:
            return case
    return None


def append_case(snapshot: dict[str, Any], source: str, mode: str,
                evidence: dict[str, Any] | None = None,
                provenance: dict[str, Any] | None = None,
                read_only_flags: dict[str, Any] | None = None) -> tuple[dict[str, Any], bool]:
    """Append a decision snapshot. Returns (case, created).

    If an identical decision already exists (same idempotency key), the existing
    case is returned and ``created`` is ``False``; nothing is written.
    """
    if mode not in VALID_MODES:
        raise ValueError(f"mode must be one of {sorted(VALID_MODES)}, got {mode!r}")
    key = idempotency_key(snapshot, source, mode)
    existing = find_case_by_idempotency(key)
    if existing is not None:
        return existing, False
    case = {
        "record_type": "case",
        "case_id": uuid.uuid4().hex,
        "schema_version": SCHEMA_VERSION,
        "created_at": now_iso(),
        "source": source,
        "mode": mode,
        "idempotency_key": key,
        "decision_snapshot": snapshot,
        "evidence": evidence or {},
        "provenance": provenance or {},
        "read_only_flags": read_only_flags or {},
    }
    case["provenance"]["content_hash"] = content_hash(case["decision_snapshot"])
    append_jsonl(CASES_PATH, case)
    return case, True


def append_outcome(case_id: str, outcome: str, *,
                   mfe_r: float | None = None, mae_r: float | None = None,
                   bars_to_resolution: int | None = None,
                   ohlc_source: str | None = None,
                   resolution_time: str | None = None,
                   post_decision: bool | None = None,
                   provenance: dict[str, Any] | None = None) -> dict[str, Any]:
    if get_case(case_id) is None:
        raise ValueError(f"unknown case_id: {case_id}")
    record = {
        "record_type": "outcome",
        "outcome_id": uuid.uuid4().hex,
        "case_id": case_id,
        "observed_at": now_iso(),
        "outcome": outcome,
        "mfe_r": mfe_r,
        "mae_r": mae_r,
        "bars_to_resolution": bars_to_resolution,
        "ohlc_source": ohlc_source,
        "resolution_time": resolution_time,
        "post_decision": post_decision,
        "provenance": provenance or {},
    }
    append_jsonl(OUTCOMES_PATH, record)
    return record


def derive_learning_class(decision_quality: str | None, market_behavior: str | None) -> str | None:
    if not decision_quality or not market_behavior:
        return None
    return LEARNING_CLASS_MATRIX.get((decision_quality, market_behavior))


def append_label(case_id: str, label: str, labeler: str, rationale: str = "",
                 decision_quality: str | None = None,
                 market_behavior: str | None = None,
                 judged_blind_to_outcome: bool | None = None,
                 premise_audit: str = "") -> dict[str, Any]:
    if get_case(case_id) is None:
        raise ValueError(f"unknown case_id: {case_id}")
    if label not in VALID_LABELS:
        raise ValueError(f"label must be one of {sorted(VALID_LABELS)}, got {label!r}")
    if not labeler or labeler.strip().lower() == "auto":
        raise ValueError("labeler must be a non-empty human identifier; automatic labels are not allowed")
    if decision_quality is None:
        decision_quality = LABEL_TO_DECISION_QUALITY.get(label)
    if decision_quality not in VALID_DECISION_QUALITY:
        raise ValueError(f"decision_quality must be one of {sorted(VALID_DECISION_QUALITY)}, got {decision_quality!r}")
    if market_behavior is not None and market_behavior not in VALID_MARKET_BEHAVIOR:
        raise ValueError(f"market_behavior must be one of {sorted(VALID_MARKET_BEHAVIOR)}, got {market_behavior!r}")
    # Hindsight guard. decision_quality must be judged from the frozen
    # decision_snapshot ALONE, blind to the outcome. We record whether the
    # reviewer actually did that, and flag any decision_quality judgement that
    # was made while already knowing the outcome as lower trust, so "the market
    # misbehaved" can never silently launder a misread premise (and vice versa).
    blind_judgement_trustworthy = (
        judged_blind_to_outcome is True
        or decision_quality == "uncertain"
    )
    record = {
        "record_type": "label",
        "label_id": uuid.uuid4().hex,
        "case_id": case_id,
        "labeled_at": now_iso(),
        "label": label,
        "decision_quality": decision_quality,
        "market_behavior": market_behavior,
        "learning_class": derive_learning_class(decision_quality, market_behavior),
        # Audit trail that keeps the two axes from collapsing into each other.
        "judged_blind_to_outcome": judged_blind_to_outcome,
        "blind_judgement_trustworthy": blind_judgement_trustworthy,
        "premise_audit": premise_audit,
        "labeler": labeler,
        "rationale": rationale,
    }
    append_jsonl(LABELS_PATH, record)
    return record


# Fields the bot determines and that a human may therefore correct. Each maps to
# a key inside the frozen decision_snapshot so a correction is auditable against
# exactly what the bot concluded.
CORRECTABLE_FIELDS = (
    "direction",
    "review_state",
    "analyzer_verdict",
    "level",
    "entry",
    "stop",
    "target",
    "trigger",
)


def bot_self_analysis(case: dict[str, Any]) -> dict[str, Any]:
    """Extract what the bot concluded for a case, for human confirmation."""
    snapshot = case.get("decision_snapshot") or {}
    return {field: snapshot.get(field) for field in CORRECTABLE_FIELDS}


def append_confirmation(case_id: str, agrees_with_bot: bool, confirmed_by: str,
                        corrections: dict[str, Any] | None = None,
                        market_behavior: str | None = None,
                        judged_blind_to_outcome: bool | None = None,
                        rationale: str = "", premise_audit: str = "") -> dict[str, Any]:
    """Record a human confirmation (or correction) of the bot's own analysis.

    This is the human-in-the-loop accumulation step: the bot shows what it
    concluded, the human either agrees or supplies corrected field values. The
    bot's frozen self-analysis is stored alongside the corrections so the case
    keeps both "what the bot said" and "what the human says it should be".
    Agreement maps to decision_quality=correct, a correction to incorrect, which
    flows into the same two-axis learning_class model.
    """
    case = get_case(case_id)
    if case is None:
        raise ValueError(f"unknown case_id: {case_id}")
    if not confirmed_by or confirmed_by.strip().lower() == "auto":
        raise ValueError("confirmed_by must be a non-empty human identifier; automatic confirmations are not allowed")
    corrections = dict(corrections or {})
    unknown = [field for field in corrections if field not in CORRECTABLE_FIELDS]
    if unknown:
        raise ValueError(f"unknown correction fields {unknown}; allowed: {list(CORRECTABLE_FIELDS)}")
    if agrees_with_bot and corrections:
        raise ValueError("cannot agree with the bot and supply corrections at the same time")
    if not agrees_with_bot and not corrections:
        raise ValueError("a correction must supply at least one corrected field (or use agree)")
    if market_behavior is not None and market_behavior not in VALID_MARKET_BEHAVIOR:
        raise ValueError(f"market_behavior must be one of {sorted(VALID_MARKET_BEHAVIOR)}, got {market_behavior!r}")

    decision_quality = "correct" if agrees_with_bot else "incorrect"
    label = "valid" if agrees_with_bot else "invalid"
    bot_analysis = bot_self_analysis(case)
    blind_judgement_trustworthy = (
        judged_blind_to_outcome is True
        or decision_quality == "uncertain"
    )
    record = {
        "record_type": "confirmation",
        "label_id": uuid.uuid4().hex,
        "case_id": case_id,
        "labeled_at": now_iso(),
        "label": label,
        "agrees_with_bot": agrees_with_bot,
        "bot_analysis": bot_analysis,
        "corrections": corrections,
        "decision_quality": decision_quality,
        "market_behavior": market_behavior,
        "learning_class": derive_learning_class(decision_quality, market_behavior),
        "judged_blind_to_outcome": judged_blind_to_outcome,
        "blind_judgement_trustworthy": blind_judgement_trustworthy,
        "premise_audit": premise_audit,
        "labeler": confirmed_by,
        "rationale": rationale,
    }
    append_jsonl(LABELS_PATH, record)
    return record


def pending_confirmation_cases() -> list[dict[str, Any]]:
    """Cases that have no human confirmation/label yet, awaiting review."""
    confirmed_ids = {row.get("case_id") for row in load_labels()}
    return [case for case in load_cases() if case.get("case_id") not in confirmed_ids]


def materialize_case(case_id: str) -> dict[str, Any] | None:
    """Read-only merged view: snapshot + outcome events + label events."""
    case = get_case(case_id)
    if case is None:
        return None
    outcomes = [row for row in load_outcomes() if row.get("case_id") == case_id]
    labels = [row for row in load_labels() if row.get("case_id") == case_id]
    return {
        "case": case,
        "outcomes": outcomes,
        "labels": labels,
        "latest_outcome": outcomes[-1] if outcomes else None,
        "latest_label": labels[-1] if labels else None,
    }
