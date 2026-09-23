from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lecture_terms import TERM_ALIASES, detect_terms, top_terms


DEFAULT_MAX_SECONDS = 150.0
DEFAULT_OVERLAP_SECONDS = 25.0
DEFAULT_MAX_CHARS = 6500


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def seconds_to_time(value: float | int | None) -> str:
    if value is None:
        return "00:00:00"
    total = max(0, int(round(float(value))))
    hours = total // 3600
    minutes = (total % 3600) // 60
    seconds = total % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def normalize_key(value: str | None) -> str:
    if not value:
        return ""
    normalized = value.lower().replace("ё", "е").strip().strip(". ")
    keep = []
    for char in normalized:
        if char.isalnum():
            keep.append(char)
    return "".join(keep)


def short_hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8", errors="ignore")).hexdigest()[:8]


def course_order_info(title: str) -> dict[str, Any]:
    normalized = title.lower().replace("ё", "е").strip()
    if "введ" in normalized or normalized == "ведение":
        return {"kind": "intro", "number": 0, "part": 0, "status": "known"}

    lesson_match = re.search(r"(?:^|\s)урок\s*0*(\d{1,3})(?:\D|$)", normalized)
    if lesson_match:
        part = 0
        part_match = re.search(r"(?:^|\s)ч\s*0*(\d{1,2})(?:\D|$)", normalized)
        if part_match:
            part = int(part_match.group(1))
        elif "обзор" in normalized:
            part = 90
        return {"kind": "lesson", "number": int(lesson_match.group(1)), "part": part, "status": "known"}

    extra_match = re.search(r"(?:^|\s)доп\s*0*(\d{1,3})(?:\D|$)", normalized)
    if extra_match:
        return {"kind": "extra", "number": int(extra_match.group(1)), "part": 0, "status": "known"}

    return {"kind": "unknown", "number": None, "part": 0, "status": "unknown"}


def course_sort_key(path: Path) -> tuple[int, int, int, str]:
    info = course_order_info(path.name)
    kind_rank = {"intro": 0, "lesson": 1, "extra": 2, "unknown": 3}.get(info["kind"], 3)
    number = info["number"] if info["number"] is not None else 9999
    return (kind_rank, int(number), int(info["part"]), normalize_key(path.name))


