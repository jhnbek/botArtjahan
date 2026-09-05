"""Read-only prototype for trader-like situation analysis.

The analyzer uses the completed manual visual review corpus as retrieval memory.
It classifies a market description into the visual-review taxonomy, retrieves
similar reviewed examples, lists missing trade-contract evidence, and emits a
bounded verdict. It never promotes a situation to ground truth.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from build_visual_review_inventory_report import (
        BASE,
        OBSERVATIONS,
        TAXONOMY,
        classify as classify_observation,
        compact,
        extract_instruments,
        flatten,
        read_jsonl,
        review_class,
        row_text,
    )
except ModuleNotFoundError:
    from knowledge_bot.build_visual_review_inventory_report import (
        BASE,
        OBSERVATIONS,
        TAXONOMY,
        classify as classify_observation,
        compact,
        extract_instruments,
        flatten,
        read_jsonl,
        review_class,
        row_text,
    )


DEFAULT_OUTPUT = BASE / "situation_analyzer_last_report.json"
FIELD_ORDER = [
    "instrument",
    "venue",
    "date_session",
    "timeframe",
    "direction",
    "level_area",
    "entry",
    "stop",
    "target",
    "trigger",
    "ohlc_verification",
]
FIELD_LABELS = {
    "instrument": "точный инструмент/тикер",
    "venue": "биржа/venue и тип инструмента",
    "date_session": "дата или сессия решения",
    "timeframe": "рабочий таймфрейм",
    "direction": "направление long/short",
    "level_area": "уровень или рабочая зона",
    "entry": "entry",
    "stop": "stop",
    "target": "target/management",
    "trigger": "trigger/условие входа",
    "ohlc_verification": "OHLC/fill verification",
}
FIELD_HINTS = {
    "instrument": "Без точного инструмента нельзя связать ситуацию с рынком и примерами.",
    "venue": "Уровни и ликвидность могут отличаться между spot/futures и биржами.",
    "date_session": "Нужна привязка к времени, иначе OHLC-проверка невозможна.",
    "timeframe": "Контекст D1/H1 и trigger M5/M15 нельзя смешивать без явного ТФ.",
    "direction": "Analyzer не должен угадывать long/short по картинке или словам.",
    "level_area": "Сетап Герчика начинается с зоны/уровня, а не с желания войти.",
    "entry": "Без entry нет trade contract.",
    "stop": "Без stop нельзя оценить риск и R/R.",
    "target": "Без target или management rule нельзя оценить запас хода.",
    "trigger": "Нужно условие входа: пробой, закрепление, возврат, бар F и т.п.",
    "ohlc_verification": "Даже полный план требует независимой OHLC/fill проверки.",
}
FIELD_KEYWORDS = {
    "instrument": ["instrument", "ticker", "symbol", "тикер", "инструмент", "монета", "акция"],
    "venue": ["venue", "exchange", "binance", "bybit", "okx", "kucoin", "nyse", "nasdaq", "spot", "futures", "биржа", "фьючерс"],
    "date_session": ["date", "session", "дата", "сессия", "сегодня", "вчера", "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"],
    "timeframe": ["timeframe", "таймфрейм", "тайм", "tf", "m5", "m15", "h1", "h4", "d1", "1d", "4h", "5m", "15m", "htf", "ltf"],
    "direction": ["long", "short", "buy", "sell", "лонг", "шорт", "покуп", "продаж", "вверх", "вниз"],
    "level_area": ["level", "support", "resistance", "уров", "поддерж", "сопротив", "зона", "границ", "high", "low"],
    "entry": ["entry", "вход", "enter", "buy stop", "sell stop", "limit entry", "точка входа", "твх"],
    "stop": ["stop", "stop-loss", "sl", "стоп", "стоплосс", "стоп-лосс"],
    "target": ["target", "take", "tp", "тейк", "цель", "3r", "2r", "4r", "management", "сопровожд"],
    "trigger": ["trigger", "триггер", "breakout", "пробой", "закреп", "reclaim", "close above", "close below", "bar f", "бар f", "бпу"],
    "ohlc_verification": ["ohlc", "fill", "executed", "execution", "исполн", "свеч", "исторические данные", "проверка"],
}
NEGATIVE_FIELD_PATTERNS = {
    "ohlc_verification": [
        r"(?:ohlc|fill|исполн|свеч|историческ|провер)[\w\s/.:-]{0,40}(?:не|нет|not|no|without|без)[\w\s/.:-]{0,30}(?:провер|verified|verification|исполн|fill)?",
        r"(?:не|нет|not|no|without|без)[\w\s/.:-]{0,40}(?:ohlc|fill|исполн|свеч|историческ|провер)",
    ],
}
STOPWORDS = {
    "and", "the", "with", "from", "this", "that", "для", "или", "это", "как", "что", "если", "при", "над", "под",
    "есть", "нет", "будет", "после", "перед", "цена", "price", "market", "рынок", "ситуация", "setup", "сетап",
}
TIMEFRAME_RE = re.compile(r"\b(?:m\d+|\d+m|h\d+|\d+h|d1|1d|w1|1w|m15|m5|h1|h4|4h|weekly|monthly|htf|ltf)\b", re.IGNORECASE)
INSTRUMENT_RE = re.compile(r"\b[A-Z0-9]{2,18}(?:USDT|USD|BTC|PERP|\.P)?\b")
DATE_RE = re.compile(r"\b(?:20\d{2}[-./]\d{1,2}[-./]\d{1,2}|\d{1,2}[-./]\d{1,2}[-./]20\d{2})\b")
NON_INSTRUMENT_TOKENS = {"ATR", "OHLC", "HTF", "LTF", "D1", "H1", "H4", "M5", "M15", "TP", "SL", "R"}


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def default_root() -> Path:
    return Path(__file__).resolve().parents[1]


def markdown_cell(value: Any) -> str:
    return compact(value).replace("|", "\\|")


def tokenize(text: str) -> set[str]:
    tokens = {token.lower() for token in re.findall(r"[\w.%-]+", text, flags=re.UNICODE)}
    return {token for token in tokens if len(token) >= 3 and token not in STOPWORDS}


def normalize_token(value: str) -> str:
    return re.sub(r"[^a-z0-9а-я_.-]+", "", value.lower())


def load_input_file(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    stripped = raw.strip()
    if path.suffix.lower() == ".json" or stripped.startswith("{"):
        payload = json.loads(stripped)
        if not isinstance(payload, dict):
            raise ValueError("JSON input must be an object")
        return payload
    return {"text": raw}


def merge_inputs(args: argparse.Namespace) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if args.input_file:
        payload.update(load_input_file(args.input_file))
    for field in ("text", "instrument", "venue", "timeframe", "direction", "level_area", "entry", "stop", "target", "trigger", "date_session"):
        value = getattr(args, field)
        if value:
            payload[field] = value
    if args.ohlc_verified:
        payload["ohlc_verification"] = "explicit_cli_flag"
    return payload


def input_text(payload: dict[str, Any]) -> str:
    parts = [payload.get("text"), payload.get("description"), payload.get("notes"), flatten(payload.get("ohlc") or {})]
    for field in FIELD_ORDER:
        if payload.get(field):
            parts.append(f"{field}: {payload[field]}")
    return "\n".join(str(part) for part in parts if part)


def classify_query(text: str) -> list[dict[str, Any]]:
    text_lower = text.lower()
    matches: list[dict[str, Any]] = []
    for category, spec in TAXONOMY.items():
        matched = [keyword for keyword in spec["keywords"] if keyword.lower() in text_lower]
        if matched:
            matches.append({
                "id": category,
                "label": spec["label"],
                "bot_question": spec["bot_question"],
                "matched_keywords": matched[:8],
            })
    if not matches:
        matches.append({
            "id": "insufficient_or_mismatch",
            "label": TAXONOMY["insufficient_or_mismatch"]["label"],
            "bot_question": TAXONOMY["insufficient_or_mismatch"]["bot_question"],
            "matched_keywords": [],
        })
    return matches


def extract_query_instruments(payload: dict[str, Any], text: str) -> set[str]:
    candidates = []
    if payload.get("instrument"):
        candidates.append(str(payload["instrument"]))
    candidates.extend(match.group(0) for match in INSTRUMENT_RE.finditer(text))
    normalized = {normalize_token(candidate) for candidate in candidates if candidate.upper() not in NON_INSTRUMENT_TOKENS}
    return {item for item in normalized if item}


def extract_timeframes_from_text(text: str) -> set[str]:
    return {normalize_token(match.group(0)) for match in TIMEFRAME_RE.finditer(text)}


def extract_timeframes_from_row(row: dict[str, Any]) -> set[str]:
    values: list[str] = []
    resolved = row.get("resolved_fields") or {}
    for key in ("timeframe", "timeframes", "chart_timeframe", "chart_timeframes", "timeframe_context"):
        value = resolved.get(key)
        if isinstance(value, list):
            values.extend(str(item) for item in value)
        elif value:
            values.append(str(value))
    values.extend(str(item) for item in (row.get("candidate_features") or {}).get("timeframes") or [])
    return {normalize_token(value) for value in values if value}


def detect_present_fields(payload: dict[str, Any], text: str) -> set[str]:
    text_lower = text.lower()
    present: set[str] = set()
    for field, keywords in FIELD_KEYWORDS.items():
        if payload.get(field):
            present.add(field)
            continue
        if any(re.search(pattern, text_lower, flags=re.IGNORECASE) for pattern in NEGATIVE_FIELD_PATTERNS.get(field, [])):
            continue
        if any(keyword.lower() in text_lower for keyword in keywords):
            present.add(field)
    if DATE_RE.search(text):
        present.add("date_session")
    if TIMEFRAME_RE.search(text):
        present.add("timeframe")
    if extract_query_instruments(payload, text):
        present.add("instrument")
    return present


def missing_evidence(present_fields: set[str]) -> list[dict[str, str]]:
    return [
        {"field": field, "label": FIELD_LABELS[field], "why_required": FIELD_HINTS[field]}
        for field in FIELD_ORDER
        if field not in present_fields
    ]


def row_compact(row: dict[str, Any], categories: list[str], score: int, reasons: list[str]) -> dict[str, Any]:
    resolved = row.get("resolved_fields") or {}
    return {
        "candidate_id": row.get("candidate_id"),
        "claim_id": row.get("claim_id"),
        "score": score,
        "match_reasons": reasons[:8],
        "review_class": review_class(row),
        "review_status": row.get("review_status"),
        "knowledge_use": row.get("knowledge_use"),
        "taxonomy": categories,
        "lecture_title": row.get("lecture_title"),
        "timecode": row.get("timecode"),
        "instruments": extract_instruments(row)[:6],
        "source_frames": row.get("source_frames") or [],
        "summary": compact(row.get("expert_verdict_summary"), 260),
        "scenario": compact(resolved.get("scenario"), 180),
        "visual_context": compact(resolved.get("visual_context"), 260),
        "blockers": row.get("promotion_blockers") or [],
    }


def score_row(
    row: dict[str, Any],
    query_categories: set[str],
    query_tokens: set[str],
    query_instruments: set[str],
    query_timeframes: set[str],
    prefer_rejected: bool,
) -> tuple[int, list[str], list[str]]:
    categories = classify_observation(row)
    reasons: list[str] = []
    score = 0
    category_overlap = sorted(query_categories & set(categories))
    if category_overlap:
        score += 28 * len(category_overlap)
        reasons.append("taxonomy_overlap:" + ",".join(category_overlap[:4]))
    row_tokens = tokenize(row_text(row))
    token_overlap = sorted(query_tokens & row_tokens)
    if token_overlap:
        score += min(30, len(token_overlap) * 3)
        reasons.append("text_overlap:" + ",".join(token_overlap[:8]))
    row_instruments = {normalize_token(item) for item in extract_instruments(row)}
    instrument_overlap = sorted(query_instruments & row_instruments)
    if instrument_overlap:
        score += 22
        reasons.append("instrument_overlap:" + ",".join(instrument_overlap[:4]))
    timeframe_overlap = sorted(query_timeframes & extract_timeframes_from_row(row))
    if timeframe_overlap:
        score += 14
        reasons.append("timeframe_overlap:" + ",".join(timeframe_overlap[:4]))
    current_class = review_class(row)
    if prefer_rejected:
        if current_class == "rejected":
            score += 18
            reasons.append("guardrail_rejected_row")
        else:
            score -= 8
    else:
        if current_class in {"partial", "hold"}:
            score += 18
            reasons.append("usable_reviewed_context")
        if row.get("visual_confirmed") is True:
            score += 6
            reasons.append("visual_confirmed")
    return score, categories, reasons


def retrieve_examples(rows: list[dict[str, Any]], query: dict[str, Any], top_k: int, prefer_rejected: bool) -> list[dict[str, Any]]:
    scored: list[dict[str, Any]] = []
    for row in rows:
        current_class = review_class(row)
        if prefer_rejected and current_class != "rejected":
            continue
        if not prefer_rejected and current_class not in {"partial", "hold"}:
            continue
        score, categories, reasons = score_row(
            row,
            query["category_ids"],
            query["tokens"],
            query["instruments"],
            query["timeframes"],
            prefer_rejected=prefer_rejected,
        )
        if score > 0:
            scored.append(row_compact(row, categories, score, reasons))
    return sorted(scored, key=lambda item: (-item["score"], item["candidate_id"] or ""))[:top_k]


def verdict_state(category_ids: set[str], present_fields: set[str], support_count: int, guardrail_count: int) -> dict[str, Any]:
    complete_without_ohlc = set(FIELD_ORDER[:-1]).issubset(present_fields)
    hard_gate_satisfied = set(FIELD_ORDER).issubset(present_fields)
    setup_tags = {"level_quality", "retest_false_breakout", "compression_accumulation", "room_to_move_atr", "trend_relative_strength"}
    if complete_without_ohlc and "ohlc_verification" not in present_fields:
        state = "needs_ohlc"
        reason = "План похож на полный trade contract, но нет независимой OHLC/fill проверки."
    elif hard_gate_satisfied:
        state = "possible_setup"
        reason = "Все обязательные поля заявлены, но прототип остается read-only и не промоутит ground truth."
    elif "insufficient_or_mismatch" in category_ids and support_count == 0:
        state = "invalid"
        reason = "Описание похоже на mismatch/недостаточные данные и не нашло поддерживающих reviewed examples."
    elif "range_saw_wait" in category_ids and not {"entry", "trigger"}.issubset(present_fields):
        state = "wait"
        reason = "Есть признаки пилы/диапазона или ожидания, а вход/trigger не определены."
    elif {"instrument", "timeframe", "level_area"}.issubset(present_fields) and category_ids & setup_tags:
        state = "possible_setup"
        reason = "Есть инструмент, таймфрейм, рабочая зона и setup-контекст, но trade contract ещё неполный."
    elif category_ids and guardrail_count >= support_count and "insufficient_or_mismatch" in category_ids:
        state = "wait"
        reason = "Больше guardrail-сигналов, чем поддержки: нужно уточнить evidence перед выводом."
    elif category_ids:
        state = "observe"
        reason = "Есть классифицируемый контекст, но недостаточно полей для setup verdict."
    else:
        state = "invalid"
        reason = "Не удалось связать описание с проверенной taxonomy."
    return {"state": state, "reason": reason, "hard_gate_satisfied": hard_gate_satisfied}


def analyze(payload: dict[str, Any], rows: list[dict[str, Any]], top_k: int) -> dict[str, Any]:
    text = input_text(payload)
    taxonomy_matches = classify_query(text)
    category_ids = {item["id"] for item in taxonomy_matches}
    query = {
        "tokens": tokenize(text),
        "category_ids": category_ids,
        "instruments": extract_query_instruments(payload, text),
        "timeframes": extract_timeframes_from_text(text),
    }
    present_fields = detect_present_fields(payload, text)
    supporting_examples = retrieve_examples(rows, query, top_k=top_k, prefer_rejected=False)
    guardrail_examples = retrieve_examples(rows, query, top_k=top_k, prefer_rejected=True)
    verdict = verdict_state(category_ids, present_fields, len(supporting_examples), len(guardrail_examples))
    missing = missing_evidence(present_fields)
    return {
        "dataset_id": "situation_analyzer_prototype_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input": {
            "fields": {key: value for key, value in payload.items() if key != "text" and value},
            "text_excerpt": compact(text, 1600),
        },
        "taxonomy_matches": taxonomy_matches,
        "trade_contract_completeness": {
            "present_fields": [field for field in FIELD_ORDER if field in present_fields],
            "missing_fields": [item["field"] for item in missing],
            "ratio": round(len(present_fields) / len(FIELD_ORDER), 3),
            "hard_gate": "instrument + venue + date/session + direction + entry + stop + target/management + trigger + OHLC/fill verification",
            "hard_gate_satisfied": verdict["hard_gate_satisfied"],
        },
        "missing_evidence": missing,
        "verdict": verdict,
        "supporting_examples": supporting_examples,
        "guardrail_examples": guardrail_examples,
        "safety_note": "Read-only analyzer: this is context retrieval and evidence checking, not a trade signal or ground-truth promotion.",
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Situation Analyzer Report",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        f"Verdict: **`{report['verdict']['state']}`** - {report['verdict']['reason']}",
        "",
        "## Taxonomy Matches",
        "",
    ]
    for item in report["taxonomy_matches"]:
        keywords = ", ".join(f"`{keyword}`" for keyword in item["matched_keywords"])
        lines.append(f"- **{item['label']}**: {item['bot_question']} ({keywords})")

    completeness = report["trade_contract_completeness"]
    present = ", ".join(f"`{field}`" for field in completeness["present_fields"]) or "none"
    missing = ", ".join(f"`{field}`" for field in completeness["missing_fields"]) or "none"
    lines.extend([
        "",
        "## Trade Contract Completeness",
        "",
        f"- Present: {present}",
        f"- Missing: {missing}",
        f"- Ratio: `{completeness['ratio']}`",
        "",
        "## Missing Evidence",
        "",
    ])
    for item in report["missing_evidence"]:
        lines.append(f"- `{item['field']}` - {item['label']}: {item['why_required']}")

    lines.extend(["", "## Supporting Examples", "", "| Candidate | Score | Status | Why similar | Summary |", "|---|---:|---|---|---|"])
    for item in report["supporting_examples"]:
        reasons = markdown_cell(", ".join(item["match_reasons"]))
        summary = markdown_cell(compact(item["summary"], 220))
        lines.append(f"| `{markdown_cell(item['candidate_id'])}` | {item['score']} | `{markdown_cell(item['review_status'])}` | {reasons} | {summary} |")

    lines.extend(["", "## Guardrail Examples", "", "| Candidate | Score | Status | Why similar | Guardrail |", "|---|---:|---|---|---|"])
    for item in report["guardrail_examples"]:
        guardrail = item["summary"] or item["visual_context"] or item["scenario"]
        reasons = markdown_cell(", ".join(item["match_reasons"]))
        guardrail_text = markdown_cell(compact(guardrail, 220))
        lines.append(f"| `{markdown_cell(item['candidate_id'])}` | {item['score']} | `{markdown_cell(item['review_status'])}` | {reasons} | {guardrail_text} |")
    lines.extend(["", f"Safety note: {report['safety_note']}", ""])
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze a market situation against the reviewed visual corpus.")
    parser.add_argument("--root", type=Path, default=default_root())
    parser.add_argument("--input-file", type=Path)
    parser.add_argument("--text")
    parser.add_argument("--instrument")
    parser.add_argument("--venue")
    parser.add_argument("--timeframe")
    parser.add_argument("--direction")
    parser.add_argument("--level-area")
    parser.add_argument("--entry")
    parser.add_argument("--stop")
    parser.add_argument("--target")
    parser.add_argument("--trigger")
    parser.add_argument("--date-session")
    parser.add_argument("--ohlc-verified", action="store_true")
    parser.add_argument("--top-k", type=int, default=7)
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    configure_stdio()
    args = parse_args()
    root = args.root.resolve()
    payload = merge_inputs(args)
    if not input_text(payload).strip():
        raise SystemExit("Provide --text, --input-file, or explicit situation fields.")
    rows = read_jsonl(root / OBSERVATIONS)
    report = analyze(payload, rows, top_k=args.top_k)
    output_path = args.output if args.output.is_absolute() else root / args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if args.format == "markdown":
        rendered = render_markdown(report)
        output_path.write_text(rendered, encoding="utf-8")
        print(rendered)
    else:
        rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        output_path.write_text(rendered, encoding="utf-8")
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())