"""Database primitives. No application, network, or video dependencies."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import uuid
from pathlib import Path, PurePosixPath, PureWindowsPath

SCHEMA_VERSION = "1"
DB_NAME = "knowledge.sqlite"
NAMESPACE = uuid.UUID("3b42aa48-01a7-48d0-8332-438aad51b0dc")


class CoreError(ValueError):
    """A data contract or integrity condition was violated."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def stable_id(namespace: str, key: str) -> str:
    return str(uuid.uuid5(NAMESPACE, namespace + ":" + key))


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_relative(value: str) -> Path:
    """Require portable relative paths, including under Windows semantics."""
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise CoreError("invalid portable relative path")
    win, posix = PureWindowsPath(value), PurePosixPath(value)
    if win.drive or win.root or posix.is_absolute() or ":" in value:
        raise CoreError("absolute, rooted, or drive-relative path is forbidden")
    if any(part in (".", "..", "") for part in value.split("/")):
        raise CoreError("non-canonical relative path is forbidden")
    if win.is_reserved() or any(part.rstrip(" .") != part or
            any(ord(char) < 32 or char in '<>"|?*' for char in part) for part in value.split("/")):
        raise CoreError("non-portable Windows path component is forbidden")
    return Path(*posix.parts)


def confined(root: Path, relative: str) -> Path:
    # Canonical relative components cannot leave this lexical root. Check every
    # existing ancestor once, before access, including ancestors above root.
    root = Path(os.path.abspath(root))
    path = root / safe_relative(relative)
    for current in reversed((path, *path.parents)):
        try:
            info = current.lstat()
        except FileNotFoundError:
            break  # A new output path has no existing descendants to follow.
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise CoreError("linked paths are forbidden in a portable data store")
    return path


def connect(path: Path | str, *, readonly: bool = False) -> sqlite3.Connection:
    path = Path(path)
    if readonly:
        conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    if readonly:
        conn.execute("PRAGMA query_only=ON")
    return conn


def initialize(conn: sqlite3.Connection) -> None:
    conn.executescript(Path(__file__).with_name("schema.sql").read_text(encoding="utf-8"))
    conn.execute("INSERT INTO metadata VALUES ('schema_version',?)", (SCHEMA_VERSION,))


def current_run(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT value FROM metadata WHERE key='current_run'").fetchone()
    if not row:
        raise CoreError("database has no accepted import")
    return row[0]
