"""Import the retained knowledge ledgers without promoting their assertions.

Only the paths below are read. Videos, old pipeline code, prompts, chat logs,
and audit directories are outside this import. The caller owns transactions
and the external content-addressed artifact store.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .db import CoreError, canonical_json, confined, stable_id


CLAIM_GLOB = "_knowledge_base/structured/lecture_pass/lectures/*/claims_multimodal.jsonl"
BASE = "_knowledge_base/structured/consolidation/"
# This whitelist is intentionally independent of the old application's imports.
JSONL_INPUTS = {
    "candidates": BASE + "canonical_candidate_pool/canonical_candidate_pool.jsonl",
    "gap_claims": BASE + "reviewed_gap_claims/reviewed_gap_claim_candidates.jsonl",
    "support_annotations": BASE + "canonical_candidate_pool/candidate_pool_support_annotations.jsonl",
    "rules": BASE + "signed_canonical_rulebook/canonical_rule_cards.jsonl",
    "traceability": BASE + "signed_canonical_rulebook/canonical_rule_traceability.jsonl",
    "candidate_ledger": BASE + "signed_canonical_rulebook/canonical_candidate_ledger.jsonl",
    "context_register": BASE + "signed_canonical_rulebook/canonical_context_register.jsonl",
    "gap_overrides": BASE + "reviewed_gap_claims/manual_gap_review_overrides.jsonl",
    "mined_candidates": BASE + "lecture_multimodal_calibration_candidates/candidates.jsonl",
}
JSON_INPUTS = {
    "visual_reviews": BASE + "lecture_multimodal_calibration_candidates/manual_visual_review_notes.json",
    "signoff": BASE + "signed_canonical_rulebook/human_signoff_record.json",
    "rulebook_status": BASE + "signed_canonical_rulebook/signed_canonical_rulebook_status.json",
}
OPTIONAL_JSON_INPUTS = {"taxonomy": "knowledge_bot/knowledge_taxonomy.json"}
RULE_ROLES = (
    "source_candidate_ids", "accepted_misc_candidate_ids", "conditional_candidate_ids",
    "warning_candidate_ids", "manual_resolution_candidate_ids",
    "resolved_conditional_candidate_ids", "condition_or_prohibition_candidate_ids",
    "rule_support_candidate_ids", "supporting_context_candidate_ids",
    "operational_formula_candidate_ids",
)


@dataclass(frozen=True)
class Row:
    path: Path
    locator: str
    payload: dict[str, Any]


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CoreError(f"{label}: expected a JSON object")
    return value


def _text(value: Any, label: str, *, optional: bool = False) -> str:
    if value is None and optional:
        return ""
    if not isinstance(value, str) or (not optional and not value.strip()):
        raise CoreError(f"{label}: expected {'a string' if optional else 'a nonempty string'}")
    return value


def _ids(value: Any, label: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise CoreError(f"{label}: expected an array of identifiers")
    return [_text(item, label) for item in value]


def _reject_constant(value: str) -> None:
    raise CoreError(f"non-finite JSON constant: {value}")


def _parse(text: str, label: str) -> dict[str, Any]:
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise CoreError(f"{label}: duplicate JSON key {key!r}")
            result[key] = value
        return result

    try:
        return _object(json.loads(text, object_pairs_hook=unique_pairs,
                                  parse_constant=_reject_constant), label)
    except json.JSONDecodeError as exc:
        raise CoreError(f"{label}: invalid JSON at line {exc.lineno}") from exc


def _path(root: Path, relative: str) -> Path:
    path = confined(root, relative)
    if not path.is_file():
        raise CoreError(f"required knowledge input is missing: {relative}")
    return path


def _jsonl(path: Path) -> list[Row]:
    result = []
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if line.strip():
            result.append(Row(path, f"line:{number}", _parse(line, f"{path.name}:{number}")))
    return result


def _unique(rows: list[Row], key: str, label: str) -> dict[str, Row]:
    result = {}
    for row in rows:
        legacy_id = _text(row.payload.get(key), f"{label}.{key}")
        if legacy_id in result:
            raise CoreError(f"{label}: duplicate legacy identifier {legacy_id!r}")
        result[legacy_id] = row
    return result


def input_paths(source_root: Path) -> list[Path]:
    """Return the complete, confined input whitelist before snapshot planning."""
    root = Path(source_root).resolve()
    claim_paths = sorted(root.glob(CLAIM_GLOB))
    if not claim_paths:
        raise CoreError("no retained claims_multimodal.jsonl inputs found")
    relatives = list(JSONL_INPUTS.values()) + list(JSON_INPUTS.values())
    relatives.extend(path.relative_to(root).as_posix() for path in claim_paths)
    for relative in OPTIONAL_JSON_INPUTS.values():
        path = confined(root, relative)
        if path.exists():
            relatives.append(relative)
    return sorted({_path(root, relative) for relative in relatives})


def _inputs(root: Path) -> tuple[dict[str, list[Row]], dict[str, Row]]:
    paths = input_paths(root)
    claim_paths = [path for path in paths if path.name == "claims_multimodal.jsonl"]
    inputs = {name: _jsonl(_path(root, relative)) for name, relative in JSONL_INPUTS.items()}
    inputs["claims"] = [row for path in claim_paths
                        for row in _jsonl(_path(root, path.relative_to(root).as_posix()))]
    documents = {}
    for name, relative in JSON_INPUTS.items():
        path = _path(root, relative)
        documents[name] = Row(path, "#", _parse(path.read_text(encoding="utf-8-sig"), path.name))
    for name, relative in OPTIONAL_JSON_INPUTS.items():
        path = root / relative
        if path in paths:
            documents[name] = Row(path, "#", _parse(path.read_text(encoding="utf-8-sig"), path.name))
    return inputs, documents


class _Importer:
    def __init__(self, ctx):
        self.ctx = ctx
        self.conn = ctx.conn
        self.counts: Counter[str] = Counter()
        self.versions: dict[tuple[str, str], str] = {}
        self.records: dict[tuple[Path, str], str] = {}

    def record(self, row: Row, kind: str, legacy_id: str | None = None) -> str:
        key = (row.path, row.locator)
        if key not in self.records:
            self.records[key] = self.ctx.record(row.path, row.locator, row.payload, kind, legacy_id)
            self.counts["legacy_records"] += 1
        return self.records[key]

    def item(self, row: Row, namespace: str, legacy_id: str, kind: str) -> str:
        payload = row.payload
        record_id = self.record(row, namespace, legacy_id)
        item_id = stable_id("knowledge_item", namespace + ":" + legacy_id)
        # The deterministic input-snapshot ID includes evidence dependencies.
        # Reusing unchanged claim JSON with a changed transcript must not attach
        # old and new evidence to the same historical claim revision.
        digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
        revision_id = stable_id("knowledge_revision", item_id + ":" + record_id + ":" + digest + ":" + self.ctx.run_id)
        if kind == "concept":
            statement = _text(payload.get("title"), "concept.title")
            interpretation = _text(payload.get("description"), "concept.description", optional=True)
            quote = ""
            conditions, exceptions = {}, {}
        elif kind == "rule":
            statement = _text(payload.get("ru_title") or payload.get("title"), "rule.title")
            interpretation = _text(payload.get("purpose"), "rule.purpose", optional=True)
            quote = ""
            conditions = {key: payload[key] for key in
                          ("conditions_for_review", "parent_conditions_for_review") if key in payload}
            exceptions = {key: payload[key] for key in
                          ("prohibitions_for_review", "parent_prohibitions_for_review") if key in payload}
            for key, value in {**conditions, **exceptions}.items():
                if not isinstance(value, list):
                    raise CoreError(f"rule.{key}: expected an array")
        else:
            statement = _text(payload.get("statement"), f"{kind}.statement")
            quote = _text(payload.get("quote"), f"{kind}.quote", optional=True)
            interpretation = _text(payload.get("interpretation"), f"{kind}.interpretation", optional=True)
            conditions, exceptions = {}, {}
        # Original field names, status vocabulary, flags and inherited content
        # remain available, including unusual historical claim_type values.
        context = {key: value for key, value in payload.items()
                   if key not in {"statement", "quote", "interpretation"}}
        status = payload.get("status")
        if status is not None:
            status = _text(status, f"{kind}.status", optional=True)
        self.conn.execute("INSERT OR IGNORE INTO knowledge_items VALUES (?,?,?,?)",
                          (item_id, namespace, legacy_id, kind))
        self.conn.execute(
            "INSERT OR IGNORE INTO knowledge_revisions "
            "(revision_id,item_id,record_id,statement,quote,interpretation,conditions_json,"
            "exceptions_json,context_json,legacy_status,review_state) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (revision_id, item_id, record_id, statement, quote, interpretation,
             canonical_json(conditions), canonical_json(exceptions), canonical_json(context),
             status, "imported_unverified"))
        existing = self.conn.execute(
            "SELECT revision_id FROM run_knowledge WHERE run_id=? AND item_id=?",
            (self.ctx.run_id, item_id)).fetchone()
        if existing and existing[0] != revision_id:
            raise CoreError("one import run cannot contain two revisions of a knowledge item")
        self.conn.execute("INSERT OR IGNORE INTO run_knowledge VALUES (?,?,?)",
                          (self.ctx.run_id, item_id, revision_id))
        self.versions[namespace, legacy_id] = revision_id
        self.counts[{"claim": "claims", "candidate": "candidates", "rule": "rules", "concept": "concepts"}[kind]] += 1
        return revision_id

    def relation(self, source: str, target: str, kind: str, role: str, record_id: str) -> None:
        self.conn.execute("INSERT OR IGNORE INTO knowledge_relations VALUES (?,?,?,?,?)",
                          (source, target, kind, role, record_id))
        self.counts["declared_relation_links"] += 1

    def evidence(self, revision: str, namespace: str, legacy_id: str, role: str) -> None:
        evidence_id = self.ctx.evidence(namespace, legacy_id)
        if evidence_id is None:
            self.ctx.issue("unresolved_knowledge_evidence", {
                "revision_id": revision, "namespace": namespace, "legacy_id": legacy_id,
                "role": role})
            self.counts["unresolved_evidence_links"] += 1
            return
        self.conn.execute("INSERT OR IGNORE INTO knowledge_evidence VALUES (?,?,?)",
                          (revision, evidence_id, role))
        self.counts["declared_evidence_links"] += 1

    def evidence_fields(self, revision: str, source: dict[str, Any], *, annotation: bool = False) -> None:
        role = "legacy_support_annotation" if annotation else "legacy_source"
        for key in ("unit_ids", "source_units"):
            for unit in _ids(source.get(key), f"source.{key}"):
                self.evidence(revision, "source_unit", unit, role)
        for key, endpoint in (("source_start_unit_id", "start"), ("source_end_unit_id", "end")):
            if source.get(key):
                self.evidence(revision, "source_unit", _text(source[key], key), role + "_range_" + endpoint)
        for key in ("frames", "source_frames"):
            frames = source.get(key) or []
            if not isinstance(frames, list):
                raise CoreError(f"source.{key}: expected an array")
            for frame in frames:
                frame = _object(frame, "source frame")
                path = _text(frame.get("path"), "source frame.path").replace("\\", "/")
                self.evidence(revision, "frame", path, role + "_frame_reference")

    def review(self, row: Row, kind: str, legacy_id: str, target: str | None,
               legacy_target: str | None, status: str | None, scope: str,
               *, actor: str | None = None, event_at: str | None = None) -> None:
        record_id = self.record(row, kind, legacy_id)
        review_id = stable_id("review_event", record_id + ":" + (target or "") + ":" + scope)
        for label, value in (("status", status), ("actor", actor), ("event_at", event_at)):
            if value is not None:
                _text(value, f"review.{label}", optional=True)
        self.conn.execute("INSERT OR IGNORE INTO review_events VALUES (?,?,?,?,?,?,?,?,?,?)",
                          (review_id, record_id, target, legacy_target, status, actor, event_at,
                           scope, "imported_legacy", canonical_json(row.payload)))
        self.conn.execute("INSERT OR IGNORE INTO run_reviews VALUES (?,?)", (self.ctx.run_id, review_id))
        self.counts["review_events"] += 1
        if target is None:
            self.ctx.issue("legacy_review_target_unresolved", {
                "record_id": record_id, "legacy_target": legacy_target, "kind": kind})


def import_knowledge(ctx) -> dict[str, int]:
    """Import explicit legacy claims, candidate lineage and recorded reviews.

    No transaction is committed here. Missing inputs and malformed schemas raise
    CoreError; unresolved historical references are retained and reported through
    ctx.issue. All normalized approval states begin as imported_unverified.
    """
    root = Path(ctx.source_root).resolve()
    paths = input_paths(root)
    for path in paths:
        # Empty JSONL ledgers still belong to the declared source snapshot and
        # must retain their exact bytes even though they contain no records.
        ctx.artifact(path, "knowledge_document")
    inputs, docs = _inputs(root)
    claims = _unique(inputs["claims"], "claim_id", "claims")
    candidates = _unique(inputs["candidates"], "candidate_id", "canonical candidates")
    rules = _unique(inputs["rules"], "rule_id", "rules")
    mined = _unique(inputs["mined_candidates"], "candidate_id", "mined candidates")
    imp = _Importer(ctx)

    for legacy_id, row in claims.items():
        rev = imp.item(row, "legacy_claim", legacy_id, "claim")
        imp.evidence_fields(rev, _object(row.payload.get("source"), "claim.source"))
    for legacy_id, row in candidates.items():
        rev = imp.item(row, "canonical_candidate", legacy_id, "candidate")
        source = _object(row.payload.get("source_trace"), "candidate.source_trace")
        imp.evidence_fields(rev, source)
        source_kind = source.get("source_kind")
        if source_kind == "observed_multimodal_claim":
            claim_rev = imp.versions.get(("legacy_claim", legacy_id))
            if claim_rev is None:
                raise CoreError("observed canonical candidate has no corresponding original claim")
            # Both the documented source kind and exact identifier must agree.
            imp.relation(rev, claim_rev, "derived_from", "observed_multimodal_claim",
                         imp.record(row, "canonical_candidate", legacy_id))
        elif source_kind != "accepted_legacy_gap_claim_candidate":
            raise CoreError(f"unsupported candidate source_kind: {source_kind!r}")
    for legacy_id, row in rules.items():
        imp.item(row, "canonical_rule", legacy_id, "rule")

    for legacy_id, row in rules.items():
        rev = imp.versions["canonical_rule", legacy_id]
        record = imp.record(row, "canonical_rule", legacy_id)
        extra_roles = {key for key in row.payload if key.endswith("_candidate_ids")} - set(RULE_ROLES)
        if extra_roles:
            raise CoreError(f"unrecognized rule candidate-role fields: {sorted(extra_roles)}")
        for role in RULE_ROLES:
            for candidate_id in _ids(row.payload.get(role), f"rule.{role}"):
                target = imp.versions.get(("canonical_candidate", candidate_id))
                if target is None:
                    raise CoreError(f"rule references an unknown canonical candidate: {candidate_id}")
                imp.relation(rev, target, "uses_candidate", role, record)

    for row in inputs["traceability"]:
        candidate_id = _text(row.payload.get("candidate_id"), "traceability.candidate_id")
        record = imp.record(row, "canonical_traceability", candidate_id)
        candidate_rev = imp.versions.get(("canonical_candidate", candidate_id))
        if candidate_rev is None:
            raise CoreError("traceability references an unknown candidate")
        for rule_id in _ids(row.payload.get("canonical_rule_ids"), "traceability.canonical_rule_ids"):
            rule_rev = imp.versions.get(("canonical_rule", rule_id))
            if rule_rev is None:
                raise CoreError("traceability references an unknown canonical rule")
            imp.relation(rule_rev, candidate_rev, "legacy_traceability", "canonical_rule_ids", record)

    for kind, id_key in (("gap_claims", "claim_id"), ("candidate_ledger", "candidate_id"),
                         ("context_register", "context_id"), ("mined_candidates", "candidate_id")):
        for row in inputs[kind]:
            imp.record(row, kind, _text(row.payload.get(id_key), f"{kind}.{id_key}"))
        imp.counts[kind + "_records"] = len(inputs[kind])
    for row in inputs["support_annotations"]:
        gap_id = _text(row.payload.get("gap_id"), "annotation.gap_id")
        imp.record(row, "candidate_support_annotation", gap_id)
        target_id = _text(row.payload.get("target_candidate_id"), "annotation.target_candidate_id")
        target = imp.versions.get(("canonical_candidate", target_id))
        if target is None:
            raise CoreError("support annotation references an unknown candidate")
        imp.evidence_fields(target, _object(row.payload.get("source_trace"), "annotation.source_trace"),
                            annotation=True)
    imp.counts["support_annotation_records"] = len(inputs["support_annotations"])
    imp.counts["traceability_records"] = len(inputs["traceability"])

    visual_doc = docs["visual_reviews"]
    imp.record(visual_doc, "visual_review_ledger", _text(visual_doc.payload.get("dataset_id"), "visual.dataset_id"))
    reviews = visual_doc.payload.get("reviews")
    if not isinstance(reviews, list):
        raise CoreError("visual review ledger.reviews: expected an array")
    for index, payload in enumerate(reviews):
        payload = _object(payload, "visual review")
        candidate_id = _text(payload.get("candidate_id"), "visual review.candidate_id")
        mined_row = mined.get(candidate_id)
        mapped_claim = mined_row.payload.get("claim_id") if mined_row else None
        explicit_claim = payload.get("claim_id")
        if explicit_claim and mapped_claim and explicit_claim != mapped_claim:
            imp.ctx.issue("visual_review_claim_disagreement", {
                "legacy_candidate_id": candidate_id, "explicit_claim": explicit_claim,
                "mined_claim": mapped_claim})
            target = None
        else:
            target = imp.versions.get(("legacy_claim", explicit_claim or mapped_claim))
        imp.review(Row(visual_doc.path, f"#/reviews/{index}", payload), "visual_review", candidate_id,
                   target, candidate_id, payload.get("review_status"), "legacy_visual_evidence_review")
    imp.counts["visual_reviews"] = len(reviews)

    for row in inputs["gap_overrides"]:
        payload = row.payload
        gap_id = _text(payload.get("gap_id"), "gap override.gap_id")
        target_id = payload.get("target_claim_id")
        target = imp.versions.get(("canonical_candidate", target_id)) if target_id else None
        imp.review(row, "gap_review_override", gap_id, target, target_id or gap_id,
                   payload.get("override_status"), "legacy_gap_review_override")
    imp.counts["gap_reviews"] = len(inputs["gap_overrides"])

    signoff = docs["signoff"]
    signoff_id = _text(signoff.payload.get("signoff_id"), "signoff.signoff_id")
    imp.record(signoff, "legacy_signoff_record", signoff_id)
    matched_rules = [(rule_id, row) for rule_id, row in rules.items()
                     if row.payload.get("human_signoff_id") == signoff_id]
    # The shared document is retained once. Each association is declared by the
    # rule's own signoff identifier; no reviewer identity is inferred elsewhere.
    for rule_id, _ in matched_rules or [(None, None)]:
        target = imp.versions.get(("canonical_rule", rule_id)) if rule_id else None
        imp.review(signoff, "legacy_signoff_record", signoff_id, target, rule_id or signoff_id,
                   canonical_json(signoff.payload.get("human_signoff_recorded")),
                   _text(signoff.payload.get("scope"), "signoff.scope", optional=True),
                   actor=signoff.payload.get("signed_by"), event_at=signoff.payload.get("signed_at"))
    imp.counts["signoff_records"] = 1
    imp.record(docs["rulebook_status"], "legacy_rulebook_status",
               docs["rulebook_status"].payload.get("canonical_rulebook_version"))
    if "taxonomy" in docs:
        taxonomy = docs["taxonomy"]
        imp.record(taxonomy, "legacy_knowledge_taxonomy", "knowledge_taxonomy")
        for concept_id, payload in taxonomy.payload.items():
            _text(concept_id, "taxonomy concept identifier")
            payload = _object(payload, "taxonomy concept")
            aliases = _ids(payload.get("aliases"), "concept.aliases")
            keywords = _ids(payload.get("keywords"), "concept.keywords")
            # JSON Pointer escaping preserves arbitrary historical topic keys.
            pointer = concept_id.replace("~", "~0").replace("/", "~1")
            row = Row(taxonomy.path, "#/" + pointer, payload)
            revision = imp.item(row, "knowledge_taxonomy", concept_id, "concept")
            terms = [(concept_id, "identifier"), (_text(payload.get("title"), "concept.title"), "preferred")]
            terms.extend((alias, "alias") for alias in aliases)
            terms.extend((keyword, "keyword") for keyword in keywords)
            # Count distinct declarations per concept, matching the table key;
            # duplicate aliases remain present in the preserved legacy payload.
            terms = sorted(set(terms))
            for term, kind in terms:
                imp.conn.execute("INSERT OR IGNORE INTO concept_terms VALUES (?,?,?)", (revision, term, kind))
            imp.counts["declared_concept_terms"] += len(terms)
    return dict(imp.counts)
