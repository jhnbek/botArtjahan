"""Deterministic browsing views of an already reviewed publication dataset.

This is a presentation export, not a privacy filter or an approval mechanism.
The caller must review the selected normalized text before publishing its output.
Source metadata, raw records and reviewer identities are deliberately not read.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import posixpath
import re
import shutil
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import quote

from .db import SCHEMA_VERSION, CoreError, canonical_json, connect, current_run
from .validation import dataset_paths, reject_linked_path

KINDS = {"claim": "Утверждения", "candidate": "Кандидаты", "rule": "Правила", "concept": "Понятия"}
EVIDENCE_KINDS = {"transcript_segment": "Транскрипт", "source_unit": "Фрагменты источников", "frame": "Кадры", "ocr": "OCR"}
NOTICE = "Материал импортирован. Статус `imported_unverified` не подтверждает содержательную достоверность."


def _text(value) -> str:
    value = html.escape(str(value), quote=False)
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", value)


def _label(value) -> str:
    value = " ".join(str(value).split())
    return _text(value if len(value) <= 100 else value[:97] + "...")


def _name(value: str) -> str:
    # Database identifiers never become filesystem syntax.
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _url(target: str, page: str) -> str:
    return quote(posixpath.relpath(target, posixpath.dirname(page) or "."), safe="/-._~")


def _link(label: str, target: str, page: str, anchor: str = "") -> str:
    return f"[{label}]({_url(target, page)}{('#' + anchor) if anchor else ''})"


def _time(value) -> str:
    if value is None:
        return "время не указано"
    seconds, milliseconds = divmod(value, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}.{milliseconds:03}"


class _Writer:
    def __init__(self, root: Path, page_bytes: int):
        self.root, self.page_bytes = root, page_bytes
        self.files = 0

    def write(self, relative: str, content: str):
        if relative.endswith(".md"):
            content = "\n".join(line.rstrip(" \t\r") for line in content.split("\n")).rstrip("\n") + "\n"
            if len(content.encode("utf-8")) > self.page_bytes:
                raise CoreError("catalog Markdown exceeds the configured page size")
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        self.files += 1

    def jsonl(self, relative: str, records):
        self.write(relative, "".join(canonical_json(record) + "\n" for record in records))

    def pages(self, folder: str, stem: str, title: str, records, *, max_records=100):
        """Keep each record together; oversized records get their own JSON file."""
        pages, locations = [], {}
        number, blocks = 1, []
        # Reserve space for a bounded heading and navigation links.
        available = self.page_bytes - 1024
        used = 0
        for key, render, original in records:
            page = f"{folder}/{stem}-{number:04}.md"
            block = render(page)
            if len(block.encode("utf-8")) > available:
                record_file = f"{folder}/records/{_name(key)[:2]}/{_name(key)}.json"
                self.write(record_file, canonical_json(original) + "\n")
                block = (f'<a id="e-{_name(key)}"></a>\n\n'
                         + _link("Полная запись без сокращений (JSON)", record_file, page)
                         + "\n\nЗапись превышает лимит размера страницы Markdown.\n\n")
            size = len(block.encode("utf-8"))
            if blocks and (used + size > available or len(blocks) >= max_records):
                pages.append((page, blocks))
                number += 1
                blocks, used = [], 0
                page = f"{folder}/{stem}-{number:04}.md"
                # Only the page filename changes; relative link directories stay the same.
            locations[key] = (page, "e-" + _name(key))
            blocks.append(block)
            used += size
        if blocks:
            pages.append((f"{folder}/{stem}-{number:04}.md", blocks))
        for index, (page, content) in enumerate(pages):
            navigation = [_link("Каталог", "README.md", page)]
            if index:
                navigation.append(_link("Предыдущая", pages[index - 1][0], page))
            if index + 1 < len(pages):
                navigation.append(_link("Следующая", pages[index + 1][0], page))
            self.write(page, f"# {_label(title)}\n\n" + " | ".join(navigation)
                       + "\n\n" + "".join(content))
        return [page for page, _ in pages], locations


def _load(database):
    conn = connect(database, readonly=True)
    try:
        conn.execute("BEGIN")
        schema = conn.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
        if schema is None or schema[0] != SCHEMA_VERSION:
            raise CoreError("unsupported catalog input schema")
        run_id = current_run(conn)
        status = conn.execute("SELECT status FROM import_runs WHERE run_id=?", (run_id,)).fetchone()
        if status is None or status[0] != "complete":
            raise CoreError("catalog input snapshot is incomplete")
        lectures = [dict(row) for row in conn.execute("""SELECT lecture_id,title,course_position
            FROM run_lectures WHERE run_id=? ORDER BY course_position IS NULL,course_position,lecture_id""", (run_id,))]
        evidence = {row["evidence_id"]: dict(row) for row in conn.execute("""SELECT e.evidence_id,e.lecture_id,
            e.kind,e.segment_index,e.start_ms,e.end_ms,e.text,a.sha256 FROM evidence_units e
            JOIN artifact_revisions a ON a.revision_id=e.artifact_revision
            WHERE EXISTS (SELECT 1 FROM run_evidence_alias r WHERE r.run_id=? AND r.evidence_id=e.evidence_id)
            ORDER BY e.lecture_id,e.kind,e.start_ms,e.segment_index,e.evidence_id""", (run_id,))}
        knowledge = {row["revision_id"]: dict(row) for row in conn.execute("""SELECT k.revision_id,k.item_id,
            i.namespace,i.legacy_id,i.kind,k.statement,k.quote,k.interpretation,k.conditions_json,
            k.exceptions_json,k.review_state FROM run_knowledge r JOIN knowledge_revisions k
            ON k.item_id=r.item_id AND k.revision_id=r.revision_id JOIN knowledge_items i ON i.item_id=k.item_id
            WHERE r.run_id=? ORDER BY i.kind,i.namespace,i.legacy_id,k.item_id""", (run_id,))}
        for value in knowledge.values():
            for key in ("conditions", "exceptions"):
                value[key] = json.loads(value.pop(key + "_json"))
            value.update(evidence=[], relations=[], terms=[])
        for row in conn.execute("""SELECT z.revision_id,z.evidence_id,z.role FROM knowledge_evidence z
            JOIN run_knowledge r ON r.revision_id=z.revision_id WHERE r.run_id=?
            ORDER BY z.revision_id,z.evidence_id,z.role""", (run_id,)):
            if row["evidence_id"] not in evidence:
                raise CoreError("knowledge evidence leaves the catalog snapshot")
            knowledge[row["revision_id"]]["evidence"].append(dict(row))
        for row in conn.execute("""SELECT z.from_revision,z.to_revision,z.relation_type,z.role
            FROM knowledge_relations z JOIN run_knowledge r ON r.revision_id=z.from_revision
            WHERE r.run_id=? ORDER BY z.from_revision,z.to_revision,z.relation_type,z.role""", (run_id,)):
            if row["to_revision"] not in knowledge:
                raise CoreError("knowledge relation leaves the catalog snapshot")
            knowledge[row["from_revision"]]["relations"].append(dict(row))
        for row in conn.execute("""SELECT t.revision_id,t.term,t.kind FROM concept_terms t JOIN run_knowledge r
            ON r.revision_id=t.revision_id WHERE r.run_id=? ORDER BY t.revision_id,t.kind,t.term""", (run_id,)):
            knowledge[row["revision_id"]]["terms"].append({"term": row["term"], "kind": row["kind"]})
        frames = defaultdict(set)
        for row in conn.execute("""SELECT DISTINCT from_evidence,to_evidence FROM evidence_links
            WHERE run_id=? ORDER BY from_evidence,to_evidence""", (run_id,)):
            left, right = row
            if left not in evidence or right not in evidence:
                raise CoreError("evidence relation leaves the catalog snapshot")
            if evidence[left]["kind"] == "frame":
                frames[right].add(left)
            if evidence[right]["kind"] == "frame":
                frames[left].add(right)
        for item in evidence.values():
            if item["kind"] == "frame":
                frames[item["evidence_id"]].add(item["evidence_id"])
            item["frame_ids"] = sorted(frames[item["evidence_id"]])
        lecture_ids = {lecture["lecture_id"] for lecture in lectures}
        if any(item["lecture_id"] not in lecture_ids for item in evidence.values()):
            raise CoreError("evidence lecture leaves the catalog snapshot")
        return run_id, lectures, evidence, knowledge
    finally:
        conn.close()


def build_catalog(database: Path | str, destination: Path | str, *, blob_prefix: str = "../blobs",
                  page_bytes: int = 131072) -> dict:
    """Render an audited snapshot into a new directory, without modifying it.

    ``blob_prefix`` is a relative URL from the catalog root to public content-addressed
    blobs. The caller controls publication; this function does not sanitize text.
    Markdown records are never cut mid-field. Oversized records remain available as
    JSON, and complete normalized evidence is also exported per lecture as JSONL.
    """
    if type(page_bytes) is not int or page_bytes < 4096:
        raise CoreError("page_bytes must be an integer of at least 4096")
    if (not isinstance(blob_prefix, str) or not blob_prefix or blob_prefix.startswith("/")
            or any(char in blob_prefix for char in "\\:#?\x00")
            or any(ord(char) < 32 for char in blob_prefix)):
        raise CoreError("blob_prefix must be a relative portable URL path")
    db_path, _ = dataset_paths(database)
    reject_linked_path(db_path.absolute())
    target = Path(destination).absolute()
    reject_linked_path(target)
    if target.exists():
        raise CoreError("catalog destination already exists")
    run_id, lectures, evidence, knowledge = _load(db_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".knowledge-catalog-", dir=target.parent))
    writer = _Writer(stage, page_bytes)
    try:
        lecture_paths = {lecture["lecture_id"]: "lectures/" + _name(lecture["lecture_id"]) for lecture in lectures}
        item_paths = {revision: f"items/{_name(item['item_id'])[:2]}/{_name(item['item_id'])}.md"
                      for revision, item in knowledge.items()}
        def frame_target(eid):
            digest = evidence[eid]["sha256"]
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise CoreError("invalid frame checksum")
            return posixpath.join(blob_prefix, digest[:2], digest)

        def evidence_block(item, page):
            block = f'<a id="e-{_name(item["evidence_id"])}"></a>\n\n'
            block += f'## {_time(item["start_ms"])}'
            if item["end_ms"] is not None:
                block += " - " + _time(item["end_ms"])
            block += "\n\n" + _text(item["text"]) + "\n\n"
            for frame in item["frame_ids"]:
                link = _link("Открыть кадр", frame_target(frame), page)
                if item["kind"] == "frame":
                    block += "!" + _link("Сохранённый кадр", frame_target(frame), page) + "\n\n"
                block += link + "\n\n"
            return block

        evidence_locations, lecture_pages = {}, {}
        by_lecture = defaultdict(list)
        for item in evidence.values():
            by_lecture[item["lecture_id"]].append(item)
        for lecture in lectures:
            lid, folder = lecture["lecture_id"], lecture_paths[lecture["lecture_id"]]
            items = by_lecture[lid]
            writer.jsonl(folder + "/evidence.jsonl", items)
            writer.jsonl(folder + "/ocr.jsonl", (item for item in items if item["kind"] == "ocr"))
            sections = {}
            for kind, title in EVIDENCE_KINDS.items():
                records = [(item["evidence_id"], lambda page, value=item: evidence_block(value, page), item)
                           for item in items if item["kind"] == kind]
                pages, locations = writer.pages(folder, kind, title + ": " + lecture["title"], records,
                                                 max_records=32 if kind == "frame" else 100)
                sections[title] = pages
                evidence_locations.update(locations)
            lecture_pages[lid] = sections

        associated = {revision: {evidence[edge["evidence_id"]]["lecture_id"] for edge in item["evidence"]}
                      for revision, item in knowledge.items()}
        changed = True
        while changed:
            changed = False
            for revision, item in knowledge.items():
                before = len(associated[revision])
                for edge in item["relations"]:
                    associated[revision].update(associated[edge["to_revision"]])
                changed |= len(associated[revision]) != before

        for revision, item in knowledge.items():
            page = item_paths[revision]
            body = "# " + _label(item["legacy_id"]) + "\n\n" + _link("Каталог", "README.md", page) + "\n\n"
            body += NOTICE + "\n\nСтатус: " + _text(item["review_state"]) + "\n\n"
            for field, heading in (("statement", "Формулировка"), ("quote", "Цитата"),
                                   ("interpretation", "Интерпретация"), ("conditions", "Условия"),
                                   ("exceptions", "Исключения"), ("terms", "Термины")):
                value = item[field]
                if value:
                    value = canonical_json(value) if not isinstance(value, str) else value
                    body += "## " + heading + "\n\n" + _text(value) + "\n\n"
            if item["evidence"]:
                body += "## Сохранённые свидетельства\n\n"
                for edge in item["evidence"]:
                    eid = edge["evidence_id"]
                    evidence_page, anchor = evidence_locations[eid]
                    body += "- " + _link(_label(edge["role"]) + ": " + _time(evidence[eid]["start_ms"]),
                                           evidence_page, page, anchor) + "\n"
                    for frame in evidence[eid]["frame_ids"]:
                        body += "  " + _link("Открыть кадр", frame_target(frame), page) + "\n"
                body += "\n"
            if item["relations"]:
                body += "## Сохранённые связи\n\n"
                for edge in item["relations"]:
                    other = knowledge[edge["to_revision"]]
                    body += "- " + _text(edge["relation_type"]) + " / " + _text(edge["role"]) + ": "
                    body += _link(_label(other["legacy_id"]), item_paths[edge["to_revision"]], page) + "\n"
            if len(body.encode("utf-8")) > page_bytes:
                record_file = page.removesuffix(".md") + ".json"
                writer.write(record_file, canonical_json(item) + "\n")
                body = "# " + _label(item["legacy_id"]) + "\n\n" + NOTICE + "\n\n"
                body += _link("Полная запись знаний без сокращений (JSON)", record_file, page) + "\n\n"
                body += "Запись превышает лимит размера страницы Markdown.\n"
            writer.write(page, body)
        writer.jsonl("knowledge.jsonl", knowledge.values())
        writer.jsonl("lectures.jsonl", lectures)

        def entry(item, page):
            return ("- " + _link(_label(item["legacy_id"]) + ": " + _label(item["statement"]),
                                  item_paths[item["revision_id"]], page) + "\n\n")

        category_pages = {}
        for kind, title in KINDS.items():
            records = [(item["revision_id"], lambda page, value=item: entry(value, page), item)
                       for item in knowledge.values() if item["kind"] == kind]
            category_pages[kind], _ = writer.pages("indexes", kind, title, records)
        for lecture in lectures:
            lid, folder = lecture["lecture_id"], lecture_paths[lecture["lecture_id"]]
            selected = [item for revision, item in knowledge.items() if lid in associated[revision]]
            records = [(item["revision_id"], lambda page, value=item: entry(value, page), item) for item in selected]
            pages, _ = writer.pages(folder, "knowledge", "Знания, связанные через сохранённые свидетельства или связи", records)
            page = folder + "/README.md"
            body = "# " + _label(lecture["title"]) + "\n\n" + _link("Каталог", "README.md", page) + "\n\n" + NOTICE + "\n\n"
            for title, section_pages in (*lecture_pages[lid].items(), ("Знания", pages)):
                body += "## " + title + "\n\n"
                body += " · ".join(_link(str(index + 1), path, page) for index, path in enumerate(section_pages)) + "\n\n"
            body += _link("Полные свидетельства (JSONL)", folder + "/evidence.jsonl", page) + "\n\n"
            body += _link("OCR JSONL", folder + "/ocr.jsonl", page) + "\n"
            writer.write(page, body)
        counts = {"lectures": len(lectures), "evidence": dict(sorted(Counter(e["kind"] for e in evidence.values()).items())),
                  "knowledge": dict(sorted(Counter(k["kind"] for k in knowledge.values()).items())),
                  "knowledge_without_lecture": sum(not lids for lids in associated.values())}
        body = "# Каталог знаний\n\n" + NOTICE + "\n\n"
        body += "Тексты, цитаты, интерпретации, условия и исключения сохранены из исходных записей.\n\n"
        body += "## Знания\n\n"
        for kind, title in KINDS.items():
            body += "- " + title + f" ({counts['knowledge'].get(kind, 0)}): "
            body += " · ".join(_link(str(index + 1), path, "README.md") for index, path in enumerate(category_pages[kind])) + "\n"
        body += "\nВыше перечислены все записи знаний, включая записи без связи с лекцией.\n\n## Лекции\n\n"
        for lecture in lectures:
            body += "- " + _link(_label(lecture["title"]), lecture_paths[lecture["lecture_id"]] + "/README.md", "README.md") + "\n"
        body += "\n[Все нормализованные записи знаний (JSONL)](knowledge.jsonl)\n"
        writer.write("README.md", body)
        writer.write("counts.json", canonical_json({"run_id": run_id, **counts}) + "\n")
        reject_linked_path(target)
        if target.exists():
            raise CoreError("catalog destination appeared during generation")
        os.rename(stage, target)
        return {"destination": str(target), "run_id": run_id, "files": writer.files, **counts}
    finally:
        if stage.exists():
            reject_linked_path(stage)
            if stage.parent.resolve() != target.parent.resolve() or not stage.name.startswith(".knowledge-catalog-"):
                raise CoreError("unexpected catalog staging directory")
            shutil.rmtree(stage)
