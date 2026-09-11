"""Synthetic provenance and migration checks; no private corpus is required."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from knowledge_core.db import CoreError, canonical_json, initialize, stable_id
from knowledge_core.legacy_knowledge import JSON_INPUTS, JSONL_INPUTS, OPTIONAL_JSON_INPUTS, import_knowledge, input_paths


class FixtureContext:
    def __init__(self, root: Path):
        self.source_root = root.resolve()
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        initialize(self.conn)
        self.run_id = "fixture-run"
        self.issues = []
        self.conn.execute("INSERT INTO import_runs VALUES (?,?,?,?,?)",
                          (self.run_id, "2026-01-01", "1", "{}", "building"))
        self.conn.execute("INSERT INTO lectures VALUES (?,?,?)", ("lecture", "lecture", "Synthetic lecture"))
        self.conn.execute("INSERT INTO artifacts VALUES (?,?,?,?)", ("evidence-artifact", "transcript", "lecture", "evidence"))
        self.conn.execute("INSERT INTO artifact_revisions VALUES (?,?,?,?,?)",
                          ("evidence-revision", "evidence-artifact", "0" * 64, 0, "blobs/evidence"))
        self.conn.execute("INSERT INTO evidence_units VALUES (?,?,?,?,?,?,?,?,?)",
                          ("unit-evidence", "evidence-revision", "lecture", "transcript_segment", 0, 0, 1000, "Synthetic quote", "{}"))
        self.conn.execute("INSERT INTO evidence_units VALUES (?,?,?,?,?,?,?,?,?)",
                          ("frame-evidence", "evidence-revision", "lecture", "frame", None, 500, None, "", "{}"))
        self.conn.executemany("INSERT INTO run_evidence_alias VALUES (?,?,?,?)", [
            (self.run_id, "source_unit", "U1", "unit-evidence"),
            (self.run_id, "frame", "lecture/frames/1.jpg", "frame-evidence"),
        ])

    def artifact(self, path, kind, lecture_id=None, identity=None):
        relative = path.relative_to(self.source_root).as_posix()
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        artifact = stable_id("fixture-artifact", identity or relative)
        revision = stable_id("fixture-artifact-revision", artifact + digest)
        self.conn.execute("INSERT OR IGNORE INTO artifacts VALUES (?,?,?,?)",
                          (artifact, kind, lecture_id, identity or relative))
        self.conn.execute("INSERT OR IGNORE INTO artifact_revisions VALUES (?,?,?,?,?)",
                          (revision, artifact, digest, len(content), "blobs/" + digest))
        self.conn.execute("INSERT OR IGNORE INTO run_artifacts VALUES (?,?,?)", (self.run_id, relative, revision))
        return revision

    def record(self, path, locator, payload, record_type, legacy_id):
        revision = self.artifact(path, "knowledge_document")
        record = stable_id("fixture-record", revision + locator + canonical_json(payload))
        self.conn.execute("INSERT OR IGNORE INTO legacy_records VALUES (?,?,?,?,?,?)",
                          (record, record_type, legacy_id, revision, locator, canonical_json(payload)))
        self.conn.execute("INSERT OR IGNORE INTO run_records VALUES (?,?)", (self.run_id, record))
        return record

    def evidence(self, namespace, legacy_id):
        row = self.conn.execute("SELECT evidence_id FROM run_evidence_alias WHERE run_id=? AND namespace=? AND legacy_id=?",
                                (self.run_id, namespace, legacy_id)).fetchone()
        return row[0] if row else None

    def issue(self, code, detail, severity="warning"):
        self.issues.append((code, detail, severity))


def write_jsonl(path: Path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(canonical_json(value) + "\n" for value in values), encoding="utf-8")


def fixture(root: Path):
    claim = {"claim_id": "C1", "statement": "A synthetic statement", "quote": "Synthetic quote",
             "interpretation": "An interpretation", "status": "historical_status",
             "review_status": "legacy_approved", "confidence": 0.8,
             "claim_type": "legacy vocabulary must survive",
             "source": {"unit_ids": ["U1"], "frames": [{"path": "lecture/frames/1.jpg", "time": "00:00:00"}]}}
    claim_path = root / "_knowledge_base/structured/lecture_pass/lectures/lecture_001/claims_multimodal.jsonl"
    write_jsonl(claim_path, [claim])
    candidate = {"candidate_id": "C1", "statement": "A synthetic statement", "quote": "Synthetic quote",
                 "interpretation": "An interpretation", "status": "accepted_legacy",
                 "source_trace": {"source_kind": "observed_multimodal_claim", "source_units": ["U1"]}}
    gap = {"candidate_id": "G1", "statement": "A conditional statement", "quote": "", "interpretation": "",
           "source_trace": {"source_kind": "accepted_legacy_gap_claim_candidate", "source_gap_id": "gap-1",
                            "source_start_unit_id": "U1", "source_end_unit_id": "U1"}}
    rule = {"rule_id": "R1", "title": "Synthetic rule", "purpose": "Explain a condition",
            "status": "signed", "human_signoff_id": "S1", "source_candidate_ids": ["C1", "G1"],
            "warning_candidate_ids": ["G1"], "conditions_for_review": ["Current condition"],
            "parent_conditions_for_review": ["Inherited condition"],
            "prohibitions_for_review": [], "parent_prohibitions_for_review": ["Inherited prohibition"]}
    values = {name: [] for name in JSONL_INPUTS}
    values.update({
        "candidates": [candidate, gap], "rules": [rule],
        "gap_claims": [{"claim_id": "G1", "source_gap_id": "gap-1", "statement_candidate": "Conditional"}],
        "traceability": [{"candidate_id": "C1", "canonical_rule_ids": ["R1"]},
                         {"candidate_id": "G1", "canonical_rule_ids": ["R1"]}],
        "candidate_ledger": [{"candidate_id": "C1", "canonical_decision": "recorded"},
                             {"candidate_id": "G1", "canonical_decision": "conditional"}],
        "support_annotations": [{"gap_id": "annotation-1", "target_candidate_id": "C1",
                                 "source_trace": {"source_start_unit_id": "U1", "source_end_unit_id": "U1"}}],
        "gap_overrides": [{"gap_id": "gap-1", "target_claim_id": "C1", "override_status": "recorded"},
                          {"gap_id": "gap-without-target", "override_status": "context_only"}],
        "mined_candidates": [{"candidate_id": "M1", "claim_id": "C1"}],
    })
    for name, relative in JSONL_INPUTS.items():
        write_jsonl(root / relative, values[name])
    documents = {
        "visual_reviews": {"dataset_id": "V1", "created_at": "2026-01-01", "reviews": [
            {"candidate_id": "M1", "review_status": "partial", "visual_confirmed": True}]},
        "signoff": {"signoff_id": "S1", "scope": "Synthetic editorial scope", "signed_by": "Recorded reviewer",
                    "signed_at": "2026-01-02", "human_signoff_recorded": True},
        "rulebook_status": {"canonical_rulebook_version": "fixture-v1", "execution_allowed": False},
    }
    for name, relative in JSON_INPUTS.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(canonical_json(documents[name]), encoding="utf-8")
    return claim_path, values, documents


class LegacyKnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="knowledge-core-legacy-test-")
        self.root = Path(self.temp.name).resolve()
        self.claim_path, self.values, self.documents = fixture(self.root)
        self.ctx = FixtureContext(self.root)

    def tearDown(self):
        self.ctx.conn.close()
        self.temp.cleanup()

    def test_preserves_evidence_roles_inheritance_and_unknown_review_authorship(self):
        counts = import_knowledge(self.ctx)
        self.assertEqual((counts["claims"], counts["candidates"], counts["rules"]), (1, 2, 1))
        conn = self.ctx.conn
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM knowledge_items WHERE legacy_id='C1'").fetchone()[0], 2)
        rule = conn.execute("SELECT r.* FROM knowledge_revisions r JOIN knowledge_items i USING(item_id) WHERE i.kind='rule'").fetchone()
        self.assertEqual(json.loads(rule["conditions_json"])["parent_conditions_for_review"], ["Inherited condition"])
        self.assertEqual(json.loads(rule["exceptions_json"])["parent_prohibitions_for_review"], ["Inherited prohibition"])
        self.assertEqual({row[0] for row in conn.execute("SELECT DISTINCT review_state FROM knowledge_revisions")}, {"imported_unverified"})
        visual = conn.execute("SELECT * FROM review_events WHERE scope='legacy_visual_evidence_review'").fetchone()
        self.assertIsNotNone(visual["target_revision"])
        self.assertIsNone(visual["actor"])
        self.assertIsNone(visual["event_at"])
        self.assertEqual(visual["legacy_target"], "M1")
        signoff = conn.execute("SELECT * FROM review_events WHERE scope='Synthetic editorial scope'").fetchone()
        self.assertEqual(signoff["actor"], "Recorded reviewer")
        self.assertEqual(signoff["target_revision"], rule["revision_id"])
        roles = {row[0] for row in conn.execute("SELECT role FROM knowledge_relations")}
        self.assertIn("warning_candidate_ids", roles)
        self.assertIn("canonical_rule_ids", roles)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM knowledge_relations WHERE relation_type='derived_from'").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM review_events WHERE target_revision IS NULL").fetchone()[0], 1)
        self.assertEqual([x[0] for x in self.ctx.issues], ["legacy_review_target_unresolved"])
        self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_identical_import_is_idempotent(self):
        import_knowledge(self.ctx)
        tables = ["knowledge_items", "knowledge_revisions", "knowledge_evidence", "knowledge_relations", "review_events", "legacy_records"]
        before = {table: self.ctx.conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] for table in tables}
        import_knowledge(self.ctx)
        after = {table: self.ctx.conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] for table in tables}
        self.assertEqual(before, after)

    def test_changed_evidence_snapshot_preserves_the_old_knowledge_graph(self):
        import_knowledge(self.ctx)
        conn = self.ctx.conn
        old_claim = conn.execute("SELECT r.revision_id FROM knowledge_revisions r JOIN knowledge_items i USING(item_id) WHERE i.namespace='legacy_claim'").fetchone()[0]
        old_revisions = {row[0] for row in conn.execute("SELECT revision_id FROM knowledge_revisions")}
        old_items = {row[0] for row in conn.execute("SELECT item_id FROM knowledge_items")}
        old_relations = set(tuple(row) for row in conn.execute("SELECT * FROM knowledge_relations"))
        self.ctx.run_id = "changed-input-snapshot"
        conn.execute("INSERT INTO import_runs VALUES (?,?,?,?,?)",
                     (self.ctx.run_id, "2026-01-03", "1", "{}", "building"))
        conn.execute("INSERT INTO artifact_revisions VALUES (?,?,?,?,?)",
                     ("corrected-evidence-revision", "evidence-artifact", "1" * 64, 1, "blobs/corrected-evidence"))
        conn.execute("INSERT INTO evidence_units VALUES (?,?,?,?,?,?,?,?,?)",
                     ("corrected-unit", "corrected-evidence-revision", "lecture", "transcript_segment", 0, 0, 1000, "Corrected synthetic quote", "{}"))
        conn.executemany("INSERT INTO run_evidence_alias VALUES (?,?,?,?)", [
            (self.ctx.run_id, "source_unit", "U1", "corrected-unit"),
            (self.ctx.run_id, "frame", "lecture/frames/1.jpg", "frame-evidence"),
        ])
        import_knowledge(self.ctx)
        new_revisions = {row[0] for row in conn.execute("SELECT revision_id FROM run_knowledge WHERE run_id=?", (self.ctx.run_id,))}
        self.assertFalse(old_revisions & new_revisions)
        self.assertEqual(old_items, {row[0] for row in conn.execute("SELECT item_id FROM knowledge_items")})
        self.assertTrue(old_relations.issubset(set(tuple(row) for row in conn.execute("SELECT * FROM knowledge_relations"))))
        old_evidence = {row[0] for row in conn.execute("SELECT evidence_id FROM knowledge_evidence WHERE revision_id=?", (old_claim,))}
        self.assertIn("unit-evidence", old_evidence)
        self.assertNotIn("corrected-unit", old_evidence)
        new_claim = conn.execute("SELECT rk.revision_id FROM run_knowledge rk JOIN knowledge_items i USING(item_id) WHERE run_id=? AND i.namespace='legacy_claim'", (self.ctx.run_id,)).fetchone()[0]
        new_evidence = {row[0] for row in conn.execute("SELECT evidence_id FROM knowledge_evidence WHERE revision_id=?", (new_claim,))}
        self.assertIn("corrected-unit", new_evidence)
        self.assertNotIn("unit-evidence", new_evidence)
        for source, target in conn.execute("SELECT from_revision,to_revision FROM knowledge_relations"):
            if source in new_revisions:
                self.assertIn(target, new_revisions)

    def test_planning_whitelist_excludes_other_local_material(self):
        unrelated = self.root / "private-chat.json"
        unrelated.write_text("{}", encoding="utf-8")
        paths = input_paths(self.root)
        self.assertEqual(len(paths), len(JSON_INPUTS) + len(JSONL_INPUTS) + 1)
        self.assertNotIn(unrelated, paths)
        self.assertIn(self.claim_path, paths)
        import_knowledge(self.ctx)
        empty_artifact = self.ctx.conn.execute(
            "SELECT a.revision_id,a.byte_size FROM run_artifacts r JOIN artifact_revisions a USING(revision_id) "
            "WHERE r.run_id=? AND r.source_path=?",
            (self.ctx.run_id, JSONL_INPUTS["context_register"])).fetchone()
        self.assertIsNotNone(empty_artifact)
        self.assertEqual(empty_artifact["byte_size"], 0)
        self.assertEqual(self.ctx.conn.execute("SELECT COUNT(*) FROM legacy_records WHERE artifact_revision=?",
                                             (empty_artifact["revision_id"],)).fetchone()[0], 0)

    def test_optional_taxonomy_preserves_terms_unknown_fields_and_json_pointer_identity(self):
        path = self.root / OPTIONAL_JSON_INPUTS["taxonomy"]
        payload = {"topic/~one": {"title": "Synthetic concept", "description": "A topic definition",
                                 "aliases": ["alternate term", "alternate term"], "keywords": ["sample"],
                                 "future_extension": {"editorial_scope": "retained exactly"}}}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(canonical_json(payload), encoding="utf-8")
        self.assertIn(path, input_paths(self.root))
        counts = import_knowledge(self.ctx)
        self.assertEqual(counts["concepts"], 1)
        self.assertEqual(counts["declared_concept_terms"], 4)
        row = self.ctx.conn.execute("SELECT r.* FROM knowledge_revisions r JOIN knowledge_items i USING(item_id) WHERE i.kind='concept'").fetchone()
        self.assertEqual(row["statement"], "Synthetic concept")
        self.assertEqual(row["interpretation"], "A topic definition")
        self.assertEqual(json.loads(row["context_json"])["future_extension"], payload["topic/~one"]["future_extension"])
        terms = {tuple(term) for term in self.ctx.conn.execute("SELECT term,kind FROM concept_terms WHERE revision_id=?", (row["revision_id"],))}
        self.assertEqual(terms, {("topic/~one", "identifier"), ("Synthetic concept", "preferred"),
                                 ("alternate term", "alias"), ("sample", "keyword")})
        record = self.ctx.conn.execute("SELECT * FROM legacy_records WHERE record_id=?", (row["record_id"],)).fetchone()
        self.assertEqual(record["locator"], "#/topic~1~0one")
        self.assertEqual(json.loads(record["raw_json"]), payload["topic/~one"])
        self.assertEqual(row["review_state"], "imported_unverified")

    def test_present_taxonomy_with_changed_alias_schema_is_rejected(self):
        path = self.root / OPTIONAL_JSON_INPUTS["taxonomy"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(canonical_json({"topic": {"title": "A topic", "aliases": "not an array"}}), encoding="utf-8")
        with self.assertRaisesRegex(CoreError, "concept.aliases: expected an array"):
            import_knowledge(self.ctx)

    def test_run_reviews_preserves_unresolved_then_resolved_review_history(self):
        mined_path = self.root / JSONL_INPUTS["mined_candidates"]
        write_jsonl(mined_path, [])
        import_knowledge(self.ctx)
        conn = self.ctx.conn
        old_run = self.ctx.run_id
        old_event = conn.execute("SELECT e.* FROM review_events e JOIN run_reviews rr USING(review_id) WHERE rr.run_id=? AND e.scope='legacy_visual_evidence_review'", (old_run,)).fetchone()
        self.assertIsNone(old_event["target_revision"])
        write_jsonl(mined_path, self.values["mined_candidates"])
        self.ctx.run_id = "resolved-review-snapshot"
        conn.execute("INSERT INTO import_runs VALUES (?,?,?,?,?)",
                     (self.ctx.run_id, "2026-01-04", "1", "{}", "building"))
        conn.execute("INSERT INTO run_evidence_alias SELECT ?,namespace,legacy_id,evidence_id FROM run_evidence_alias WHERE run_id=?",
                     (self.ctx.run_id, old_run))
        import_knowledge(self.ctx)
        new_event = conn.execute("SELECT e.* FROM review_events e JOIN run_reviews rr USING(review_id) WHERE rr.run_id=? AND e.scope='legacy_visual_evidence_review'", (self.ctx.run_id,)).fetchone()
        self.assertIsNotNone(new_event["target_revision"])
        self.assertNotEqual(old_event["review_id"], new_event["review_id"])
        self.assertEqual(old_event["record_id"], new_event["record_id"])
        self.assertEqual(old_event["payload_json"], new_event["payload_json"])
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM run_reviews WHERE run_id=? AND review_id=?", (old_run, new_event["review_id"])).fetchone()[0], 0)

    def test_visual_claim_disagreement_is_retained_without_inventing_a_target(self):
        document = self.documents["visual_reviews"]
        document["reviews"][0]["claim_id"] = "different-claim"
        (self.root / JSON_INPUTS["visual_reviews"]).write_text(canonical_json(document), encoding="utf-8")
        import_knowledge(self.ctx)
        event = self.ctx.conn.execute("SELECT * FROM review_events WHERE scope='legacy_visual_evidence_review'").fetchone()
        self.assertIsNone(event["target_revision"])
        self.assertEqual(json.loads(event["payload_json"])["claim_id"], "different-claim")
        self.assertIn("visual_review_claim_disagreement", [x[0] for x in self.ctx.issues])

    def test_unresolved_evidence_does_not_discard_the_claim(self):
        self.ctx.conn.execute("DELETE FROM run_evidence_alias WHERE namespace='source_unit'")
        counts = import_knowledge(self.ctx)
        self.assertEqual(counts["claims"], 1)
        self.assertGreater(counts["unresolved_evidence_links"], 0)
        self.assertIn("unresolved_knowledge_evidence", [x[0] for x in self.ctx.issues])

    def test_duplicate_claim_identity_is_rejected(self):
        claim = json.loads(self.claim_path.read_text(encoding="utf-8"))
        write_jsonl(self.claim_path, [claim, claim])
        with self.assertRaisesRegex(CoreError, "duplicate legacy identifier"):
            import_knowledge(self.ctx)

    def test_missing_whitelisted_input_is_explicit(self):
        (self.root / JSONL_INPUTS["candidates"]).unlink()
        with self.assertRaisesRegex(CoreError, "required knowledge input is missing"):
            import_knowledge(self.ctx)

    def test_unknown_rule_role_is_not_silently_dropped(self):
        self.values["rules"][0]["new_unhandled_candidate_ids"] = ["C1"]
        write_jsonl(self.root / JSONL_INPUTS["rules"], self.values["rules"])
        with self.assertRaisesRegex(CoreError, "unrecognized rule candidate-role fields"):
            import_knowledge(self.ctx)

    def test_duplicate_json_key_is_rejected(self):
        self.claim_path.write_text('{"claim_id":"C1","claim_id":"C2"}\n', encoding="utf-8")
        with self.assertRaisesRegex(CoreError, "duplicate JSON key"):
            import_knowledge(self.ctx)


if __name__ == "__main__":
    unittest.main()
