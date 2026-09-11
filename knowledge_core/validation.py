"""Independent checks for a completed local knowledge dataset."""
from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any

from .db import DB_NAME, SCHEMA_VERSION, CoreError, confined, connect, current_run, file_hash, initialize, safe_relative


def dataset_paths(path: Path | str, artifact_root: Path | str | None = None) -> tuple[Path, Path]:
    """Accept either a dataset directory or its database file."""
    path = Path(path)
    database = path / DB_NAME if path.is_dir() else path
    root = Path(artifact_root) if artifact_root is not None else database.parent
    return database, root


def reject_linked_path(path: Path) -> None:
    """Reject links before resolving them, including linked ancestors."""
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise CoreError("linked paths are forbidden")


@lru_cache(maxsize=1)
def _schema_contract() -> dict[tuple[str, str], str]:
    conn = sqlite3.connect(":memory:")
    try:
        initialize(conn)
        return _schema_objects(conn)
    finally:
        conn.close()


def _schema_objects(conn: sqlite3.Connection) -> dict[tuple[str, str], str]:
    return {
        (row[0], row[1]): " ".join(row[2].split())
        for row in conn.execute("SELECT type,name,sql FROM sqlite_schema WHERE sql IS NOT NULL")
    }


def _current_counts(conn: sqlite3.Connection, run_id: str) -> dict[str, int]:
    counts = {}
    for key, table in (("lectures", "run_lectures"), ("artifacts", "run_artifacts"),
                       ("records", "run_records"), ("knowledge_items", "run_knowledge")):
        counts[key] = conn.execute(f"SELECT count(*) FROM {table} WHERE run_id=?", (run_id,)).fetchone()[0]
    counts["evidence_units"] = conn.execute(
        "SELECT count(DISTINCT evidence_id) FROM run_evidence_alias WHERE run_id=?", (run_id,)
    ).fetchone()[0]
    for kind in ("claim", "candidate", "rule", "concept"):
        counts[kind + "s"] = conn.execute(
            "SELECT count(*) FROM run_knowledge r JOIN knowledge_items k USING(item_id) "
            "WHERE r.run_id=? AND k.kind=?", (run_id, kind)
        ).fetchone()[0]
    for key, condition in (("review_events", "1=1"), ("unknown_review_actors", "v.actor IS NULL"),
                           ("unknown_review_dates", "v.event_at IS NULL")):
        counts[key] = conn.execute(
            "SELECT count(*) FROM review_events v JOIN run_reviews r USING(review_id) "
            f"WHERE r.run_id=? AND {condition}", (run_id,)
        ).fetchone()[0]
    return counts


def _source_counts(conn: sqlite3.Connection, run_id: str) -> dict[str, int]:
    counts = {"lectures": conn.execute(
        "SELECT count(*) FROM run_lectures WHERE run_id=?", (run_id,)
    ).fetchone()[0]}
    for name, kind in (("segments", "transcript_segment"), ("frames", "raw_frame"),
                       ("source_units", "source_unit"), ("chunks", "search_chunk")):
        counts[name] = conn.execute(
            "SELECT count(*) FROM run_records r JOIN legacy_records l USING(record_id) "
            "WHERE r.run_id=? AND l.record_type=?", (run_id, kind)
        ).fetchone()[0]
    counts["ocr"] = conn.execute(
        "SELECT count(DISTINCT e.evidence_id) FROM run_evidence_alias a "
        "JOIN evidence_units e USING(evidence_id) WHERE a.run_id=? AND e.kind='ocr'", (run_id,)
    ).fetchone()[0]
    counts["source_unit_frame_links"] = conn.execute(
        "SELECT count(*) FROM evidence_links WHERE run_id=? AND role='source_frame'", (run_id,)
    ).fetchone()[0]
    return counts


