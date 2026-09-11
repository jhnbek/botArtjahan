"""Synthetic, deterministic publication views without copying private metadata."""
import json
import re
import tempfile
import unittest
from pathlib import Path
from urllib.parse import unquote, urlsplit

from knowledge_core.catalog import build_catalog
from knowledge_core.db import CoreError, canonical_json, connect, file_hash
from knowledge_core.importer import build_core
from knowledge_core.sources import read_json
from test_knowledge_core_importer import source_fixture


class KnowledgeCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="knowledge-catalog-test-")
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "source"
        self.lecture = source_fixture(self.source)
        self.dataset = self.root / "dataset"
        build_core(self.source, self.dataset)

    def tearDown(self):
        self.temp.cleanup()

    def test_all_items_evidence_links_and_deterministic_bytes(self):
        original_hash = file_hash(self.dataset / "knowledge.sqlite")
        first, second = self.dataset / "catalog-one", self.dataset / "catalog-two"
        result = build_catalog(self.dataset, first)
        build_catalog(self.dataset, second)
        self.assertEqual(result["lectures"], 1)
        self.assertEqual(result["knowledge"], {"claim": 1, "candidate": 2, "rule": 1, "concept": 1})
        self.assertEqual(result["evidence"], {"transcript_segment": 1, "source_unit": 1, "frame": 1, "ocr": 1})
        self.assertGreaterEqual(result["knowledge_without_lecture"], 1)
        self.assertEqual(len(list((first / "items").glob("*/*.md"))), 5)
        contents = lambda root: {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}
        self.assertEqual(contents(first), contents(second))
        self.assertEqual(file_hash(self.dataset / "knowledge.sqlite"), original_hash)
        self.assertEqual(len((first / "knowledge.jsonl").read_text(encoding="utf-8").splitlines()), 5)
        all_markdown = "\n".join(path.read_text(encoding="utf-8") for path in first.rglob("*.md"))
        self.assertIn("00:00:00.500", all_markdown)
        self.assertIn("Raw transcription", all_markdown)
        self.assertIn("Visible chart annotation", all_markdown)
        self.assertIn("Inherited condition", all_markdown)
        self.assertIn("imported_unverified", all_markdown)
        self.assertIn("Сохранённые связи", all_markdown)
        self.assertIn("[Открыть кадр]", all_markdown)
        for page in first.rglob("*.md"):
            self.assertLessEqual(page.stat().st_size, 131072)
            content = page.read_text(encoding="utf-8")
            self.assertTrue(content.endswith("\n"))
            self.assertFalse(content.endswith("\n\n"))
            self.assertTrue(all(line == line.rstrip(" \t\r") for line in content.split("\n")))
            for href in re.findall(r"(?<!\\)\]\(([^)]+)\)", content):
                parsed = urlsplit(href)
                self.assertFalse(parsed.scheme)
                target = (page.parent / unquote(parsed.path)).resolve()
                self.assertTrue(target.is_file(), (page.name, href))
                if parsed.fragment:
                    self.assertIn(f'id="{parsed.fragment}"', target.read_text(encoding="utf-8"))

    def test_metadata_and_reviewer_values_are_not_exported_and_markup_is_literal(self):
        sentinel = "synthetic-internal-metadata"
        markup = '<script>alert("sample")</script> ![remote](https://invalid.example/image)'
        conn = connect(self.dataset / "knowledge.sqlite")
        try:
            conn.execute("UPDATE evidence_units SET metadata_json=?", (canonical_json({"local": sentinel}),))
            conn.execute("UPDATE review_events SET actor=?,payload_json=?", (sentinel, canonical_json({"local": sentinel})))
            conn.execute("UPDATE lectures SET identity_key=?", (sentinel,))
            conn.execute("UPDATE run_lectures SET directory=?", (sentinel,))
            conn.execute("UPDATE knowledge_revisions SET context_json=?", (canonical_json({"local": sentinel}),))
            conn.execute("UPDATE knowledge_revisions SET statement=? WHERE item_id IN (SELECT item_id FROM knowledge_items WHERE kind='claim')", (markup,))
            conn.commit()
        finally:
            conn.close()
        destination = self.dataset / "catalog"
        build_catalog(self.dataset, destination)
        contents = "\n".join(path.read_text(encoding="utf-8") for path in destination.rglob("*") if path.is_file())
        self.assertNotIn(sentinel, contents)
        for path in destination.rglob("*.md"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("<script>", text)
            self.assertNotIn("![remote](", text)
        values = [json.loads(line) for line in (destination / "knowledge.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(next(item for item in values if item["kind"] == "claim")["statement"], markup)

    def test_oversized_fields_are_complete_json_records_and_pages_stay_bounded(self):
        # Unicode exercises the UTF-8 byte budget, rather than character count.
        oversized = "\u0416" * 6000
        conn = connect(self.dataset / "knowledge.sqlite")
        try:
            conn.execute("UPDATE evidence_units SET text=? WHERE kind='transcript_segment'", (oversized,))
            conn.execute("UPDATE knowledge_revisions SET quote=? WHERE item_id IN (SELECT item_id FROM knowledge_items WHERE kind='claim')", (oversized,))
            conn.commit()
        finally:
            conn.close()
        destination = self.dataset / "catalog"
        build_catalog(self.dataset, destination, page_bytes=4096)
        for page in destination.rglob("*.md"):
            self.assertLessEqual(page.stat().st_size, 4096)
        records = [json.loads(path.read_text(encoding="utf-8")) for path in destination.rglob("*.json")]
        self.assertTrue(any(record.get("quote") == oversized for record in records))
        self.assertTrue(any(record.get("text") == oversized for record in records))
        evidence_file = next((destination / "lectures").glob("*/evidence.jsonl"))
        records = [json.loads(line) for line in evidence_file.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(next(item for item in records if item["kind"] == "transcript_segment")["text"], oversized)

    def test_pagination_retains_each_complete_segment_once_and_navigation_resolves(self):
        conn = connect(self.dataset / "knowledge.sqlite")
        try:
            source = conn.execute("SELECT artifact_revision,lecture_id FROM evidence_units WHERE kind='transcript_segment'").fetchone()
            run_id = conn.execute("SELECT value FROM metadata WHERE key='current_run'").fetchone()[0]
            for index in range(120):
                identifier = f"synthetic-segment-{index}"
                text = f"Unique passage {index:04}: " + "ordinary retained text " * 20
                conn.execute("INSERT INTO evidence_units VALUES (?,?,?,?,?,?,?,?,?)",
                             (identifier, source[0], source[1], "transcript_segment", index + 1,
                              (index + 1) * 1000, (index + 2) * 1000, text, "{}"))
                conn.execute("INSERT INTO run_evidence_alias VALUES (?,?,?,?)",
                             (run_id, "synthetic_segment", identifier, identifier))
            conn.commit()
        finally:
            conn.close()
        destination = self.dataset / "catalog"
        result = build_catalog(self.dataset, destination, page_bytes=4096)
        self.assertEqual(result["evidence"]["transcript_segment"], 121)
        pages = sorted((destination / "lectures").glob("*/transcript_segment-*.md"))
        self.assertGreater(len(pages), 1)
        text = "\n".join(page.read_text(encoding="utf-8") for page in pages)
        for index in range(120):
            self.assertEqual(text.count(f"Unique passage {index:04}:"), 1)
        for page in pages:
            self.assertLessEqual(page.stat().st_size, 4096)
            for href in re.findall(r"(?<!\\)\]\(([^)]+)\)", page.read_text(encoding="utf-8")):
                self.assertTrue((page.parent / unquote(urlsplit(href).path)).is_file())

    def test_current_snapshot_only_and_portable_escaped_blob_prefix(self):
        transcript = read_json(self.lecture / "transcript.json")
        transcript["segments"][0]["text"] = "Current synthetic transcript"
        (self.lecture / "transcript.json").write_text(canonical_json(transcript), encoding="utf-8")
        current = self.root / "current"
        build_core(self.source, current, previous=self.dataset)
        destination = current / "catalog"
        build_catalog(current, destination, blob_prefix="../public blobs")
        all_text = "\n".join(path.read_text(encoding="utf-8") for path in destination.rglob("*") if path.is_file())
        self.assertIn("Current synthetic transcript", all_text)
        self.assertNotIn("Raw transcription", all_text)
        self.assertIn("public%20blobs/", all_text)

    def test_refuses_existing_destination_and_unsafe_blob_url(self):
        destination = self.dataset / "catalog"
        destination.mkdir()
        marker = destination / "marker"
        marker.write_text("keep", encoding="utf-8")
        with self.assertRaises(CoreError):
            build_catalog(self.dataset, destination)
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
        for prefix in ("https://invalid.example", "/absolute", "C:relative", "../blobs#fragment", "../blobs\\nested"):
            with self.subTest(prefix=prefix), self.assertRaises(CoreError):
                build_catalog(self.dataset, self.dataset / "not-created", blob_prefix=prefix)
        self.assertFalse((self.dataset / "not-created").exists())


if __name__ == "__main__":
    unittest.main()
