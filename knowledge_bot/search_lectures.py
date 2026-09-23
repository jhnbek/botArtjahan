from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

from lecture_terms import TERM_ALIASES, expand_query_terms, normalize_text, tokenize


def default_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_chunks(path: Path) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                chunks.append(json.loads(line))
    return chunks


def document_tokens(chunk: dict[str, Any]) -> list[str]:
    parts = [
        chunk.get("lecture_title", ""),
        " ".join(chunk.get("keywords", [])),
        chunk.get("text", ""),
    ]
    return tokenize(" ".join(parts))


def build_idf(token_counters: list[Counter[str]]) -> dict[str, float]:
    document_count = len(token_counters)
    df: Counter[str] = Counter()
    for counter in token_counters:
        df.update(counter.keys())
    return {token: math.log((document_count + 1) / (freq + 0.5)) + 1.0 for token, freq in df.items()}


def phrase_candidates(query: str) -> list[str]:
    normalized = normalize_text(query)
    phrases = [normalized]
    for aliases in TERM_ALIASES.values():
        if any(normalize_text(alias) in normalized for alias in aliases):
            phrases.extend(normalize_text(alias) for alias in aliases if len(normalize_text(alias)) > 3)
    seen = set()
    result = []
    for phrase in phrases:
        if phrase and phrase not in seen:
            seen.add(phrase)
            result.append(phrase)
    return result


def score_chunks(chunks: list[dict[str, Any]], query: str) -> list[tuple[float, dict[str, Any]]]:
    query_terms = expand_query_terms(query)
    if not query_terms:
        query_terms = set(tokenize(query))
    token_counters = [Counter(document_tokens(chunk)) for chunk in chunks]
    idf = build_idf(token_counters)
    avg_len = sum(sum(counter.values()) for counter in token_counters) / max(1, len(token_counters))
    phrases = phrase_candidates(query)
    scored: list[tuple[float, dict[str, Any]]] = []

    for chunk, counter in zip(chunks, token_counters):
        doc_len = max(1, sum(counter.values()))
        score = 0.0
        for term in query_terms:
            freq = counter.get(term, 0)
            if not freq:
                continue
            denominator = freq + 1.2 * (1 - 0.75 + 0.75 * doc_len / max(1.0, avg_len))
            score += idf.get(term, 1.0) * (freq * 2.2 / denominator)

        normalized_text = normalize_text(chunk.get("text", ""))
        normalized_title = normalize_text(chunk.get("lecture_title", ""))
        for phrase in phrases:
            if len(phrase.split()) > 1 and phrase in normalized_text:
                score += 4.0
            elif phrase in normalized_title:
                score += 2.5

        keywords = set(chunk.get("keywords", []))
        for keyword, aliases in TERM_ALIASES.items():
            if keyword in keywords and any(token in query_terms for alias in aliases for token in tokenize(alias)):
                score += 1.2

        if score > 0:
            scored.append((score, chunk))
    return sorted(scored, key=lambda item: item[0], reverse=True)


def make_excerpt(text: str, query_terms: set[str], max_chars: int = 520) -> str:
    if len(text) <= max_chars:
        return text
    normalized = normalize_text(text)
    positions = [normalized.find(term) for term in query_terms if len(term) > 2 and normalized.find(term) >= 0]
    if positions:
        center = min(positions)
        start = max(0, center - max_chars // 3)
    else:
        start = 0
    end = min(len(text), start + max_chars)
    start = max(0, end - max_chars)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(text) else ""
    return prefix + text[start:end].strip() + suffix


def format_result(score: float, chunk: dict[str, Any], query_terms: set[str]) -> str:
    keywords = ", ".join(chunk.get("keywords", [])[:8]) or "none"
    frames = chunk.get("frames", [])
    frame_text = ""
    if frames:
        frame_text = "\nFrames: " + "; ".join(f"{frame['time']} {frame['path']}" for frame in frames[:3])
    excerpt = make_excerpt(chunk.get("text", ""), query_terms)
    return (
        f"Score: {score:.2f}\n"
        f"{chunk.get('lecture_title')} | {chunk.get('start_time')} - {chunk.get('end_time')} | {chunk.get('chunk_id')}\n"
        f"Keywords: {keywords}{frame_text}\n"
        f"{excerpt}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Search local lecture RAG chunks without external dependencies.")
    parser.add_argument("query", nargs="+", help="Search query, for example: БСУ БПУ уровень")
    parser.add_argument("--root", type=Path, default=default_root())
    parser.add_argument("--index", type=Path, default=None)
    parser.add_argument("--top", type=int, default=8)
    parser.add_argument("--json", action="store_true", help="Print raw JSON results")
    args = parser.parse_args()

    root = args.root.resolve()
    index_path = args.index or (root / "_knowledge_base" / "lecture_chunks.jsonl")
    if not index_path.exists():
        raise SystemExit(
            f"Index not found: {index_path}\n"
            "Build it first: python .\\knowledge_bot\\build_knowledge_base.py"
        )

    query = " ".join(args.query)
    chunks = load_chunks(index_path)
    scored = score_chunks(chunks, query)[: args.top]
    query_terms = expand_query_terms(query)

    if args.json:
        output = [{"score": round(score, 4), **chunk} for score, chunk in scored]
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return

    if not scored:
        print("No matches found.")
        return

    print(f"Query: {query}")
    print(f"Index: {index_path}")
    print("")
    for index, (score, chunk) in enumerate(scored, start=1):
        print(f"## Result {index}")
        print(format_result(score, chunk, query_terms))
        print("")


if __name__ == "__main__":
    main()