def _check_inventory(
    conn: sqlite3.Connection, run_id: str, inventory: Any, errors: list[str], counts: dict[str, int],
) -> dict[str, int]:
    actual_sources = _source_counts(conn, run_id)
    if not isinstance(inventory, dict):
        errors.append("current_inventory_not_object")
        return actual_sources
    if set(inventory) != {"inputs", "source_counts", "knowledge_counts"}:
        errors.append("current_inventory_contract_mismatch")
    inputs = inventory.get("inputs")
    if not isinstance(inputs, dict) or not inputs:
        errors.append("inventory_inputs_missing_or_empty")
    else:
        well_formed = True
        for relative, details in inputs.items():
            try:
                safe_relative(relative)
            except CoreError:
                errors.append("inventory_input_path_invalid")
                well_formed = False
            if (not isinstance(details, dict) or set(details) != {"sha256", "byte_size"}
                    or not isinstance(details.get("sha256"), str)
                    or not re.fullmatch(r"[0-9a-f]{64}", details.get("sha256", ""))
                    or type(details.get("byte_size")) is not int or details["byte_size"] < 0):
                errors.append("inventory_input_metadata_invalid")
                well_formed = False
        actual_inputs = {
            row[0]: {"sha256": row[1], "byte_size": row[2]}
            for row in conn.execute(
                "SELECT a.source_path,r.sha256,r.byte_size FROM run_artifacts a "
                "JOIN artifact_revisions r ON r.revision_id=a.revision_id WHERE a.run_id=?", (run_id,)
            )
        }
        if well_formed and inputs != actual_inputs:
            errors.append("inventory_inputs_mismatch")
    declared_sources = inventory.get("source_counts")
    if not isinstance(declared_sources, dict) or set(declared_sources) != set(actual_sources):
        errors.append("source_counts_contract_mismatch")
    else:
        for field, actual in actual_sources.items():
            if type(declared_sources[field]) is not int or declared_sources[field] != actual:
                errors.append("source_counts_mismatch:" + field)
    knowledge_counts = inventory.get("knowledge_counts")
    if not isinstance(knowledge_counts, dict):
        errors.append("knowledge_counts_not_object")
    else:
        for field, value in knowledge_counts.items():
            if type(value) is not int or value < 0:
                errors.append("knowledge_count_invalid:" + field)
        # The importer omits zero-valued Counter keys. These normalized totals
        # have an independent relational definition; other import diagnostics do not.
        for field in ("claims", "candidates", "rules", "concepts", "review_events"):
            if knowledge_counts.get(field, 0) != counts[field]:
                errors.append("knowledge_counts_mismatch:" + field)
    return actual_sources


def _check_current_links(conn: sqlite3.Connection, run_id: str, errors: list[str], warnings: list[str]) -> None:
    evidence = {row[0] for row in conn.execute(
        "SELECT DISTINCT evidence_id FROM run_evidence_alias WHERE run_id=?", (run_id,)
    )}
    records = {row[0] for row in conn.execute("SELECT record_id FROM run_records WHERE run_id=?", (run_id,))}
    revisions = {row[0] for row in conn.execute(
        "SELECT revision_id FROM run_knowledge WHERE run_id=?", (run_id,)
    )}
    artifacts = {row[0] for row in conn.execute(
        "SELECT revision_id FROM run_artifacts WHERE run_id=?", (run_id,)
    )}
    lectures = {row[0] for row in conn.execute("SELECT lecture_id FROM run_lectures WHERE run_id=?", (run_id,))}
    invalid_records = sum(row[0] not in artifacts for row in conn.execute(
        "SELECT l.artifact_revision FROM run_records r JOIN legacy_records l USING(record_id) WHERE r.run_id=?",
        (run_id,),
    ))
    if invalid_records:
        errors.append(f"current_record_artifact_not_current:{invalid_records}")
    invalid_evidence = sum(artifact not in artifacts or lecture not in lectures for artifact, lecture, _identity in conn.execute(
        "SELECT DISTINCT e.artifact_revision,e.lecture_id,e.evidence_id FROM run_evidence_alias a "
        "JOIN evidence_units e USING(evidence_id) WHERE a.run_id=?", (run_id,),
    ))
    if invalid_evidence:
        errors.append(f"current_evidence_owner_not_current:{invalid_evidence}")
    invalid_edges = sum(
        record not in records or source not in evidence or target not in evidence
        for source, target, record in conn.execute(
            "SELECT from_evidence,to_evidence,record_id FROM evidence_links WHERE run_id=?", (run_id,)
        )
    )
    if invalid_edges:
        errors.append(f"current_evidence_link_not_current:{invalid_edges}")
    reviews = list(conn.execute(
        "SELECT v.record_id,v.target_revision FROM run_reviews r JOIN review_events v USING(review_id) "
        "WHERE r.run_id=?", (run_id,)
    ))
    invalid_reviews = sum(record not in records or (target is not None and target not in revisions)
                          for record, target in reviews)
    if invalid_reviews:
        errors.append(f"current_review_not_current:{invalid_reviews}")
    unresolved = sum(target is None for _record, target in reviews)
    if unresolved:
        warnings.append(f"unresolved_review_targets:{unresolved}")
    orphan_knowledge = sum(row[0] not in records for row in conn.execute(
        "SELECT k.record_id FROM run_knowledge r JOIN knowledge_revisions k "
        "ON k.revision_id=r.revision_id WHERE r.run_id=?", (run_id,)
    ))
    if orphan_knowledge:
        errors.append(f"current_knowledge_record_not_current:{orphan_knowledge}")
    orphan_knowledge_evidence = sum(row[0] not in evidence for row in conn.execute(
        "SELECT e.evidence_id FROM run_knowledge r JOIN knowledge_evidence e ON e.revision_id=r.revision_id "
        "WHERE r.run_id=?", (run_id,),
    ))
    if orphan_knowledge_evidence:
        errors.append(f"current_knowledge_evidence_not_current:{orphan_knowledge_evidence}")
    orphan_relations = sum(target not in revisions or record not in records for target, record in conn.execute(
        "SELECT z.to_revision,z.record_id FROM run_knowledge r JOIN knowledge_relations z "
        "ON z.from_revision=r.revision_id WHERE r.run_id=?", (run_id,),
    ))
    if orphan_relations:
        errors.append(f"current_knowledge_relation_not_current:{orphan_relations}")


