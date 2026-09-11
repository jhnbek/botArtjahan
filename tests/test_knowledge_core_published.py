"""Synthetic checks for restoring a complete published, compressed dataset."""
import contextlib
import gzip
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from knowledge_core.__main__ import main
from knowledge_core.db import DB_NAME, CoreError, canonical_json, connect, file_hash
from knowledge_core.importer import build_core
from knowledge_core.published import PACKED_DATABASE_NAME, PACKED_MANIFEST_NAME, restore_published
from knowledge_core.transfer import MANIFEST_NAME, export_core
from knowledge_core.validation import validate_core
from test_knowledge_core_importer import source_fixture


class PublishedKnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="knowledge-core-published-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.source = self.root / "source"
        source_fixture(self.source)
        self.original = self.root / "original"
        build_core(self.source, self.original)
        self.bundle = self.root / "repository" / "dataset"
        self.manifest = export_core(self.original, self.bundle)["manifest"]
        self.rows = self.logical_rows(self.bundle / DB_NAME)
        self.compressed = self.bundle / PACKED_DATABASE_NAME
        with self.compressed.open("wb") as output:
            with gzip.GzipFile(filename="", fileobj=output, mode="wb", mtime=0) as compressed:
                compressed.write((self.bundle / DB_NAME).read_bytes())
        (self.bundle / DB_NAME).unlink()
        self.packed = {"format": "knowledge-core-published", "format_version": 1,
                       "schema_version": "1", "database": {"path": PACKED_DATABASE_NAME}}
        self.refresh_packed_metadata()
        # Published inputs may live inside Git; restored output must not.
        (self.bundle.parent / ".git").write_text("gitdir: synthetic", encoding="utf-8")
        self.destination = self.root / "output-parent" / "restored"

    @staticmethod
    def logical_rows(database):
        conn = connect(database, readonly=True)
        try:
            tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_schema WHERE type='table'")]
            return {name: sorted((tuple(row) for row in conn.execute(f'SELECT * FROM "{name}"')), key=repr)
                    for name in tables}
        finally:
            conn.close()

    def refresh_packed_metadata(self):
        self.packed["database"].update(sha256=file_hash(self.compressed), byte_size=self.compressed.stat().st_size)
        self.write_packed()

    def write_packed(self):
        (self.bundle / PACKED_MANIFEST_NAME).write_text(canonical_json(self.packed), encoding="utf-8")

    def write_manifest(self):
        (self.bundle / MANIFEST_NAME).write_text(canonical_json(self.manifest), encoding="utf-8")

    def assert_rejected(self, message):
        with self.assertRaisesRegex(CoreError, message):
            restore_published(self.bundle, self.destination)
        self.assertFalse(self.destination.exists())

    def test_roundtrip_preserves_all_tables_and_blobs_without_original_sources(self):
        for path in (self.source, self.original):
            self.assertTrue(path.is_relative_to(self.root))
            shutil.rmtree(path)
        before = {p.relative_to(self.bundle).as_posix(): file_hash(p)
                  for p in self.bundle.rglob("*") if p.is_file()}
        result = restore_published(self.bundle, self.destination)
        self.assertTrue(result["validation"]["valid"])
        self.assertEqual(self.logical_rows(self.destination / DB_NAME), self.rows)
        for relative, metadata in self.manifest["files"].items():
            if relative != DB_NAME:
                self.assertEqual(file_hash(self.destination / relative), metadata["sha256"])
        self.assertTrue(validate_core(self.destination)["valid"])
        after = {p.relative_to(self.bundle).as_posix(): file_hash(p)
                 for p in self.bundle.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertFalse((self.bundle / DB_NAME).exists())

    def test_corrupted_compressed_bytes_fail_before_output_creation(self):
        content = bytearray(self.compressed.read_bytes())
        content[len(content) // 2] ^= 1
        self.compressed.write_bytes(content)
        self.assert_rejected("packed database checksum")
        self.assertFalse(self.destination.parent.exists())

    def test_truncated_gzip_with_matching_packed_checksum_is_rejected(self):
        self.compressed.write_bytes(self.compressed.read_bytes()[:-8])
        self.refresh_packed_metadata()
        self.assert_rejected("invalid or truncated gzip")
        self.assertFalse(self.destination.parent.exists())

    def test_decompressed_database_cannot_exceed_declared_size(self):
        self.manifest["files"][DB_NAME]["byte_size"] = 7
        self.write_manifest()
        self.assert_rejected("exceeds declared size")
        self.assertFalse(self.destination.parent.exists())

    def test_uncompressed_database_checksum_is_verified(self):
        self.manifest["files"][DB_NAME]["sha256"] = "0" * 64
        self.write_manifest()
        self.assert_rejected("decompressed database checksum")

    def test_both_manifests_reject_duplicate_keys(self):
        for name in (PACKED_MANIFEST_NAME, MANIFEST_NAME):
            with self.subTest(manifest=name):
                path = self.bundle / name
                original = path.read_text(encoding="utf-8")
                path.write_text(original[:-1] + ',"format_version":1}', encoding="utf-8")
                try:
                    self.assert_rejected("invalid .*manifest")
                finally:
                    path.write_text(original, encoding="utf-8")

    def test_packed_manifest_rejects_path_substitution_and_unknown_fields(self):
        original = canonical_json(self.packed)
        for path in ("../knowledge.sqlite.gz", "/knowledge.sqlite.gz", "C:/knowledge.sqlite.gz",
                     "blobs/knowledge.sqlite.gz", "knowledge.sqlite"):
            with self.subTest(path=path):
                self.packed["database"]["path"] = path
                self.write_packed()
                self.assert_rejected("packed database path")
        self.packed = json.loads(original)
        self.packed["extra"] = "unexpected"
        self.write_packed()
        self.assert_rejected("packed manifest contract")

    def test_packed_manifest_requires_supported_version_hash_and_integer_size(self):
        original = canonical_json(self.packed)
        for key, value in (("format_version", True), ("format_version", 2), ("schema_version", "2")):
            with self.subTest(field=key, value=value):
                self.packed = json.loads(original)
                self.packed[key] = value
                self.write_packed()
                self.assert_rejected("unsupported packed")
        for key, value in (("sha256", "invalid"), ("byte_size", True), ("byte_size", -1)):
            with self.subTest(field=key, value=value):
                self.packed = json.loads(original)
                self.packed["database"][key] = value
                self.write_packed()
                self.assert_rejected("invalid packed database")

    def test_missing_manifest_blob_is_rejected_before_export(self):
        relative = next(key for key in self.manifest["files"] if key != DB_NAME)
        del self.manifest["files"][relative]
        self.write_manifest()
        with patch("knowledge_core.published.export_core") as export:
            self.assert_rejected("blob inventory differs")
            export.assert_not_called()

    def test_changed_blob_is_rejected_and_temporary_database_is_removed(self):
        relative = next(key for key in self.manifest["files"] if key != DB_NAME)
        (self.bundle / relative).write_bytes(b"corrupted synthetic artifact")
        with patch("knowledge_core.published.tempfile.gettempdir", return_value=str(self.root)):
            self.assert_rejected("checksum mismatch")
        self.assertFalse(list(self.root.glob(".knowledge-core-published-*")))
        self.assertFalse(list(self.destination.parent.glob(".knowledge-core-*")))

    def test_existing_and_git_destinations_are_preserved(self):
        self.destination.mkdir(parents=True)
        sentinel = self.destination / "keep.txt"
        sentinel.write_text("preserve", encoding="utf-8")
        with self.assertRaisesRegex(CoreError, "destination already exists"):
            restore_published(self.bundle, self.destination)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")
        git_target = self.bundle.parent / "must-not-create" / "restored"
        with self.assertRaisesRegex(CoreError, "outside any Git repository"):
            restore_published(self.bundle, git_target)
        self.assertFalse(git_target.parent.exists())

    def test_linked_published_root_is_rejected_before_reading_manifests(self):
        link = self.root / "linked"
        try:
            if os.name == "nt":
                import _winapi
                _winapi.CreateJunction(str(self.bundle), str(link))
                self.addCleanup(link.rmdir)
            else:
                link.symlink_to(self.bundle, target_is_directory=True)
                self.addCleanup(link.unlink)
        except (OSError, AttributeError) as exc:
            self.skipTest(f"local directory links unavailable: {exc}")
        with patch("knowledge_core.published._read_manifest") as read:
            with self.assertRaisesRegex(CoreError, "linked paths"):
                restore_published(link, self.destination)
            read.assert_not_called()
        self.assertFalse(self.destination.parent.exists())

    def test_cli_restores_complete_dataset(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = main(["restore-published", str(self.bundle), str(self.destination)])
        self.assertEqual(status, 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["file_count"], len(self.manifest["files"]))
        self.assertTrue(result["validation"]["valid"])


if __name__ == "__main__":
    unittest.main()
