"""Robot connection: real synthetic publications, refresh and failure isolation."""
import contextlib
import gzip
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from knowledge_bot.knowledge_base import KnowledgeBase, attach_knowledge_context, main
from knowledge_core.db import CoreError, canonical_json, file_hash
from knowledge_core.importer import build_core
from knowledge_core.transfer import export_core
from test_knowledge_core_importer import source_fixture


class KnowledgeConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="robot-knowledge-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.lecture = source_fixture(self.source)
        self.dataset = self.root / "original"
        build_core(self.source, self.dataset)
        self.published = self.root / "published"
        self.publish(self.dataset, self.published)
        self.kb = KnowledgeBase(self.published, self.root / "cache")

    @staticmethod
    def publish(dataset, destination):
        export_core(dataset, destination)
        db = destination / "knowledge.sqlite"
        packed = destination / "knowledge.sqlite.gz"
        with db.open("rb") as reader, gzip.open(packed, "wb") as writer:
            shutil.copyfileobj(reader, writer)
        db.unlink()
        metadata = {"format": "knowledge-core-published", "format_version": 1,
                    "schema_version": "1", "database": {"path": packed.name,
                    "sha256": file_hash(packed), "byte_size": packed.stat().st_size}}
        (destination / "packed-manifest.json").write_text(canonical_json(metadata), encoding="utf-8")

    def test_connect_search_and_trace_preserve_source_and_review_status(self):
        before = {str(p): file_hash(p) for p in self.published.rglob("*") if p.is_file()}
        status = self.kb.connect()
        self.assertTrue(status["validation"]["valid"])
        hits = self.kb.search("Alternativeword")
        self.assertTrue(hits)
        self.assertEqual(hits[0]["review_state"], "imported_unverified")
        evidence = self.kb.show(self.kb.search("annotation")[0]["target_id"])
        self.assertTrue(evidence["evidence"])
        self.assertTrue(all(Path(e["file"]).is_file() for e in evidence["evidence"]))
        self.assertEqual(before, {str(p): file_hash(p) for p in self.published.rglob("*") if p.is_file()})
        self.assertFalse((self.published / "knowledge.sqlite").exists())

    def test_failed_refresh_preserves_active_snapshot(self):
        old = self.kb.connect()
        packed = self.published / "knowledge.sqlite.gz"
        packed.write_bytes(b"corrupted")
        with self.assertRaises(CoreError):
            self.kb.connect()
        self.assertEqual(self.kb.status()["snapshot"], old["snapshot"])
        self.assertTrue(self.kb.search("annotation"))

    def test_updated_publication_is_visible_only_after_connect(self):
        old = self.kb.connect()
        transcript = self.lecture / "transcript.json"
        content = json.loads(transcript.read_text(encoding="utf-8"))
        content["segments"][0]["text"] = "Newtrainingword"
        transcript.write_text(canonical_json(content), encoding="utf-8")
        # Source-unit text is searchable in preference to its transcript.
        units = self.source / "_knowledge_base/structured/lecture_pass/lectures/lecture_001/source_multimodal_units.jsonl"
        unit = json.loads(units.read_text(encoding="utf-8"))
        unit["text"] = "Newtrainingword"
        units.write_text(canonical_json(unit) + "\n", encoding="utf-8")
        updated = self.root / "updated"
        build_core(self.source, updated)
        next_bundle = self.root / "next-publication"
        self.publish(updated, next_bundle)
        shutil.copytree(next_bundle, self.published, dirs_exist_ok=True)
        self.assertFalse(self.kb.search("Newtrainingword"))
        new = self.kb.connect()
        self.assertNotEqual(old["run_id"], new["run_id"])
        self.assertTrue(self.kb.search("Newtrainingword"))
        self.assertTrue(Path(old["dataset"]).is_dir())

    def test_context_does_not_enable_execution_and_reports_missing_connection(self):
        packet = {"execution_allowed": False}
        attach_knowledge_context(packet, "annotation", knowledge=self.kb)
        self.assertEqual(packet["knowledge_references"]["status"], "unavailable")
        self.kb.connect()
        attach_knowledge_context(packet, "annotation", knowledge=self.kb)
        self.assertEqual(packet["knowledge_references"]["status"], "ok")
        self.assertTrue(packet["knowledge_references"]["results"])
        self.assertFalse(packet["execution_allowed"])
        self.assertFalse(packet["knowledge_references"]["automatic_execution_allowed"])

    def test_cli_and_configuration_boundaries(self):
        with self.assertRaises(CoreError):
            KnowledgeBase(self.published, self.published / "cache")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--source", str(self.published), "--cache", str(self.root / "cache"), "status"]), 1)
        self.kb.connect()
        with contextlib.redirect_stdout(io.StringIO()) as output:
            code = main(["--source", str(self.published), "--cache", str(self.root / "cache"), "search", "annotation"])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(output.getvalue()))
        other = KnowledgeBase(self.root / "another-source", self.root / "cache")
        with self.assertRaises(CoreError):
            other.search("annotation")
