"""Restore a published SQLite gzip and its original content-addressed blobs."""
from __future__ import annotations

import gzip
import hashlib
import json
import re
import tempfile
import zlib
from pathlib import Path

from .db import DB_NAME, SCHEMA_VERSION, CoreError, confined, file_hash
from .transfer import _blob_inventory, _destination, _read_manifest, _unique_object, export_core
from .validation import reject_linked_path, validate_core

PACKED_MANIFEST_NAME = "packed-manifest.json"
PACKED_DATABASE_NAME = DB_NAME + ".gz"
FORMAT = "knowledge-core-published"
BLOCK_SIZE = 1024 * 1024


def _read_packed_manifest(root: Path) -> dict:
    path = confined(root, PACKED_MANIFEST_NAME)
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except (OSError, ValueError) as exc:
        raise CoreError("invalid packed manifest") from exc
    if not isinstance(manifest, dict) or set(manifest) != {
            "format", "format_version", "schema_version", "database"}:
        raise CoreError("packed manifest contract mismatch")
    if (manifest["format"] != FORMAT or type(manifest["format_version"]) is not int
            or manifest["format_version"] != 1 or manifest["schema_version"] != SCHEMA_VERSION):
        raise CoreError("unsupported packed format or schema version")
    database = manifest["database"]
    if not isinstance(database, dict) or set(database) != {"path", "sha256", "byte_size"}:
        raise CoreError("invalid packed database metadata")
    if database["path"] != PACKED_DATABASE_NAME:
        raise CoreError("packed database path must be " + PACKED_DATABASE_NAME)
    if (not isinstance(database["sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", database["sha256"])
            or type(database["byte_size"]) is not int or database["byte_size"] <= 0):
        raise CoreError("invalid packed database checksum or size")
    return manifest


def _decompress_database(source: Path, target: Path, expected: dict) -> None:
    digest = hashlib.sha256()
    written = 0
    try:
        with gzip.open(source, "rb") as reader, target.open("xb") as writer:
            while True:
                # Read at most one byte beyond the declared size, then reject it.
                block = reader.read(min(BLOCK_SIZE, expected["byte_size"] - written + 1))
                if not block:
                    break
                written += len(block)
                if written > expected["byte_size"]:
                    raise CoreError("decompressed database exceeds declared size")
                digest.update(block)
                writer.write(block)
    except (gzip.BadGzipFile, EOFError, zlib.error) as exc:
        raise CoreError("invalid or truncated gzip database") from exc
    if written != expected["byte_size"] or digest.hexdigest() != expected["sha256"]:
        raise CoreError("decompressed database checksum or size mismatch")


def restore_published(bundle: Path | str, destination: Path | str) -> dict:
    """Restore from a published folder without the original lecture workspace.

    The folder may be inside Git. Only a temporary database is decompressed;
    export_core verifies and copies registered blobs to a fresh external target.
    """
    source = Path(bundle).absolute()
    reject_linked_path(source)
    target = _destination(destination, source)
    manifest = _read_manifest(source)
    packed = _read_packed_manifest(source)["database"]
    compressed = confined(source, packed["path"])
    if (not compressed.is_file() or compressed.stat().st_size != packed["byte_size"]
            or file_hash(compressed) != packed["sha256"]):
        raise CoreError("packed database checksum or size mismatch")

    temporary_root = Path(tempfile.gettempdir()).absolute()
    reject_linked_path(temporary_root)
    temporary_root = temporary_root.resolve()
    repository = Path(__file__).resolve().parents[1]
    if (temporary_root.is_relative_to(repository)
            or any((p / ".git").exists() for p in (temporary_root, *temporary_root.parents))):
        raise CoreError("temporary database directory must be outside Git repositories")
    with tempfile.TemporaryDirectory(prefix=".knowledge-core-published-", dir=temporary_root) as directory:
        database = Path(directory) / DB_NAME
        _decompress_database(compressed, database, manifest["files"][DB_NAME])
        validation = validate_core(database, artifact_root=source, verify_files=False)
        if not validation["valid"]:
            raise CoreError("published database validation failed: " + "; ".join(validation["errors"]))
        registered = _blob_inventory(database)
        if {key: value for key, value in manifest["files"].items() if key != DB_NAME} != registered:
            raise CoreError("published blob inventory differs from database artifact revisions")
        return export_core(database, target, artifact_root=source)
