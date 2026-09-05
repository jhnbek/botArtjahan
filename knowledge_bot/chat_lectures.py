from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:  # Prompt-only mode can run before optional packages are installed.
    load_dotenv = None

from make_rag_prompt import ANSWER_RULES
from search_lectures import load_chunks, score_chunks


DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_MAX_CONTEXT_CHARS = 16000
DEFAULT_MAX_CHARS_PER_CHUNK = 2200


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def default_root() -> Path:
    return Path(__file__).resolve().parents[1]


def trim_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def ensure_index(root: Path) -> Path:
    index_path = root / "_knowledge_base" / "lecture_chunks.jsonl"
    if not index_path.exists():
        raise SystemExit(
            f"Index not found: {index_path}\n"
            "Build it first: python .\\knowledge_bot\\build_knowledge_base.py"
        )
    return index_path


def build_context_blocks(
    scored_chunks: list[tuple[float, dict[str, Any]]],
    max_chars_per_chunk: int,
    max_context_chars: int,
) -> tuple[str, list[dict[str, Any]]]:
    blocks: list[str] = []
    sources: list[dict[str, Any]] = []
    current_size = 0
    for result_index, (score, chunk) in enumerate(scored_chunks, start=1):
        keywords = ", ".join(chunk.get("keywords", [])[:10]) or "none"
        frames = chunk.get("frames", [])
        frame_line = ""
        if frames:
            frame_line = "\nКадры: " + "; ".join(
                f"{frame['time']} {frame['path']}" for frame in frames[:4]
            )
        text = trim_text(chunk.get("text", ""), max_chars_per_chunk)
        block = "\n".join(
            [
                f"## Фрагмент {result_index}",
                f"Источник: {chunk.get('lecture_title')} | {chunk.get('start_time')} - {chunk.get('end_time')} | {chunk.get('chunk_id')}",
                f"Score: {score:.2f}",
                f"Ключевые темы: {keywords}{frame_line}",
                "",
                text,
            ]
        )
        if blocks and current_size + len(block) > max_context_chars:
            break
        blocks.append(block)
        current_size += len(block)
        sources.append(
            {
                "score": round(score, 2),
                "chunk_id": chunk.get("chunk_id"),
                "lecture_title": chunk.get("lecture_title"),
                "start_time": chunk.get("start_time"),
                "end_time": chunk.get("end_time"),
                "keywords": chunk.get("keywords", []),
                "frames": frames[:4],
            }
        )
    return "\n\n".join(blocks), sources