def _check_search_index(conn: sqlite3.Connection, run_id: str, errors: list[str]) -> int:
    """Compare all searchable rows with current evidence and knowledge revisions."""
    expected: Counter[tuple[str, str, str, str]] = Counter()
    for row in conn.execute(
        "SELECT k.revision_id,i.kind,k.statement,k.quote,k.interpretation,k.conditions_json,k.exceptions_json "
        "FROM run_knowledge r JOIN knowledge_revisions k ON k.revision_id=r.revision_id "
        "JOIN knowledge_items i ON i.item_id=k.item_id WHERE r.run_id=?", (run_id,),
    ):
        revision, kind, statement, quote, interpretation, conditions, exceptions = row
        terms = " ".join(term[0] for term in conn.execute(
            "SELECT term FROM concept_terms WHERE revision_id=? ORDER BY term,kind", (revision,)
        ))
        expected[(revision, kind, statement,
                  " ".join((statement, quote, interpretation, conditions, exceptions, terms)))] += 1
    evidence = {
        row[0]: tuple(row)
        for row in conn.execute(
            "SELECT e.evidence_id,e.kind,l.title,e.text FROM run_evidence_alias a "
            "JOIN evidence_units e USING(evidence_id) "
            "JOIN run_lectures l ON l.lecture_id=e.lecture_id AND l.run_id=a.run_id WHERE a.run_id=?", (run_id,),
        )
    }
    covered_segments = {
        row[0] for row in conn.execute(
            "SELECT to_evidence FROM evidence_links WHERE run_id=? AND role='derived_from_segment'", (run_id,),
        )
    }
    for evidence_id, row in evidence.items():
        _identity, kind, _title, text = row
        if (text.strip(" ") and (kind in ("source_unit", "ocr")
                                 or (kind == "transcript_segment" and evidence_id not in covered_segments))):
            expected[row] += 1
    actual = Counter(tuple(row) for row in conn.execute("SELECT target_id,kind,title,text FROM search_index"))
    missing, unexpected = sum((expected - actual).values()), sum((actual - expected).values())
    if missing:
        errors.append(f"search_index_missing_rows:{missing}")
    if unexpected:
        errors.append(f"search_index_unexpected_rows:{unexpected}")
    return sum(expected.values())


