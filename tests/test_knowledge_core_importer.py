"""End-to-end synthetic checks for immutable, portable knowledge snapshots."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from knowledge_core.db import CoreError, canonical_json, confined, file_hash
from knowledge_core.importer import build_core, plan_inputs
from knowledge_core.query import search, trace
from knowledge_core.sources import read_json
from test_knowledge_core_legacy import fixture, write_jsonl


def source_fixture(root):
    fixture(root)
    lecture = root / "_new_lecture_corpus/lecture"
    lecture.mkdir(parents=True)
    (lecture / "frames").mkdir()
    (lecture / "frames/1.jpg").write_bytes(b"synthetic-image-bytes")
    transcript = {"lecture_uid": "synthetic-stable-source", "source_video": "missing/original.mp4",
                  "segments": [{"id": 7, "start": 0, "end": 1, "text": "Raw transcription"}]}
    (lecture / "transcript.json").write_text(canonical_json(transcript), encoding="utf-8")
    visual = {"frames": [{"path": "frames/1.jpg", "time": 0.5, "linked_segment_index": 0,
                          "ocr_text": "Visible chart annotation", "phash": "original"}]}
    (lecture / "visual_index.json").write_text(canonical_json(visual), encoding="utf-8")
    unit = {"unit_id": "U1", "lecture_title": "lecture", "segment_index": 0,
            "start_sec": 0, "end_sec": 1, "text": "Synthetic quote",
            "frames": [{"path": "lecture/frames/1.jpg"}]}
    write_jsonl(root / "_knowledge_base/structured/lecture_pass/lectures/lecture_001/source_multimodal_units.jsonl", [unit])
    taxonomy = root / "knowledge_bot/knowledge_taxonomy.json"
    taxonomy.parent.mkdir()
    taxonomy.write_text(canonical_json({"synthetic": {"title": "Synthetic concept",
        "description": "A fixture definition", "aliases": ["Alternativeword"], "keywords": ["Vocabulary"]}}), encoding="utf-8")
    return lecture


class KnowledgeImporterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="knowledge-core-integration-test-")
        self.parent = Path(self.temp.name)
        self.source = self.parent / "source"
        self.lecture = source_fixture(self.source)
        self.output = self.parent / "dataset"

    def tearDown(self):
        self.temp.cleanup()

    def test_complete_round_trip_query_source_preservation_and_noop(self):
        original = plan_inputs(self.source)
        result = build_core(self.source, self.output)
        self.assertTrue(result["validation"]["valid"])
        self.assertEqual(plan_inputs(self.source), original)
        self.assertEqual(result["source_counts"], dict(lectures=1, segments=1, frames=1, ocr=1,
            source_units=1, source_unit_frame_links=1, chunks=0))
        self.assertEqual(search(self.output, "Alternativeword")[0]["kind"], "concept")
        self.assertEqual(search(self.output, "annotation")[0]["kind"], "ocr")
        ocr_hit = search(self.output, "annotation")[0]
        self.assertEqual({e["kind"] for e in trace(self.output, ocr_hit["target_id"])["evidence"]},
                         {"ocr", "frame", "source_unit", "transcript_segment"})
        with self.assertRaisesRegex(CoreError, "multiple namespaces"):
            trace(self.output, "C1")
        rule = trace(self.output, "R1")
        self.assertEqual({node["kind"] for node in rule["knowledge"]}, {"rule", "candidate", "claim"})
        self.assertEqual({e["kind"] for e in rule["evidence"]}, {"source_unit", "transcript_segment", "frame", "ocr"})
        texts = {e["kind"]: e["text"] for e in rule["evidence"]}
        self.assertEqual(texts["source_unit"], "Synthetic quote")
        self.assertEqual(texts["transcript_segment"], "Raw transcription")
        self.assertTrue(all(Path(e["file"]).is_file() for e in rule["evidence"]))
        before = file_hash(self.output / "knowledge.sqlite")
        self.assertTrue(build_core(self.source, self.output)["unchanged"])
        self.assertEqual(file_hash(self.output / "knowledge.sqlite"), before)

    def test_changed_transcript_and_ocr_keep_each_historical_graph_separate(self):
        first = build_core(self.source, self.output)
        old = trace(self.output, "C1", namespace="legacy_claim")
        transcript = read_json(self.lecture / "transcript.json")
        transcript["segments"][0]["text"] = "Corrected raw transcription"
        (self.lecture / "transcript.json").write_text(canonical_json(transcript), encoding="utf-8")
        visual = read_json(self.lecture / "visual_index.json")
        visual["frames"][0].update(ocr_text="Corrected chart annotation", phash="updated")
        (self.lecture / "visual_index.json").write_text(canonical_json(visual), encoding="utf-8")
        second_path = self.parent / "second"
        second = build_core(self.source, second_path, previous=self.output)
        self.assertNotEqual(first["run_id"], second["run_id"])
        current = trace(second_path, "C1", namespace="legacy_claim")
        previous = trace(second_path, "C1", namespace="legacy_claim", run_id=first["run_id"])
        self.assertEqual(previous["knowledge"], old["knowledge"])
        self.assertEqual({e["text"] for e in previous["evidence"]}, {e["text"] for e in old["evidence"]})
        self.assertEqual(len(current["evidence"]), len(old["evidence"]))
        self.assertEqual(current["knowledge"][0]["item_id"], old["knowledge"][0]["item_id"])
        self.assertNotEqual(current["target_id"], old["target_id"])
        self.assertIn("Corrected raw transcription", {e["text"] for e in current["evidence"]})
        self.assertIn("Corrected chart annotation", {e["text"] for e in current["evidence"]})
        self.assertNotIn("Visible chart annotation", {e["text"] for e in current["evidence"]})
        self.assertEqual(second["validation"]["counts"]["review_events"], first["validation"]["counts"]["review_events"])

    def test_workspace_move_keeps_identical_snapshot_and_identifiers(self):
        first = build_core(self.source, self.output)
        relocated = self.parent / "relocated"
        shutil.copytree(self.source, relocated)
        second = build_core(relocated, self.parent / "another")
        self.assertEqual(first["run_id"], second["run_id"])
        self.assertEqual(trace(self.output, "R1")["knowledge"], trace(self.parent / "another", "R1")["knowledge"])

    def test_lecture_rename_and_course_reorder_keep_identity_and_current_title(self):
        course = self.source / "_knowledge_base/course_order.json"
        course.write_text(canonical_json({"lectures": [{"lecture_id": "L1", "title": "lecture", "course_position": 1}]}), encoding="utf-8")
        build_core(self.source, self.output)
        old = trace(self.output, "C1", namespace="legacy_claim")
        renamed = self.lecture.with_name("renamed")
        # Moving this known synthetic directory stays inside the verified fixture root.
        self.assertTrue(renamed.resolve().is_relative_to(self.source.resolve()))
        self.lecture.rename(renamed)
        for path in self.source.rglob("*.jsonl"):
            path.write_text(path.read_text(encoding="utf-8").replace('"lecture"', '"renamed"').replace("lecture/frames/", "renamed/frames/"), encoding="utf-8")
        course.write_text(canonical_json({"lectures": [{"lecture_id": "L1", "title": "renamed", "course_position": 8}]}), encoding="utf-8")
        second = self.parent / "renamed-dataset"
        build_core(self.source, second, previous=self.output)
        current = trace(second, "C1", namespace="legacy_claim")
        self.assertEqual(current["knowledge"][0]["item_id"], old["knowledge"][0]["item_id"])
        self.assertEqual({e["lecture_id"] for e in current["evidence"]}, {e["lecture_id"] for e in old["evidence"]})
        self.assertEqual({e["title"] for e in current["evidence"]}, {"renamed"})
        self.assertEqual(search(second, "annotation")[0]["title"], "renamed")

    def test_duplicate_source_identity_is_rejected(self):
        other = self.lecture.with_name("another-lecture")
        shutil.copytree(self.lecture, other)
        with self.assertRaisesRegex(CoreError, "same source identity"):
            build_core(self.source, self.output)
        self.assertFalse(self.output.exists())

    def test_output_never_overwrites_source_or_other_git_checkout(self):
        for target in (self.source, self.source / "nested"):
            with self.assertRaises(CoreError):
                build_core(self.source, target)
        git = self.parent / "other-git"
        git.mkdir()
        (git / ".git").write_text("gitdir: placeholder", encoding="utf-8")
        with self.assertRaises(CoreError):
            build_core(self.source, git / "private")
        self.assertFalse((git / "private").exists())

    def test_video_reference_is_rejected_before_open_or_stat(self):
        visual = read_json(self.lecture / "visual_index.json")
        visual["frames"][0]["path"] = "original.mp4"
        (self.lecture / "visual_index.json").write_text(canonical_json(visual), encoding="utf-8")
        original_open, original_stat = Path.open, Path.stat
        def guarded_open(path, *args, **kwargs):
            self.assertNotEqual(path.suffix, ".mp4")
            return original_open(path, *args, **kwargs)
        def guarded_stat(path, *args, **kwargs):
            self.assertNotEqual(path.suffix, ".mp4")
            return original_stat(path, *args, **kwargs)
        with patch.object(Path, "open", guarded_open), patch.object(Path, "stat", guarded_stat):
            with self.assertRaisesRegex(CoreError, "retained image"):
                build_core(self.source, self.output)
        self.assertFalse(self.output.exists())

    def test_failed_import_never_replaces_prior_snapshot(self):
        build_core(self.source, self.output)
        old_hash = file_hash(self.output / "knowledge.sqlite")
        transcript = read_json(self.lecture / "transcript.json")
        transcript["segments"][0]["end"] = -1
        (self.lecture / "transcript.json").write_text(canonical_json(transcript), encoding="utf-8")
        with self.assertRaises(CoreError):
            build_core(self.source, self.parent / "failed", previous=self.output)
        self.assertFalse((self.parent / "failed").exists())
        self.assertEqual(file_hash(self.output / "knowledge.sqlite"), old_hash)

    def test_duplicate_json_keys_fail_before_replacement(self):
        (self.lecture / "transcript.json").write_text('{"segments":[],"segments":[{}]}', encoding="utf-8")
        with self.assertRaisesRegex(CoreError, "duplicate JSON"):
            build_core(self.source, self.output)
        self.assertFalse(self.output.exists())

    def test_noncanonical_windows_paths_cannot_escape_lexical_confinement(self):
        for relative in ('.. /outside', 'folder./file', 'folder /file', 'NUL', 'a:stream', 'a/../file'):
            with self.subTest(relative=relative), self.assertRaises(CoreError):
                confined(self.source, relative)

    def test_search_handles_untrusted_syntax_as_text(self):
        build_core(self.source, self.output)
        self.assertEqual(search(self.output, 'quote" OR * NOT :x'), [])
        with self.assertRaises(CoreError):
            search(self.output, '*"')
        with self.assertRaises(CoreError):
            search(self.output, "quote", limit=0)


if __name__ == "__main__":
    unittest.main()
