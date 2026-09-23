"""Materialize visual/text/OCR/review knowledge into a SQLite database.

The primary knowledge base remains JSON/JSONL for auditability. This script
builds a queryable SQLite snapshot that joins the same layers:

- raw lecture frames + OCR from _new_lecture_corpus/*/visual_index.json
- source multimodal units from _knowledge_base/structured/lecture_pass
- mined multimodal calibration candidates
- manual visual review ledger
- joined visual knowledge observations
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Iterable


BASE = Path("_knowledge_base/structured/consolidation/lecture_multimodal_calibration_candidates")
DB_PATH = BASE / "visual_knowledge.sqlite"
CANDIDATES = BASE / "candidates.jsonl"
LEDGER = BASE / "manual_visual_review_notes.json"
OBSERVATIONS = BASE / "visual_knowledge_observations.jsonl"
SOURCE_UNITS_GLOB = "_knowledge_base/structured/lecture_pass/lectures/*/source_multimodal_units.jsonl"
RAW_CORPUS = Path("_new_lecture_corpus")


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


def to_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        PRAGMA journal_mode = WAL;
        PRAGMA synchronous = NORMAL;

        DROP TABLE IF EXISTS raw_frames;
        DROP TABLE IF EXISTS source_units;
        DROP TABLE IF EXISTS source_unit_frames;
        DROP TABLE IF EXISTS candidates;
        DROP TABLE IF EXISTS candidate_source_units;
        DROP TABLE IF EXISTS candidate_frames;
        DROP TABLE IF EXISTS visual_reviews;
        DROP TABLE IF EXISTS visual_observations;

        CREATE TABLE raw_frames (
            id INTEGER PRIMARY KEY,
            lecture_folder TEXT NOT NULL,
            frame_path TEXT NOT NULL,
            physical_path TEXT NOT NULL UNIQUE,
            time TEXT,
            trigger TEXT,
            scene_index INTEGER,
            phash TEXT,
            duplicate_of TEXT,
            linked_segment_index INTEGER,
            ocr_text TEXT,
            ocr_line_count INTEGER,
            raw_json TEXT NOT NULL
        );

        CREATE TABLE source_units (
            unit_id TEXT PRIMARY KEY,
            lecture_title TEXT,
            lecture_id TEXT,
            course_position INTEGER,
            segment_index INTEGER,
            start_sec REAL,
            end_sec REAL,
            timecode TEXT,
            text TEXT,
            frame_count INTEGER,
            ocr_frame_count INTEGER,
            ocr_chars INTEGER,
            frames_json TEXT,
            raw_json TEXT NOT NULL
        );

        CREATE TABLE source_unit_frames (
            unit_id TEXT NOT NULL,
            frame_path TEXT,
            physical_path TEXT,
            time TEXT,
            ocr_text TEXT,
            ocr_line_count INTEGER,
            raw_json TEXT NOT NULL
        );

        CREATE TABLE candidates (
            candidate_id TEXT PRIMARY KEY,
            claim_id TEXT,
            classification TEXT,
            readiness_score INTEGER,
            lecture_title TEXT,
            lecture_dir TEXT,
            timecode TEXT,
            statement_excerpt TEXT,
            quote_excerpt TEXT,
            visual_evidence_excerpt TEXT,
            source_text_excerpt TEXT,
            features_json TEXT,
            raw_json TEXT NOT NULL
        );

        CREATE TABLE candidate_source_units (
            candidate_id TEXT NOT NULL,
            unit_id TEXT NOT NULL,
            matched INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (candidate_id, unit_id, matched)
        );

        CREATE TABLE candidate_frames (
            candidate_id TEXT NOT NULL,
            frame_path TEXT,
            physical_path TEXT,
            time TEXT,
            exists_on_disk INTEGER,
            raw_json TEXT NOT NULL
        );

        CREATE TABLE visual_reviews (
            candidate_id TEXT,
            claim_id TEXT,
            lecture_title TEXT,
            timecode TEXT,
            review_status TEXT,
            promotion_decision TEXT,
            visual_confirmed INTEGER,
            expert_verdict_summary TEXT,
            resolved_fields_json TEXT,
            visual_evidence_json TEXT,
            promotion_blockers_json TEXT,
            source_frames_json TEXT,
            next_action TEXT,
            raw_json TEXT NOT NULL
        );

        CREATE TABLE visual_observations (
            candidate_id TEXT,
            claim_id TEXT,
            knowledge_use TEXT,
            review_status TEXT,
            promotion_decision TEXT,
            visual_confirmed INTEGER,
            lecture_title TEXT,
            lecture_dir TEXT,
            timecode TEXT,
            expert_verdict_summary TEXT,
            source_unit_ids_json TEXT,
            source_frames_json TEXT,
            resolved_fields_json TEXT,
            candidate_statement_excerpt TEXT,
            candidate_quote_excerpt TEXT,
            candidate_source_text_excerpt TEXT,
            candidate_features_json TEXT,
            has_text_join INTEGER,
            raw_json TEXT NOT NULL
        );

        CREATE INDEX idx_raw_frames_physical_path ON raw_frames(physical_path);
        CREATE INDEX idx_raw_frames_lecture ON raw_frames(lecture_folder);
        CREATE INDEX idx_source_units_lecture_time ON source_units(lecture_title, timecode);
        CREATE INDEX idx_source_unit_frames_unit ON source_unit_frames(unit_id);
        CREATE INDEX idx_candidates_classification ON candidates(classification);
        CREATE INDEX idx_candidates_lecture_time ON candidates(lecture_title, timecode);
        CREATE INDEX idx_candidate_source_units_unit ON candidate_source_units(unit_id);
        CREATE INDEX idx_candidate_frames_candidate ON candidate_frames(candidate_id);
        CREATE INDEX idx_visual_reviews_candidate ON visual_reviews(candidate_id);
        CREATE INDEX idx_visual_reviews_status ON visual_reviews(review_status);
        CREATE INDEX idx_visual_observations_candidate ON visual_observations(candidate_id);
        CREATE INDEX idx_visual_observations_use ON visual_observations(knowledge_use);
        """
    )


