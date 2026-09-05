from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def default_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_positions(value: str | None) -> set[int] | None:
    if not value:
        return None
    positions: set[int] = set()
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_text, end_text = part.split("-", 1)
            start = int(start_text.strip())
            end = int(end_text.strip())
            if end < start:
                raise ValueError(f"bad range: {part}")
            positions.update(range(start, end + 1))
        else:
            positions.add(int(part))
    return positions


def preview_text(value: str, limit: int) -> str:
    compact = " ".join((value or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "..."


def slugify_title(title: str) -> str:
    lowered = title.lower().replace("ё", "е")
    replacements = {
        "ведение": "vedenie",
        "урок": "urok",
        "доп": "dop",
        "часть": "chast",
        "ч": "ch",
        "обзор": "obzor",
    }
    for source, target in replacements.items():
        lowered = lowered.replace(source, target)
    lowered = re.sub(r"[^a-z0-9]+", "_", lowered)
    return lowered.strip("_") or "lecture"


def fallback_folder(root: Path, lecture: dict[str, Any]) -> Path:
    position = int(lecture["course_position"])
    return root / "_knowledge_base" / "structured" / "lecture_pass" / "lectures" / (
        f"lecture_{position:03d}_{slugify_title(lecture['title'])}"
    )


def load_chunks(root: Path) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    chunks_path = root / "_knowledge_base" / "lecture_chunks.jsonl"
    with chunks_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            chunk = json.loads(line)
            position = chunk.get("course_position")
            if position is not None:
                grouped[int(position)].append(chunk)
    for chunks in grouped.values():
        chunks.sort(key=lambda item: (float(item.get("start_sec") or 0), item.get("chunk_id") or ""))
    return grouped


def raw_frame_ocr_lookup(root: Path, lecture_dir: str | None) -> dict[str, dict[str, Any]]:
    if not lecture_dir:
        return {}
    visual_path = root / "_new_lecture_corpus" / lecture_dir / "visual_index.json"
    if not visual_path.exists():
        return {}
    visual = load_json(visual_path)
    lookup: dict[str, dict[str, Any]] = {}
    for frame in visual.get("frames") or []:
        rel_path = frame.get("path")
        if not rel_path:
            continue
        full_rel_path = f"{lecture_dir}/{rel_path}".replace("\\", "/")
        lookup[full_rel_path] = frame
    return lookup


def enriched_frames(root: Path, chunk: dict[str, Any], raw_lookup: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    frames = []
    for frame in chunk.get("frames") or []:
        merged = dict(frame)
        raw_frame = raw_lookup.get(str(frame.get("path") or "").replace("\\", "/"))
        if raw_frame:
            for key in ("ocr_text", "ocr_line_count", "ocr_error", "ocr_repair_version", "ocr_processed_at"):
                if raw_frame.get(key) not in (None, ""):
                    merged[key] = raw_frame[key]
        frames.append(merged)
    return frames


def frame_line(frame: dict[str, Any], ocr_preview_chars: int) -> list[str]:
    ocr_text = (frame.get("ocr_text") or "").strip()
    parts = [
        str(frame.get("time") or "00:00:00"),
        f"path={frame.get('path')}",
    ]
    if frame.get("trigger"):
        parts.append(f"trigger={frame.get('trigger')}")
    if frame.get("scene_index") is not None:
        parts.append(f"scene={frame.get('scene_index')}")
    if frame.get("linked_segment_index") is not None:
        parts.append(f"segment={frame.get('linked_segment_index')}")
    if frame.get("ocr_line_count") is not None:
        parts.append(f"ocr_lines={frame.get('ocr_line_count')}")
    elif ocr_text:
        parts.append("ocr_lines=unknown")
    lines = ["- " + " | ".join(parts)]
    if ocr_text:
        lines.append(f"  ocr: {preview_text(ocr_text, ocr_preview_chars)}")
    return lines


def write_overview(
    root: Path,
    lecture: dict[str, Any],
    chunks: list[dict[str, Any]],
    max_frames_per_chunk: int,
    ocr_preview_chars: int,
) -> dict[str, Any]:
    folder_text = lecture.get("folder")
    folder = (root / folder_text) if folder_text else fallback_folder(root, lecture)
    folder.mkdir(parents=True, exist_ok=True)

    frame_count = 0
    ocr_frame_count = 0
    raw_lookup = raw_frame_ocr_lookup(root, chunks[0].get("lecture_dir") if chunks else None)
    lines = [
        "# Frame-Aware Source Overview",
        "",
        f"generated_at: {datetime.now(timezone.utc).isoformat()}",
        f"course_position: {lecture['course_position']}",
        f"title: {lecture['title']}",
        f"lecture_id: {lecture['lecture_id']}",
        f"extraction_status: {lecture.get('extraction_status')}",
        "evidence_mode: transcript + frame refs + OCR previews",
        "",
    ]
    for chunk in chunks:
        frames = enriched_frames(root, chunk, raw_lookup)
        frame_count += len(frames)
        ocr_frame_count += sum(1 for frame in frames if (frame.get("ocr_text") or "").strip())
        keywords = ", ".join(chunk.get("keywords") or [])
        lines.extend(
            [
                f"## {lecture['course_position']} | {lecture['title']} | "
                f"{chunk.get('start_time')}-{chunk.get('end_time')} | {chunk.get('chunk_id')}",
                f"keywords: {keywords}",
                "",
                "text:",
                chunk.get("text") or "",
                "",
                f"frames: {len(frames)}",
            ]
        )
        if frames:
            shown_frames = frames[:max_frames_per_chunk]
            for frame in shown_frames:
                lines.extend(frame_line(frame, ocr_preview_chars))
            hidden = len(frames) - len(shown_frames)
            if hidden > 0:
                lines.append(f"- ... {hidden} more frame(s) omitted in overview")
        lines.append("")

    output_path = folder / "source_overview_frame_aware.txt"
    output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8", newline="\n")
    return {
        "course_position": lecture["course_position"],
        "title": lecture["title"],
        "folder": str(folder.relative_to(root)).replace("\\", "/"),
        "chunks": len(chunks),
        "frames": frame_count,
        "ocr_frames": ocr_frame_count,
        "output": str(output_path.relative_to(root)).replace("\\", "/"),
    }


def build_overviews(
    root: Path,
    positions: set[int] | None = None,
    observed_only: bool = True,
    max_frames_per_chunk: int = 6,
    ocr_preview_chars: int = 220,
) -> dict[str, Any]:
    lecture_pass = root / "_knowledge_base" / "structured" / "lecture_pass"
    index = load_json(lecture_pass / "lecture_pass_index.json")
    chunks_by_position = load_chunks(root)
    items = []
    for lecture in index["lectures"]:
        position = int(lecture["course_position"])
        if positions is not None and position not in positions:
            continue
        if observed_only and lecture.get("extraction_status") != "observed_draft_done":
            continue
        chunks = chunks_by_position.get(position, [])
        if not chunks:
            continue
        items.append(write_overview(root, lecture, chunks, max_frames_per_chunk, ocr_preview_chars))

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "observed_only": observed_only,
        "positions": sorted(positions) if positions else None,
        "counts": {
            "lectures": len(items),
            "chunks": sum(item["chunks"] for item in items),
            "frames": sum(item["frames"] for item in items),
            "ocr_frames": sum(item["ocr_frames"] for item in items),
        },
        "lectures": items,
    }
    report_path = lecture_pass / "frame_aware_overview_status.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build frame-aware source overviews for lecture_pass.")
    parser.add_argument("--root", type=Path, default=default_root())
    parser.add_argument("--positions", help="Course positions, e.g. 1-16 or 10,16.")
    parser.add_argument("--all", action="store_true", help="Include pending lectures too.")
    parser.add_argument("--max-frames-per-chunk", type=int, default=6)
    parser.add_argument("--ocr-preview-chars", type=int, default=220)
    return parser.parse_args()


def main() -> int:
    configure_stdio()
    args = parse_args()
    report = build_overviews(
        root=args.root.resolve(),
        positions=parse_positions(args.positions),
        observed_only=not args.all,
        max_frames_per_chunk=args.max_frames_per_chunk,
        ocr_preview_chars=args.ocr_preview_chars,
    )
    counts = report["counts"]
    print(
        "frame_aware_overviews="
        f"lectures={counts['lectures']} chunks={counts['chunks']} "
        f"frames={counts['frames']} ocr_frames={counts['ocr_frames']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())