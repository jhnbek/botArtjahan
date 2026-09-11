"""Lossless import of retained transcripts, frames, OCR and source units."""
from __future__ import annotations

import json
import math
import shutil
from pathlib import Path

from .db import CoreError, canonical_json, confined, file_hash, stable_id

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


def image_path(root: Path, relative: str) -> Path:
    # Check the declared type before any filesystem access, including stat/hash.
    if not isinstance(relative, str) or Path(relative).suffix.lower() not in IMAGE_SUFFIXES:
        raise CoreError("frame path must name a retained image")
    return confined(root, relative.replace("\\", "/"))


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise CoreError("duplicate JSON object key")
        value[key] = item
    return value


def read_json(path: Path):
    with path.open(encoding="utf-8-sig") as handle:
        return json.load(handle, object_pairs_hook=_unique_object,
                         parse_constant=lambda value: (_ for _ in ()).throw(CoreError("nonfinite JSON number")))


def read_jsonl(path: Path):
    with path.open(encoding="utf-8-sig") as handle:
        for line_no, line in enumerate(handle, 1):
            if line.strip():
                value = json.loads(line, object_pairs_hook=_unique_object)
                if not isinstance(value, dict):
                    raise CoreError(f"expected an object at {path.name}:{line_no}")
                canonical_json(value)  # Reject non-finite values before persistence.
                yield line_no, value


def milliseconds(value) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise CoreError("timestamp must be a finite nonnegative number")
    return round(value * 1000)


def input_paths(root: Path) -> list[Path]:
    """Enumerate only retained extraction inputs; never enumerate video files."""
    corpus = confined(root, "_new_lecture_corpus")
    paths: set[Path] = set()
    transcripts = sorted(corpus.glob("*/transcript.json"))
    if not transcripts:
        raise CoreError("no retained transcripts; empty import is forbidden")
    for transcript in transcripts:
        transcript = confined(root, transcript.relative_to(root).as_posix())
        paths.add(transcript)
        for name in ("transcript.txt", "transcript.srt", "visual_index.json", "visual_ocr.json"):
            path = confined(root, (transcript.parent / name).relative_to(root).as_posix())
            if path.is_file():
                paths.add(path)
        visual = transcript.parent / "visual_index.json"
        if visual.is_file():
            value = read_json(visual)
            if not isinstance(value, dict) or not isinstance(value.get("frames"), list):
                raise CoreError("visual index requires a frames list")
            for frame in value["frames"]:
                relative = frame.get("path")
                if not isinstance(relative, str):
                    raise CoreError("frame path must be a string")
                paths.add(image_path(transcript.parent, relative))
    paths.update(root.glob("_knowledge_base/structured/lecture_pass/lectures/*/source_multimodal_units.jsonl"))
    for path in [corpus / "manifest.json", *(root / "_knowledge_base" / name for name in
                    ("lecture_chunks.jsonl", "course_order.json", "lecture_index.json", "topic_coverage.json"))]:
        path = confined(root, path.relative_to(root).as_posix())
        if path.is_file():
            paths.add(path)
    return sorted(paths)