def bool_to_int(value: Any) -> int | None:
    if value is None:
        return None
    return 1 if bool(value) else 0


def insert_raw_frames(conn: sqlite3.Connection, root: Path) -> int:
    count = 0
    for visual_index in sorted((root / RAW_CORPUS).glob("*/visual_index.json")):
        lecture_folder = visual_index.parent.name
        data = json.loads(visual_index.read_text(encoding="utf-8"))
        frames = data.get("frames") if isinstance(data, dict) else data
        if not isinstance(frames, list):
            continue
        for frame in frames:
            frame_path = frame.get("path") if isinstance(frame, dict) else None
            physical_path = (RAW_CORPUS / lecture_folder / (frame_path or "")).as_posix()
            conn.execute(
                """
                INSERT INTO raw_frames (
                    lecture_folder, frame_path, physical_path, time, trigger, scene_index,
                    phash, duplicate_of, linked_segment_index, ocr_text, ocr_line_count, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    lecture_folder,
                    frame_path,
                    physical_path,
                    frame.get("time"),
                    frame.get("trigger"),
                    frame.get("scene_index"),
                    frame.get("phash"),
                    frame.get("duplicate_of"),
                    frame.get("linked_segment_index"),
                    frame.get("ocr_text"),
                    frame.get("ocr_line_count"),
                    to_json(frame),
                ),
            )
            count += 1
    return count


def insert_source_units(conn: sqlite3.Connection, root: Path) -> tuple[int, int]:
    unit_count = 0
    frame_count = 0
    for path in sorted(root.glob(SOURCE_UNITS_GLOB)):
        for unit in read_jsonl(path):
            frames = unit.get("frames") or []
            conn.execute(
                """
                INSERT INTO source_units (
                    unit_id, lecture_title, lecture_id, course_position, segment_index,
                    start_sec, end_sec, timecode, text, frame_count, ocr_frame_count,
                    ocr_chars, frames_json, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    unit.get("unit_id"),
                    unit.get("lecture_title"),
                    unit.get("lecture_id"),
                    unit.get("course_position"),
                    unit.get("segment_index"),
                    unit.get("start_sec"),
                    unit.get("end_sec"),
                    unit.get("time"),
                    unit.get("text"),
                    unit.get("frame_count"),
                    unit.get("ocr_frame_count"),
                    unit.get("ocr_chars"),
                    to_json(frames),
                    to_json(unit),
                ),
            )
            unit_count += 1
            for frame in frames:
                conn.execute(
                    """
                    INSERT INTO source_unit_frames (
                        unit_id, frame_path, physical_path, time, ocr_text, ocr_line_count, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        unit.get("unit_id"),
                        frame.get("path"),
                        frame.get("physical_path") or frame.get("path"),
                        frame.get("time"),
                        frame.get("ocr_text"),
                        frame.get("ocr_line_count"),
                        to_json(frame),
                    ),
                )
                frame_count += 1
    return unit_count, frame_count


def insert_candidates(conn: sqlite3.Connection, root: Path) -> tuple[int, int, int]:
    candidate_count = 0
    unit_link_count = 0
    frame_link_count = 0
    for row in read_jsonl(root / CANDIDATES):
        conn.execute(
            """
            INSERT INTO candidates (
                candidate_id, claim_id, classification, readiness_score, lecture_title,
                lecture_dir, timecode, statement_excerpt, quote_excerpt,
                visual_evidence_excerpt, source_text_excerpt, features_json, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row.get("candidate_id"),
                row.get("claim_id"),
                row.get("classification"),
                row.get("readiness_score"),
                row.get("lecture_title"),
                row.get("lecture_dir"),
                row.get("time"),
                row.get("statement_excerpt"),
                row.get("quote_excerpt"),
                row.get("visual_evidence_excerpt"),
                row.get("source_text_excerpt"),
                to_json(row.get("features") or {}),
                to_json(row),
            ),
        )
        candidate_count += 1
        candidate_id = row.get("candidate_id")
        matched = set(row.get("matched_source_unit_ids") or [])
        for unit_id in row.get("source_unit_ids") or []:
            conn.execute(
                "INSERT OR IGNORE INTO candidate_source_units(candidate_id, unit_id, matched) VALUES (?, ?, ?)",
                (candidate_id, unit_id, 1 if unit_id in matched else 0),
            )
            unit_link_count += 1
        for frame in row.get("frame_refs") or []:
            conn.execute(
                """
                INSERT INTO candidate_frames(candidate_id, frame_path, physical_path, time, exists_on_disk, raw_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate_id,
                    frame.get("path"),
                    frame.get("physical_path"),
                    frame.get("time"),
                    bool_to_int(frame.get("exists")),
                    to_json(frame),
                ),
            )
            frame_link_count += 1
    return candidate_count, unit_link_count, frame_link_count


def insert_visual_reviews(conn: sqlite3.Connection, root: Path) -> int:
    ledger = json.loads((root / LEDGER).read_text(encoding="utf-8"))
    count = 0
    for row in ledger.get("reviews") or []:
        conn.execute(
            """
            INSERT INTO visual_reviews (
                candidate_id, claim_id, lecture_title, timecode, review_status,
                promotion_decision, visual_confirmed, expert_verdict_summary,
                resolved_fields_json, visual_evidence_json, promotion_blockers_json,
                source_frames_json, next_action, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row.get("candidate_id"),
                row.get("claim_id"),
                row.get("lecture_title"),
                row.get("timecode"),
                row.get("review_status"),
                row.get("promotion_decision"),
                bool_to_int(row.get("visual_confirmed")),
                row.get("expert_verdict_summary"),
                to_json(row.get("resolved_fields") or {}),
                to_json(row.get("visual_evidence") or []),
                to_json(row.get("promotion_blockers") or []),
                to_json(row.get("source_frames") or []),
                row.get("next_action"),
                to_json(row),
            ),
        )
        count += 1
    return count