def build_messages(question: str, context: str) -> list[dict[str, str]]:
    system_prompt = "\n\n".join(
        [
            "Ты локальный RAG-бот по транскрибированным лекциям трейдинга.",
            ANSWER_RULES.strip(),
            "Формат ответа: сначала короткий прямой ответ, затем правила/условия, затем источники. Не давай персональных инвестиционных рекомендаций.",
        ]
    )
    user_prompt = "\n\n".join(
        [
            "# Вопрос",
            question,
            "# Контекст из лекций",
            context,
            "# Задача",
            "Ответь на вопрос строго по контексту. Если в контексте недостаточно материала, скажи это явно и перечисли, какие фрагменты надо найти дополнительно.",
        ]
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_prompt_document(question: str, context: str) -> str:
    return "\n\n".join(
        [
            "# Вопрос",
            question,
            "# Инструкция",
            ANSWER_RULES.strip(),
            "# Контекст из лекций",
            context,
            "# Что сделать",
            "Ответь строго по контексту, с источниками по лекциям и таймкодам.",
        ]
    )


def post_json(url: str, headers: dict[str, str], payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_text = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        error_body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {error.code} from {url}: {error_body}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Cannot reach {url}: {error}") from error
    return json.loads(response_text)


def call_openai_compatible(
    messages: list[dict[str, str]],
    model: str,
    base_url: str,
    api_key: str | None,
    timeout: int,
    temperature: float,
) -> str:
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    response = post_json(url, headers, payload, timeout)
    choices = response.get("choices") or []
    if not choices:
        raise RuntimeError(f"OpenAI-compatible response has no choices: {response}")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if not content:
        raise RuntimeError(f"OpenAI-compatible response has no message content: {response}")
    return str(content).strip()


def call_ollama(
    messages: list[dict[str, str]],
    model: str,
    base_url: str,
    timeout: int,
    temperature: float,
) -> str:
    url = base_url.rstrip("/") + "/api/chat"
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": temperature},
    }
    response = post_json(url, {"Content-Type": "application/json"}, payload, timeout)
    message = response.get("message") or {}
    content = message.get("content")
    if not content:
        raise RuntimeError(f"Ollama response has no message content: {response}")
    return str(content).strip()


def resolve_provider(provider: str) -> str:
    if provider != "auto":
        return provider
    if os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_BASE_URL"):
        return "openai"
    if os.getenv("OLLAMA_MODEL"):
        return "ollama"
    return "prompt"


def answer_question(
    question: str,
    chunks: list[dict[str, Any]],
    provider: str,
    top: int,
    max_chars_per_chunk: int,
    max_context_chars: int,
    model: str | None,
    timeout: int,
    temperature: float,
    save_context: Path | None,
    show_sources: bool,
) -> str:
    scored_chunks = score_chunks(chunks, question)[:top]
    if not scored_chunks:
        return "Не нашел релевантные фрагменты в локальной базе лекций. Попробуй переформулировать вопрос или добавить термин из лекций."

    context, sources = build_context_blocks(scored_chunks, max_chars_per_chunk, max_context_chars)
    prompt_document = build_prompt_document(question, context)
    if save_context:
        save_context.parent.mkdir(parents=True, exist_ok=True)
        save_context.write_text(prompt_document, encoding="utf-8")

    resolved_provider = resolve_provider(provider)
    messages = build_messages(question, context)

    if resolved_provider == "prompt":
        answer = prompt_document
    elif resolved_provider == "openai":
        openai_model = model or os.getenv("OPENAI_MODEL") or "gpt-4.1-mini"
        base_url = os.getenv("OPENAI_BASE_URL") or DEFAULT_OPENAI_BASE_URL
        api_key = os.getenv("OPENAI_API_KEY")
        answer = call_openai_compatible(messages, openai_model, base_url, api_key, timeout, temperature)
    elif resolved_provider == "ollama":
        ollama_model = model or os.getenv("OLLAMA_MODEL")
        if not ollama_model:
            raise RuntimeError("Ollama model is not set. Use --model or set OLLAMA_MODEL.")
        base_url = os.getenv("OLLAMA_BASE_URL") or DEFAULT_OLLAMA_BASE_URL
        answer = call_ollama(messages, ollama_model, base_url, timeout, temperature)
    else:
        raise RuntimeError(f"Unknown provider: {provider}")

    if show_sources and resolved_provider != "prompt":
        answer = answer.rstrip() + "\n\nИсточники retrieval:\n" + format_sources(sources)
    return answer


def format_sources(sources: list[dict[str, Any]]) -> str:
    lines = []
    for source_index, source in enumerate(sources, start=1):
        lines.append(
            f"{source_index}. {source['lecture_title']} | {source['start_time']} - {source['end_time']} | "
            f"{source['chunk_id']} | score {source['score']}"
        )
    return "\n".join(lines)


def run_interactive(args: argparse.Namespace, chunks: list[dict[str, Any]]) -> None:
    resolved_provider = resolve_provider(args.provider)
    print(f"Lecture bot готов. Provider: {resolved_provider}. Для выхода: exit / quit / q.")
    while True:
        try:
            question = input("\nТы: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nВыход.")
            return
        if not question:
            continue
        if question.lower() in {"exit", "quit", "q", "выход"}:
            print("Выход.")
            return
        try:
            answer = answer_question(
                question=question,
                chunks=chunks,
                provider=args.provider,
                top=args.top,
                max_chars_per_chunk=args.max_chars_per_chunk,
                max_context_chars=args.max_context_chars,
                model=args.model,
                timeout=args.timeout,
                temperature=args.temperature,
                save_context=None,
                show_sources=args.show_sources,
            )
        except Exception as error:
            print(f"Ошибка: {error}")
            continue
        print("\nБот:\n" + answer)


def main() -> None:
    configure_stdio()
    parser = argparse.ArgumentParser(description="Ask questions against the local lecture knowledge base.")
    parser.add_argument("question", nargs="*", help="Question for the lecture bot. Omit for interactive mode.")
    parser.add_argument("--root", type=Path, default=default_root())
    parser.add_argument("--provider", choices=["auto", "prompt", "openai", "ollama"], default="auto")
    parser.add_argument("--model", default=None, help="Model name for OpenAI-compatible API or Ollama.")
    parser.add_argument("--top", type=int, default=8)
    parser.add_argument("--max-chars-per-chunk", type=int, default=DEFAULT_MAX_CHARS_PER_CHUNK)
    parser.add_argument("--max-context-chars", type=int, default=DEFAULT_MAX_CONTEXT_CHARS)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--save-context", type=Path, default=None)
    parser.add_argument("--show-sources", dest="show_sources", action="store_true", default=True)
    parser.add_argument("--no-sources", dest="show_sources", action="store_false")
    args = parser.parse_args()

    root = args.root.resolve()
    if load_dotenv is not None:
        load_dotenv(root / ".env", override=False)
    index_path = ensure_index(root)
    chunks = load_chunks(index_path)
    question = " ".join(args.question).strip()

    if not question:
        run_interactive(args, chunks)
        return

    try:
        answer = answer_question(
            question=question,
            chunks=chunks,
            provider=args.provider,
            top=args.top,
            max_chars_per_chunk=args.max_chars_per_chunk,
            max_context_chars=args.max_context_chars,
            model=args.model,
            timeout=args.timeout,
            temperature=args.temperature,
            save_context=args.save_context,
            show_sources=args.show_sources,
        )
    except Exception as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    print(answer)


if __name__ == "__main__":
    main()
