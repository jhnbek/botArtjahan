from __future__ import annotations

import argparse
from pathlib import Path

from lecture_terms import expand_query_terms
from search_lectures import load_chunks, score_chunks


ANSWER_RULES = """Ты отвечаешь как помощник по лекциям трейдинга.

Правила ответа:
- Отвечай на русском.
- Используй только приведенные фрагменты лекций и явно отделяй выводы от цитируемого материала.
- Не выдумывай правила, которых нет в контексте.
- Если контекста мало, скажи, какой темы или таймкода не хватает.
- В каждом важном тезисе указывай источник: название лекции и таймкод.
- Когда есть противоречащие факторы, перечисляй их отдельно: что за пробой, что за ложный пробой, что усиливает, что ослабляет.
- Это учебный разбор методики, а не персональная финансовая рекомендация.
"""


def default_root() -> Path:
    return Path(__file__).resolve().parents[1]


def trim_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def build_prompt(question: str, root: Path, top: int, max_chars_per_chunk: int) -> str:
    index_path = root / "_knowledge_base" / "lecture_chunks.jsonl"
    if not index_path.exists():
        raise SystemExit(
            f"Index not found: {index_path}\n"
            "Build it first: python .\\knowledge_bot\\build_knowledge_base.py"
        )
    chunks = load_chunks(index_path)
    scored = score_chunks(chunks, question)[:top]
    if not scored:
        raise SystemExit("No relevant chunks found for this question.")

    lines = [
        "# Задача",
        "",
        question,
        "",
        "# Инструкция для ответа",
        "",
        ANSWER_RULES.strip(),
        "",
        "# Найденные фрагменты лекций",
        "",
    ]
    for index, (score, chunk) in enumerate(scored, start=1):
        keywords = ", ".join(chunk.get("keywords", [])[:10]) or "none"
        frames = chunk.get("frames", [])
        frame_line = ""
        if frames:
            frame_line = "\nКадры: " + "; ".join(f"{frame['time']} {frame['path']}" for frame in frames[:4])
        lines.extend(
            [
                f"## Фрагмент {index}",
                "",
                f"Источник: {chunk.get('lecture_title')} | {chunk.get('start_time')} - {chunk.get('end_time')} | {chunk.get('chunk_id')}",
                f"Score: {score:.2f}",
                f"Ключевые темы: {keywords}{frame_line}",
                "",
                trim_text(chunk.get("text", ""), max_chars_per_chunk),
                "",
            ]
        )

    query_terms = ", ".join(sorted(expand_query_terms(question)))
    lines.extend(
        [
            "# Контроль",
            "",
            f"Расширенные поисковые термины: {query_terms}",
            "Перед финальным ответом проверь, что каждый тезис можно привязать к одному из фрагментов выше.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build an LLM-ready prompt from retrieved lecture chunks.")
    parser.add_argument("question", nargs="+", help="Question for the lecture bot")
    parser.add_argument("--root", type=Path, default=default_root())
    parser.add_argument("--top", type=int, default=8)
    parser.add_argument("--max-chars-per-chunk", type=int, default=2200)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    question = " ".join(args.question)
    prompt = build_prompt(question, args.root.resolve(), args.top, args.max_chars_per_chunk)
    if args.out:
        args.out.write_text(prompt, encoding="utf-8")
        print(f"Wrote prompt: {args.out}")
    else:
        print(prompt)


if __name__ == "__main__":
    main()