def validate_core(
    db_path: Path | str, *, artifact_root: Path | str | None = None, verify_files: bool = True,
) -> dict[str, Any]:
    """Validate structure, foreign keys, accepted-run state, and retained bytes.

    This checks preservation and technical consistency, not the truth of claims
    or the competence of a legacy reviewer. Unknown reviewer fields stay unknown.
    """
    errors: list[str] = []
    warnings: list[str] = []
    result: dict[str, Any] = {
        "valid": False, "errors": errors, "warnings": warnings, "schema_version": None,
        "current_run": None, "counts": {}, "verified_blob_count": 0,
        "artifact_bytes_verified": verify_files,
    }
    conn = None
    try:
        database, root = dataset_paths(db_path, artifact_root)
        reject_linked_path(database)
        reject_linked_path(root)
        if not database.is_file():
            raise CoreError("database_missing")
        conn = connect(database, readonly=True)
        integrity = [row[0] for row in conn.execute("PRAGMA integrity_check")]
        if integrity != ["ok"]:
            errors.extend("sqlite_integrity:" + text for text in integrity)
        if _schema_objects(conn) != _schema_contract():
            raise CoreError("schema_contract_mismatch")
        version = conn.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
        result["schema_version"] = version[0] if version else None
        if result["schema_version"] != SCHEMA_VERSION:
            raise CoreError("schema_version_mismatch")
        violations = list(conn.execute("PRAGMA foreign_key_check"))
        errors.extend(f"foreign_key:{row[0]}:{row[1]}:{row[2]}" for row in violations)
        run_id = current_run(conn)
        result["current_run"] = run_id
        run = conn.execute("SELECT status,inventory_json FROM import_runs WHERE run_id=?", (run_id,)).fetchone()
        if run is None or run[0] != "complete":
            raise CoreError("current_run_not_complete")
        inventory = json.loads(run[1])
        if not isinstance(inventory, dict) or not inventory:
            errors.append("current_inventory_missing_or_empty")
        counts = _current_counts(conn, run_id)
        result["counts"] = counts
        result["source_counts"] = _check_inventory(conn, run_id, inventory, errors, counts)
        _check_current_links(conn, run_id, errors, warnings)
        result["expected_search_rows"] = _check_search_index(conn, run_id, errors)
        historical_runs = list(conn.execute(
            "SELECT run_id,inventory_json FROM import_runs WHERE status='complete' AND run_id<>? ORDER BY run_id",
            (run_id,),
        ))
        result["historical_runs_checked"] = len(historical_runs)
        for previous_id, previous_inventory_json in historical_runs:
            previous_errors: list[str] = []
            previous_warnings: list[str] = []
            previous_counts = _current_counts(conn, previous_id)
            _check_inventory(conn, previous_id, json.loads(previous_inventory_json), previous_errors, previous_counts)
            _check_current_links(conn, previous_id, previous_errors, previous_warnings)
            for key in ("lectures", "artifacts", "records", "evidence_units"):
                if previous_counts[key] == 0:
                    previous_errors.append("current_run_empty:" + key)
            for row in conn.execute(
                "SELECT code FROM import_issues WHERE run_id=? AND severity='error'", (previous_id,)
            ):
                previous_errors.append("current_import_error:" + row[0])
            errors.extend(f"historical_run:{previous_id}:{error}" for error in previous_errors)
            warnings.extend(f"historical_run:{previous_id}:{warning}" for warning in previous_warnings)
        for key in ("lectures", "artifacts", "records", "evidence_units"):
            if counts[key] == 0:
                errors.append("current_run_empty:" + key)
        for row in conn.execute("SELECT code FROM import_issues WHERE run_id=? AND severity='error'", (run_id,)):
            errors.append("current_import_error:" + row[0])
        warning_count = conn.execute(
            "SELECT count(*) FROM import_issues WHERE run_id=? AND severity='warning'", (run_id,)
        ).fetchone()[0]
        if warning_count:
            warnings.append(f"current_import_warnings:{warning_count}")
        if counts["unknown_review_actors"]:
            warnings.append(f"unknown_review_actors:{counts['unknown_review_actors']}")
        if counts["unknown_review_dates"]:
            warnings.append(f"unknown_review_dates:{counts['unknown_review_dates']}")
        for (kind, name), _sql in _schema_contract().items():
            if kind != "table" or name.startswith("search_index"):
                continue
            for column in conn.execute(f'PRAGMA table_info("{name}")'):
                if column[1].endswith("_json"):
                    invalid = conn.execute(
                        f'SELECT count(*) FROM "{name}" WHERE NOT json_valid("{column[1]}")'
                    ).fetchone()[0]
                    if invalid:
                        errors.append(f"invalid_json:{name}:{column[1]}:{invalid}")
        checked: dict[str, tuple[str, int]] = {}
        for row in conn.execute(
            "SELECT r.revision_id,r.sha256,r.byte_size,r.blob_path,a.kind FROM artifact_revisions r "
            "JOIN artifacts a USING(artifact_id)"
        ):
            revision, digest, size, relative, kind = row
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                errors.append(f"invalid_artifact_hash:{revision}")
                continue
            if relative != f"blobs/{digest[:2]}/{digest}":
                errors.append(f"noncanonical_blob_path:{revision}")
                continue
            if kind.lower() in {"video", "source_video", "audio", "source_audio"}:
                errors.append(f"excluded_artifact_kind:{revision}")
                continue
            if not isinstance(size, int) or size < 0:
                errors.append(f"invalid_artifact_size:{revision}")
                continue
            if relative in checked:
                if checked[relative] != (digest, size):
                    errors.append(f"inconsistent_blob_metadata:{revision}")
                continue
            checked[relative] = (digest, size)
            try:
                blob = confined(root, relative)
                if verify_files:
                    if not blob.is_file():
                        errors.append(f"blob_missing:{revision}")
                    elif blob.stat().st_size != size or file_hash(blob) != digest:
                        errors.append(f"blob_content_mismatch:{revision}")
                    else:
                        result["verified_blob_count"] += 1
            except (CoreError, OSError) as exc:
                errors.append(f"blob_path_invalid:{revision}:{exc}")
    except (CoreError, OSError, sqlite3.Error, ValueError, TypeError) as exc:
        errors.append(str(exc))
    finally:
        if conn is not None:
            conn.close()
    result["valid"] = not errors
    return result
