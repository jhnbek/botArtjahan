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


def seconds_to_time(value: float | int | None) -> str:
    if value is None:
        return "00:00:00"
    total = max(0, int(round(float(value))))
    hours = total // 3600
    minutes = (total % 3600) // 60
    seconds = total % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def parse_time_value(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        try:
            return float(value)
        except ValueError:
            pass
        parts = value.split(":")
        if len(parts) == 3:
            try:
                hours, minutes, seconds = parts
                return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
            except ValueError:
                return None
    return None


def preview_text(value: str, limit: int) -> str:
    compact = " ".join((value or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "..."


def markdown_preview_text(value: str, limit: int) -> str:
    preview = preview_text(value, limit)
    return preview.replace("\\", "\\\\").replace("_", "\\_").replace("*", "\\*")


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
    return root / "_knowledge_base" / "structured" / "lecture_pass" / "lectures" / (
        f"lecture_{int(lecture['course_position']):03d}_{slugify_title(lecture['title'])}"
    )


def selected_lectures(root: Path, positions: set[int] | None, observed_only: bool) -> list[dict[str, Any]]:
    index = load_json(root / "_knowledge_base" / "structured" / "lecture_pass" / "lecture_pass_index.json")
    lectures = []
    for lecture in index["lectures"]:
        position = int(lecture["course_position"])
        if positions is not None and position not in positions:
            continue
        if observed_only and lecture.get("extraction_status") != "observed_draft_done":
            continue
        lectures.append(lecture)
    return lectures


def load_segments(transcript_path: Path) -> list[dict[str, Any]]:
    transcript = load_json(transcript_path)
    segments = []
    for index, segment in enumerate(transcript.get("segments") or []):
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        start = parse_time_value(segment.get("start"))
        end = parse_time_value(segment.get("end"))
        if start is None:
            continue
        if end is None or end < start:
            end = start
        segments.append({"index": index, "start": start, "end": end, "text": text})
    return segments


def load_frames(lecture_dir: Path, lecture_title: str) -> list[dict[str, Any]]:
    visual_path = lecture_dir / "visual_index.json"
    if not visual_path.exists():
        return []
    visual = load_json(visual_path)
    frames = []
    for frame in visual.get("frames") or []:
        if frame.get("duplicate_of"):
            continue
        time_sec = parse_time_value(frame.get("time_sec", frame.get("time")))
        if time_sec is None:
            continue
        frame_path = frame.get("path") or frame.get("source_sample")
        if not frame_path:
            continue
        linked = frame.get("linked_segment_index")
        try:
            linked_idx = int(linked) if linked is not None else None
        except (TypeError, ValueError):
            linked_idx = None
        ocr_text = str(frame.get("ocr_text") or "").strip()
        frames.append(
            {
                "time_sec": time_sec,
                "time": seconds_to_time(time_sec),
                "path": f"{lecture_title}/{frame_path}".replace("\\", "/"),
                "trigger": frame.get("trigger"),
                "scene_index": frame.get("scene_index"),
                "linked_segment_index": linked_idx,
                "ocr_text": ocr_text,
                "ocr_line_count": int(frame.get("ocr_line_count") or 0),
                **({"ocr_error": frame.get("ocr_error")} if frame.get("ocr_error") else {}),
                **({"ocr_repair_version": frame.get("ocr_repair_version")} if frame.get("ocr_repair_version") else {}),
            }
        )
    frames.sort(key=lambda item: item["time_sec"])
    return frames


def attach_frames_to_segments(segments: list[dict[str, Any]], frames: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    attached: dict[int, list[dict[str, Any]]] = defaultdict(list)
    segment_by_index = {segment["index"]: segment for segment in segments}

    for frame in frames:
        linked = frame.get("linked_segment_index")
        if linked in segment_by_index:
            attached[int(linked)].append(frame)
            continue
        frame_time = float(frame["time_sec"])
        for segment in segments:
            if segment["start"] <= frame_time <= segment["end"]:
                attached[segment["index"]].append(frame)
                break

    for segment_index, segment_frames in attached.items():
        seen = set()
        deduped = []
        for frame in sorted(segment_frames, key=lambda item: item["time_sec"]):
            if frame["path"] in seen:
                continue
            seen.add(frame["path"])
            deduped.append(frame)
        attached[segment_index] = deduped
    return attached


def make_unit(lecture: dict[str, Any], segment: dict[str, Any], frames: list[dict[str, Any]]) -> dict[str, Any]:
    position = int(lecture["course_position"])
    segment_index = int(segment["index"])
    return {
        "unit_id": f"{lecture['lecture_id']}_seg_{segment_index:04d}",
        "artifact_type": "multimodal_evidence_unit",
        "course_position": position,
        "lecture_title": lecture["title"],
        "lecture_id": lecture["lecture_id"],
        "segment_index": segment_index,
        "start_sec": round(float(segment["start"]), 3),
        "end_sec": round(float(segment["end"]), 3),
        "time": f"{seconds_to_time(segment['start'])}-{seconds_to_time(segment['end'])}",
        "text": segment["text"],
        "frames": frames,
        "frame_count": len(frames),
        "ocr_frame_count": sum(1 for frame in frames if frame.get("ocr_text")),
        "ocr_chars": sum(len(frame.get("ocr_text") or "") for frame in frames),
    }


def write_markdown(units: list[dict[str, Any]], output_path: Path, ocr_preview_chars: int) -> None:
    if not units:
        output_path.write_text("# Multimodal Evidence Units\n\nNo units generated.\n", encoding="utf-8")
        return
    first = units[0]
    lines = [
        "# Multimodal Evidence Units",
        "",
        f"generated_at: {datetime.now(timezone.utc).isoformat()}",
        f"course_position: {first['course_position']}",
        f"title: {first['lecture_title']}",
        f"lecture_id: {first['lecture_id']}",
        "evidence_mode: exact transcript segment + timestamp-matched frames + OCR",
        "",
    ]
    for unit in units:
        lines.extend(
            [
                f"## {unit['unit_id']} | {unit['time']}",
                "",
                "text:",
                unit["text"],
                "",
                f"frames: {unit['frame_count']} | ocr_frames: {unit['ocr_frame_count']}",
            ]
        )
        if unit["frames"]:
            lines.append("")
        for frame in unit["frames"]:
            details = [frame["time"], f"path={frame['path']}"]
            if frame.get("trigger"):
                details.append(f"trigger={frame['trigger']}")
            if frame.get("scene_index") is not None:
                details.append(f"scene={frame['scene_index']}")
            if frame.get("ocr_line_count") is not None:
                details.append(f"ocr_lines={frame['ocr_line_count']}")
            lines.append("- " + " | ".join(details))
            if frame.get("ocr_text"):
                lines.append(f"  ocr: {markdown_preview_text(frame['ocr_text'], ocr_preview_chars)}")
        lines.append("")
    output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8", newline="\n")


def build_for_lecture(root: Path, lecture: dict[str, Any], ocr_preview_chars: int) -> dict[str, Any]:
    lecture_title = lecture["title"]
    raw_dir = root / "_new_lecture_corpus" / lecture_title
    segments = load_segments(raw_dir / "transcript.json")
    frames = load_frames(raw_dir, lecture_title)
    frames_by_segment = attach_frames_to_segments(segments, frames)
    units = [make_unit(lecture, segment, frames_by_segment.get(segment["index"], [])) for segment in segments]

    folder_text = lecture.get("folder")
    folder = (root / folder_text) if folder_text else fallback_folder(root, lecture)
    folder.mkdir(parents=True, exist_ok=True)

    jsonl_path = folder / "source_multimodal_units.jsonl"
    with jsonl_path.open("w", encoding="utf-8", newline="\n") as handle:
        for unit in units:
            handle.write(json.dumps(unit, ensure_ascii=False) + "\n")

    md_path = folder / "source_multimodal_units.md"
    write_markdown(units, md_path, ocr_preview_chars)

    return {
        "course_position": lecture["course_position"],
        "title": lecture_title,
        "segments": len(segments),
        "units": len(units),
        "units_with_frames": sum(1 for unit in units if unit["frame_count"]),
        "frames": sum(unit["frame_count"] for unit in units),
        "ocr_frames": sum(unit["ocr_frame_count"] for unit in units),
        "ocr_chars": sum(unit["ocr_chars"] for unit in units),
        "jsonl": str(jsonl_path.relative_to(root)).replace("\\", "/"),
        "markdown": str(md_path.relative_to(root)).replace("\\", "/"),
    }


def build_multimodal_units(
    root: Path,
    positions: set[int] | None = None,
    observed_only: bool = True,
    ocr_preview_chars: int = 220,
) -> dict[str, Any]:
    lectures = selected_lectures(root, positions, observed_only)
    items = [build_for_lecture(root, lecture, ocr_preview_chars) for lecture in lectures]
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "observed_only": observed_only,
        "positions": sorted(positions) if positions else None,
        "counts": {
            "lectures": len(items),
            "units": sum(item["units"] for item in items),
            "units_with_frames": sum(item["units_with_frames"] for item in items),
            "frames": sum(item["frames"] for item in items),
            "ocr_frames": sum(item["ocr_frames"] for item in items),
            "ocr_chars": sum(item["ocr_chars"] for item in items),
        },
        "lectures": items,
    }
    report_path = root / "_knowledge_base" / "structured" / "lecture_pass" / "multimodal_evidence_status.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build exact timestamp multimodal evidence units for lecture extraction.")
    parser.add_argument("--root", type=Path, default=default_root())
    parser.add_argument("--positions", help="Course positions, e.g. 1-16 or 1,3.")
    parser.add_argument("--all", action="store_true", help="Include pending lectures too.")
    parser.add_argument("--ocr-preview-chars", type=int, default=220)
    return parser.parse_args()


def main() -> int:
    configure_stdio()
    args = parse_args()
    report = build_multimodal_units(
        root=args.root.resolve(),
        positions=parse_positions(args.positions),
        observed_only=not args.all,
        ocr_preview_chars=args.ocr_preview_chars,
    )
    counts = report["counts"]
    print(
        "multimodal_evidence_units="
        f"lectures={counts['lectures']} units={counts['units']} "
        f"units_with_frames={counts['units_with_frames']} "
        f"frames={counts['frames']} ocr_frames={counts['ocr_frames']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
