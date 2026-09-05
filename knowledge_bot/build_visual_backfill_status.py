from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class LectureVisualStatus:
    course_position: int
    title: str
    extraction_status: str
    claim_count: int
    chunk_count: int
    chunks_with_frames: int = 0
    frame_count: int = 0
    frames_with_ocr: int = 0
    ocr_chars: int = 0
    visual_review_done: bool = False
    visual_claim_count: int = 0
    sample_frames: list[dict[str, Any]] = field(default_factory=list)

    @property
    def visual_backfill_status(self) -> str:
        if self.extraction_status != "observed_draft_done":
            return "not_started"
        if self.visual_review_done:
            return "initial_visual_backfill_done"
        if self.frame_count == 0:
            return "no_frames"
        return "pending_visual_review"


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def default_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_raw_ocr_lookup(root: Path) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    corpus_root = root / "_new_lecture_corpus"
    if not corpus_root.exists():
        return lookup
    for visual_path in corpus_root.glob("*/visual_index.json"):
        lecture_dir = visual_path.parent.name
        try:
            visual = load_json(visual_path)
        except json.JSONDecodeError:
            continue
        for frame in visual.get("frames") or []:
            rel_path = frame.get("path")
            if rel_path:
                lookup[f"{lecture_dir}/{rel_path}".replace("\\", "/")] = frame
    return lookup


def enrich_frame_ocr(frame: dict[str, Any], raw_lookup: dict[str, dict[str, Any]]) -> dict[str, Any]:
    merged = dict(frame)
    raw_frame = raw_lookup.get(str(frame.get("path") or "").replace("\\", "/"))
    if raw_frame:
        for key in ("ocr_text", "ocr_line_count", "ocr_error", "ocr_repair_version", "ocr_processed_at"):
            if raw_frame.get(key) not in (None, ""):
                merged[key] = raw_frame[key]
    return merged


def frame_summary(frame: dict[str, Any]) -> dict[str, Any]:
    summary = {
        "time": frame.get("time"),
        "path": frame.get("path"),
        "trigger": frame.get("trigger"),
    }
    if frame.get("scene_index") is not None:
        summary["scene_index"] = frame.get("scene_index")
    if frame.get("linked_segment_index") is not None:
        summary["linked_segment_index"] = frame.get("linked_segment_index")
    ocr_text = (frame.get("ocr_text") or "").strip()
    if ocr_text:
        summary["ocr_preview"] = " ".join(ocr_text.split())[:180]
    return {key: value for key, value in summary.items() if value not in (None, "")}


def build_status(root: Path) -> dict[str, Any]:
    lecture_pass = root / "_knowledge_base" / "structured" / "lecture_pass"
    index = load_json(lecture_pass / "lecture_pass_index.json")
    statuses: dict[int, LectureVisualStatus] = {}
    for lecture in index["lectures"]:
        statuses[lecture["course_position"]] = LectureVisualStatus(
            course_position=lecture["course_position"],
            title=lecture["title"],
            extraction_status=lecture["extraction_status"],
            claim_count=lecture["claim_count"],
            chunk_count=lecture["chunk_count"],
        )
        folder = lecture.get("folder")
        if folder:
            folder_path = root / folder
            statuses[lecture["course_position"]].visual_review_done = (folder_path / "visual_backfill_review.md").exists()
            visual_claims_path = folder_path / "claims_visual_backfill.jsonl"
            if visual_claims_path.exists():
                statuses[lecture["course_position"]].visual_claim_count = sum(
                    1 for line in visual_claims_path.read_text(encoding="utf-8").splitlines() if line.strip()
                )

    chunks_path = root / "_knowledge_base" / "lecture_chunks.jsonl"
    raw_ocr_lookup = load_raw_ocr_lookup(root)
    with chunks_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            chunk = json.loads(line)
            status = statuses.get(chunk.get("course_position"))
            if status is None:
                continue
            frames = chunk.get("frames") or []
            if frames:
                status.chunks_with_frames += 1
            status.frame_count += len(frames)
            for frame in frames:
                frame = enrich_frame_ocr(frame, raw_ocr_lookup)
                ocr_text = (frame.get("ocr_text") or "").strip()
                if ocr_text:
                    status.frames_with_ocr += 1
                    status.ocr_chars += len(ocr_text)
            if frames and len(status.sample_frames) < 3:
                first_frame = enrich_frame_ocr(frames[0], raw_ocr_lookup)
                status.sample_frames.append(
                    {
                        "chunk_id": chunk.get("chunk_id"),
                        "chunk_time": f"{chunk.get('start_time')}-{chunk.get('end_time')}",
                        "frame": frame_summary(first_frame),
                    }
                )

    lecture_items = []
    for pos in sorted(statuses):
        status = statuses[pos]
        lecture_items.append(
            {
                "course_position": status.course_position,
                "title": status.title,
                "extraction_status": status.extraction_status,
                "visual_backfill_status": status.visual_backfill_status,
                "claim_count": status.claim_count,
                "chunk_count": status.chunk_count,
                "chunks_with_frames": status.chunks_with_frames,
                "frame_count": status.frame_count,
                "frames_with_ocr": status.frames_with_ocr,
                "ocr_chars": status.ocr_chars,
                "visual_review_done": status.visual_review_done,
                "visual_claim_count": status.visual_claim_count,
                "sample_frames": status.sample_frames,
            }
        )

    processed = [item for item in lecture_items if item["extraction_status"] == "observed_draft_done"]
    pending_visual = [item for item in processed if item["visual_backfill_status"] == "pending_visual_review"]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "policy": {
            "existing_claims_kept": True,
            "visual_backfill_is_additive": True,
            "claim_maturity_remains_observed": True,
            "ocr_absent_requires_frame_inspection": True,
        },
        "counts": {
            "lectures_total": len(lecture_items),
            "lectures_observed_draft_done": len(processed),
            "lectures_pending_visual_review": len(pending_visual),
            "lectures_visual_backfill_done": sum(1 for item in processed if item["visual_review_done"]),
            "processed_claims_total": sum(item["claim_count"] for item in processed),
            "processed_visual_claims_total": sum(item["visual_claim_count"] for item in processed),
            "processed_frames_total": sum(item["frame_count"] for item in processed),
            "processed_frames_with_ocr": sum(item["frames_with_ocr"] for item in processed),
        },
        "lectures": lecture_items,
    }


