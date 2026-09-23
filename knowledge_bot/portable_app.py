from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from make_rag_prompt import build_prompt
from search_lectures import expand_query_terms, format_result, load_chunks, score_chunks


APP_NAME = "KnowledgeBot"
SAFETY_NOTE = "read-only: no signals, no orders, no PnL, no paper/live trading"


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def resolve_root(raw_root: str | Path | None = None) -> Path:
    if raw_root:
        return Path(raw_root).expanduser().resolve()
    env_root = os.getenv("KNOWLEDGE_BOT_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()
    base = app_dir()
    for candidate in (base, *base.parents):
        if (candidate / "_knowledge_base").exists():
            return candidate
    return base


def kb_dir(root: Path) -> Path:
    return root / "_knowledge_base"


def long_path(path: Path) -> str:
    resolved = str(path.resolve())
    if os.name != "nt" or resolved.startswith("\\\\?\\"):
        return resolved
    if resolved.startswith("\\\\"):
        return "\\\\?\\UNC\\" + resolved.lstrip("\\")
    return "\\\\?\\" + resolved


def directory_stats(path: Path) -> dict[str, Any]:
    file_count = 0
    total_bytes = 0
    if path.exists():
        for dirpath, _, filenames in os.walk(long_path(path)):
            for filename in filenames:
                file_count += 1
                total_bytes += os.path.getsize(os.path.join(dirpath, filename))
    return {
        "path": str(path),
        "exists": path.exists(),
        "files": file_count,
        "bytes": total_bytes,
        "mb": round(total_bytes / 1024 / 1024, 2),
    }


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def print_status(args: argparse.Namespace) -> int:
    root = resolve_root(args.root)
    knowledge_base = kb_dir(root)
    stats = directory_stats(knowledge_base)
    build_report = knowledge_base / "build_report.md"
    inventory = knowledge_base / "knowledge_inventory.md"
    coverage_status = load_json(
        knowledge_base
        / "structured"
        / "consolidation"
        / "kb_coverage_audit"
        / "kb_coverage_audit_status.json"
    )
    layer6_status = load_json(
        knowledge_base
        / "structured"
        / "consolidation"
        / "layer6_golden_packet_validation"
        / "layer6_golden_packet_validation_status.json"
    )

    print(f"{APP_NAME} portable status")
    print(f"Root: {root}")
    print(f"Knowledge base: {knowledge_base}")
    print(f"Safety: {SAFETY_NOTE}")
    print("")
    print(f"KB exists: {stats['exists']}")
    print(f"KB files: {stats['files']}")
    print(f"KB size: {stats['mb']} MB")
    print(f"build_report.md: {build_report.exists()}")
    print(f"knowledge_inventory.md: {inventory.exists()}")

    counts = coverage_status.get("counts") or {}
    coverage = coverage_status.get("coverage_status_counts") or {}
    all_rows = coverage.get("all_rows") or {}
    if counts:
        print("")
        print("Canonical KB:")
        print(f"  CRD: {counts.get('crd')}")
        print(f"  FCD: {counts.get('fcd')}")
        print(f"  RSCD checklists: {counts.get('rscd_checklists')}")
        print(f"  RSCD items: {counts.get('rscd_items')}")
    if all_rows:
        print("")
        print("Coverage all_rows:")
        for key, value in all_rows.items():
            print(f"  {key}: {value}")
        print(f"  not_used_or_unverified: {coverage_status.get('not_used_or_unverified_count')}")
    if layer6_status:
        print("")
        print("Layer 6 golden:")
        print(f"  cases: {layer6_status.get('case_count')}")
        print(f"  assertions: {layer6_status.get('passed_assertion_count')}/{layer6_status.get('assertion_count')}")
        print(f"  ready: {layer6_status.get('layer6_golden_packet_validation_ready')}")
    return 0 if stats["exists"] else 2


def run_search(args: argparse.Namespace) -> int:
    root = resolve_root(args.root)
    index_path = Path(args.index).resolve() if args.index else kb_dir(root) / "lecture_chunks.jsonl"
    if not index_path.exists():
        print(f"Index not found: {index_path}", file=sys.stderr)
        return 2

    query = " ".join(args.query).strip()
    chunks = load_chunks(index_path)
    scored = score_chunks(chunks, query)[: args.top]
    query_terms = expand_query_terms(query)

    if args.json:
        print(json.dumps([{"score": round(score, 4), **chunk} for score, chunk in scored], ensure_ascii=False, indent=2))
        return 0

    print(f"Query: {query}")
    print(f"Index: {index_path}")
    print("")
    if not scored:
        print("No matches found.")
        return 1
    for result_index, (score, chunk) in enumerate(scored, start=1):
        print(f"## Result {result_index}")
        print(format_result(score, chunk, query_terms))
        print("")
    return 0


def run_prompt(args: argparse.Namespace) -> int:
    root = resolve_root(args.root)
    question = " ".join(args.question).strip()
    prompt = build_prompt(question, root, args.top, args.max_chars_per_chunk)
    if args.out:
        output_path = Path(args.out)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(prompt, encoding="utf-8")
        print(f"Wrote prompt: {output_path}")
    else:
        print(prompt)
    return 0


def run_reports(args: argparse.Namespace) -> int:
    root = resolve_root(args.root)
    knowledge_base = kb_dir(root)
    reports = [
        knowledge_base / "build_report.md",
        knowledge_base / "knowledge_inventory.md",
        knowledge_base / "structured" / "consolidation" / "kb_coverage_audit" / "kb_coverage_audit.md",
        knowledge_base / "structured" / "consolidation" / "layer6_golden_packet_validation" / "layer6_golden_packet_validation.md",
        knowledge_base / "structured" / "consolidation" / "methodology_coverage_map" / "methodology_coverage_map.md",
    ]
    for report in reports:
        print(f"{'OK' if report.exists() else 'MISSING'}  {report}")
    if args.open:
        for report in reports:
            if report.exists():
                os.startfile(report)  # type: ignore[attr-defined]
                break
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=APP_NAME,
        description="Portable read-only launcher for the full lecture knowledge base.",
    )
    parser.add_argument("--root", type=Path, default=None, help="Folder that contains _knowledge_base. Defaults to the exe folder.")
    subparsers = parser.add_subparsers(dest="command")

    status_parser = subparsers.add_parser("status", help="Show KB size, coverage and safety status.")
    status_parser.set_defaults(func=print_status)

    search_parser = subparsers.add_parser("search", help="Search lecture RAG chunks locally.")
    search_parser.add_argument("query", nargs="+", help="Search query, for example: БСУ БПУ уровень")
    search_parser.add_argument("--index", type=Path, default=None, help="Custom lecture_chunks.jsonl path.")
    search_parser.add_argument("--top", type=int, default=8)
    search_parser.add_argument("--json", action="store_true", help="Print raw JSON results.")
    search_parser.set_defaults(func=run_search)

    prompt_parser = subparsers.add_parser("prompt", help="Build an LLM-ready prompt from retrieved lecture chunks.")
    prompt_parser.add_argument("question", nargs="+", help="Question for the lecture KB.")
    prompt_parser.add_argument("--top", type=int, default=8)
    prompt_parser.add_argument("--max-chars-per-chunk", type=int, default=2200)
    prompt_parser.add_argument("--out", type=Path, default=None)
    prompt_parser.set_defaults(func=run_prompt)

    reports_parser = subparsers.add_parser("reports", help="Print important KB report paths.")
    reports_parser.add_argument("--open", action="store_true", help="Open the first available report in the default app.")
    reports_parser.set_defaults(func=run_reports)
    return parser


def wait_before_exit_if_double_clicked(args: argparse.Namespace) -> None:
    if args.command or not getattr(sys, "frozen", False) or not sys.stdin.isatty():
        return
    print("")
    try:
        input("Press Enter to close...")
    except EOFError:
        pass


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        exit_code = print_status(args)
        wait_before_exit_if_double_clicked(args)
        return exit_code
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())