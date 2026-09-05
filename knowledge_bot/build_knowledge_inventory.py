from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lecture_terms import normalize_text, tokenize
from search_lectures import load_chunks


STRONG_SCORE = 8.0
DEFAULT_TOP_EXCERPTS = 30
DEFAULT_MAX_REFS = 160


@dataclass(frozen=True)
class Topic:
    topic_id: str
    title: str
    description: str
    aliases: tuple[str, ...]
    keywords: tuple[str, ...]


def default_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_taxonomy(path: Path) -> list[Topic]:
    raw = load_json(path)
    topics: list[Topic] = []
    for topic_id, value in raw.items():
        topics.append(
            Topic(
                topic_id=topic_id,
                title=value.get("title", topic_id),
                description=value.get("description", ""),
                aliases=tuple(value.get("aliases", [])),
                keywords=tuple(value.get("keywords", [])),
            )
        )
    return topics


def safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9а-яА-ЯёЁ_-]+", "_", value).strip("_")
    return cleaned or "topic"


def make_excerpt(text: str, aliases: tuple[str, ...], max_chars: int = 820) -> str:
    if len(text) <= max_chars:
        return text
    normalized = normalize_text(text)
    positions = []
    for alias in aliases:
        alias_norm = normalize_text(alias)
        if len(alias_norm) < 3:
            continue
        position = normalized.find(alias_norm)
        if position >= 0:
            positions.append(position)
    if positions:
        start = max(0, min(positions) - max_chars // 4)
    else:
        start = 0
    end = min(len(text), start + max_chars)
    start = max(0, end - max_chars)
    return ("..." if start else "") + text[start:end].strip() + ("..." if end < len(text) else "")


def phrase_hit_score(text_norm: str, alias: str) -> float:
    alias_norm = normalize_text(alias)
    if not alias_norm:
        return 0.0
    if alias_norm in text_norm:
        return 7.0 if " " in alias_norm else 4.0
    alias_tokens = [token for token in tokenize(alias_norm) if len(token) > 2]
    if not alias_tokens:
        return 0.0
    hit_tokens = sum(1 for token in alias_tokens if token in text_norm)
    if hit_tokens == len(alias_tokens) and len(alias_tokens) > 1:
        return 3.5
    if hit_tokens:
        return 1.2 * hit_tokens / len(alias_tokens)
    return 0.0


def score_topic(chunk: dict[str, Any], topic: Topic) -> float:
    text_norm = normalize_text(
        " ".join(
            [
                chunk.get("lecture_title", ""),
                chunk.get("text", ""),
                " ".join(chunk.get("keywords", [])),
            ]
        )
    )
    score = 0.0
    for alias in topic.aliases:
        score += phrase_hit_score(text_norm, alias)
    chunk_keywords = set(chunk.get("keywords", []))
    keyword_hits = chunk_keywords.intersection(topic.keywords)
    score += 2.5 * len(keyword_hits)
    return round(score, 3)


def chunk_ref(chunk: dict[str, Any], score: float) -> dict[str, Any]:
    return {
        "score": score,
        "strong": score >= STRONG_SCORE,
        "chunk_id": chunk.get("chunk_id"),
        "lecture_id": chunk.get("lecture_id"),
        "lecture_title": chunk.get("lecture_title"),
        "start_time": chunk.get("start_time"),
        "end_time": chunk.get("end_time"),
        "start_sec": chunk.get("start_sec"),
        "end_sec": chunk.get("end_sec"),
        "keywords": chunk.get("keywords", []),
        "frames": chunk.get("frames", [])[:4],
    }


def build_inventory(
    root: Path,
    taxonomy_path: Path,
    output_dir: Path,
    top_excerpts: int,
    max_refs: int,
) -> dict[str, Any]:
    chunks_path = output_dir / "lecture_chunks.jsonl"
    index_path = output_dir / "lecture_index.json"
    if not chunks_path.exists():
        raise SystemExit("Build lecture chunks first: python .\\knowledge_bot\\build_knowledge_base.py")
    chunks = load_chunks(chunks_path)
    lecture_index = load_json(index_path) if index_path.exists() else {"lectures": []}
    lectures = {lecture["lecture_id"]: lecture for lecture in lecture_index.get("lectures", [])}
    topics = load_taxonomy(taxonomy_path)

    topic_hits: dict[str, list[dict[str, Any]]] = {}
    lecture_topic_counts: dict[str, Counter[str]] = defaultdict(Counter)
    lecture_topic_strong_counts: dict[str, Counter[str]] = defaultdict(Counter)

    for topic in topics:
        hits: list[dict[str, Any]] = []
        for chunk in chunks:
            score = score_topic(chunk, topic)
            if score <= 0:
                continue
            ref = chunk_ref(chunk, score)
            hits.append(ref)
            lecture_topic_counts[str(chunk.get("lecture_id"))][topic.topic_id] += 1
            if score >= STRONG_SCORE:
                lecture_topic_strong_counts[str(chunk.get("lecture_id"))][topic.topic_id] += 1
        hits.sort(key=lambda item: item["score"], reverse=True)
        topic_hits[topic.topic_id] = hits

    topic_summary = []
    for topic in topics:
        hits = topic_hits[topic.topic_id]
        strong_hits = [hit for hit in hits if hit["strong"]]
        lecture_count = len({hit["lecture_id"] for hit in hits})
        strong_lecture_count = len({hit["lecture_id"] for hit in strong_hits})
        topic_summary.append(
            {
                "topic_id": topic.topic_id,
                "title": topic.title,
                "description": topic.description,
                "hit_count": len(hits),
                "strong_hit_count": len(strong_hits),
                "lecture_count": lecture_count,
                "strong_lecture_count": strong_lecture_count,
                "aliases": list(topic.aliases),
                "keywords": list(topic.keywords),
                "top_hits": hits[:max_refs] if max_refs else hits,
            }
        )

    inventory = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_root": str(root),
        "chunks": len(chunks),
        "lectures": len(lectures) or len({chunk.get("lecture_id") for chunk in chunks}),
        "strong_score_threshold": STRONG_SCORE,
        "topics": topic_summary,
    }

    write_json(output_dir / "topic_coverage.json", inventory)
    write_topic_coverage_csv(output_dir / "topic_coverage.csv", topic_summary)
    write_lecture_matrix(output_dir / "lecture_topic_matrix.csv", lectures, topics, lecture_topic_counts, lecture_topic_strong_counts)
    write_inventory_report(output_dir / "knowledge_inventory.md", inventory)
    write_topic_packets(output_dir / "topic_packets", topics, topic_hits, chunks, top_excerpts, max_refs)
    return inventory