def display_path(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def manifest_records(root: Path) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    manifest_path = root / "manifest.json"
    if not manifest_path.exists():
        return [], {}
    manifest = load_json(manifest_path)
    records = list(manifest.get("results", [])) if isinstance(manifest, dict) else []
    lookup: dict[str, dict[str, Any]] = {}
    for record in records:
        candidates = []
        for key in ("source", "output_dir", "transcript_json", "transcript_txt", "transcript_md"):
            value = record.get(key)
            if value:
                candidates.append(Path(str(value)).stem)
                candidates.append(Path(str(value)).parent.name)
        for candidate in candidates:
            normalized = normalize_key(candidate)
            if normalized:
                lookup.setdefault(normalized, record)
    return records, lookup


def transcript_dirs(root: Path) -> list[Path]:
    return sorted(
        [path for path in root.iterdir() if path.is_dir() and (path / "transcript.json").exists()],
        key=course_sort_key,
    )


def load_segments(transcript_path: Path) -> list[dict[str, Any]]:
    data = load_json(transcript_path)
    segments = []
    for index, segment in enumerate(data.get("segments", [])):
        text = str(segment.get("text", "")).strip()
        if not text:
            continue
        try:
            start = float(segment.get("start", 0.0))
            end = float(segment.get("end", start))
        except (TypeError, ValueError):
            continue
        if end < start:
            end = start
        segments.append({"index": index, "start": start, "end": end, "text": text})
    return segments


def load_frames(lecture_dir: Path) -> list[dict[str, Any]]:
    visual_path = lecture_dir / "visual_index.json"
    if not visual_path.exists():
        return []
    try:
        visual = load_json(visual_path)
    except json.JSONDecodeError:
        return []
    frames = []
    for frame in visual.get("frames", []):
        try:
            time_value = float(frame.get("time", 0.0))
        except (TypeError, ValueError):
            continue
        frame_path = frame.get("path") or frame.get("source_sample")
        if not frame_path:
            continue
        if frame.get("duplicate_of"):
            # Skip near-duplicate frames in chunk attachment; keep unique anchor.
            continue
        linked = frame.get("linked_segment_index")
        try:
            linked_idx = int(linked) if linked is not None else None
        except (TypeError, ValueError):
            linked_idx = None
        frames.append({
            "time": time_value,
            "path": frame_path,
            "trigger": frame.get("trigger"),
            "scene_index": frame.get("scene_index"),
            "phash": frame.get("phash"),
            "linked_segment_index": linked_idx,
            "ocr_text": (frame.get("ocr_text") or "").strip(),
            "ocr_line_count": frame.get("ocr_line_count") or 0,
        })
    return frames


def frames_for_chunk(
    frames: list[dict[str, Any]],
    segment_indexes: list[int],
    start: float,
    end: float,
    limit: int = 8,
) -> list[dict[str, Any]]:
    # 1. Prefer frames whose linked_segment_index falls within the chunk's segments.
    seg_set = set(segment_indexes)
    selected: list[dict[str, Any]] = []
    if seg_set:
        for frame in frames:
            linked = frame.get("linked_segment_index")
            if linked is not None and linked in seg_set:
                selected.append(frame)
    # 2. Add frames whose time falls inside [start, end] (skip already-selected).
    seen_paths = {f["path"] for f in selected}
    for frame in frames:
        if frame["path"] in seen_paths:
            continue
        if start <= frame["time"] <= end:
            selected.append(frame)
            seen_paths.add(frame["path"])
    # 3. Fallback: nearest frame within 90s of midpoint.
    if not selected and frames:
        midpoint = (start + end) / 2.0
        nearest = min(frames, key=lambda frame: abs(frame["time"] - midpoint))
        if abs(nearest["time"] - midpoint) <= 90:
            selected = [nearest]
    selected.sort(key=lambda frame: frame["time"])
    return selected[:limit]


def make_chunks(
    segments: list[dict[str, Any]],
    max_seconds: float,
    overlap_seconds: float,
    max_chars: int,
) -> list[list[dict[str, Any]]]:
    chunks: list[list[dict[str, Any]]] = []
    index = 0
    while index < len(segments):
        chunk_start = segments[index]["start"]
        chunk_end_limit = chunk_start + max_seconds
        char_count = 0
        end_index = index
        current: list[dict[str, Any]] = []
        while end_index < len(segments):
            segment = segments[end_index]
            segment_text_len = len(segment["text"])
            if current and (segment["start"] >= chunk_end_limit or char_count + segment_text_len > max_chars):
                break
            current.append(segment)
            char_count += segment_text_len + 1
            end_index += 1
        if not current:
            current = [segments[index]]
            end_index = index + 1
        chunks.append(current)

        last_end = current[-1]["end"]
        next_start_target = max(chunk_start + 1.0, last_end - overlap_seconds)
        next_index = index + 1
        while next_index < len(segments) and segments[next_index]["start"] < next_start_target:
            next_index += 1
        if next_index <= index:
            next_index = index + 1
        index = next_index
    return chunks


def build(root: Path, output_dir: Path, max_seconds: float, overlap_seconds: float, max_chars: int) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    records, manifest_lookup = manifest_records(root)
    dirs = transcript_dirs(root)

    chunks_path = output_dir / "lecture_chunks.jsonl"
    lectures: list[dict[str, Any]] = []
    warnings: list[str] = []
    term_counter: Counter[str] = Counter()
    lecture_term_counter: dict[str, Counter[str]] = defaultdict(Counter)
    total_segments = 0
    total_duration = 0.0
    total_frames = 0
    total_chunks = 0

    with chunks_path.open("w", encoding="utf-8", newline="\n") as handle:
        for lecture_number, lecture_dir in enumerate(dirs, start=1):
            lecture_id = f"lec_{lecture_number:03d}_{short_hash(lecture_dir.name)}"
            order_info = course_order_info(lecture_dir.name)
            manifest_record = manifest_lookup.get(normalize_key(lecture_dir.name), {})
            transcript_path = lecture_dir / "transcript.json"
            segments = load_segments(transcript_path)
            frames = load_frames(lecture_dir)
            duration = 0.0
            if segments:
                duration = max(segment["end"] for segment in segments)
            elif manifest_record.get("duration_seconds"):
                duration = float(manifest_record.get("duration_seconds") or 0.0)

            chunk_groups = make_chunks(segments, max_seconds, overlap_seconds, max_chars)
            lecture_chunks = 0
            lecture_terms: Counter[str] = Counter()

            for chunk_number, group in enumerate(chunk_groups):
                text = " ".join(segment["text"] for segment in group).strip()
                start = group[0]["start"]
                end = group[-1]["end"]
                keywords = detect_terms(text)
                for keyword in keywords:
                    term_counter[keyword] += 1
                    lecture_terms[keyword] += 1
                segment_indexes_list = [segment["index"] for segment in group]
                frame_refs = []
                for frame in frames_for_chunk(frames, segment_indexes_list, start, end):
                    frame_path = str(frame["path"]).replace("\\", "/")
                    ref = {
                        "time_sec": frame["time"],
                        "time": seconds_to_time(frame["time"]),
                        "path": f"{lecture_dir.name}/{frame_path}",
                    }
                    if frame.get("trigger"):
                        ref["trigger"] = frame["trigger"]
                    if frame.get("scene_index") is not None:
                        ref["scene_index"] = frame["scene_index"]
                    if frame.get("linked_segment_index") is not None:
                        ref["linked_segment_index"] = frame["linked_segment_index"]
                    if frame.get("ocr_text"):
                        ref["ocr_text"] = frame["ocr_text"]
                        ref["ocr_line_count"] = frame.get("ocr_line_count") or 0
                    frame_refs.append(ref)
                record = {
                    "chunk_id": f"{lecture_id}_{chunk_number:04d}",
                    "lecture_id": lecture_id,
                    "lecture_title": lecture_dir.name,
                    "lecture_dir": lecture_dir.name,
                    "course_position": lecture_number,
                    "course_kind": order_info["kind"],
                    "course_number": order_info["number"],
                    "course_part": order_info["part"],
                    "course_order_status": order_info["status"],
                    "source_video": manifest_record.get("source"),
                    "artifact_type": "transcript_chunk",
                    "start_sec": round(start, 2),
                    "end_sec": round(end, 2),
                    "start_time": seconds_to_time(start),
                    "end_time": seconds_to_time(end),
                    "duration_sec": round(end - start, 2),
                    "segment_indexes": segment_indexes_list,
                    "keywords": keywords,
                    "frames": frame_refs,
                    "text": text,
                    "estimated_tokens": math.ceil(len(text) / 4),
                }
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                lecture_chunks += 1

            total_segments += len(segments)
            total_duration += duration
            total_frames += len(frames)
            total_chunks += lecture_chunks
            lecture_term_counter[lecture_id] = lecture_terms
            lectures.append(
                {
                    "lecture_id": lecture_id,
                    "title": lecture_dir.name,
                    "lecture_dir": lecture_dir.name,
                    "course_position": lecture_number,
                    "course_kind": order_info["kind"],
                    "course_number": order_info["number"],
                    "course_part": order_info["part"],
                    "course_order_status": order_info["status"],
                    "source_video": manifest_record.get("source"),
                    "duration_sec": round(duration, 2),
                    "duration": seconds_to_time(duration),
                    "segment_count": len(segments),
                    "chunk_count": lecture_chunks,
                    "frame_count": len(frames),
                    "top_keywords": [term for term, _ in lecture_terms.most_common(12)],
                    "top_words": top_terms(" ".join(segment["text"] for segment in segments), limit=12),
                }
            )

            if not segments:
                warnings.append(f"{lecture_dir.name}: transcript.json has no readable segments")
            if not frames:
                warnings.append(f"{lecture_dir.name}: visual_index.json has no readable frames")

    index = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_root": str(root),
        "chunking": {
            "max_seconds": max_seconds,
            "overlap_seconds": overlap_seconds,
            "max_chars": max_chars,
        },
        "totals": {
            "manifest_records": len(records),
            "lectures": len(lectures),
            "chunks": total_chunks,
            "segments": total_segments,
            "duration_hours": round(total_duration / 3600, 2),
            "frames": total_frames,
        },
        "term_aliases": TERM_ALIASES,
        "top_keywords": term_counter.most_common(40),
        "lectures": lectures,
        "warnings": warnings,
        "files": {
            "chunks_jsonl": display_path(chunks_path, root),
            "lecture_index": display_path(output_dir / "lecture_index.json", root),
            "build_report": display_path(output_dir / "build_report.md", root),
            "course_order_json": display_path(output_dir / "course_order.json", root),
            "course_order_report": display_path(output_dir / "course_order.md", root),
        },
    }
    write_json(output_dir / "lecture_index.json", index)
    write_course_order_files(output_dir, index)
    write_report(root, output_dir / "build_report.md", index)
    return index


