"""Lossless local dataset transfer with verified, portable file manifests."""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import tempfile
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any, Callable

from .db import DB_NAME, SCHEMA_VERSION, CoreError, canonical_json, confined, connect, file_hash, safe_relative
from .validation import dataset_paths, reject_linked_path, validate_core

MANIFEST_NAME = "manifest.json"
FORMAT = "knowledge-core-bundle"
TRANSFER_WORKERS = 8


def _destination(destination: Path | str, source: Path) -> Path:
    target = Path(destination).absolute()
    reject_linked_path(target)
    resolved, origin = target.resolve(), source.resolve()
    repository = Path(__file__).resolve().parents[1]
    if resolved.is_relative_to(repository):
        raise CoreError("private dataset output must be outside the code repository")
    if any((parent / ".git").exists() for parent in (target, *target.parents)):
        raise CoreError("private dataset output must be outside any Git repository")
    if resolved.is_relative_to(origin) or origin.is_relative_to(resolved):
        raise CoreError("destination must be separate from the source dataset")
    if target.exists():
        raise CoreError("destination already exists; refusing to replace it")
    return target


def _discard_stage(stage: Path, parent: Path) -> None:
    # Delete only the unique directory this operation created, within its parent.
    if stage.exists():
        reject_linked_path(stage)
        if stage.parent.resolve() != parent.resolve() or not stage.name.startswith(".knowledge-core-"):
            raise CoreError("unexpected transfer staging directory")
        shutil.rmtree(stage)


def _blob_inventory(database: Path) -> dict[str, dict[str, Any]]:
    conn = connect(database, readonly=True)
    try:
        result = {}
        for row in conn.execute("SELECT blob_path,sha256,byte_size FROM artifact_revisions"):
            relative, digest, size = row
            expected = {"sha256": digest, "byte_size": size}
            if relative in result and result[relative] != expected:
                raise CoreError("inconsistent blob inventory")
            result[relative] = expected
        return result
    finally:
        conn.close()


def _file_metadata(path: Path) -> dict[str, Any]:
    return {"sha256": file_hash(path), "byte_size": path.stat().st_size}


def _copy_verified(source: Path, target: Path, expected: dict[str, Any]) -> None:
    reject_linked_path(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents accidental replacement inside the new stage.
    with source.open("rb") as reader, target.open("xb") as writer:
        shutil.copyfileobj(reader, writer, length=1024 * 1024)
    if _file_metadata(target) != expected:
        raise CoreError("file changed or checksum mismatch during transfer")


def _parallel_file_tasks(
    files: dict[str, dict[str, Any]], operation: Callable[[str, dict[str, Any]], None],
) -> None:
    """Bound queued work and join every writer before success or failure returns."""
    if not files:
        return
    executor = ThreadPoolExecutor(max_workers=TRANSFER_WORKERS, thread_name_prefix="knowledge-transfer")
    pending = set()
    entries = iter(files.items())

    def submit_next() -> bool:
        try:
            relative, metadata = next(entries)
        except StopIteration:
            return False
        pending.add(executor.submit(operation, relative, metadata))
        return True

    try:
        for _ in range(TRANSFER_WORKERS * 2):
            if not submit_next():
                break
        while pending:
            completed, pending = wait(pending, return_when=FIRST_COMPLETED)
            # Detect any completed error before scheduling another batch.
            for future in completed:
                future.result()
            for _ in completed:
                if not submit_next():
                    break
    finally:
        # On failure, keep the original exception. Running jobs cannot be
        # cancelled, so waiting here must precede caller validation or cleanup.
        executor.shutdown(wait=True, cancel_futures=True)


def _copy_files(source_root: Path, target_root: Path, files: dict[str, dict[str, Any]]) -> None:
    def copy(relative: str, metadata: dict[str, Any]) -> None:
        _copy_verified(confined(source_root, relative), confined(target_root, relative), metadata)

    _parallel_file_tasks(files, copy)


def _verify_files(root: Path, files: dict[str, dict[str, Any]]) -> None:
    def verify(relative: str, metadata: dict[str, Any]) -> None:
        path = confined(root, relative)
        if not path.is_file() or _file_metadata(path) != metadata:
            raise CoreError("bundle file missing or checksum mismatch: " + relative)

    _parallel_file_tasks(files, verify)


def _publish(stage: Path, target: Path) -> None:
    reject_linked_path(target)
    if target.exists():
        raise CoreError("destination appeared during transfer; refusing to replace it")
    os.rename(stage, target)


def export_core(
    db_path: Path | str, destination: Path | str, *, artifact_root: Path | str | None = None,
) -> dict[str, Any]:
    """Export only a consistent SQLite snapshot and its registered artifact bytes."""
    database, source_root = dataset_paths(db_path, artifact_root)
    target = _destination(destination, source_root)
    source_check = validate_core(database, artifact_root=source_root, verify_files=False)
    if not source_check["valid"]:
        raise CoreError("source validation failed: " + "; ".join(source_check["errors"]))
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".knowledge-core-", dir=target.parent))
    try:
        source_conn = connect(database, readonly=True)
        snapshot_conn = sqlite3.connect(stage / DB_NAME)
        try:
            source_conn.backup(snapshot_conn)
        finally:
            snapshot_conn.close()
            source_conn.close()
        snapshot_check = validate_core(stage, verify_files=False)
        if not snapshot_check["valid"]:
            raise CoreError("snapshot validation failed: " + "; ".join(snapshot_check["errors"]))
        files = _blob_inventory(stage / DB_NAME)
        _copy_files(source_root, stage, files)
        validation = validate_core(stage)
        if not validation["valid"]:
            raise CoreError("export validation failed: " + "; ".join(validation["errors"]))
        files[DB_NAME] = _file_metadata(stage / DB_NAME)
        manifest = {"format": FORMAT, "format_version": 1, "schema_version": SCHEMA_VERSION, "files": files}
        (stage / MANIFEST_NAME).write_text(canonical_json(manifest) + "\n", encoding="utf-8")
        _publish(stage, target)
        return {"destination": str(target), "manifest": manifest, "validation": validation}
    finally:
        _discard_stage(stage, target.parent)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise CoreError("duplicate manifest key")
        result[key] = value
    return result


