"""Synthetic integrity and transfer regressions; no private corpus is loaded."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from knowledge_core.db import DB_NAME, CoreError, connect, file_hash, initialize, safe_relative
from knowledge_core.transfer import MANIFEST_NAME, export_core, restore_core
from knowledge_core.validation import validate_core
from knowledge_core import transfer


class CoreIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="knowledge-core-test-")
        self.root = Path(self.temporary.name).resolve()
        self.assertTrue(self.root.is_relative_to(Path(tempfile.gettempdir()).resolve()))
        self.addCleanup(self.temporary.cleanup)
        self.dataset = self.root / "dataset"
        self.dataset.mkdir()
        self.database = self.dataset / DB_NAME
        content = b'{"text":"Exact retained text","video":"missing.mp4"}\n'
        self.digest = hashlib.sha256(content).hexdigest()
        self.blob_relative = f"blobs/{self.digest[:2]}/{self.digest}"
        self.blob = self.dataset / self.blob_relative
        self.blob.parent.mkdir(parents=True)
        self.blob.write_bytes(content)
        self.inventory = {
            "inputs": {"lecture/transcript.json": {"sha256": self.digest, "byte_size": len(content)}},
            "source_counts": {"lectures": 1, "segments": 1, "frames": 0, "ocr": 0,
                              "source_units": 0, "source_unit_frame_links": 0, "chunks": 0},
            "knowledge_counts": {"claims": 1, "review_events": 1},
        }
        conn = connect(self.database)
        try:
            initialize(conn)
            conn.execute("INSERT INTO metadata VALUES ('current_run','run-1')")
            conn.execute("INSERT INTO import_runs VALUES ('run-1','2026-01-01','1',?, 'complete')",
                         (json.dumps(self.inventory),))
            conn.execute("INSERT INTO lectures VALUES ('lecture-1','source-identity','Synthetic lecture')")
            conn.execute("INSERT INTO run_lectures VALUES ('run-1','lecture-1','lecture','Synthetic lecture',1)")
            conn.execute("INSERT INTO artifacts VALUES ('artifact-1','transcript','lecture-1','transcript-identity')")
            conn.execute("INSERT INTO artifact_revisions VALUES ('artifact-rev-1','artifact-1',?,?,?)",
                         (self.digest, len(content), self.blob_relative))
            conn.execute("INSERT INTO run_artifacts VALUES ('run-1','lecture/transcript.json','artifact-rev-1')")
            conn.execute("INSERT INTO legacy_records VALUES ('record-1','claim','old-1','artifact-rev-1','/0',?)",
                         ('{"statement":"Synthetic claim","reviewer":null}',))
            conn.execute("INSERT INTO run_records VALUES ('run-1','record-1')")
            conn.execute("INSERT INTO legacy_records VALUES ('segment-record','transcript_segment','0',"
                         "'artifact-rev-1','/segments/0','{\"text\":\"Exact retained text\"}')")
            conn.execute("INSERT INTO run_records VALUES ('run-1','segment-record')")
            conn.execute("INSERT INTO evidence_units VALUES ('evidence-1','artifact-rev-1','lecture-1',"
                         "'transcript_segment',0,0,1000,'Exact retained text','{}')")
            conn.execute("INSERT INTO run_evidence_alias VALUES ('run-1','lecture-1:segment','0','evidence-1')")
            conn.execute("INSERT INTO knowledge_items VALUES ('item-1','claims','old-1','claim')")
            conn.execute("INSERT INTO knowledge_revisions VALUES ('knowledge-rev-1','item-1','record-1',"
                         "'Synthetic claim','Exact retained text','','[]','[]','{}','candidate','imported_unverified')")
            conn.execute("INSERT INTO run_knowledge VALUES ('run-1','item-1','knowledge-rev-1')")
            conn.execute("INSERT INTO knowledge_evidence VALUES ('knowledge-rev-1','evidence-1','support')")
            conn.execute("INSERT INTO review_events VALUES ('review-1','record-1','knowledge-rev-1',NULL,"
                         "'legacy',NULL,NULL,'unknown','imported_legacy','{}')")
            conn.execute("INSERT INTO run_reviews VALUES ('run-1','review-1')")
            conn.execute("INSERT INTO concept_terms VALUES ('knowledge-rev-1','term','term')")
            conn.execute("INSERT INTO search_index VALUES ('knowledge-rev-1','claim','Synthetic claim',"
                         "'Synthetic claim Exact retained text  [] [] term')")
            conn.execute("INSERT INTO search_index VALUES ('evidence-1','transcript_segment','Synthetic lecture',"
                         "'Exact retained text')")
            conn.commit()
        finally:
            conn.close()

    def test_valid_store_reports_real_zero_counts_and_unknown_review_identity(self) -> None:
        before = file_hash(self.database)
        result = validate_core(self.dataset)
        self.assertTrue(result["valid"], result)
        self.assertEqual(result["counts"]["rules"], 0)
        self.assertEqual(result["counts"]["unknown_review_actors"], 1)
        self.assertEqual(result["verified_blob_count"], 1)
        self.assertEqual(file_hash(self.database), before)

    def test_foreign_keys_reject_new_orphans_and_validator_detects_external_corruption(self) -> None:
        conn = connect(self.database)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO knowledge_evidence VALUES ('knowledge-rev-1','missing','support')")
            conn.rollback()
        finally:
            conn.close()
        conn = sqlite3.connect(self.database)
        try:
            conn.execute("INSERT INTO knowledge_evidence VALUES ('knowledge-rev-1','missing','support')")
            conn.commit()
        finally:
            conn.close()
        result = validate_core(self.dataset)
        self.assertFalse(result["valid"])
        self.assertTrue(any(error.startswith("foreign_key:") for error in result["errors"]))

    def test_empty_inventory_cannot_bypass_validation(self) -> None:
        conn = connect(self.database)
        try:
            conn.execute("UPDATE import_runs SET inventory_json='{}'")
            conn.commit()
        finally:
            conn.close()
        result = validate_core(self.dataset)
        self.assertFalse(result["valid"])
        self.assertIn("current_inventory_missing_or_empty", result["errors"])

    def test_incomplete_run_is_not_accepted(self) -> None:
        conn = connect(self.database)
        try:
            conn.execute("UPDATE import_runs SET status='building'")
            conn.commit()
        finally:
            conn.close()
        self.assertIn("current_run_not_complete", validate_core(self.dataset)["errors"])

    def test_schema_contract_cannot_be_replaced_with_unconstrained_tables(self) -> None:
        conn = connect(self.database)
        try:
            conn.execute("CREATE TABLE unexpected_table(value TEXT)")
            conn.commit()
        finally:
            conn.close()
        self.assertIn("schema_contract_mismatch", validate_core(self.dataset)["errors"])

    def test_changed_blob_is_rejected(self) -> None:
        self.blob.write_bytes(b"corrupted")
        result = validate_core(self.dataset)
        self.assertFalse(result["valid"])
        self.assertTrue(any(error.startswith("blob_content_mismatch:") for error in result["errors"]))

    def test_noncanonical_blob_path_is_rejected_before_access(self) -> None:
        conn = connect(self.database)
        try:
            conn.execute("UPDATE artifact_revisions SET blob_path='../outside.mp4'")
            conn.commit()
        finally:
            conn.close()
        result = validate_core(self.dataset)
        self.assertFalse(result["valid"])
        self.assertEqual(result["verified_blob_count"], 0)
        self.assertTrue(any(error.startswith("noncanonical_blob_path:") for error in result["errors"]))

    def test_linked_blob_directory_cannot_escape_the_dataset(self) -> None:
        source = self.blob.parent
        external = self.root / "external-blobs"
        source.rename(external)
        try:
            if os.name == "nt":
                import _winapi
                _winapi.CreateJunction(str(external), str(source))
                self.addCleanup(source.rmdir)
            else:
                source.symlink_to(external, target_is_directory=True)
                self.addCleanup(source.unlink)
        except (OSError, AttributeError) as exc:
            self.skipTest(f"local directory links unavailable: {exc}")
        result = validate_core(self.dataset)
        self.assertFalse(result["valid"])
        self.assertTrue(any(error.startswith("blob_path_invalid:") for error in result["errors"]))
        with self.assertRaises(CoreError):
            export_core(self.dataset, self.root / "bundle")
        self.assertFalse((self.root / "bundle").exists())

    def test_cross_platform_unsafe_paths_are_rejected(self) -> None:
        for path in ("/root", "\\root", "C:relative", "C:/absolute", "//server/share", "../escape",
                     "a/../b", "a//b", "./a", "a\\b", "file:stream", ""):
            with self.subTest(path=path), self.assertRaises(CoreError):
                safe_relative(path)

    @staticmethod
    def _logical_rows(database: Path) -> dict[str, list[tuple]]:
        conn = connect(database, readonly=True)
        try:
            names = [row[0] for row in conn.execute("SELECT name FROM sqlite_schema WHERE type='table'")]
            return {name: sorted((tuple(row) for row in conn.execute(f'SELECT * FROM "{name}"')), key=repr)
                    for name in names}
        finally:
            conn.close()

    def test_roundtrip_preserves_all_rows_ids_revisions_and_nullable_provenance(self) -> None:
        bundle, restored = self.root / "bundle", self.root / "restored"
        original_rows = self._logical_rows(self.database)
        export_core(self.dataset, bundle)
        restore_core(bundle, restored)
        self.assertEqual(self._logical_rows(restored / DB_NAME), original_rows)
        self.assertEqual((restored / self.blob_relative).read_bytes(), self.blob.read_bytes())
        self.assertTrue(validate_core(restored)["valid"])
        conn = connect(restored / DB_NAME, readonly=True)
        try:
            self.assertEqual(tuple(conn.execute("SELECT actor,event_at FROM review_events").fetchone()), (None, None))
        finally:
            conn.close()

    def test_transfer_does_not_touch_videos_or_copy_unregistered_files(self) -> None:
        video = self.dataset / "unused.mp4"
        video.write_bytes(b"not a real video")
        original_open, original_stat = Path.open, Path.stat

        def guarded_open(path, *args, **kwargs):
            if path.suffix.lower() == ".mp4":
                self.fail("video file was opened")
            return original_open(path, *args, **kwargs)

        def guarded_stat(path, *args, **kwargs):
            if path.suffix.lower() == ".mp4":
                self.fail("video file was probed")
            return original_stat(path, *args, **kwargs)

        with patch.object(Path, "open", guarded_open), patch.object(Path, "stat", guarded_stat):
            result = export_core(self.dataset, self.root / "bundle")
            restore_core(self.root / "bundle", self.root / "restored")
        self.assertEqual(set(result["manifest"]["files"]), {DB_NAME, self.blob_relative})
        self.assertFalse((self.root / "bundle" / "unused.mp4").exists())

    def test_corrupted_bundle_keeps_existing_target_untouched(self) -> None:
        bundle, restored = self.root / "bundle", self.root / "existing"
        export_core(self.dataset, bundle)
        restored.mkdir()
        sentinel = restored / "keep.txt"
        sentinel.write_text("preserve", encoding="utf-8")
        (bundle / self.blob_relative).write_bytes(b"changed")
        with self.assertRaises(CoreError):
            restore_core(bundle, restored)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")
        with self.assertRaises(CoreError):
            restore_core(bundle, self.root / "new")
        self.assertFalse((self.root / "new").exists())

    def test_empty_and_traversal_manifest_fail_before_restore(self) -> None:
        bundle = self.root / "bundle"
        export_core(self.dataset, bundle)
        manifest = json.loads((bundle / MANIFEST_NAME).read_text(encoding="utf-8"))
        for value in ({}, {**manifest, "files": {**manifest["files"], "../outside": {"sha256": "0" * 64, "byte_size": 0}}}):
            with self.subTest(value=value.keys()):
                (bundle / MANIFEST_NAME).write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaises(CoreError):
                    restore_core(bundle, self.root / "restore")
                self.assertFalse((self.root / "restore").exists())

    def test_missing_manifest_blob_is_not_silently_omitted(self) -> None:
        bundle = self.root / "bundle"
        export_core(self.dataset, bundle)
        manifest = json.loads((bundle / MANIFEST_NAME).read_text(encoding="utf-8"))
        del manifest["files"][self.blob_relative]
        (bundle / MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(CoreError, "inventory"):
            restore_core(bundle, self.root / "restore")
        self.assertFalse((self.root / "restore").exists())

    def test_export_cannot_replace_source_or_existing_target(self) -> None:
        before = file_hash(self.database)
        for path in (self.dataset, self.dataset / "nested", self.root):
            with self.subTest(path=path), self.assertRaises(CoreError):
                export_core(self.dataset, path)
        self.assertEqual(file_hash(self.database), before)

    def test_input_inventory_checks_exact_membership_hashes_and_sizes(self) -> None:
        variants = []
        for field, value in (("sha256", "0" * 64), ("byte_size", 0), ("byte_size", False)):
            updated = json.loads(json.dumps(self.inventory))
            updated["inputs"]["lecture/transcript.json"][field] = value
            variants.append(updated)
        added = json.loads(json.dumps(self.inventory))
        added["inputs"]["unregistered.json"] = {"sha256": "0" * 64, "byte_size": 0}
        variants.append(added)
        for inventory in variants:
            with self.subTest(inputs=inventory["inputs"]):
                conn = connect(self.database)
                try:
                    conn.execute("UPDATE import_runs SET inventory_json=?", (json.dumps(inventory),))
                    conn.commit()
                finally:
                    conn.close()
                result = validate_core(self.dataset)
                self.assertFalse(result["valid"])
                self.assertTrue(any(error.startswith("inventory_input") for error in result["errors"]))

    def test_source_counts_reject_wrong_numbers_and_boolean_zero(self) -> None:
        for field, value in (("segments", 2), ("frames", False), ("source_unit_frame_links", 1)):
            with self.subTest(field=field):
                inventory = json.loads(json.dumps(self.inventory))
                inventory["source_counts"][field] = value
                conn = connect(self.database)
                try:
                    conn.execute("UPDATE import_runs SET inventory_json=?", (json.dumps(inventory),))
                    conn.commit()
                finally:
                    conn.close()
                self.assertIn("source_counts_mismatch:" + field, validate_core(self.dataset)["errors"])

    def test_search_index_rejects_missing_duplicate_and_stale_text_rows(self) -> None:
        mutations = (
            "DELETE FROM search_index WHERE target_id='evidence-1'",
            "INSERT INTO search_index SELECT * FROM search_index WHERE target_id='knowledge-rev-1'",
            "UPDATE search_index SET text='stale text' WHERE target_id='knowledge-rev-1'",
        )
        conn = connect(self.database)
        try:
            original = [tuple(row) for row in conn.execute("SELECT * FROM search_index")]
        finally:
            conn.close()
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                conn = connect(self.database)
                try:
                    conn.execute("DELETE FROM search_index")
                    conn.executemany("INSERT INTO search_index VALUES (?,?,?,?)", original)
                    conn.execute(mutation)
                    conn.commit()
                finally:
                    conn.close()
                result = validate_core(self.dataset)
                self.assertFalse(result["valid"])
                self.assertTrue(any(error.startswith("search_index_") for error in result["errors"]))

    def test_search_title_uses_current_lecture_revision(self) -> None:
        conn = connect(self.database)
        try:
            conn.execute("UPDATE run_lectures SET title='Renamed lecture'")
            conn.commit()
        finally:
            conn.close()
        self.assertFalse(validate_core(self.dataset)["valid"])
        conn = connect(self.database)
        try:
            conn.execute("UPDATE search_index SET title='Renamed lecture' WHERE target_id='evidence-1'")
            conn.commit()
        finally:
            conn.close()
        self.assertTrue(validate_core(self.dataset)["valid"])

    def _add_historical_run(self) -> None:
        conn = connect(self.database)
        try:
            conn.execute("INSERT INTO import_runs VALUES ('old-run','2025-01-01','1',?,'complete')",
                         (json.dumps(self.inventory),))
            conn.execute("INSERT INTO run_lectures SELECT 'old-run',lecture_id,directory,title,course_position "
                         "FROM run_lectures WHERE run_id='run-1'")
            conn.execute("INSERT INTO run_artifacts SELECT 'old-run',source_path,revision_id "
                         "FROM run_artifacts WHERE run_id='run-1'")
            conn.execute("INSERT INTO run_records SELECT 'old-run',record_id FROM run_records WHERE run_id='run-1'")
            conn.execute("INSERT INTO run_evidence_alias SELECT 'old-run',namespace,legacy_id,evidence_id "
                         "FROM run_evidence_alias WHERE run_id='run-1'")
            conn.execute("INSERT INTO legacy_records VALUES ('old-record','claim','old-1',"
                         "'artifact-rev-1','/old-claim','{}')")
            conn.execute("INSERT INTO run_records VALUES ('old-run','old-record')")
            conn.execute("INSERT INTO knowledge_revisions VALUES ('old-revision','item-1','old-record',"
                         "'Earlier statement','','','[]','[]','{}','candidate','imported_unverified')")
            conn.execute("INSERT INTO run_knowledge VALUES ('old-run','item-1','old-revision')")
            conn.execute("INSERT INTO review_events VALUES ('old-review','record-1','old-revision',NULL,"
                         "'legacy','known actor','2025-01-01','unknown','imported_legacy','{}')")
            conn.execute("INSERT INTO run_reviews VALUES ('old-run','old-review')")
            conn.commit()
        finally:
            conn.close()

    def test_historical_review_events_are_not_counted_as_current(self) -> None:
        self._add_historical_run()
        result = validate_core(self.dataset)
        self.assertTrue(result["valid"], result)
        self.assertEqual(result["historical_runs_checked"], 1)
        self.assertEqual(result["counts"]["review_events"], 1)
        self.assertEqual(result["counts"]["unknown_review_actors"], 1)
        conn = connect(self.database)
        try:
            conn.execute("UPDATE run_reviews SET review_id='old-review' WHERE run_id='run-1'")
            conn.commit()
        finally:
            conn.close()
        self.assertIn("current_review_not_current:1", validate_core(self.dataset)["errors"])

    def test_historical_inventory_tampering_is_not_hidden_by_valid_current_run(self) -> None:
        self._add_historical_run()
        self.assertTrue(validate_core(self.dataset)["valid"])
        changed = json.loads(json.dumps(self.inventory))
        changed["inputs"]["lecture/transcript.json"]["byte_size"] += 1
        changed["source_counts"]["segments"] += 1
        changed["knowledge_counts"]["claims"] += 1
        conn = connect(self.database)
        try:
            conn.execute("UPDATE import_runs SET inventory_json=? WHERE run_id='old-run'", (json.dumps(changed),))
            conn.commit()
        finally:
            conn.close()
        result = validate_core(self.dataset)
        self.assertFalse(result["valid"])
        for code in ("inventory_inputs_mismatch", "source_counts_mismatch:segments", "knowledge_counts_mismatch:claims"):
            self.assertIn("historical_run:old-run:" + code, result["errors"])

    def test_current_relation_cannot_point_to_historical_revision_or_record(self) -> None:
        self._add_historical_run()
        for target, record in (("old-revision", "record-1"), ("knowledge-rev-1", "old-record")):
            with self.subTest(target=target, record=record):
                conn = connect(self.database)
                try:
                    conn.execute("DELETE FROM knowledge_relations")
                    conn.execute("INSERT INTO knowledge_relations VALUES ('knowledge-rev-1',?,'supports','context',?)",
                                 (target, record))
                    conn.commit()
                finally:
                    conn.close()
                result = validate_core(self.dataset)
                self.assertFalse(result["valid"])
                self.assertIn("current_knowledge_relation_not_current:1", result["errors"])

    def test_concept_aliases_are_required_in_current_search_text(self) -> None:
        from knowledge_core.importer import rebuild_search

        conn = connect(self.database)
        try:
            conn.execute("INSERT INTO concept_terms VALUES ('knowledge-rev-1','alternate','alias')")
            conn.commit()
            self.assertFalse(validate_core(self.dataset)["valid"])
            rebuild_search(conn, "run-1")
            conn.commit()
            row = conn.execute("SELECT target_id,text FROM search_index WHERE search_index MATCH 'alternate'").fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], "knowledge-rev-1")
            self.assertEqual(row[1], "Synthetic claim Exact retained text  [] [] alternate term")
        finally:
            conn.close()
        self.assertTrue(validate_core(self.dataset)["valid"])

    def test_unresolved_review_target_is_visible_without_inventing_identity(self) -> None:
        conn = connect(self.database)
        try:
            conn.execute("UPDATE review_events SET target_revision=NULL")
            conn.commit()
        finally:
            conn.close()
        result = validate_core(self.dataset)
        self.assertTrue(result["valid"], result)
        self.assertIn("unresolved_review_targets:1", result["warnings"])
        self.assertEqual(result["counts"]["unknown_review_actors"], 1)

    def test_current_evidence_cannot_belong_to_an_unselected_lecture(self) -> None:
        conn = connect(self.database)
        try:
            conn.execute("INSERT INTO lectures VALUES ('unselected-lecture','other-identity','Not selected')")
            conn.execute("UPDATE evidence_units SET lecture_id='unselected-lecture'")
            conn.commit()
        finally:
            conn.close()
        result = validate_core(self.dataset)
        self.assertFalse(result["valid"])
        self.assertIn("current_evidence_owner_not_current:1", result["errors"])

    def test_current_knowledge_cannot_cite_unselected_evidence(self) -> None:
        conn = connect(self.database)
        try:
            conn.execute("INSERT INTO evidence_units VALUES ('unselected-evidence','artifact-rev-1','lecture-1',"
                         "'transcript_segment',1,1000,2000,'Not selected','{}')")
            conn.execute("INSERT INTO knowledge_evidence VALUES ('knowledge-rev-1','unselected-evidence','support')")
            conn.commit()
        finally:
            conn.close()
        result = validate_core(self.dataset)
        self.assertFalse(result["valid"])
        self.assertIn("current_knowledge_evidence_not_current:1", result["errors"])

    def test_transfer_rejects_other_git_repositories_before_creating_output(self) -> None:
        bundle = self.root / "bundle"
        export_core(self.dataset, bundle)
        for git_is_file in (False, True):
            other = self.root / ("worktree" if git_is_file else "other-repository")
            other.mkdir()
            if git_is_file:
                (other / ".git").write_text("gitdir: synthetic", encoding="utf-8")
            else:
                (other / ".git").mkdir()
            destination = other / "not-created" / "dataset"
            with self.subTest(git_is_file=git_is_file):
                with self.assertRaisesRegex(CoreError, "Git repository"):
                    export_core(self.dataset, destination)
                with self.assertRaisesRegex(CoreError, "Git repository"):
                    restore_core(bundle, destination)
                self.assertFalse(destination.parent.exists())

    def _add_transfer_blob(self, name: str) -> tuple[str, bytes]:
        content = ("synthetic transfer artifact " + name).encode("utf-8")
        digest = hashlib.sha256(content).hexdigest()
        relative = f"blobs/{digest[:2]}/{digest}"
        blob = self.dataset / relative
        blob.parent.mkdir(parents=True, exist_ok=True)
        blob.write_bytes(content)
        conn = connect(self.database)
        try:
            conn.execute("INSERT INTO artifacts VALUES (?,'retained_document',NULL,?)", (name, name))
            conn.execute("INSERT INTO artifact_revisions VALUES (?,?,?,?,?)",
                         (name + "-revision", name, digest, len(content), relative))
            conn.commit()
        finally:
            conn.close()
        return relative, content

    def test_failed_parallel_transfer_joins_writers_before_cleanup_and_never_publishes(self) -> None:
        slow_relative, _slow_content = self._add_transfer_blob("slow")
        bad_relative, bad_content = self._add_transfer_blob("bad")
        original_copy = transfer._copy_verified
        for mode in ("export", "restore"):
            with self.subTest(mode=mode):
                (self.dataset / bad_relative).write_bytes(bad_content)
                source = self.dataset
                if mode == "restore":
                    source = self.root / "bundle"
                    export_core(self.dataset, source)
                destination = self.root / (mode + "-must-not-publish")
                slow_started, release_slow = threading.Event(), threading.Event()
                corruption_detected, slow_finished = threading.Event(), threading.Event()

                def controlled_copy(src, target, expected):
                    if src.name == Path(slow_relative).name:
                        slow_started.set()
                        if not release_slow.wait(5):
                            raise RuntimeError("test did not release the blocked writer")
                        try:
                            return original_copy(src, target, expected)
                        finally:
                            slow_finished.set()
                    if src.name == Path(bad_relative).name:
                        if not slow_started.wait(5):
                            raise RuntimeError("parallel writer did not start")
                        # Restore has already verified this file: model a source
                        # changing during copying, after the initial check.
                        src.write_bytes(b"corrupted while copying")
                        try:
                            return original_copy(src, target, expected)
                        except CoreError:
                            corruption_detected.set()
                            raise
                    return original_copy(src, target, expected)

                operation = export_core if mode == "export" else restore_core
                with patch.object(transfer, "_copy_verified", controlled_copy), \
                     patch.object(transfer, "_publish", wraps=transfer._publish) as publish, \
                     patch.object(transfer, "_discard_stage", wraps=transfer._discard_stage) as cleanup, \
                     ThreadPoolExecutor(max_workers=1) as caller:
                    future = caller.submit(operation, source, destination)
                    try:
                        self.assertTrue(corruption_detected.wait(5))
                        self.assertFalse(future.done(), "failure returned with a writer still running")
                        cleanup.assert_not_called()
                        publish.assert_not_called()
                        self.assertFalse(destination.exists())
                    finally:
                        release_slow.set()
                    with self.assertRaisesRegex(CoreError, "checksum mismatch during transfer"):
                        future.result(timeout=5)
                    self.assertTrue(slow_finished.is_set())
                    cleanup.assert_called_once()
                    publish.assert_not_called()
                self.assertFalse(destination.exists())
                self.assertFalse(list(self.root.glob(".knowledge-core-*")))
                self.assertFalse(any(thread.name.startswith("knowledge-transfer")
                                     for thread in threading.enumerate()))

    def test_late_manifest_file_corruption_never_creates_restore_destination(self) -> None:
        late_relative = None
        for number in range(20):
            late_relative, _ = self._add_transfer_blob(f"extra-{number}")
        bundle = self.root / "many-file-bundle"
        export_core(self.dataset, bundle)
        (bundle / late_relative).write_bytes(b"corrupted")
        destination = self.root / "not-created" / "restore"
        with self.assertRaisesRegex(CoreError, "checksum mismatch"):
            restore_core(bundle, destination)
        self.assertFalse(destination.parent.exists())
        self.assertFalse(any(thread.name.startswith("knowledge-transfer") for thread in threading.enumerate()))


if __name__ == "__main__":
    unittest.main()