def write_topic_coverage_csv(path: Path, topic_summary: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["topic_id", "title", "hit_count", "strong_hit_count", "lecture_count", "strong_lecture_count"],
        )
        writer.writeheader()
        for topic in topic_summary:
            writer.writerow({key: topic[key] for key in writer.fieldnames})


def write_lecture_matrix(
    path: Path,
    lectures: dict[str, dict[str, Any]],
    topics: list[Topic],
    counts: dict[str, Counter[str]],
    strong_counts: dict[str, Counter[str]],
) -> None:
    fieldnames = ["lecture_id", "lecture_title", "duration", "chunk_count"]
    for topic in topics:
        fieldnames.append(f"{topic.topic_id}_hits")
        fieldnames.append(f"{topic.topic_id}_strong")
    lecture_ids = sorted(counts.keys(), key=lambda lecture_id: lectures.get(lecture_id, {}).get("title", lecture_id))
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for lecture_id in lecture_ids:
            lecture = lectures.get(lecture_id, {})
            row = {
                "lecture_id": lecture_id,
                "lecture_title": lecture.get("title", lecture_id),
                "duration": lecture.get("duration", ""),
                "chunk_count": lecture.get("chunk_count", ""),
            }
            for topic in topics:
                row[f"{topic.topic_id}_hits"] = counts[lecture_id].get(topic.topic_id, 0)
                row[f"{topic.topic_id}_strong"] = strong_counts[lecture_id].get(topic.topic_id, 0)
            writer.writerow(row)