def write_course_order_files(output_dir: Path, index: dict[str, Any]) -> None:
    lectures = index.get("lectures", [])
    known = [lecture for lecture in lectures if lecture.get("course_order_status") == "known"]
    unknown = [lecture for lecture in lectures if lecture.get("course_order_status") != "known"]
    payload = {
        "generated_at": index.get("generated_at"),
        "source_root": index.get("source_root"),
        "status": "draft_order_pending_unknown_review",
        "known_order_count": len(known),
        "unknown_order_count": len(unknown),
        "lectures": [
            {
                "course_position": lecture.get("course_position"),
                "course_kind": lecture.get("course_kind"),
                "course_number": lecture.get("course_number"),
                "course_part": lecture.get("course_part"),
                "course_order_status": lecture.get("course_order_status"),
                "title": lecture.get("title"),
                "lecture_id": lecture.get("lecture_id"),
                "duration": lecture.get("duration"),
                "segment_count": lecture.get("segment_count"),
                "chunk_count": lecture.get("chunk_count"),
                "frame_count": lecture.get("frame_count"),
                "source_video": lecture.get("source_video"),
            }
            for lecture in lectures
        ],
    }
    write_json(output_dir / "course_order.json", payload)

    lines = [
        "# Course Order Draft",
        "",
        f"Generated at: `{index.get('generated_at')}`",
        f"Source root: `{index.get('source_root')}`",
        "",
        "This file fixes the known course order before rule extraction. Unknown-order records stay at the end until reviewed.",
        "",
        "## Known Course Order",
        "",
        "| Pos | Kind | Number | Part | Title | Duration | Chunks | Frames |",
        "|---:|---|---:|---:|---|---:|---:|---:|",
    ]
    for lecture in known:
        lines.append(
            "| "
            f"{lecture.get('course_position')} | "
            f"{lecture.get('course_kind')} | "
            f"{lecture.get('course_number')} | "
            f"{lecture.get('course_part')} | "
            f"{lecture.get('title')} | "
            f"{lecture.get('duration')} | "
            f"{lecture.get('chunk_count')} | "
            f"{lecture.get('frame_count')} |"
        )

    lines.extend(
        [
            "",
            "## Unknown Order",
            "",
            "| Pos | Title | Duration | Chunks | Frames | Source |",
            "|---:|---|---:|---:|---:|---|",
        ]
    )
    for lecture in unknown:
        source = str(lecture.get("source_video") or "").replace("\\", "/")
        lines.append(
            "| "
            f"{lecture.get('course_position')} | "
            f"{lecture.get('title')} | "
            f"{lecture.get('duration')} | "
            f"{lecture.get('chunk_count')} | "
            f"{lecture.get('frame_count')} | "
            f"{source} |"
        )
    lines.append("")
    (output_dir / "course_order.md").write_text("\n".join(lines), encoding="utf-8")