def write_markdown(report: dict[str, Any], path: Path) -> None:
    counts = report["counts"]
    lines = [
        "# Visual Backfill Status",
        "",
        "This report tracks the visual pass needed after transcript-first claim extraction.",
        "",
        "## Current State",
        f"- Lectures total: {counts['lectures_total']}",
        f"- Observed draft done: {counts['lectures_observed_draft_done']}",
        f"- Pending visual review: {counts['lectures_pending_visual_review']}",
        f"- Initial visual backfill done: {counts['lectures_visual_backfill_done']}",
        f"- Processed claims total: {counts['processed_claims_total']}",
        f"- Processed visual claims total: {counts['processed_visual_claims_total']}",
        f"- Processed frames total: {counts['processed_frames_total']}",
        f"- Processed frames with OCR: {counts['processed_frames_with_ocr']}",
        "",
        "## Policy",
        "- Keep existing `claims_observed.jsonl` files; they are the transcript-first pass.",
        "- Add visual review as an additive backfill layer, not a rewrite from scratch.",
        "- When OCR is absent, inspect frame images directly before upgrading visual/chart claims.",
        "- Keep claim maturity as `observed` until visual and later-lecture evidence are consolidated.",
        "",
        "## Lecture Status",
        "| Pos | Title | Claims | Chunks | Chunks With Frames | Frames | OCR Frames | Visual Status |",
        "|---:|---|---:|---:|---:|---:|---:|---|",
    ]
    for item in report["lectures"]:
        lines.append(
            "| {course_position} | {title} | {claim_count} | {chunk_count} | "
            "{chunks_with_frames} | {frame_count} | {frames_with_ocr} | `{visual_backfill_status}` |".format(**item)
        )
    lines.extend(
        [
            "",
            "## Next Backfill Step",
            "Start with the already processed lectures that are visually dense: create frame-aware overviews, inspect key chart frames, and add visual evidence notes without changing the transcript claims unless a visual contradiction is found.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build visual backfill status for lecture_pass.")
    parser.add_argument("--root", type=Path, default=default_root(), help="Workspace root.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory. Defaults to _knowledge_base/structured/lecture_pass under root.",
    )
    return parser.parse_args()


def main() -> int:
    configure_stdio()
    args = parse_args()
    root = args.root.resolve()
    output_dir = args.output_dir or root / "_knowledge_base" / "structured" / "lecture_pass"
    output_dir.mkdir(parents=True, exist_ok=True)
    report = build_status(root)
    json_path = output_dir / "visual_backfill_status.json"
    md_path = output_dir / "visual_backfill_status.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    write_markdown(report, md_path)
    counts = report["counts"]
    print(f"visual_backfill_status={md_path}")
    print(
        "visual_backfill_counts="
        f"done={counts['lectures_observed_draft_done']} "
        f"pending_visual={counts['lectures_pending_visual_review']} "
        f"frames={counts['processed_frames_total']} "
        f"ocr_frames={counts['processed_frames_with_ocr']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())