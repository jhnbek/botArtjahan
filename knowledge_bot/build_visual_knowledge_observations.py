"""Build joined visual-knowledge observations from manual frame reviews.

The manual visual ledger is conservative: most reviewed frames are not promoted
to OHLC-ready ground truth. That does not make them useless. This script joins
each visual review back to its mined multimodal candidate so the chart frame,
timecode, source units, transcript excerpts, and extracted visual lesson live in
one machine-readable artifact.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BASE = Path("_knowledge_base/structured/consolidation/lecture_multimodal_calibration_candidates")
CANDIDATES = BASE / "candidates.jsonl"
LEDGER = BASE / "manual_visual_review_notes.json"
OUTPUT_JSONL = BASE / "visual_knowledge_observations.jsonl"
OUTPUT_SUMMARY = BASE / "visual_knowledge_observations_summary.json"


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def default_root() -> Path:
    return Path(__file__).resolve().parents[1]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def classify_knowledge_use(review: dict[str, Any]) -> str:
    status = str(review.get("review_status") or "")
    decision = str(review.get("promotion_decision") or "")
    blockers = " ".join(str(value) for value in review.get("promotion_blockers") or [])
    text = f"{status} {decision} {blockers}".lower()

    if decision == "promoted_to_ground_truth":
        return "ground_truth"
    if decision == "hold_for_ohlc_verification" or "public_daily_ohlc" in review:
        return "ohlc_verifiable_scenario_candidate"
    if status.startswith("partial"):
        if any(token in text for token in ("scenario", "entry", "trigger", "tvx", "tbx", "vwap")):
            return "scenario_context_candidate"
        return "visual_context_candidate"
    if any(token in text for token in ("navigation", "symbol", "mismatch")):
        return "screen_navigation_or_mismatch"
    if any(token in text for token in ("replay", "simulator")):
        return "replay_training_evidence"
    if any(token in text for token in ("whiteboard", "drawing")):
        return "whiteboard_methodology_evidence"
    if any(token in text for token in ("level", "gap", "tail")):
        return "methodology_level_evidence"
    if status.startswith("rejected_methodology") or "methodology" in text or "rule" in text:
        return "methodology_evidence"
    return "other_visual_evidence"


def build_observation(review: dict[str, Any], candidate: dict[str, Any] | None) -> dict[str, Any]:
    candidate = candidate or {}
    features = candidate.get("features") or {}
    return {
        "candidate_id": review.get("candidate_id"),
        "claim_id": review.get("claim_id") or candidate.get("claim_id"),
        "knowledge_use": classify_knowledge_use(review),
        "review_status": review.get("review_status"),
        "promotion_decision": review.get("promotion_decision"),
        "visual_confirmed": review.get("visual_confirmed"),
        "lecture_title": review.get("lecture_title") or candidate.get("lecture_title"),
        "lecture_dir": candidate.get("lecture_dir"),
        "timecode": review.get("timecode") or candidate.get("time"),
        "source_unit_ids": candidate.get("source_unit_ids") or [],
        "matched_source_unit_ids": candidate.get("matched_source_unit_ids") or [],
        "source_frames": review.get("source_frames") or [],
        "candidate_frame_refs": candidate.get("frame_refs") or [],
        "resolved_fields": review.get("resolved_fields") or {},
        "visual_evidence": review.get("visual_evidence") or [],
        "expert_verdict_summary": review.get("expert_verdict_summary"),
        "promotion_blockers": review.get("promotion_blockers") or [],
        "next_action": review.get("next_action"),
        "candidate_statement_excerpt": candidate.get("statement_excerpt"),
        "candidate_quote_excerpt": candidate.get("quote_excerpt"),
        "candidate_visual_evidence_excerpt": candidate.get("visual_evidence_excerpt"),
        "candidate_source_text_excerpt": candidate.get("source_text_excerpt"),
        "candidate_features": {
            "direction": features.get("direction"),
            "instrument_candidates": features.get("instrument_candidates") or [],
            "exact_date_candidates": features.get("exact_date_candidates") or [],
            "relative_date_cues": features.get("relative_date_cues") or [],
            "price_candidates": features.get("price_candidates") or [],
            "scenario_candidates": features.get("scenario_candidates") or [],
            "timeframes": features.get("timeframes") or [],
        },
        "has_text_join": bool(candidate),
    }


def build(root: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    candidates = {row.get("candidate_id"): row for row in read_jsonl(root / CANDIDATES)}
    ledger = json.loads((root / LEDGER).read_text(encoding="utf-8"))
    observations = [
        build_observation(review, candidates.get(review.get("candidate_id")))
        for review in ledger.get("reviews", [])
    ]

    knowledge_counts = Counter(row["knowledge_use"] for row in observations)
    status_counts = Counter(row.get("review_status") for row in observations)
    joined_count = sum(1 for row in observations if row["has_text_join"])
    visual_confirmed_count = sum(1 for row in observations if row.get("visual_confirmed") is True)
    visual_unconfirmed_count = sum(1 for row in observations if row.get("visual_confirmed") is False)
    summary = {
        "dataset_id": "lecture_multimodal_visual_knowledge_observations_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_ledger": LEDGER.as_posix(),
        "source_candidates": CANDIDATES.as_posix(),
        "observation_count": len(observations),
        "joined_to_candidate_text_count": joined_count,
        "missing_candidate_join_count": len(observations) - joined_count,
        "visual_confirmed_count": visual_confirmed_count,
        "visual_unconfirmed_count": visual_unconfirmed_count,
        "knowledge_use_counts": dict(sorted(knowledge_counts.items())),
        "review_status_counts": dict(status_counts.most_common()),
        "outputs": {
            "jsonl": OUTPUT_JSONL.as_posix(),
            "summary": OUTPUT_SUMMARY.as_posix(),
        },
    }
    return observations, summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Join visual frame reviews with source text/timecode evidence.")
    parser.add_argument("--root", type=Path, default=default_root())
    return parser.parse_args()


def main() -> int:
    configure_stdio()
    args = parse_args()
    root = args.root.resolve()
    observations, summary = build(root)
    out_jsonl = root / OUTPUT_JSONL
    out_summary = root / OUTPUT_SUMMARY
    out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    out_jsonl.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in observations), encoding="utf-8")
    out_summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"[OK] observations={summary['observation_count']} "
        f"joined={summary['joined_to_candidate_text_count']} "
        f"visual_confirmed={summary['visual_confirmed_count']} "
        f"uses={summary['knowledge_use_counts']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
