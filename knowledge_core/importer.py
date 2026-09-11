"""Build validated knowledge snapshots outside the source repository."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .db import DB_NAME, SCHEMA_VERSION, CoreError, canonical_json, confined, connect, file_hash, initialize, stable_id
from .sources import ImportContext, import_sources, input_paths as source_inputs


def _output_boundary(source: Path, destination: Path) -> None:
    code_root = Path(__file__).resolve().parents[1]
    if destination == source or destination.is_relative_to(source):
        raise CoreError("private knowledge output must be outside the source workspace")
    if destination == code_root or destination.is_relative_to(code_root):
        raise CoreError("private knowledge output must be outside the code repository")
    for parent in (destination, *destination.parents):
        if (parent / ".git").exists():
            raise CoreError("private knowledge output must not be inside a Git repository")


def plan_inputs(source: Path) -> dict:
    from .legacy_knowledge import input_paths as knowledge_inputs
    paths = sorted(set(source_inputs(source)) | set(knowledge_inputs(source)))
    inventory = {}
    for path in paths:
        relative = path.relative_to(source).as_posix()
        actual = confined(source, relative)
        if not actual.is_file():
            raise CoreError(f"required input is missing: {relative}")
        inventory[relative] = {"sha256": file_hash(actual), "byte_size": actual.stat().st_size}
    if not inventory:
        raise CoreError("empty input inventory")
    return inventory


def _copy_previous(previous: Path, stage: Path) -> None:
    from .validation import validate_core
    validation = validate_core(previous)
    if not validation["valid"]:
        raise CoreError("previous snapshot fails validation")
    old = connect(previous / DB_NAME, readonly=True)
    try:
        target = sqlite3.connect(stage / DB_NAME)
        try:
            old.backup(target)
        finally:
            target.close()
        for row in old.execute("SELECT DISTINCT blob_path FROM artifact_revisions"):
            src = confined(previous, row[0])
            dst = confined(stage, row[0])
            dst.parent.mkdir(parents=True, exist_ok=True)
            # Copies stay independent: later external corruption must not affect both snapshots.
            shutil.copyfile(src, dst)
    finally:
        old.close()


def rebuild_search(conn, run_id: str) -> None:
    conn.execute("DELETE FROM search_index")
    conn.execute("""INSERT INTO search_index(target_id,kind,title,text)
        SELECT k.revision_id,i.kind,k.statement,
               k.statement || ' ' || k.quote || ' ' || k.interpretation || ' ' ||
               k.conditions_json || ' ' || k.exceptions_json || ' ' ||
               COALESCE((SELECT group_concat(term,' ') FROM
                 (SELECT term FROM concept_terms t WHERE t.revision_id=k.revision_id ORDER BY term,kind)), '')
        FROM run_knowledge r JOIN knowledge_revisions k ON r.revision_id=k.revision_id
        JOIN knowledge_items i ON i.item_id=k.item_id WHERE r.run_id=?""", (run_id,))
    conn.execute("""INSERT INTO search_index(target_id,kind,title,text)
        SELECT DISTINCT e.evidence_id,e.kind,l.title,e.text FROM run_evidence_alias a
        JOIN evidence_units e ON e.evidence_id=a.evidence_id
        JOIN run_lectures l ON l.lecture_id=e.lecture_id AND l.run_id=a.run_id
        WHERE a.run_id=? AND e.kind IN ('source_unit','ocr') AND trim(e.text)<>''""", (run_id,))
    conn.execute("""INSERT INTO search_index(target_id,kind,title,text)
        SELECT DISTINCT e.evidence_id,e.kind,l.title,e.text FROM run_evidence_alias a
        JOIN evidence_units e ON e.evidence_id=a.evidence_id
        JOIN run_lectures l ON l.lecture_id=e.lecture_id AND l.run_id=a.run_id WHERE a.run_id=?
        AND e.kind='transcript_segment' AND trim(e.text)<>'' AND NOT EXISTS (
          SELECT 1 FROM evidence_links z JOIN run_evidence_alias current_alias
          ON current_alias.evidence_id=z.from_evidence AND current_alias.run_id=a.run_id
          WHERE z.to_evidence=e.evidence_id AND z.role='derived_from_segment' AND z.run_id=a.run_id)""", (run_id,))


def build_core(source_root: Path | str, destination: Path | str, *, previous: Path | str | None = None) -> dict:
    """Publish a new directory only after validation. Existing targets are never replaced.

    An identical import to an existing valid target is a read-only no-op. For changed
    inputs choose a fresh destination and pass previous to retain the earlier revisions.
    Failed staging directories are retained for local inspection, outside the repository.
    """
    from .legacy_knowledge import import_knowledge
    from .validation import validate_core, reject_linked_path
    reject_linked_path(Path(source_root).absolute())
    reject_linked_path(Path(destination).absolute())
    source, target = Path(source_root).resolve(), Path(destination).resolve()
    _output_boundary(source, target)
    inventory = plan_inputs(source)
    snapshot = canonical_json({"schema": SCHEMA_VERSION, "importer": __version__, "inputs": inventory})
    run_id = stable_id("import", snapshot)
    if target.exists():
        if not target.is_dir() or not (target / DB_NAME).is_file():
            raise CoreError("destination already exists and is not a knowledge snapshot")
        validation = validate_core(target)
        if validation["valid"] and validation.get("current_run") == run_id:
            return {"destination": str(target), "run_id": run_id, "unchanged": True, "validation": validation}
        raise CoreError("destination exists; use a new directory and --previous to retain history")
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".knowledge-building-", dir=target.parent)).resolve()
    if not stage.is_relative_to(target.parent) or stage == target:
        raise CoreError("invalid staging boundary")
    if previous is not None:
        reject_linked_path(Path(previous).absolute())
        _copy_previous(Path(previous).resolve(), stage)
    conn = connect(stage / DB_NAME)
    try:
        if previous is None:
            initialize(conn)
        existing = conn.execute("SELECT status FROM import_runs WHERE run_id=?", (run_id,)).fetchone()
        if existing:
            raise CoreError("previous snapshot already contains this input revision; use its existing snapshot")
        conn.execute("INSERT INTO import_runs VALUES (?,?,?,?,?)", (run_id, datetime.now(timezone.utc).isoformat(),
                     __version__, canonical_json({"inputs": inventory}), "building"))
        ctx = ImportContext(conn, source, stage, run_id)
        source_counts = import_sources(ctx)
        knowledge_counts = import_knowledge(ctx)
        observed = {path: {key: entry[key] for key in ("sha256", "byte_size")} for path, entry in ctx.inventory.items()}
        if observed != inventory:
            missing = sorted(set(inventory) - set(observed))
            extra = sorted(set(observed) - set(inventory))
            changed = sorted(p for p in set(observed) & set(inventory) if observed[p] != inventory[p])
            raise CoreError(f"input reconciliation failed: missing={missing}, extra={extra}, changed={changed}")
        # Recheck all planned inputs after processing: none may change mid-build.
        for relative, details in inventory.items():
            if file_hash(confined(source, relative)) != details["sha256"]:
                raise CoreError(f"input changed during build: {relative}")
        summary = {"inputs": inventory, "source_counts": source_counts, "knowledge_counts": knowledge_counts}
        conn.execute("UPDATE import_runs SET inventory_json=?,status='complete' WHERE run_id=?", (canonical_json(summary), run_id))
        conn.execute("INSERT OR REPLACE INTO metadata VALUES ('current_run',?)", (run_id,))
        rebuild_search(conn, run_id)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    validation = validate_core(stage)
    if not validation["valid"]:
        raise CoreError("candidate validation failed: " + canonical_json(validation["errors"]))
    (stage / "validation.json").write_text(canonical_json(validation) + "\n", encoding="utf-8")
    (stage / "inventory.json").write_text(canonical_json(summary) + "\n", encoding="utf-8")
    # Destination was checked above; rename refuses an existing destination on Windows.
    if target.exists():
        raise CoreError("destination appeared during the build")
    os.rename(stage, target)
    return {"destination": str(target), "run_id": run_id, "unchanged": False,
            "source_counts": source_counts, "knowledge_counts": knowledge_counts, "validation": validation}
