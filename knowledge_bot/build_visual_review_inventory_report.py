"""Build a taxonomy and inventory report for completed visual reviews.

The completed manual visual pass is mostly methodology/scenario evidence, not
trade ground truth. This script converts it into a compact map for a future
situation analyzer: taxonomy coverage, blockers, exemplars, and guardrails.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BASE = Path("_knowledge_base/structured/consolidation/lecture_multimodal_calibration_candidates")
LEDGER = BASE / "manual_visual_review_notes.json"
OBSERVATIONS = BASE / "visual_knowledge_observations.jsonl"
OUTPUT_JSON = BASE / "visual_review_inventory_report.json"
OUTPUT_MD = BASE / "visual_review_inventory_report.md"
OUTPUT_TAXONOMY = BASE / "visual_review_taxonomy.json"


TAXONOMY: dict[str, dict[str, Any]] = {
    "level_quality": {
        "label": "Уровень и качество уровня",
        "bot_question": "Где сильный уровень, локальный ли он, чистый ли он и от какой базы построен?",
        "keywords": ["level", "уров", "support", "resistance", "границ", "basis", "касани", "tail", "хвост", "bsu", "bpu"],
    },
    "retest_false_breakout": {
        "label": "Ретест и ложный пробой",
        "bot_question": "Это ретест, ложный пробой, возврат за уровень или шум вокруг линии?",
        "keywords": ["false_breakout", "ложн", "retest", "ретест", "reclaim", "breakdown", "пробой", "trap"],
    },
    "compression_accumulation": {
        "label": "Поджатие, накопление, база",
        "bot_question": "Есть сжатие/накопление перед уровнем или цена просто резко прилетела?",
        "keywords": ["compression", "поджат", "накоп", "accum", "base", "база", "consolidation", "standing", "close near"],
    },
    "range_saw_wait": {
        "label": "Пила, диапазон, wait/invalid",
        "bot_question": "Ситуация торгуемая или уровень пилится и лучше ждать/пропустить?",
        "keywords": ["saw_range", "пил", "range", "диапазон", "wait", "invalid", "skip", "no trade", "middle", "середин"],
    },
    "room_to_move_atr": {
        "label": "Запас хода, ATR, left-side traffic",
        "bot_question": "Хватает места до следующей границы/препятствия с учетом ATR и R/R?",
        "keywords": ["room_to_move", "room", "atr", "атр", "left-side", "traffic", "запас", "ход", "risk/reward", "3r", "4 stops"],
    },
    "trend_relative_strength": {
        "label": "Тренд, поводырь, относительная сила/слабость",
        "bot_question": "Инструмент сильнее или слабее рынка/поводыря, и совпадает ли это с направлением?",
        "keywords": ["trend", "тренд", "relative", "strength", "weak", "сильн", "слаб", "btc", "spy", "s&p", "leader", "поводыр"],
    },
    "volume_liquidity": {
        "label": "Объём, ликвидность, оборот, tokenomics",
        "bot_question": "Хватает ликвидности и что означает видимый объем/оборот/капитализация?",
        "keywords": ["volume", "объем", "объём", "liquidity", "ликвид", "turnover", "оборот", "market cap", "supply", "tokenomics", "24h"],
    },
    "risk_position_sizing": {
        "label": "Риск, стоп, плечо, размер позиции",
        "bot_question": "Какой стоп, денежный риск, размер позиции, плечо и риск ликвидации?",
        "keywords": ["risk", "риск", "stop", "стоп", "position", "позици", "leverage", "плеч", "margin", "ликвид", "commission", "fee", "quantity"],
    },
    "orderbook_microstructure": {
        "label": "Стакан, лимиты, market/stop orders",
        "bot_question": "Что видно в стакане/ленте, а что является только намерением или исполнением?",
        "keywords": ["order book", "стакан", "limit", "лимит", "market order", "stop market", "tape", "bid", "ask", "depth"],
    },
    "timeframe_context": {
        "label": "Таймфреймы и старший контекст",
        "bot_question": "Какой таймфрейм рабочий, какой старший, и где искать подтверждение?",
        "keywords": ["timeframe", "тайм", "d1", "1d", "h1", "4h", "m5", "5m", "weekly", "monthly", "w/m", "htf", "ltf"],
    },
    "venue_instrument_consistency": {
        "label": "Биржа, spot/futures, инструмент",
        "bot_question": "Уровень построен на той бирже и типе инструмента, где будет сделка?",
        "keywords": ["exchange", "бирж", "venue", "spot", "futures", "perpetual", "binance", "bybit", "okx", "kucoin", "nyse", "nasdaq"],
    },
    "indicator_tool_context": {
        "label": "Индикаторы и инструменты разметки",
        "bot_question": "Индикатор помогает контексту или подменяет чтение цены?",
        "keywords": ["vwap", "sma", "macd", "pitchfork", "fibonacci", "indicator", "индик", "position tool", "магнит", "tradingview"],
    },
    "workflow_navigation": {
        "label": "Workflow, screener, watchlist, навигация",
        "bot_question": "Это рыночная ситуация или только рабочий процесс/поиск/экран навигации?",
        "keywords": ["workflow", "screener", "watchlist", "search", "navigation", "screen", "overlay", "browser", "finviz", "moomoo", "coingecko"],
    },
    "insufficient_or_mismatch": {
        "label": "Недостаточно данных / визуальное несоответствие",
        "bot_question": "Можно честно подтвердить тезис кадром или это mismatch/недостаточно данных?",
        "keywords": ["mismatch", "insufficient", "not visible", "не подтверж", "не видно", "unidentifiable", "no chart", "non-chart", "homepage", "screen_navigation"],
    },
}


INSTRUMENT_KEYS = {"instrument", "instruments", "ticker", "tickers", "ticker_on_screen", "tickers_on_screen", "actual_ticker_on_screen", "name", "platform", "venue", "venue_context"}


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def default_root() -> Path:
    return Path(__file__).resolve().parents[1]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def flatten(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(f"{key} {flatten(item)}" for key, item in value.items())
    if isinstance(value, list):
        return " ".join(flatten(item) for item in value)
    return str(value)


def compact(text: Any, limit: int | None = None) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if limit and len(cleaned) > limit:
        return cleaned[: limit - 1].rstrip() + "…"
    return cleaned


def md(value: Any) -> str:
    return compact(value).replace("|", "\\|")


def row_text(row: dict[str, Any]) -> str:
    parts = [
        row.get("knowledge_use"),
        row.get("review_status"),
        row.get("expert_verdict_summary"),
        row.get("next_action"),
        row.get("candidate_statement_excerpt"),
        row.get("candidate_quote_excerpt"),
        flatten(row.get("resolved_fields") or {}),
        flatten((row.get("candidate_features") or {}).get("scenario_candidates") or []),
    ]
    return " ".join(str(part) for part in parts if part).lower()


def classify(row: dict[str, Any]) -> list[str]:
    text = row_text(row)
    categories = [category for category, spec in TAXONOMY.items() if any(keyword.lower() in text for keyword in spec["keywords"])]
    guardrail_text = " ".join(
        str(part or "")
        for part in (row.get("knowledge_use"), row.get("review_status"), row.get("expert_verdict_summary"))
    ).lower()
    guardrail_terms = ("mismatch", "insufficient", "not visible", "no chart", "non-chart", "screen_navigation", "navigation", "не видно")
    if any(term in guardrail_text for term in guardrail_terms) and "insufficient_or_mismatch" not in categories:
        categories.append("insufficient_or_mismatch")
    if categories:
        return categories
    knowledge_use = str(row.get("knowledge_use") or "")
    if "scenario" in knowledge_use:
        return ["retest_false_breakout"]
    if "methodology" in knowledge_use:
        return ["level_quality"]
    return ["insufficient_or_mismatch"]


def review_class(row: dict[str, Any]) -> str:
    status = str(row.get("review_status") or "")
    decision = str(row.get("promotion_decision") or "")
    if decision == "promoted_to_ground_truth":
        return "promoted"
    if decision == "hold_for_ohlc_verification":
        return "hold"
    if status.startswith("partial"):
        return "partial"
    if status.startswith("rejected"):
        return "rejected"
    return "other"


def extract_instruments(row: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key, value in (row.get("resolved_fields") or {}).items():
        if key in INSTRUMENT_KEYS:
            values.extend(value if isinstance(value, list) else [value])
    values.extend((row.get("candidate_features") or {}).get("instrument_candidates") or [])
    cleaned: list[str] = []
    for value in values:
        for part in re.split(r"[,;/]", str(value)):
            item = compact(part, 80)
            if item and not item.isdigit():
                cleaned.append(item)
    return cleaned[:12]


def example_score(row: dict[str, Any], categories: list[str]) -> int:
    score = 0
    if review_class(row) in {"partial", "hold"}:
        score += 40
    if row.get("visual_confirmed") is True:
        score += 15
    if row.get("knowledge_use") in {"scenario_context_candidate", "methodology_level_evidence"}:
        score += 8
    resolved = row.get("resolved_fields") or {}
    if resolved.get("visual_context"):
        score += 8
    if resolved.get("visible_values"):
        score += min(8, len(resolved.get("visible_values") or []))
    if row.get("source_frames"):
        score += min(10, len(row["source_frames"]) * 3)
    if row.get("candidate_statement_excerpt"):
        score += 4
    score += min(6, len(categories) * 2)
    return score


def compact_example(row: dict[str, Any], categories: list[str], score: int) -> dict[str, Any]:
    resolved = row.get("resolved_fields") or {}
    return {
        "candidate_id": row.get("candidate_id"),
        "claim_id": row.get("claim_id"),
        "review_class": review_class(row),
        "review_status": row.get("review_status"),
        "knowledge_use": row.get("knowledge_use"),
        "taxonomy": categories,
        "score": score,
        "lecture_title": row.get("lecture_title"),
        "timecode": row.get("timecode"),
        "instruments": extract_instruments(row)[:6],
        "source_frames": row.get("source_frames") or [],
        "summary": compact(row.get("expert_verdict_summary"), 260),
        "scenario": compact(resolved.get("scenario"), 180),
        "visual_context": compact(resolved.get("visual_context"), 260),
        "blockers": row.get("promotion_blockers") or [],
        "next_action": compact(row.get("next_action"), 220),
    }


def build_inventory(rows: list[dict[str, Any]], ledger: dict[str, Any]) -> dict[str, Any]:
    category_counts: dict[str, Counter[str]] = {category: Counter() for category in TAXONOMY}
    category_examples: dict[str, list[dict[str, Any]]] = defaultdict(list)
    blocker_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    knowledge_counts: Counter[str] = Counter()
    lecture_counts: Counter[str] = Counter()
    instrument_counts: Counter[str] = Counter()
    timeframe_counts: Counter[str] = Counter()
    all_examples: list[dict[str, Any]] = []

    for row in rows:
        categories = classify(row)
        row_class = review_class(row)
        score = example_score(row, categories)
        example = compact_example(row, categories, score)
        all_examples.append(example)
        status_counts[str(row.get("review_status"))] += 1
        knowledge_counts[str(row.get("knowledge_use"))] += 1
        lecture_counts[str(row.get("lecture_title") or "unknown")] += 1
        for blocker in row.get("promotion_blockers") or []:
            blocker_counts[str(blocker)] += 1
        for instrument in extract_instruments(row):
            instrument_counts[instrument] += 1
        resolved = row.get("resolved_fields") or {}
        for key in ("timeframe", "timeframes", "chart_timeframe", "chart_timeframes", "timeframe_context"):
            value = resolved.get(key)
            if isinstance(value, list):
                timeframe_counts.update(str(item) for item in value)
            elif value:
                timeframe_counts[str(value)] += 1
        timeframe_counts.update(str(item) for item in (row.get("candidate_features") or {}).get("timeframes") or [])
        for category in categories:
            category_counts[category][row_class] += 1
            category_examples[category].append(example)

    taxonomy_report: dict[str, Any] = {}
    for category, spec in TAXONOMY.items():
        examples = sorted(category_examples[category], key=lambda item: (-item["score"], item["candidate_id"] or ""))
        counts = category_counts[category]
        taxonomy_report[category] = {
            "label": spec["label"],
            "bot_question": spec["bot_question"],
            "counts": dict(counts),
            "total": sum(counts.values()),
            "example_ids": [item["candidate_id"] for item in examples[:8]],
            "top_examples": examples[:5],
        }

    best_examples = sorted(
        [item for item in all_examples if item["review_class"] in {"partial", "hold"}],
        key=lambda item: (-item["score"], item["candidate_id"] or ""),
    )[:50]
    rejected_examples = sorted(
        [item for item in all_examples if item["review_class"] == "rejected"],
        key=lambda item: (-item["score"], item["candidate_id"] or ""),
    )[:30]
    return {
        "dataset_id": "visual_review_inventory_report_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sources": {"ledger": LEDGER.as_posix(), "observations": OBSERVATIONS.as_posix()},
        "summary": {
            "ledger_review_count": ledger.get("review_count"),
            "observation_count": len(rows),
            "partial_verified_count": ledger.get("partial_verified_count"),
            "rejected_count": ledger.get("rejected_count"),
            "hold_for_verification_count": ledger.get("hold_for_verification_count"),
            "promoted_count": ledger.get("promoted_count"),
            "visual_confirmed_count": sum(1 for row in rows if row.get("visual_confirmed") is True),
            "visual_unconfirmed_count": sum(1 for row in rows if row.get("visual_confirmed") is False),
            "joined_to_text_count": sum(1 for row in rows if row.get("has_text_join")),
        },
        "status_counts": dict(status_counts.most_common()),
        "knowledge_use_counts": dict(knowledge_counts.most_common()),
        "blocker_counts": dict(blocker_counts.most_common()),
        "top_lectures": dict(lecture_counts.most_common(30)),
        "top_instruments": dict(instrument_counts.most_common(50)),
        "top_timeframes": dict(timeframe_counts.most_common(30)),
        "taxonomy": taxonomy_report,
        "best_visual_examples": best_examples,
        "rejected_examples": rejected_examples,
        "analyzer_contract": {
            "input": ["instrument", "timeframe", "OHLC/window", "candidate levels", "trend/leader context", "ATR/room-to-move", "volume/liquidity", "screenshots if available"],
            "retrieval_keys": ["taxonomy", "instrument family", "timeframe", "scenario keywords", "blockers", "visual_context"],
            "output_fields": ["situation_class", "supporting_examples", "contradicting_examples", "missing_evidence", "trade_contract_completeness", "verdict: wait/observe/possible_setup/invalid/needs_ohlc"],
            "hard_gate": "Do not emit promoted trade ground truth unless instrument, date/session, direction, entry, stop, target, trigger, and OHLC/fill verification are all present.",
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Visual Review Inventory Report",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "## Executive Summary",
        "",
        f"- Reviewed observations: **{summary['observation_count']} / {summary['ledger_review_count']}**",
        f"- Partial visual/methodology confirmations: **{summary['partial_verified_count']}**",
        f"- Rejected / mismatch / insufficient evidence rows: **{summary['rejected_count']}**",
        f"- Hold for OHLC verification: **{summary['hold_for_verification_count']}**",
        f"- Promoted ground-truth trades: **{summary['promoted_count']}**",
        f"- Visual confirmed rows: **{summary['visual_confirmed_count']}**",
        "",
        "The reviewed layer is a verified scenario/methodology corpus, not a trade-entry corpus. It is useful for trader-like situation analysis because it teaches context recognition and missing-evidence discipline while preserving the hard rule that incomplete trade contracts are not ground truth.",
        "",
        "## Situation Taxonomy",
        "",
        "| Taxonomy | Total hits | Partial/Hold | Rejected | Bot question | Example IDs |",
        "|---|---:|---:|---:|---|---|",
    ]
    for category, item in sorted(report["taxonomy"].items(), key=lambda pair: (-pair[1]["total"], pair[0])):
        counts = item["counts"]
        examples = ", ".join(f"`{candidate_id}`" for candidate_id in item["example_ids"][:5] if candidate_id)
        partial_hold = counts.get("partial", 0) + counts.get("hold", 0)
        lines.append(f"| {md(item['label'])} | {item['total']} | {partial_hold} | {counts.get('rejected', 0)} | {md(item['bot_question'])} | {examples} |")
    lines.extend(["", "## Blocker Map", "", "| Blocker | Count | Analyzer implication |", "|---|---:|---|"])
    implications = {
        "claim_type_not_direct_trade_case": "Use as methodology/context retrieval, not executable signal.",
        "missing_exact_decision_date": "Require date/session anchoring before OHLC validation.",
        "relative_date_requires_video/session_anchor": "Resolve relative cues before dated evidence.",
        "branching_direction_needs_verdict_side": "Ask for explicit side/verdict; do not infer long/short automatically.",
        "missing_entry_stop_target_trigger": "Block trade-contract completeness.",
        "missing_ohlc_fill_verification": "Needs market-data/fill validation before promotion.",
    }
    for blocker, count in list(report["blocker_counts"].items())[:20]:
        lines.append(f"| `{md(blocker)}` | {count} | {md(implications.get(blocker, 'Use as a missing-evidence feature in analyzer output.'))} |")
    lines.extend(["", "## Knowledge Use Coverage", "", "| Knowledge use | Count |", "|---|---:|"])
    for knowledge_use, count in report["knowledge_use_counts"].items():
        lines.append(f"| `{md(knowledge_use)}` | {count} |")
    lines.extend(["", "## Top Exemplar Observations", "", "These are high-signal partial/hold examples for retrieval templates. They should seed the first situation analyzer, not a buy/sell model.", "", "| ID | Taxonomy | Lecture | Instruments | Summary |", "|---|---|---|---|---|"])
    for item in report["best_visual_examples"][:30]:
        labels = ", ".join(report["taxonomy"][category]["label"] for category in item["taxonomy"] if category in report["taxonomy"])
        instruments = ", ".join(item["instruments"][:4])
        summary_text = item["summary"] or item["visual_context"] or item["scenario"]
        lines.append(f"| `{md(item['candidate_id'])}` | {md(labels)} | {md(item['lecture_title'])} {md(item['timecode'])} | {md(instruments)} | {md(summary_text)} |")
    lines.extend(["", "## Rejected Pattern Examples", "", "These rows are useful negative evidence: they teach the bot when not to force a scenario from weak visuals.", "", "| ID | Status | Main taxonomy | Why useful |", "|---|---|---|---|"])
    for item in report["rejected_examples"][:20]:
        category = item["taxonomy"][0] if item["taxonomy"] else "insufficient_or_mismatch"
        why = item["summary"] or item["visual_context"] or item["next_action"]
        lines.append(f"| `{md(item['candidate_id'])}` | `{md(item['review_status'])}` | {md(report['taxonomy'].get(category, {}).get('label', category))} | {md(why)} |")
    contract = report["analyzer_contract"]
    lines.extend(["", "## Analyzer Contract Draft", "", "Input features:"])
    lines.extend(f"- `{item}`" for item in contract["input"])
    lines.extend(["", "Retrieval keys:"])
    lines.extend(f"- `{item}`" for item in contract["retrieval_keys"])
    lines.extend(["", "Output fields:"])
    lines.extend(f"- `{item}`" for item in contract["output_fields"])
    lines.extend(["", f"Hard gate: **{contract['hard_gate']}**", "", "## Next Build Step", "", "Build a read-only `situation_analyzer` prototype that takes a text/OHLC/chart description, assigns taxonomy labels, retrieves 5-10 similar reviewed observations, lists missing evidence, and returns `wait / observe / possible_setup / invalid / needs_ohlc` without producing trade ground truth.", ""])
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build visual review inventory taxonomy and report.")
    parser.add_argument("--root", type=Path, default=default_root())
    return parser.parse_args()


def main() -> int:
    configure_stdio()
    root = parse_args().root.resolve()
    ledger = json.loads((root / LEDGER).read_text(encoding="utf-8"))
    rows = read_jsonl(root / OBSERVATIONS)
    if len(rows) != ledger.get("review_count"):
        raise RuntimeError(f"Observation count {len(rows)} does not match ledger review_count {ledger.get('review_count')}")
    report = build_inventory(rows, ledger)
    (root / OUTPUT_JSON).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (root / OUTPUT_TAXONOMY).write_text(json.dumps(report["taxonomy"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (root / OUTPUT_MD).write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({
        "observations": report["summary"]["observation_count"],
        "taxonomy_categories": len(report["taxonomy"]),
        "best_examples": len(report["best_visual_examples"]),
        "rejected_examples": len(report["rejected_examples"]),
        "outputs": [OUTPUT_JSON.as_posix(), OUTPUT_MD.as_posix(), OUTPUT_TAXONOMY.as_posix()],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())