from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def default_repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def run_step(name: str, command: list[str], log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{name}.log"
    print(f"== {name} ==")
    print(" ".join(str(part) for part in command))
    with log_path.open("w", encoding="utf-8") as log_file:
        log_file.write(f"# {name}\n")
        log_file.write(f"started_at={datetime.now(timezone.utc).isoformat()}\n")
        log_file.write("command=" + " ".join(str(part) for part in command) + "\n\n")
        log_file.flush()
        completed = subprocess.run(
            command,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        log_file.write(f"\nfinished_at={datetime.now(timezone.utc).isoformat()}\n")
        log_file.write(f"exit_code={completed.returncode}\n")
    if completed.returncode != 0:
        raise RuntimeError(f"step failed: {name}; see {log_path}")
    print(f"log: {log_path}")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_run_report(
    pipeline_root: Path,
    knowledge_dir: Path,
    source_dir: Path,
    repo_root: Path,
) -> None:
    manifest_path = pipeline_root / "manifest.json"
    index_path = knowledge_dir / "lecture_index.json"
    coverage_path = knowledge_dir / "topic_coverage.json"

    manifest = load_json(manifest_path) if manifest_path.exists() else {"results": []}
    index = load_json(index_path) if index_path.exists() else {"totals": {}}
    coverage = load_json(coverage_path) if coverage_path.exists() else {"topics": []}
    totals = index.get("totals", {}) if isinstance(index, dict) else {}
    topics = coverage.get("topics", []) if isinstance(coverage, dict) else []
    top_topics = sorted(
        [topic for topic in topics if isinstance(topic, dict)],
        key=lambda topic: int(topic.get("strong_hit_count", 0) or 0),
        reverse=True,
    )[:12]

    lines = [
        "# New Lecture Knowledge Pipeline Run",
        "",
        f"Generated at: `{datetime.now(timezone.utc).isoformat()}`",
        f"Source dir: `{source_dir}`",
        f"Pipeline root: `{pipeline_root}`",
        f"Knowledge base: `{knowledge_dir}`",
        f"Repo root: `{repo_root}`",
        "",
        "## Automatic Stages Completed",
        "",
        "- Raw video ingest: transcript.json, transcript.txt, transcript.srt, frames/, visual_index.json, manifest.json",
        "- Knowledge base build: lecture_chunks.jsonl, lecture_index.json, build_report.md",
        "- Topic inventory: topic_coverage.json/csv, lecture_topic_matrix.csv, topic_packets/",
        "- Draft rulebook skeleton: rulebook_draft/",
        "",
        "## Totals",
        "",
        f"- Manifest records: {len(manifest.get('results', [])) if isinstance(manifest, dict) else 0}",
        f"- Lectures: {totals.get('lectures', 0)}",
        f"- Duration hours: {totals.get('duration_hours', 0)}",
        f"- Transcript segments: {totals.get('segments', 0)}",
        f"- RAG chunks: {totals.get('chunks', 0)}",
        f"- Visual frames indexed: {totals.get('frames', 0)}",
        "",
        "## Strongest Topic Coverage",
        "",
    ]
    if top_topics:
        for topic in top_topics:
            lines.append(
                f"- `{topic.get('topic_id')}`: {topic.get('strong_hit_count', 0)} strong / {topic.get('hit_count', 0)} total"
            )
    else:
        lines.append("- No topic coverage generated.")
    lines.extend(
        [
            "",
            "## Manual Next Stage",
            "",
            "The next honest step is manual distillation of `rulebook_draft/` into an operational rulebook. Do not treat skeleton cards as final rules.",
        ]
    )
    (pipeline_root / "PIPELINE_RUN_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    repo_root = default_repo_root()
    parser = argparse.ArgumentParser(description="Run the restored lecture knowledge pipeline on a new raw-video corpus.")
    parser.add_argument("--source-dir", required=True, type=Path)
    parser.add_argument("--pipeline-root", type=Path, default=repo_root / "_new_lecture_corpus")
    parser.add_argument(
        "--knowledge-dir",
        type=Path,
        default=repo_root / "_knowledge_base",
        help="Knowledge-base output directory (default: <repo>/_knowledge_base).",
    )
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--model", default="large-v3-turbo",
                        help="faster-whisper model: tiny|base|small|medium|large-v3|large-v3-turbo|distil-large-v3 (default large-v3-turbo).")
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--beam-size", type=int, default=5)
    parser.add_argument("--batch-size", type=int, default=4,
                        help="BatchedInferencePipeline batch size (>=2 enables batched). Default 4 for GTX 1050 Ti 4GB.")
    parser.add_argument("--delete-source", action="store_true",
                        help="Delete source video files after successful ingest.")
    parser.add_argument("--no-vad", action="store_true")
    parser.add_argument("--language", default="ru")
    parser.add_argument("--max-gap-seconds", type=float, default=60.0)
    parser.add_argument("--frame-interval-seconds", type=float, default=None,
                        help="Backward-compat alias for --max-gap-seconds.")
    parser.add_argument("--scene-threshold", type=float, default=27.0,
                        help="PySceneDetect ContentDetector threshold.")
    parser.add_argument("--scene-min-len-seconds", type=float, default=2.0)
    parser.add_argument("--scene-min-gap-seconds", type=float, default=None,
                        help="Backward-compat alias for --scene-min-len-seconds.")
    parser.add_argument("--frame-width", type=int, default=1280)
    parser.add_argument("--frame-jpeg-quality", type=int, default=4)
    parser.add_argument("--phash-threshold", type=int, default=5)
    parser.add_argument("--no-ocr", action="store_true")
    parser.add_argument("--ocr-languages", default="ru,en")
    parser.add_argument("--ocr-min-conf", type=float, default=0.30)
    parser.add_argument("--ocr-gpu", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--skip-frames", action="store_true")
    parser.add_argument("--skip-transcription", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--skip-ingest", action="store_true")
    parser.add_argument("--skip-rulebook", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = default_repo_root().resolve()
    source_dir = args.source_dir.resolve()
    pipeline_root = args.pipeline_root.resolve()
    knowledge_dir = args.knowledge_dir.resolve()
    python_exe = args.python.resolve()
    log_dir = pipeline_root / "_pipeline_logs"

    if not source_dir.exists():
        print(f"error: source dir not found: {source_dir}", file=sys.stderr)
        return 2
    if not python_exe.exists():
        print(f"error: python executable not found: {python_exe}", file=sys.stderr)
        return 2

    pipeline_root.mkdir(parents=True, exist_ok=True)
    taxonomy_source = repo_root / "knowledge_bot" / "knowledge_taxonomy.json"
    taxonomy_copy = pipeline_root / "knowledge_taxonomy.json"
    shutil.copy2(taxonomy_source, taxonomy_copy)

    try:
        if not args.skip_ingest:
            max_gap = args.frame_interval_seconds if args.frame_interval_seconds is not None else args.max_gap_seconds
            min_scene_len = args.scene_min_gap_seconds if args.scene_min_gap_seconds is not None else args.scene_min_len_seconds
            ingest_command = [
                str(python_exe),
                str(repo_root / "knowledge_bot" / "ingest_raw_lectures.py"),
                "--source-dir", str(source_dir),
                "--output-root", str(pipeline_root),
                "--model", args.model,
                "--compute-type", args.compute_type,
                "--device", args.device,
                "--beam-size", str(args.beam_size),
                "--batch-size", str(args.batch_size),
                "--language", args.language,
                "--recursive",
                "--max-gap-seconds", str(max_gap),
                "--scene-threshold", str(args.scene_threshold),
                "--scene-min-len-seconds", str(min_scene_len),
                "--frame-width", str(args.frame_width),
                "--frame-jpeg-quality", str(args.frame_jpeg_quality),
                "--phash-threshold", str(args.phash_threshold),
                "--ocr-languages", args.ocr_languages,
                "--ocr-min-conf", str(args.ocr_min_conf),
            ]
            if args.no_vad:
                ingest_command.append("--no-vad")
            if args.no_ocr:
                ingest_command.append("--no-ocr")
            if args.ocr_gpu:
                ingest_command.append("--ocr-gpu")
            if args.delete_source:
                ingest_command.append("--delete-source")
            if args.overwrite:
                ingest_command.append("--overwrite")
            if args.skip_frames:
                ingest_command.append("--skip-frames")
            if args.skip_transcription:
                ingest_command.append("--skip-transcription")
            if args.limit is not None:
                ingest_command.extend(["--limit", str(args.limit)])
            run_step("01_ingest_raw_lectures", ingest_command, log_dir)

        run_step(
            "02_build_knowledge_base",
            [
                str(python_exe),
                str(repo_root / "knowledge_bot" / "build_knowledge_base.py"),
                "--root",
                str(pipeline_root),
                "--output",
                str(knowledge_dir),
            ],
            log_dir,
        )
        run_step(
            "03_build_knowledge_inventory",
            [
                str(python_exe),
                str(repo_root / "knowledge_bot" / "build_knowledge_inventory.py"),
                "--root",
                str(pipeline_root),
                "--taxonomy",
                str(taxonomy_copy),
                "--output",
                str(knowledge_dir),
            ],
            log_dir,
        )
        if not args.skip_rulebook:
            run_step(
                "04_build_rulebook_skeleton",
                [
                    str(python_exe),
                    str(repo_root / "knowledge_bot" / "build_rulebook_skeleton.py"),
                    "--root",
                    str(pipeline_root),
                    "--output",
                    str(knowledge_dir / "rulebook_draft"),
                ],
                log_dir,
            )
        write_run_report(pipeline_root, knowledge_dir, source_dir, repo_root)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"pipeline root: {pipeline_root}")
    print(f"knowledge base: {knowledge_dir}")
    print(f"report: {pipeline_root / 'PIPELINE_RUN_REPORT.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