class ImportContext:
    def __init__(self, conn, source_root: Path, destination: Path, run_id: str):
        self.conn, self.source_root, self.destination, self.run_id = conn, source_root, destination, run_id
        self.inventory: dict[str, dict] = {}
        self.lecture_dirs: dict[str, str] = {}

    def artifact(self, path: Path, kind: str, lecture_id=None, identity=None) -> str:
        path = Path(path)
        if not path.is_absolute():
            path = self.source_root / path
        try:
            relative = path.relative_to(self.source_root).as_posix()
        except ValueError as exc:
            raise CoreError("input artifact outside source root") from exc
        source = confined(self.source_root, relative)
        if source.suffix.lower() in {".mp4", ".mkv", ".mov", ".avi", ".webm", ".env"}:
            raise CoreError("video and secret files are outside the knowledge contract")
        if relative in self.inventory:
            return self.inventory[relative]["revision_id"]
        if not source.is_file():
            raise CoreError(f"required retained artifact is missing: {relative}")
        sha = file_hash(source)
        size = source.stat().st_size
        blob_path = f"blobs/{sha[:2]}/{sha}"
        target = confined(self.destination, blob_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            with source.open("rb") as incoming, target.open("xb") as outgoing:
                shutil.copyfileobj(incoming, outgoing)
        if file_hash(target) != sha or file_hash(source) != sha:
            raise CoreError(f"artifact changed during import: {relative}")
        identity = identity or relative
        aid = stable_id("artifact", identity)
        rid = stable_id("artifact_revision", aid + ":" + sha)
        self.conn.execute("INSERT OR IGNORE INTO artifacts VALUES (?,?,?,?)", (aid, kind, lecture_id, identity))
        self.conn.execute("INSERT OR IGNORE INTO artifact_revisions VALUES (?,?,?,?,?)", (rid, aid, sha, size, blob_path))
        self.conn.execute("INSERT INTO run_artifacts VALUES (?,?,?)", (self.run_id, relative, rid))
        self.inventory[relative] = {"sha256": sha, "byte_size": size, "revision_id": rid}
        return rid

    def record(self, path: Path, locator: str, payload, record_type: str, legacy_id=None) -> str:
        revision = self.artifact(path, "knowledge_document")
        raw = canonical_json(payload)
        record_id = stable_id("record", revision + ":" + str(locator))
        previous = self.conn.execute("SELECT raw_json FROM legacy_records WHERE record_id=?", (record_id,)).fetchone()
        if previous and previous[0] != raw:
            raise CoreError("one source locator resolves to multiple records")
        self.conn.execute("INSERT OR IGNORE INTO legacy_records VALUES (?,?,?,?,?,?)",
                          (record_id, record_type, legacy_id, revision, str(locator), raw))
        self.conn.execute("INSERT OR IGNORE INTO run_records VALUES (?,?)", (self.run_id, record_id))
        return record_id

    def alias(self, namespace: str, legacy_id: str, evidence_id: str) -> None:
        existing = self.evidence(namespace, legacy_id)
        if existing and existing != evidence_id:
            raise CoreError(f"ambiguous evidence alias: {namespace}:{legacy_id}")
        self.conn.execute("INSERT OR IGNORE INTO run_evidence_alias VALUES (?,?,?,?)",
                          (self.run_id, namespace, legacy_id, evidence_id))

    def evidence(self, namespace: str, legacy_id: str):
        row = self.conn.execute("SELECT evidence_id FROM run_evidence_alias WHERE run_id=? AND namespace=? AND legacy_id=?",
                                (self.run_id, namespace, legacy_id)).fetchone()
        return row[0] if row else None

    def issue(self, code: str, detail, severity="warning") -> None:
        raw = canonical_json(detail)
        iid = stable_id("issue", self.run_id + ":" + severity + ":" + code + ":" + raw)
        self.conn.execute("INSERT OR IGNORE INTO import_issues VALUES (?,?,?,?,?)", (iid, self.run_id, severity, code, raw))

    def add_evidence(self, revision: str, lecture_id: str, kind: str, locator: str,
                     text="", start_ms=None, end_ms=None, segment_index=None, metadata=None) -> str:
        eid = stable_id("evidence", revision + ":" + kind + ":" + locator)
        meta = {"locator": locator, **(metadata or {})}
        self.conn.execute("INSERT OR IGNORE INTO evidence_units VALUES (?,?,?,?,?,?,?,?,?)",
                          (eid, revision, lecture_id, kind, segment_index, start_ms, end_ms, text, canonical_json(meta)))
        return eid


def import_sources(ctx: ImportContext) -> dict:
    root = ctx.source_root
    corpus = root / "_new_lecture_corpus"
    paths = sorted(corpus.glob("*/transcript.json"))
    if not paths:
        raise CoreError("no retained transcripts; empty import is forbidden")
    course_path = root / "_knowledge_base/course_order.json"
    course = read_json(course_path).get("lectures", []) if course_path.is_file() else []
    course_by_title = {row["title"]: row for row in course}
    legacy_lectures: dict[str, str] = {}
    totals = {"lectures": 0, "segments": 0, "frames": 0, "ocr": 0, "source_units": 0, "source_unit_frame_links": 0, "chunks": 0}
    for path in paths:
        path = confined(root, path.relative_to(root).as_posix())
        transcript = read_json(path)
        if not isinstance(transcript, dict) or not isinstance(transcript.get("segments"), list) or not transcript["segments"]:
            raise CoreError("transcript has no segment records")
        directory = path.parent.name
        # A persisted provenance key is used as metadata only. Videos are never accessed.
        identity = transcript.get("lecture_uid") or transcript.get("source_video")
        if not isinstance(identity, str) or not identity.strip():
            raise CoreError("a stable lecture_uid or retained source_video metadata value is required")
        identity = identity.replace("\\", "/")
        lid = stable_id("lecture", identity)
        if lid in ctx.lecture_dirs.values():
            raise CoreError("multiple transcript directories claim the same source identity")
        ctx.conn.execute("INSERT OR IGNORE INTO lectures VALUES (?,?,?)", (lid, identity, directory))
        ctx.conn.execute("INSERT INTO run_lectures VALUES (?,?,?,?,?)",
                         (ctx.run_id, lid, directory, directory, course_by_title.get(directory, {}).get("course_position")))
        ctx.lecture_dirs[directory] = lid
        if course_by_title.get(directory, {}).get("lecture_id"):
            legacy_lectures[course_by_title[directory]["lecture_id"]] = directory
        revision = ctx.artifact(path, "transcript", lid, lid + ":transcript")
        totals["lectures"] += 1
        for index, segment in enumerate(transcript["segments"]):
            if not isinstance(segment, dict) or not isinstance(segment.get("text"), str):
                raise CoreError("segment requires text")
            start, end = milliseconds(segment.get("start")), milliseconds(segment.get("end"))
            if end < start:
                raise CoreError("segment end precedes start")
            record = ctx.record(path, f"/segments/{index}", segment, "transcript_segment", str(segment.get("id", index)))
            eid = ctx.add_evidence(revision, lid, "transcript_segment", f"/segments/{index}",
                                   segment["text"], start, end, index, {"source_segment_id": segment.get("id")})
            ctx.alias("segment", f"{directory}:{index}", eid)
            totals["segments"] += 1
        for optional in ("transcript.txt", "transcript.srt", "visual_ocr.json"):
            extra = path.parent / optional
            if extra.is_file():
                ctx.artifact(extra, optional.split(".")[0], lid, lid + ":" + optional)
        visual_path = confined(root, (path.parent / "visual_index.json").relative_to(root).as_posix())
        if not visual_path.is_file():
            ctx.issue("visual_index_absent", {"lecture_id": lid})
            continue
        visual = read_json(visual_path)
        visual_revision = ctx.artifact(visual_path, "visual_index", lid, lid + ":visual_index")
        frames = visual.get("frames")
        if not isinstance(frames, list):
            raise CoreError("visual index requires a frames list")
        for index, frame in enumerate(frames):
            relative = frame.get("path")
            if not isinstance(relative, str):
                raise CoreError("frame path must be a string")
            relative = relative.replace("\\", "/")
            frame_path = image_path(path.parent, relative)
            timestamp = milliseconds(frame.get("time"))
            record = ctx.record(visual_path, f"/frames/{index}", frame, "raw_frame", relative)
            frame_revision = ctx.artifact(frame_path, "frame", lid, f"{lid}:frame:{timestamp}")
            frame_evidence = ctx.add_evidence(frame_revision, lid, "frame", "/", start_ms=timestamp,
                                              metadata={})
            for alias in (f"{directory}/{relative}", f"_new_lecture_corpus/{directory}/{relative}"):
                ctx.alias("frame", alias, frame_evidence)
            segment_index = frame.get("linked_segment_index")
            if segment_index is not None:
                segment_evidence = ctx.evidence("segment", f"{directory}:{segment_index}")
                if not segment_evidence:
                    raise CoreError("frame references an absent transcript segment")
                ctx.conn.execute("INSERT OR IGNORE INTO evidence_links VALUES (?,?,?,?,?)",
                                 (segment_evidence, frame_evidence, "frame_association", record, ctx.run_id))
            ocr = frame.get("ocr_text")
            if ocr:
                if not isinstance(ocr, str):
                    raise CoreError("OCR text must be a string")
                ocr_evidence = ctx.add_evidence(visual_revision, lid, "ocr", f"/frames/{index}/ocr_text", ocr,
                                                timestamp, timestamp, metadata={"repair_version": frame.get("ocr_repair_version"), "error": frame.get("ocr_error")})
                ctx.conn.execute("INSERT OR IGNORE INTO evidence_links VALUES (?,?,?,?,?)", (frame_evidence, ocr_evidence, "ocr_of_frame", record, ctx.run_id))
                ctx.alias("ocr", f"{directory}/{relative}", ocr_evidence)
                totals["ocr"] += 1
            totals["frames"] += 1
    for path in sorted(root.glob("_knowledge_base/structured/lecture_pass/lectures/*/source_multimodal_units.jsonl")):
        revision = ctx.artifact(path, "source_units")
        for line_no, unit in read_jsonl(path):
            title = unit.get("lecture_title")
            directory = title if title in ctx.lecture_dirs else legacy_lectures.get(unit.get("lecture_id"))
            if directory not in ctx.lecture_dirs:
                raise CoreError("source unit lecture is unresolved")
            uid = unit.get("unit_id")
            if not isinstance(uid, str) or not uid:
                raise CoreError("source unit has no stable legacy id")
            index = unit.get("segment_index")
            if isinstance(index, bool) or not isinstance(index, int) or index < 0:
                raise CoreError("source unit segment_index must be a nonnegative integer")
            if not isinstance(unit.get("text"), str):
                raise CoreError("source unit requires text")
            original = ctx.evidence("segment", f"{directory}:{index}")
            if not original:
                raise CoreError("source unit segment is unresolved")
            record = ctx.record(path, f"line:{line_no}", unit, "source_unit", uid)
            eid = ctx.add_evidence(revision, ctx.lecture_dirs[directory], "source_unit", f"line:{line_no}",
                                   unit.get("text", ""), milliseconds(unit["start_sec"]), milliseconds(unit["end_sec"]), index,
                                   {"legacy_unit_id": uid})
            ctx.alias("source_unit", uid, eid)
            ctx.conn.execute("INSERT OR IGNORE INTO evidence_links VALUES (?,?,?,?,?)", (eid, original, "derived_from_segment", record, ctx.run_id))
            for frame in unit.get("frames", []):
                key = (frame.get("physical_path") or frame.get("path", "")).replace("\\", "/")
                target = ctx.evidence("frame", key)
                if not target:
                    raise CoreError("source unit frame is unresolved")
                ctx.conn.execute("INSERT OR IGNORE INTO evidence_links VALUES (?,?,?,?,?)", (eid, target, "source_frame", record, ctx.run_id))
                totals["source_unit_frame_links"] += 1
            totals["source_units"] += 1
    for name in ("manifest.json",):
        path = corpus / name
        if path.is_file():
            ctx.record(path, "/", read_json(path), "corpus_manifest", name)
    for name in ("course_order.json", "lecture_index.json", "topic_coverage.json"):
        path = root / "_knowledge_base" / name
        if path.is_file():
            ctx.record(path, "/", read_json(path), "legacy_index", name)
    chunks_path = root / "_knowledge_base/lecture_chunks.jsonl"
    if chunks_path.is_file():
        for line_no, chunk in read_jsonl(chunks_path):
            ctx.record(chunks_path, f"line:{line_no}", chunk, "search_chunk", chunk.get("chunk_id"))
            totals["chunks"] += 1
    return totals