def write_report(root: Path, report_path: Path, index: dict[str, Any]) -> None:
    totals = index["totals"]
    lines = [
        "# Lecture Knowledge Base Build Report",
        "",
        f"Generated at: `{index['generated_at']}`",
        f"Source root: `{index['source_root']}`",
        "",
        "## Totals",
        "",
        f"- Lectures: {totals['lectures']}",
        f"- Duration: {totals['duration_hours']} h",
        f"- Transcript segments: {totals['segments']}",
        f"- RAG chunks: {totals['chunks']}",
        f"- Visual frames indexed: {totals['frames']}",
        "",
        "## Chunking",
        "",
        f"- Max seconds: {index['chunking']['max_seconds']}",
        f"- Overlap seconds: {index['chunking']['overlap_seconds']}",
        f"- Max chars: {index['chunking']['max_chars']}",
        "",
        "## Output Files",
        "",
        f"- `{index['files']['chunks_jsonl']}`",
        f"- `{index['files']['lecture_index']}`",
        f"- `{index['files']['build_report']}`",
        "",
        "## Top Keywords",
        "",
    ]
    for term, count in index["top_keywords"][:30]:
        lines.append(f"- `{term}`: {count}")

    lines.extend(["", "## Longest Lectures", ""])
    for lecture in sorted(index["lectures"], key=lambda item: item["duration_sec"], reverse=True)[:12]:
        lines.append(
            f"- {lecture['title']} — {lecture['duration']}, "
            f"{lecture['segment_count']} segments, {lecture['chunk_count']} chunks"
        )

    if index["warnings"]:
        lines.extend(["", "## Warnings", ""])
        for warning in index["warnings"]:
            lines.append(f"- {warning}")
    else:
        lines.extend(["", "## Warnings", "", "- None"])

    lines.extend(
        [
            "",
            "## Smoke Test",
            "",
            "```powershell",
            "python .\\knowledge_bot\\search_lectures.py \"БСУ БПУ уровень\" --top 5",
            "python .\\knowledge_bot\\search_lectures.py \"ложный пробой после резкого подхода\" --top 5",
            "python .\\knowledge_bot\\search_lectures.py \"куда ставить стоп при пробое\" --top 5",
            "python .\\knowledge_bot\\make_rag_prompt.py \"объясни БСУ и БПУ и когда модель входа ломается\" --top 8 --out .\\_knowledge_base\\prompt_bsu_bpu.md",
            "```",
            "",
        ]
    )
    report_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a local RAG-ready knowledge base from lecture transcripts.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--max-seconds", type=float, default=DEFAULT_MAX_SECONDS)
    parser.add_argument("--overlap-seconds", type=float, default=DEFAULT_OVERLAP_SECONDS)
    parser.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
    args = parser.parse_args()

    root = args.root.resolve()
    output_dir = (args.output or (root / "_knowledge_base")).resolve()
    index = build(root, output_dir, args.max_seconds, args.overlap_seconds, args.max_chars)
    totals = index["totals"]
    print(
        f"Built {totals['chunks']} chunks from {totals['lectures']} lectures "
        f"({totals['duration_hours']} h, {totals['segments']} segments)."
    )
    print(f"Output: {output_dir}")


if __name__ == "__main__":
    main()