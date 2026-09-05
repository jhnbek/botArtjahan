from __future__ import annotations

import argparse
import ctypes
import sys
from pathlib import Path

from build_knowledge_base import (
    DEFAULT_MAX_CHARS,
    DEFAULT_MAX_SECONDS,
    DEFAULT_OVERLAP_SECONDS,
    build,
)
from build_visual_backfill_status import build_status, write_markdown
from build_frame_aware_overviews import build_overviews
from build_multimodal_evidence_units import build_multimodal_units


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def default_root() -> Path:
    return Path(__file__).resolve().parents[1]


def wait_for_windows_pid(pid: int) -> None:
    synchronize = 0x00100000
    infinite = 0xFFFFFFFF
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(synchronize, False, pid)
    if not handle:
        print(f"pid={pid} is not running; rebuilding now", flush=True)
        return
    try:
        print(f"waiting_for_pid={pid}", flush=True)
        kernel32.WaitForSingleObject(handle, infinite)
    finally:
        kernel32.CloseHandle(handle)


def rebuild(root: Path) -> None:
    source_root = root / "_new_lecture_corpus"
    output_dir = root / "_knowledge_base"
    index = build(source_root, output_dir, DEFAULT_MAX_SECONDS, DEFAULT_OVERLAP_SECONDS, DEFAULT_MAX_CHARS)
    totals = index["totals"]
    print(
        f"rebuilt_kb chunks={totals['chunks']} lectures={totals['lectures']} "
        f"segments={totals['segments']} frames={totals['frames']}",
        flush=True,
    )

    lecture_pass = output_dir / "structured" / "lecture_pass"
    report = build_status(root)
    (lecture_pass / "visual_backfill_status.json").write_text(
        __import__("json").dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )
    write_markdown(report, lecture_pass / "visual_backfill_status.md")
    counts = report["counts"]
    print(
        "rebuilt_visual_backfill "
        f"done={counts['lectures_observed_draft_done']} "
        f"pending_visual={counts['lectures_pending_visual_review']} "
        f"frames={counts['processed_frames_total']} "
        f"ocr_frames={counts['processed_frames_with_ocr']}",
        flush=True,
    )

    overview_report = build_overviews(root)
    overview_counts = overview_report["counts"]
    print(
        "rebuilt_frame_aware_overviews "
        f"lectures={overview_counts['lectures']} "
        f"chunks={overview_counts['chunks']} "
        f"frames={overview_counts['frames']} "
        f"ocr_frames={overview_counts['ocr_frames']}",
        flush=True,
    )

    multimodal_report = build_multimodal_units(root)
    multimodal_counts = multimodal_report["counts"]
    print(
        "rebuilt_multimodal_evidence_units "
        f"lectures={multimodal_counts['lectures']} "
        f"units={multimodal_counts['units']} "
        f"units_with_frames={multimodal_counts['units_with_frames']} "
        f"frames={multimodal_counts['frames']} "
        f"ocr_frames={multimodal_counts['ocr_frames']}",
        flush=True,
    )


def rebuild_multimodal_only(root: Path) -> None:
    multimodal_report = build_multimodal_units(root)
    multimodal_counts = multimodal_report["counts"]
    print(
        "rebuilt_multimodal_evidence_units "
        f"lectures={multimodal_counts['lectures']} "
        f"units={multimodal_counts['units']} "
        f"units_with_frames={multimodal_counts['units_with_frames']} "
        f"frames={multimodal_counts['frames']} "
        f"ocr_frames={multimodal_counts['ocr_frames']}",
        flush=True,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Wait for OCR repair PID, then rebuild KB and visual backfill report.")
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--root", type=Path, default=default_root())
    parser.add_argument("--multimodal-only", action="store_true", help="After waiting, rebuild only exact text+frame+OCR evidence units.")
    return parser.parse_args()


def main() -> int:
    configure_stdio()
    args = parse_args()
    if sys.platform != "win32":
        raise RuntimeError("This watcher currently uses Windows process handles.")
    root = args.root.resolve()
    wait_for_windows_pid(args.pid)
    if args.multimodal_only:
        rebuild_multimodal_only(root)
        return 0
    rebuild(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())