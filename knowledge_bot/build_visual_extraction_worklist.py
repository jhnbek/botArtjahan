"""Build a resumable, candidate-centric visual review worklist.

For every high-priority lecture multimodal candidate, pick the 1-3 frames most
likely to carry a decisive chart header (ticker / date / OHLC / level), so a
human-or-vision reviewer can open a small batch, read the chart, and turn each
candidate into a promoted / partial / rejected decision.

This does NOT classify anything by itself. It only orders the visual work and
tracks progress against the manual review ledger so the effort is incremental
and never restarts from zero.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CANDIDATES = Path(
    "_knowledge_base/structured/consolidation/lecture_multimodal_calibration_candidates/candidates.jsonl"
)
LEDGER = Path(
    "_knowledge_base/structured/consolidation/lecture_multimodal_calibration_candidates/manual_visual_review_notes.json"
)
OUTPUT = Path(
    "_knowledge_base/structured/consolidation/lecture_multimodal_calibration_candidates/visual_review_worklist.json"
)

PRICE_HINT = re.compile(r"\d{1,6}[.,]\d{1,6}|\d{1,2}/\d{1,2}/\d{2,4}|\$")
TICKER_HINT = re.compile(r"\b[A-Z]{2,6}USD?T?\b|\bD1\b|\bNYSE\b|\bNASDAQ\b|TradingView", re.IGNORECASE)


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def default_root() -> Path:
    return Path(__file__).resolve().parents[1]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def load_done_ids(root: Path) -> set[str]:
    ledger_path = root / LEDGER
    if not ledger_path.exists():
        return set()
    data = json.loads(ledger_path.read_text(encoding="utf-8"))
    return {row.get("candidate_id") for row in data.get("reviews") or [] if row.get("candidate_id")}


def frame_score(frame: dict[str, Any], ocr_by_path: dict[str, str]) -> int:
    score = 0
    text = ocr_by_path.get(frame.get("path") or "", "")
    score += min(len(text), 200) // 10
    if PRICE_HINT.search(text):
        score += 25
    if TICKER_HINT.search(text):
        score += 15
    if frame.get("exists"):
        score += 5
    return score


def pick_frames(row: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    ocr_by_path = {s.get("path"): s.get("ocr_excerpt", "") for s in row.get("ocr_snippets") or []}
    frames = [f for f in row.get("frame_refs") or [] if f.get("exists")]
    if not frames:
        return []
    scored = sorted(frames, key=lambda f: frame_score(f, ocr_by_path), reverse=True)
    top = scored[: max(1, limit)]
    # if nothing carried OCR signal, also include the temporal middle frame for context
    if all(not ocr_by_path.get(f.get("path") or "") for f in top) and len(frames) > 1:
        middle = frames[len(frames) // 2]
        if middle not in top:
            top = (top + [middle])[:limit]
    return [
        {
            "physical_path": f.get("physical_path"),
            "path": f.get("path"),
            "time": f.get("time"),
            "has_ocr": bool(ocr_by_path.get(f.get("path") or "")),
            "ocr_excerpt": (ocr_by_path.get(f.get("path") or "") or "")[:240],
        }
        for f in top
    ]


def is_promotable(row: dict[str, Any]) -> bool:
    """Strict prioritization gate: the candidate's TEXT features already carry
    every field needed to attempt a closed-bar ground-truth case, so a single
    visual confirmation can promote it. Frames still get verified by vision; this
    only orders work, it does not promote anything.
    """
    f = row.get("features", {})
    if not (f.get("exact_date_candidates") or []):
        return False
    if not (f.get("price_candidates") or []):
        return False
    if f.get("direction") not in {"long", "short"}:
        return False
    if not (f.get("instrument_candidates") or []):
        return False
    if (f.get("existing_frame_count") or 0) <= 0:
        return False
    return True


def build(root: Path, frames_per_candidate: int, batch_size: int, promotable_only: bool) -> dict[str, Any]:
    rows = read_jsonl(root / CANDIDATES)
    done = load_done_ids(root)
    hp = [r for r in rows if r.get("classification") == "high_priority_visual_review"]
    if promotable_only:
        hp = [r for r in hp if is_promotable(r)]
    # rank by readiness desc, then by single-instrument clarity (fewer instrument candidates first)
    hp.sort(
        key=lambda r: (
            -(r.get("readiness_score") or 0),
            len(r.get("features", {}).get("instrument_candidates") or []),
        )
    )
    pending = []
    rank = 0
    for row in hp:
        cid = row.get("candidate_id")
        if cid in done:
            continue
        frames = pick_frames(row, frames_per_candidate)
        if not frames:
            continue
        rank += 1
        features = row.get("features", {})
        pending.append(
            {
                "rank": rank,
                "batch": (rank - 1) // batch_size + 1,
                "candidate_id": cid,
                "claim_id": row.get("claim_id"),
                "lecture_title": row.get("lecture_title"),
                "lecture_dir": row.get("lecture_dir"),
                "time": row.get("time"),
                "readiness_score": row.get("readiness_score"),
                "direction": features.get("direction"),
                "instrument_candidates": (features.get("instrument_candidates") or [])[:6],
                "price_candidates": (features.get("price_candidates") or [])[:8],
                "scenario_candidates": (features.get("scenario_candidates") or [])[:5],
                "promotion_blockers": row.get("promotion_blockers") or [],
                "frames_to_view": frames,
                "statement_excerpt": row.get("statement_excerpt"),
            }
        )
    return {
        "dataset_id": "lecture_multimodal_visual_review_worklist_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "promotable_only": promotable_only,
        "source_candidates": CANDIDATES.as_posix(),
        "ledger": LEDGER.as_posix(),
        "frames_per_candidate": frames_per_candidate,
        "batch_size": batch_size,
        "total_high_priority": len(hp),
        "already_reviewed": len(done & {r.get("candidate_id") for r in hp}),
        "pending_count": len(pending),
        "pending_batches": (len(pending) + batch_size - 1) // batch_size,
        "frames_to_view_total": sum(len(p["frames_to_view"]) for p in pending),
        "worklist": pending,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a resumable visual review worklist for lecture multimodal candidates.")
    parser.add_argument("--root", type=Path, default=default_root())
    parser.add_argument("--frames-per-candidate", type=int, default=2)
    parser.add_argument("--batch-size", type=int, default=12)
    parser.add_argument("--promotable-only", action="store_true", help="Keep only candidates whose text features already carry exact date + price + clear direction + instrument + frames.")
    return parser.parse_args()


def main() -> int:
    configure_stdio()
    args = parse_args()
    root = args.root.resolve()
    payload = build(root, args.frames_per_candidate, args.batch_size, args.promotable_only)
    out = root / OUTPUT
    if args.promotable_only:
        out = out.with_name("visual_review_worklist_promotable.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"[OK] worklist: {payload['pending_count']} pending candidates, "
        f"{payload['frames_to_view_total']} frames to view, "
        f"{payload['pending_batches']} batches; already_reviewed={payload['already_reviewed']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