def _read_manifest(root: Path) -> dict[str, Any]:
    reject_linked_path(root)
    manifest_path = confined(root, MANIFEST_NAME)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except (OSError, ValueError) as exc:
        raise CoreError("invalid bundle manifest") from exc
    if not isinstance(manifest, dict) or set(manifest) != {"format", "format_version", "schema_version", "files"}:
        raise CoreError("bundle manifest contract mismatch")
    if (manifest["format"] != FORMAT or type(manifest["format_version"]) is not int
            or manifest["format_version"] != 1 or manifest["schema_version"] != SCHEMA_VERSION):
        raise CoreError("unsupported bundle format or schema version")
    files = manifest["files"]
    if not isinstance(files, dict) or DB_NAME not in files:
        raise CoreError("bundle database entry is missing")
    for relative, metadata in files.items():
        safe_relative(relative)
        if relative != DB_NAME and not re.fullmatch(r"blobs/[0-9a-f]{2}/[0-9a-f]{64}", relative):
            raise CoreError("bundle contains an unregistered file type")
        if not isinstance(metadata, dict) or set(metadata) != {"sha256", "byte_size"}:
            raise CoreError("invalid file metadata")
        digest, size = metadata["sha256"], metadata["byte_size"]
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise CoreError("invalid file checksum")
        if type(size) is not int or size < 0:
            raise CoreError("invalid file size")
        if relative != DB_NAME and relative != f"blobs/{digest[:2]}/{digest}":
            raise CoreError("blob path does not match its checksum")
    return manifest


def restore_core(bundle: Path | str, destination: Path | str) -> dict[str, Any]:
    """Restore a verified bundle to a new directory, preserving IDs and revisions."""
    source = Path(bundle).absolute()
    target = _destination(destination, source)
    manifest = _read_manifest(source)
    files = manifest["files"]
    # Validate all entries and exact blob membership before creating a target.
    _verify_files(source, files)
    validation = validate_core(source)
    if not validation["valid"]:
        raise CoreError("bundle validation failed: " + "; ".join(validation["errors"]))
    registered = _blob_inventory(source / DB_NAME)
    if {key: value for key, value in files.items() if key != DB_NAME} != registered:
        raise CoreError("bundle inventory differs from database artifact revisions")
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".knowledge-core-", dir=target.parent))
    try:
        _copy_files(source, stage, files)
        restored_check = validate_core(stage)
        if not restored_check["valid"]:
            raise CoreError("restored dataset validation failed: " + "; ".join(restored_check["errors"]))
        (stage / MANIFEST_NAME).write_text(canonical_json(manifest) + "\n", encoding="utf-8")
        _publish(stage, target)
        return {"destination": str(target), "manifest": manifest, "validation": restored_check}
    finally:
        _discard_stage(stage, target.parent)
