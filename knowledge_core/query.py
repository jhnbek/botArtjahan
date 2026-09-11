"""Read-only search and traceable evidence retrieval without application services."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .db import CoreError, confined, connect, current_run
from .validation import dataset_paths, reject_linked_path


def _open(dataset, run_id=None):
    database, root = dataset_paths(dataset)
    reject_linked_path(database)
    conn = connect(database, readonly=True)
    try:
        run_id = run_id or current_run(conn)
        row = conn.execute("SELECT status FROM import_runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None or row[0] != "complete":
            raise CoreError("requested snapshot is not complete")
    except Exception:
        conn.close()
        raise
    return conn, root, run_id


def search(dataset: Path | str, text: str, *, limit: int = 20, prefix: bool = True) -> list[dict]:
    """Search current snapshot; all Unicode word tokens must match.

    FTS operators in user input are treated as punctuation/words. Prefix matching
    is explicit and supports word endings; this is not a semantic search engine.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise CoreError("limit must be between 1 and 100")
    tokens = re.findall(r"\w+", text, re.UNICODE)
    if not tokens or len(tokens) > 32:
        raise CoreError("query must contain between 1 and 32 words")
    expression = " AND ".join('"' + word + '"' + ("*" if prefix else "") for word in tokens)
    conn, _, run_id = _open(dataset)
    try:
        rows = conn.execute("""SELECT target_id,kind,title,
            snippet(search_index,-1,'[',']',' ... ',40) AS excerpt, bm25(search_index) AS rank
            FROM search_index WHERE search_index MATCH ? ORDER BY rank,target_id LIMIT ?""",
                            (expression, limit)).fetchall()
        results = []
        for row in rows:
            result = dict(row)
            identity = conn.execute("""SELECT i.namespace,i.legacy_id,k.review_state,k.legacy_status
                FROM knowledge_revisions k JOIN knowledge_items i USING(item_id)
                JOIN run_knowledge r ON r.item_id=k.item_id AND r.revision_id=k.revision_id
                WHERE r.run_id=? AND k.revision_id=?""", (run_id, row["target_id"])).fetchone()
            if identity:
                result.update(dict(identity))
            result["run_id"] = run_id
            results.append(result)
        return results
    finally:
        conn.close()


