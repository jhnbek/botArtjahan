from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from search_lectures import load_chunks


DEFAULT_TOP_EVIDENCE = 18
DEFAULT_MAX_EXCERPT_CHARS = 900


def default_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9а-яА-ЯёЁ_-]+", "_", value).strip("_")
    return cleaned or "topic"


def trim_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def source_line(hit: dict[str, Any]) -> str:
    return f"{hit['lecture_title']} | {hit['start_time']} - {hit['end_time']} | `{hit['chunk_id']}` | score {hit['score']}"


def build_rule_card(topic: dict[str, Any], chunk_by_id: dict[str, dict[str, Any]], top_evidence: int, max_excerpt_chars: int) -> str:
    top_hits = topic.get("top_hits", [])[:top_evidence]
    lines = [
        f"# {topic['title']}",
        "",
        "Status: draft skeleton. Needs manual distillation from the evidence below.",
        "",
        "## Purpose",
        "",
        topic.get("description", ""),
        "",
        "## Coverage Snapshot",
        "",
        f"- Matching chunks: {topic.get('hit_count', 0)}",
        f"- Strong matching chunks: {topic.get('strong_hit_count', 0)}",
        f"- Lectures with matches: {topic.get('lecture_count', 0)}",
        f"- Lectures with strong matches: {topic.get('strong_lecture_count', 0)}",
        "",
        "## Canonical Rule",
        "",
        "TODO: Write the rule only after checking multiple evidence chunks. Keep it short and operational.",
        "",
        "## Timeframe",
        "",
        "TODO: Specify whether this is daily context, H1 confirmation, M5/M15 execution, or cross-timeframe logic.",
        "",
        "## Required Conditions",
        "",
        "- TODO",
        "",
        "## Strengthening Factors",
        "",
        "- TODO",
        "",
        "## Weakening Factors / Contradictions",
        "",
        "- TODO",
        "",
        "## Invalidation / When The Model Breaks",
        "",
        "- TODO",
        "",
        "## Entry Logic",
        "",
        "- TODO",
        "",
        "## Stop Logic",
        "",
        "- TODO",
        "",
        "## Take / Exit Logic",
        "",
        "- TODO",
        "",
        "## Checklist Version",
        "",
        "Use this section later for the scenario/checklist assistant.",
        "",
        "- [ ] Level is identified and justified",
        "- [ ] Direction/scenario is separated from entry trigger",
        "- [ ] Required conditions are present",
        "- [ ] Strengthening factors are listed",
        "- [ ] Contradictions are listed",
        "- [ ] Logical stop location exists",
        "- [ ] Invalidation is clear",
        "- [ ] Missing information is stated",
        "",
        "## Evidence To Distill",
        "",
    ]
    for index, hit in enumerate(top_hits, start=1):
        chunk = chunk_by_id.get(hit["chunk_id"], {})
        lines.extend(
            [
                f"### Evidence {index}",
                "",
                source_line(hit),
                "",
                trim_text(chunk.get("text", ""), max_excerpt_chars),
                "",
            ]
        )
    lines.extend(
        [
            "## Source Reference List",
            "",
            "The complete topic hit metadata is in `_knowledge_base/topic_coverage.json`; the human evidence pack is in `_knowledge_base/topic_packets/`.",
            "",
        ]
    )
    for hit in top_hits:
        lines.append(f"- {source_line(hit)}")
    lines.extend(
        [
            "",
            "## Distillation Notes",
            "",
            "TODO: When this card is manually distilled, write unclear terms, edge cases, and conflicts here instead of forcing a fake clean rule.",
            "",
        ]
    )
    return "\n".join(lines)


def build_rulebook(root: Path, output_dir: Path, top_evidence: int, max_excerpt_chars: int) -> dict[str, Any]:
    knowledge_dir = root / "_knowledge_base"
    coverage_path = knowledge_dir / "topic_coverage.json"
    chunks_path = knowledge_dir / "lecture_chunks.jsonl"
    if not coverage_path.exists():
        raise SystemExit("Build inventory first: python .\\knowledge_bot\\build_knowledge_inventory.py")
    if not chunks_path.exists():
        raise SystemExit("Build chunks first: python .\\knowledge_bot\\build_knowledge_base.py")

    coverage = load_json(coverage_path)
    chunks = load_chunks(chunks_path)
    chunk_by_id = {chunk["chunk_id"]: chunk for chunk in chunks}
    output_dir.mkdir(parents=True, exist_ok=True)

    index_lines = [
        "# Rulebook Index",
        "",
        f"Generated at: `{datetime.now(timezone.utc).isoformat()}`",
        "",
        "These files are draft skeletons, not final rules. They are designed to prevent knowledge loss while the lectures are distilled.",
        "",
        "## Cards",
        "",
    ]
    card_records = []
    for topic in coverage.get("topics", []):
        filename = f"{safe_filename(topic['topic_id'])}.md"
        card_path = output_dir / filename
        card_path.write_text(build_rule_card(topic, chunk_by_id, top_evidence, max_excerpt_chars), encoding="utf-8")
        card_records.append({"topic_id": topic["topic_id"], "title": topic["title"], "file": filename})
        index_lines.append(f"- [{topic['title']}]({filename})")

    index_lines.extend(
        [
            "",
            "## Distillation Order",
            "",
            "Recommended order:",
            "",
            "1. `core_trade_framework`",
            "2. `level_selection_strength`",
            "3. `timeframe_workflow`",
            "4. `breakout_preconditions`",
            "5. `false_breakout_reversal`",
            "6. `near_far_retest`",
            "7. `fixation_return_entry`",
            "8. `risk_stop_take`",
            "9. Entry-model-specific cards such as `bsu_bpu_entry`, `tbx_entry_models`, formations, and trade reviews.",
            "",
        ]
    )
    (output_dir / "README.md").write_text("\n".join(index_lines), encoding="utf-8")
    return {"cards": card_records, "output_dir": str(output_dir)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Create draft rulebook cards from the knowledge inventory.")
    parser.add_argument("--root", type=Path, default=default_root())
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--top-evidence", type=int, default=DEFAULT_TOP_EVIDENCE)
    parser.add_argument("--max-excerpt-chars", type=int, default=DEFAULT_MAX_EXCERPT_CHARS)
    args = parser.parse_args()

    root = args.root.resolve()
    output = (args.output or (root / "_knowledge_base" / "rulebook")).resolve()
    result = build_rulebook(root, output, args.top_evidence, args.max_excerpt_chars)
    print(f"Rulebook skeleton built: {len(result['cards'])} cards")
    print(f"Output: {result['output_dir']}")


if __name__ == "__main__":
    main()