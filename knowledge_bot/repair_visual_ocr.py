from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ingest_raw_lectures import get_ocr_reader, ocr_frame, release_ocr_reader


REPAIR_VERSION = "ocr_unicode_path_repair_v1"


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


def course_position_dirs(root: Path, positions: set[int] | None) -> list[tuple[int, Path]]:
    order_path = root / "_knowledge_base" / "course_order.json"
    order = load_json(order_path)
    items: list[tuple[int, Path]] = []
    for lecture in order["lectures"]:
        pos = int(lecture["course_position"])
        if positions is not None and pos not in positions:
            continue
        lecture_dir = root / "_new_lecture_corpus" / lecture["title"]
        if lecture_dir.exists() and (lecture_dir / "visual_index.json").exists():
            items.append((pos, lecture_dir))
        else:
            print(f"warning: missing visual_index for position={pos} title={lecture['title']}", flush=True)
    return items


def backup_once(path: Path) -> None:
    backup_path = path.with_suffix(path.suffix + ".before_ocr_repair")
    if not backup_path.exists():
        shutil.copy2(path, backup_path)


def repair_lecture(
    pos: int,
    lecture_dir: Path,
    reader: Any,
    min_conf: float,
    overwrite: bool,
    limit_frames: int | None,
    checkpoint_frames: int,
    progress_every: int,
    max_dimension: int | None,
) -> dict[str, Any]:
    visual_path = lecture_dir / "visual_index.json"
    visual = load_json(visual_path)
    frames = visual.get("frames") or []

    ocr_path = lecture_dir / "visual_ocr.json"
    ocr_records_by_path: dict[str, dict[str, Any]] = {}
    if ocr_path.exists():
        try:
            existing_ocr = load_json(ocr_path)
            for item in existing_ocr.get("frames") or []:
                path = item.get("path")
                if path:
                    ocr_records_by_path[path] = item
        except json.JSONDecodeError:
            pass

    targets = []
    for frame in frames:
        if frame.get("duplicate_of"):
            continue
        rel_path = frame.get("path") or frame.get("source_sample")
        existing_ocr_record = ocr_records_by_path.get(rel_path)
        already_repaired = frame.get("ocr_repair_version") == REPAIR_VERSION or (
            existing_ocr_record is not None and existing_ocr_record.get("repair_version") == REPAIR_VERSION
        )
        if not overwrite and (already_repaired or (frame.get("ocr_text") or "").strip()):
            continue
        targets.append(frame)
    if limit_frames is not None:
        targets = targets[:limit_frames]

    repaired = 0
    errors = 0
    nonempty = 0

    def write_artifacts() -> None:
        backup_once(visual_path)
        visual["ocr_engine"] = "easyocr"
        visual["ocr_languages"] = visual.get("ocr_languages") or ["ru", "en"]
        visual["ocr_repaired_at"] = datetime.now(timezone.utc).isoformat()
        visual["ocr_repair_version"] = REPAIR_VERSION
        visual_path.write_text(json.dumps(visual, ensure_ascii=False, indent=2), encoding="utf-8")

        if ocr_path.exists():
            backup_once(ocr_path)
        ocr_payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "engine": "easyocr",
            "languages": visual.get("ocr_languages") or ["ru", "en"],
            "min_conf": min_conf,
            "max_dimension": max_dimension,
            "repair_version": REPAIR_VERSION,
            "frames": list(ocr_records_by_path.values()),
        }
        ocr_path.write_text(json.dumps(ocr_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    for idx, frame in enumerate(targets, start=1):
        rel_path = frame.get("path") or frame.get("source_sample")
        if not rel_path:
            errors += 1
            continue
        full_path = lecture_dir / rel_path
        if progress_every and (idx == 1 or idx % progress_every == 0):
            print(f"  position={pos} {lecture_dir.name}: ocr {idx}/{len(targets)} path={rel_path}", flush=True)
        start = time.perf_counter()
        ocr = ocr_frame(reader, full_path, min_conf, max_dimension=max_dimension)
        elapsed = time.perf_counter() - start
        if ocr.get("error"):
            errors += 1
        text = ocr.get("text", "")
        lines = ocr.get("lines", [])
        frame["ocr_text"] = text
        frame["ocr_line_count"] = len(lines)
        frame["ocr_repair_version"] = REPAIR_VERSION
        frame["ocr_processed_at"] = datetime.now(timezone.utc).isoformat()
        if ocr.get("error"):
            frame["ocr_error"] = ocr["error"]
        else:
            frame.pop("ocr_error", None)
        repaired += 1
        if text.strip():
            nonempty += 1
        ocr_records_by_path[rel_path] = {
            "path": rel_path,
            "time": frame.get("time"),
            "text": text,
            "lines": lines,
            "repair_version": REPAIR_VERSION,
            **({"error": ocr["error"]} if ocr.get("error") else {}),
        }
        if progress_every and (idx == 1 or idx % progress_every == 0 or idx == len(targets)):
            print(
                f"  position={pos} {lecture_dir.name}: done {idx}/{len(targets)} "
                f"elapsed={elapsed:.1f}s nonempty={nonempty} errors={errors}",
                flush=True,
            )
        if checkpoint_frames and repaired % checkpoint_frames == 0:
            write_artifacts()

    if repaired:
        write_artifacts()

    return {
        "course_position": pos,
        "title": lecture_dir.name,
        "frames_total": len(frames),
        "targets": len(targets),
        "repaired": repaired,
        "ocr_nonempty": nonempty,
        "ocr_errors": errors,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Repair OCR for existing visual_index frame images.")
    parser.add_argument("--root", type=Path, default=default_root(), help="Workspace root.")
    parser.add_argument("--positions", help="Course positions to repair, e.g. 1-16 or 9,13-16.")
    parser.add_argument("--languages", default="ru,en", help="EasyOCR languages, comma-separated.")
    parser.add_argument("--min-conf", type=float, default=0.3, help="Minimum OCR confidence to keep.")
    parser.add_argument("--gpu", action="store_true", help="Use EasyOCR GPU mode.")
    parser.add_argument("--overwrite", action="store_true", help="Recompute frames that already have OCR text.")
    parser.add_argument("--limit-frames", type=int, default=None, help="Repair only first N target frames per lecture.")
    parser.add_argument("--checkpoint-frames", type=int, default=10, help="Write progress every N repaired frames.")
    parser.add_argument("--progress-every", type=int, default=1, help="Print progress every N frames.")
    parser.add_argument("--max-dimension", type=int, default=960, help="Resize frames to this max side before OCR; 0 keeps original size.")
    return parser.parse_args()


def main() -> int:
    configure_stdio()
    args = parse_args()
    root = args.root.resolve()
    positions = parse_positions(args.positions)
    languages = [item.strip() for item in args.languages.split(",") if item.strip()]
    max_dimension = args.max_dimension or None
    lectures = course_position_dirs(root, positions)
    if not lectures:
        print("no lectures selected")
        return 0
    print(f"selected_lectures={len(lectures)} positions={[pos for pos, _ in lectures]}", flush=True)
    reader = get_ocr_reader(languages, args.gpu)
    results = []
    try:
        for pos, lecture_dir in lectures:
            print(f"repairing position={pos} title={lecture_dir.name}", flush=True)
            results.append(
                repair_lecture(
                    pos=pos,
                    lecture_dir=lecture_dir,
                    reader=reader,
                    min_conf=args.min_conf,
                    overwrite=args.overwrite,
                    limit_frames=args.limit_frames,
                    checkpoint_frames=args.checkpoint_frames,
                    progress_every=args.progress_every,
                    max_dimension=max_dimension,
                )
            )
    finally:
        release_ocr_reader()

    print("ocr_repair_summary")
    for result in results:
        print(json.dumps(result, ensure_ascii=False))
    print(
        "ocr_repair_totals="
        f"lectures={len(results)} "
        f"repaired={sum(item['repaired'] for item in results)} "
        f"nonempty={sum(item['ocr_nonempty'] for item in results)} "
        f"errors={sum(item['ocr_errors'] for item in results)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())