def insert_observations(conn: sqlite3.Connection, root: Path) -> int:
    count = 0
    for row in read_jsonl(root / OBSERVATIONS):
        conn.execute(
            """
            INSERT INTO visual_observations (
                candidate_id, claim_id, knowledge_use, review_status, promotion_decision,
                visual_confirmed, lecture_title, lecture_dir, timecode,
                expert_verdict_summary, source_unit_ids_json, source_frames_json,
                resolved_fields_json, candidate_statement_excerpt, candidate_quote_excerpt,
                candidate_source_text_excerpt, candidate_features_json, has_text_join, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row.get("candidate_id"),
                row.get("claim_id"),
                row.get("knowledge_use"),
                row.get("review_status"),
                row.get("promotion_decision"),
                bool_to_int(row.get("visual_confirmed")),
                row.get("lecture_title"),
                row.get("lecture_dir"),
                row.get("timecode"),
                row.get("expert_verdict_summary"),
                to_json(row.get("source_unit_ids") or []),
                to_json(row.get("source_frames") or []),
                to_json(row.get("resolved_fields") or {}),
                row.get("candidate_statement_excerpt"),
                row.get("candidate_quote_excerpt"),
                row.get("candidate_source_text_excerpt"),
                to_json(row.get("candidate_features") or {}),
                bool_to_int(row.get("has_text_join")),
                to_json(row),
            ),
        )
        count += 1
    return count


def table_count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def build_database(root: Path, output: Path) -> dict[str, int | str]:
    db_path = root / output
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    with sqlite3.connect(db_path) as conn:
        create_schema(conn)
        raw_frame_count = insert_raw_frames(conn, root)
        source_unit_count, source_unit_frame_count = insert_source_units(conn, root)
        candidate_count, candidate_unit_links, candidate_frame_links = insert_candidates(conn, root)
        review_count = insert_visual_reviews(conn, root)
        observation_count = insert_observations(conn, root)
        conn.commit()

        counts = {
            table: table_count(conn, table)
            for table in [
                "raw_frames",
                "source_units",
                "source_unit_frames",
                "candidates",
                "candidate_source_units",
                "candidate_frames",
                "visual_reviews",
                "visual_observations",
            ]
        }

    return {
        "database": output.as_posix(),
        "raw_frame_count": raw_frame_count,
        "source_unit_count": source_unit_count,
        "source_unit_frame_count": source_unit_frame_count,
        "candidate_count": candidate_count,
        "candidate_unit_links": candidate_unit_links,
        "candidate_frame_links": candidate_frame_links,
        "review_count": review_count,
        "observation_count": observation_count,
        **counts,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a SQLite snapshot of visual/text/OCR knowledge artifacts.")
    parser.add_argument("--root", type=Path, default=default_root())
    parser.add_argument("--output", type=Path, default=DB_PATH)
    return parser.parse_args()


def main() -> int:
    configure_stdio()
    args = parse_args()
    summary = build_database(args.root.resolve(), args.output)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())