def trace(dataset: Path | str, identifier: str, *, namespace: str | None = None,
          run_id: str | None = None) -> dict:
    """Return complete stored text, declared knowledge lineage and source evidence.

    Accept a search target UUID, stable item UUID, or an unambiguous legacy ID.
    Only declared outgoing knowledge relations are followed; no contradiction,
    confidence or approval is inferred. Every evidence edge belongs to this run.
    """
    conn, root, run_id = _open(dataset, run_id)
    try:
        sql = """SELECT k.*,i.namespace,i.legacy_id,i.kind FROM run_knowledge r
            JOIN knowledge_revisions k ON r.revision_id=k.revision_id
            JOIN knowledge_items i ON i.item_id=k.item_id
            WHERE r.run_id=? AND (i.item_id=? OR i.legacy_id=? OR k.revision_id=?)"""
        args = [run_id, identifier, identifier, identifier]
        if namespace is not None:
            sql += " AND i.namespace=?"
            args.append(namespace)
        matches = conn.execute(sql, args).fetchall()
        if len(matches) > 1:
            raise CoreError("identifier exists in multiple namespaces; supply --namespace or a UUID")
        nodes, relations, evidence, evidence_edges, evidence_roles, reviews = {}, {}, {}, {}, {}, {}
        records = {}
        files = {}
        source_paths = {}
        for row in conn.execute(
                "SELECT revision_id,source_path FROM run_artifacts WHERE run_id=? ORDER BY source_path", (run_id,)):
            source_paths.setdefault(row["revision_id"], []).append(row["source_path"])
        reviews_by_target = {}
        for row in conn.execute("""SELECT v.* FROM review_events v JOIN run_reviews r USING(review_id)
                WHERE r.run_id=?""", (run_id,)):
            reviews_by_target.setdefault(row["target_revision"], []).append(row)

        def original(record_id):
            if record_id not in records:
                records[record_id] = _record(conn, record_id)
            return records[record_id]

        pending = [matches[0]["revision_id"]] if matches else []
        start = pending[0] if pending else identifier
        evidence_pending = []
        if not pending:
            if namespace is not None:
                raise CoreError("knowledge identifier not found in the requested namespace")
            found = conn.execute("SELECT 1 FROM run_evidence_alias WHERE run_id=? AND evidence_id=?",
                                 (run_id, identifier)).fetchone()
            if not found:
                raise CoreError("identifier not found in the requested snapshot")
            evidence_pending.append(identifier)
        while pending:
            revision = pending.pop()
            if revision in nodes:
                continue
            row = conn.execute("""SELECT k.*,i.namespace,i.legacy_id,i.kind FROM knowledge_revisions k
                JOIN knowledge_items i USING(item_id)
                JOIN run_knowledge r ON r.item_id=k.item_id AND r.revision_id=k.revision_id
                WHERE r.run_id=? AND k.revision_id=?""", (run_id, revision)).fetchone()
            if not row:
                raise CoreError("knowledge relation leaves the requested snapshot")
            node = dict(row)
            for field in ("conditions", "exceptions", "context"):
                node[field] = json.loads(node.pop(field + "_json"))
            node["original"] = original(node["record_id"])
            nodes[revision] = node
            for edge in conn.execute("SELECT * FROM knowledge_relations WHERE from_revision=?", (revision,)):
                relations[tuple(edge)] = dict(edge)
                pending.append(edge["to_revision"])
            for edge in conn.execute("SELECT * FROM knowledge_evidence WHERE revision_id=?", (revision,)):
                evidence_roles[tuple(edge)] = dict(edge)
                evidence_pending.append(edge["evidence_id"])
            for event in reviews_by_target.get(revision, []):
                review = dict(event)
                review["payload"] = json.loads(review.pop("payload_json"))
                reviews[review["review_id"]] = review
            if len(nodes) > 10000:
                raise CoreError("knowledge lineage exceeds the retrieval safety limit")
        while evidence_pending:
            eid = evidence_pending.pop()
            if eid in evidence:
                continue
            row = conn.execute("""SELECT e.*,l.title,a.sha256,a.byte_size,a.blob_path
                FROM evidence_units e JOIN artifact_revisions a ON a.revision_id=e.artifact_revision
                JOIN run_lectures l ON l.lecture_id=e.lecture_id AND l.run_id=? WHERE e.evidence_id=?
                AND EXISTS(SELECT 1 FROM run_evidence_alias a WHERE a.run_id=l.run_id AND a.evidence_id=e.evidence_id)""",
                               (run_id, eid)).fetchone()
            if not row:
                raise CoreError("evidence leaves the requested snapshot")
            item = dict(row)
            item["metadata"] = json.loads(item.pop("metadata_json"))
            if item["blob_path"] not in files:
                files[item["blob_path"]] = str(confined(root, item["blob_path"]))
            item["file"] = files[item["blob_path"]]
            item["source_paths"] = source_paths.get(item["artifact_revision"], [])
            # Sorting these few aliases in Python lets SQLite use its exact
            # (run_id,evidence_id) index instead of scanning the run for order.
            item["aliases"] = sorted((dict(r) for r in conn.execute(
                "SELECT namespace,legacy_id FROM run_evidence_alias WHERE run_id=? AND evidence_id=?",
                (run_id, eid))), key=lambda alias: (alias["namespace"], alias["legacy_id"]))
            evidence[eid] = item
            # Parent associations make an OCR/image search hit traceable back to
            # its frame/text. Return the original edge direction and role.
            # An OR predicate can scan every edge in a run for each visited
            # node. UNION gives both directions their own indexed lookup and
            # returns a self-loop once, just like the original association.
            for edge in conn.execute("""SELECT * FROM evidence_links WHERE run_id=? AND from_evidence=?
                    UNION SELECT * FROM evidence_links WHERE run_id=? AND to_evidence=?""",
                                     (run_id, eid, run_id, eid)):
                value = dict(edge)
                value["original"] = original(edge["record_id"])
                evidence_edges[tuple(edge)] = value
                evidence_pending.append(edge["to_evidence"])
                evidence_pending.append(edge["from_evidence"])
            if len(evidence) > 100000:
                raise CoreError("evidence lineage exceeds the retrieval safety limit")
        return {"run_id": run_id, "target_id": start, "knowledge": list(nodes.values()),
                "relations": list(relations.values()), "knowledge_evidence": list(evidence_roles.values()),
                "evidence": list(evidence.values()), "evidence_links": list(evidence_edges.values()),
                "reviews": list(reviews.values())}
    finally:
        conn.close()


def _record(conn, record_id):
    row = conn.execute("SELECT * FROM legacy_records WHERE record_id=?", (record_id,)).fetchone()
    result = dict(row)
    result["payload"] = json.loads(result.pop("raw_json"))
    return result
