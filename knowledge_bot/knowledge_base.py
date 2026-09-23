"""Verified knowledge references for manual review and correction of the robot.

Run ``python -m knowledge_bot.knowledge_base connect`` before querying.
The published source is never modified. Updates become visible only after a
successful restore and an atomic switch of the active snapshot.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
from pathlib import Path

from knowledge_core.db import CoreError, canonical_json, confined
from knowledge_core.published import _read_packed_manifest, restore_published
from knowledge_core.query import search, trace
from knowledge_core.transfer import _read_manifest
from knowledge_core.validation import reject_linked_path, validate_core

WORKSPACE = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = WORKSPACE / "botArtjahan-main" / "knowledge"
DEFAULT_CACHE = WORKSPACE / ".bot_knowledge"


class KnowledgeBase:
    def __init__(self, source=None, cache=None):
        self.source = Path(source or os.environ.get("BOT_KNOWLEDGE_SOURCE") or DEFAULT_SOURCE).absolute()
        self.cache = Path(cache or os.environ.get("BOT_KNOWLEDGE_CACHE") or DEFAULT_CACHE).absolute()
        reject_linked_path(self.source)
        reject_linked_path(self.cache)
        if (self.cache.resolve().is_relative_to(self.source.resolve())
                or self.source.resolve().is_relative_to(self.cache.resolve())):
            raise CoreError("knowledge cache must be separate from its source")

    def _active(self):
        path = confined(self.cache, "active.json")
        if not path.is_file():
            raise CoreError("База не подключена. Выполните: python -m knowledge_bot.knowledge_base connect")
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("source") != str(self.source.resolve()):
            raise CoreError("Источник базы изменён. Выполните команду connect для нового источника")
        dataset = confined(self.cache, value["snapshot"])
        if not (dataset / "knowledge.sqlite").is_file():
            raise CoreError("Активный снимок отсутствует. Повторите connect")
        return value, dataset

    def connect(self):
        """Validate a fresh snapshot; failed refresh leaves the old one active."""
        manifest = _read_manifest(self.source)
        packed = _read_packed_manifest(self.source)
        fingerprint = hashlib.sha256(canonical_json([manifest, packed]).encode("utf-8")).hexdigest()
        # Each refresh verifies source bytes, even when manifests have not changed.
        # Unique destinations also allow concurrent refreshes without replacement.
        self.cache.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".connection-", dir=self.cache) as temporary:
            import uuid
            name = "snapshot-" + uuid.uuid4().hex
            target = confined(self.cache, name)
            result = restore_published(self.source, target)
            metadata = {
                "source": str(self.source.resolve()), "snapshot": name,
                "fingerprint": fingerprint,
                "run_id": result["validation"]["current_run"],
                "validation": result["validation"],
            }
            pointer = Path(temporary) / "active.json"
            pointer.write_text(canonical_json(metadata) + "\n", encoding="utf-8")
            os.replace(pointer, confined(self.cache, "active.json"))
        return self.status()

    def status(self):
        metadata, dataset = self._active()
        return {**metadata, "dataset": str(dataset), "connected": True}

    def validate(self):
        _, dataset = self._active()
        return validate_core(dataset)

    def search(self, text, *, limit=5):
        _, dataset = self._active()
        return search(dataset, text, limit=limit)

    def show(self, identifier, *, namespace=None):
        _, dataset = self._active()
        return trace(dataset, identifier, namespace=namespace)

    def context(self, text, *, limit=5):
        # Pin one snapshot for the entire response, including concurrent refresh.
        metadata, dataset = self._active()
        hits = search(dataset, text, limit=limit)
        return {
            "status": "ok", "query": text, "source": metadata["source"],
            "run_id": metadata["run_id"], "dataset": str(dataset),
            "usage": "reference_only", "automatic_execution_allowed": False,
            "notice": "Найденные материалы требуют проверки; поиск не подтверждает торговое правило.",
            "results": hits,
        }


def attach_knowledge_context(packet, query="уровень", *, knowledge=None):
    """Attach separately identified references without changing strategy gates."""
    try:
        packet["knowledge_references"] = (knowledge or KnowledgeBase()).context(query)
    except (CoreError, OSError, sqlite3.Error, ValueError, KeyError, TypeError) as exc:
        packet["knowledge_references"] = {"status": "unavailable", "error": str(exc),
                                           "automatic_execution_allowed": False}
    return packet


def main(argv=None):
    parser = argparse.ArgumentParser(description="Подключение базы знаний к роботу")
    parser.add_argument("--source", help="Каталог опубликованной базы")
    parser.add_argument("--cache", help="Каталог снимков вне Git")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("connect", "status", "validate"):
        sub.add_parser(name)
    for name in ("search", "context"):
        command = sub.add_parser(name)
        command.add_argument("text")
        command.add_argument("--limit", type=int, default=5)
    show = sub.add_parser("show")
    show.add_argument("identifier")
    show.add_argument("--namespace")
    args = parser.parse_args(argv)
    try:
        kb = KnowledgeBase(args.source, args.cache)
        if args.command in ("search", "context"):
            result = getattr(kb, args.command)(args.text, limit=args.limit)
        elif args.command == "show":
            result = kb.show(args.identifier, namespace=args.namespace)
        else:
            result = getattr(kb, args.command)()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return int(isinstance(result, dict) and result.get("valid") is False)
    except (CoreError, OSError, sqlite3.Error, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