def write_inventory_report(path: Path, inventory: dict[str, Any]) -> None:
    lines = [
        "# Knowledge Inventory",
        "",
        f"Generated at: `{inventory['generated_at']}`",
        f"Source root: `{inventory['source_root']}`",
        "",
        "## Purpose",
        "",
        "This inventory is the anti-loss layer: it maps every lecture chunk to a topic taxonomy before any bot or rulebook is built.",
        "The goal is to avoid overfitting the knowledge base to whichever topic we happened to search first.",
        "",
        "## Totals",
        "",
        f"- Lectures: {inventory['lectures']}",
        f"- Chunks scanned: {inventory['chunks']}",
        f"- Strong hit threshold: {inventory['strong_score_threshold']}",
        "",
        "## Coverage By Topic",
        "",
        "| Topic | Hits | Strong hits | Lectures | Strong lectures |",
        "|---|---:|---:|---:|---:|",
    ]
    for topic in sorted(inventory["topics"], key=lambda item: item["strong_hit_count"], reverse=True):
        lines.append(
            f"| {topic['title']} | {topic['hit_count']} | {topic['strong_hit_count']} | "
            f"{topic['lecture_count']} | {topic['strong_lecture_count']} |"
        )
    lines.extend(
        [
            "",
            "## Generated Files",
            "",
            "- `_knowledge_base/topic_coverage.json`: machine-readable full coverage with top references per topic.",
            "- `_knowledge_base/topic_coverage.csv`: compact topic coverage table.",
            "- `_knowledge_base/lecture_topic_matrix.csv`: lecture by topic matrix for spotting gaps.",
            "- `_knowledge_base/topic_packets/*.md`: human-readable evidence packs per topic.",
            "",
            "## How To Use This",
            "",
            "1. Pick a topic packet, not a random search query.",
            "2. Read top evidence and the reference table.",
            "3. Create or update a canonical rule card only after checking multiple lectures/time ranges.",
            "4. When a rule card is written, keep source chunk ids and timecodes attached.",
            "5. If a topic has too few strong hits, expand aliases in `knowledge_bot/knowledge_taxonomy.json` and rebuild inventory.",
            "",
            "## Next Command",
            "",
            "```powershell",
            "python .\\knowledge_bot\\build_knowledge_inventory.py",
            "```",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def write_topic_packets(
    output_dir: Path,
    topics: list[Topic],
    topic_hits: dict[str, list[dict[str, Any]]],
    chunks: list[dict[str, Any]],
    top_excerpts: int,
    max_refs: int,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    chunk_by_id = {chunk["chunk_id"]: chunk for chunk in chunks}
    for topic in topics:
        hits = topic_hits[topic.topic_id]
        strong_hits = [hit for hit in hits if hit["strong"]]
        path = output_dir / f"{safe_filename(topic.topic_id)}.md"
        lines = [
            f"# {topic.title}",
            "",
            topic.description,
            "",
            "## Coverage",
            "",
            f"- Total matching chunks: {len(hits)}",
            f"- Strong matching chunks: {len(strong_hits)}",
            f"- Lectures with matches: {len({hit['lecture_id'] for hit in hits})}",
            f"- Lectures with strong matches: {len({hit['lecture_id'] for hit in strong_hits})}",
            "",
            "## Aliases And Keywords",
            "",
            "Aliases: " + "; ".join(topic.aliases),
            "",
            "Keywords: " + "; ".join(topic.keywords),
            "",
            "## Top Evidence Excerpts",
            "",
        ]
        for index, hit in enumerate(hits[:top_excerpts], start=1):
            chunk = chunk_by_id.get(hit["chunk_id"], {})
            lines.extend(
                [
                    f"### {index}. {hit['lecture_title']} | {hit['start_time']} - {hit['end_time']} | score {hit['score']}",
                    "",
                    f"Chunk: `{hit['chunk_id']}`",
                    "",
                    make_excerpt(chunk.get("text", ""), topic.aliases),
                    "",
                ]
            )
        reference_hits = hits[:max_refs] if max_refs else hits
        lines.extend(
            [
                "## Reference Table",
                "",
                "| Score | Strong | Lecture | Time | Chunk | Keywords |",
                "|---:|:---:|---|---|---|---|",
            ]
        )
        for hit in reference_hits:
            keywords = ", ".join(hit.get("keywords", [])[:8])
            strong = "yes" if hit["strong"] else "no"
            lines.append(
                f"| {hit['score']} | {strong} | {hit['lecture_title']} | "
                f"{hit['start_time']} - {hit['end_time']} | `{hit['chunk_id']}` | {keywords} |"
            )
        if len(hits) > len(reference_hits):
            lines.extend(
                [
                    "",
                    f"Reference table is capped at {len(reference_hits)} rows. Full hit metadata is in `_knowledge_base/topic_coverage.json`.",
                ]
            )
        path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a topic inventory and evidence packs from lecture chunks.")
    parser.add_argument("--root", type=Path, default=default_root())
    parser.add_argument("--taxonomy", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--top-excerpts", type=int, default=DEFAULT_TOP_EXCERPTS)
    parser.add_argument("--max-refs", type=int, default=DEFAULT_MAX_REFS)
    args = parser.parse_args()

    root = args.root.resolve()
    taxonomy = (args.taxonomy or (root / "knowledge_bot" / "knowledge_taxonomy.json")).resolve()
    output = (args.output or (root / "_knowledge_base")).resolve()
    inventory = build_inventory(root, taxonomy, output, args.top_excerpts, args.max_refs)
    print(
        f"Inventory built: {len(inventory['topics'])} topics, {inventory['chunks']} chunks, "
        f"{inventory['lectures']} lectures."
    )
    for topic in sorted(inventory["topics"], key=lambda item: item["strong_hit_count"], reverse=True)[:8]:
        print(f"- {topic['topic_id']}: {topic['strong_hit_count']} strong / {topic['hit_count']} total")


if __name__ == "__main__":
    main()