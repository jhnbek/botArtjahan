from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LECTURE_PASS_DIR = Path("_knowledge_base/structured/lecture_pass/lectures")
OUTPUT_DIR = Path("_knowledge_base/structured/consolidation/lecture_multimodal_calibration_candidates")

CLAIM_TEXT_FIELDS = (
    "topic",
    "scenario",
    "claim_type",
    "statement",
    "quote",
    "visual_evidence",
    "ocr_evidence",
    "interpretation",
)

SYMBOL_NAME_MAP = {
    "apt": "APT",
    "arot": "AROT",
    "auction": "AUCTION",
    "ava": "AVA",
    "bitcoin": "BTC",
    "биткоин": "BTC",
    "block": "SQ",
    "bnx": "BNX",
    "bnt": "BNT",
    "btc": "BTC",
    "cohr": "COHR",
    "doge": "DOGE",
    "dogecoin": "DOGE",
    "eth": "ETH",
    "ethereum": "ETH",
    "imx": "IMX",
    "link": "LINK",
    "litecoin": "LTC",
    "loom": "LOOM",
    "ltc": "LTC",
    "ma": "MA",
    "mtz": "MTZ",
    "nvda": "NVDA",
    "pyth": "PYTH",
    "ripple": "XRP",
    "silk": "SILK",
    "solana": "SOL",
    "sq": "SQ",
    "starbucks": "SBUX",
    "stpt": "STPT",
    "sushi": "SUSHI",
    "ton": "TON",
    "uber": "UBER",
    "wif": "WIF",
    "wld": "WLD",
    "xrp": "XRP",
}

LATIN_STOPWORDS = {
    "ABOVE",
    "AFTER",
    "AND",
    "ATR",
    "ATP",
    "BAR",
    "BARS",
    "BEFORE",
    "BELOW",
    "BINANCE",
    "BYBIT",
    "CASE",
    "CHANNEL",
    "CHART",
    "CLOSE",
    "COINBASE",
    "COMMON",
    "CONTRACT",
    "D1",
    "DO",
    "ENERGY",
    "ETF",
    "EXIT",
    "FROM",
    "FUTURE",
    "H1",
    "H4",
    "HIGH",
    "INSIDE",
    "INVALID",
    "LEVEL",
    "LIMIT",
    "LONG",
    "LOW",
    "M1",
    "M5",
    "M15",
    "MARKET",
    "MIDDLE",
    "MOEX",
    "NASDAQ",
    "NEEDS",
    "NOT",
    "NYSE",
    "OCR",
    "OPEN",
    "OR",
    "PERPETUAL",
    "QUALITY",
    "REWARD",
    "RISK",
    "ROOM",
    "SHORT",
    "STOP",
    "STOCK",
    "STRONG",
    "TAKES",
    "TEST",
    "TIMING",
    "TO",
    "TREND",
    "TRADINGVIEW",
    "USD",
    "USDT",
    "VALID",
    "WAIT",
    "WITH",
}

TOPIC_STOPWORDS = {
    "active",
    "after",
    "bar",
    "break",
    "breakout",
    "case",
    "chart",
    "close",
    "daily",
    "definition",
    "entry",
    "example",
    "false",
    "failed",
    "level",
    "local",
    "long",
    "near",
    "plan",
    "price",
    "retest",
    "risk",
    "room",
    "rule",
    "scenario",
    "short",
    "tail",
    "trade",
    "weekly",
    "zone",
}

MONTHS_RU = {
    "января": "01",
    "февраля": "02",
    "марта": "03",
    "апреля": "04",
    "мая": "05",
    "июня": "06",
    "июля": "07",
    "августа": "08",
    "сентября": "09",
    "октября": "10",
    "ноября": "11",
    "декабря": "12",
}

SCENARIO_KEYWORDS = {
    "breakout": ("пробой", "пробива", "breakout", "breakdown", "continuation"),
    "false_breakout": ("ложн", "false_breakout", "failed", "reclaim", "не смог"),
    "retest": ("ретест", "retest", "возврат"),
    "near_retest": ("ближн", "near_retest", "near retest"),
    "far_retest": ("дальн", "far_retest", "far retest"),
    "rebound": ("отбой", "отскок", "rebound", "reaction"),
    "compression": ("поджат", "накоплен", "compression", "accumulation"),
    "saw_range": ("распил", "пилит", "проторгов", "saw"),
    "room_to_move": ("запас хода", "room", "пустот"),
    "risk_management": ("стоп", "stop", "risk", "atr", "тейк", "take"),
    "invalid_or_wait": ("нельзя", "не торг", "ждать", "skip", "не делать"),
}

TRADE_CLAIM_TYPES = {
    "case_study",
    "example",
    "setup_component",
    "pattern_rule",
    "breakout_premise_rule",
    "false_breakout_rule",
    "continuation_rule",
    "entry_timing_rule",
    "trade_level_selection_rule",
    "level_and_entry_filter_rule",
}


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def default_root() -> Path:
    return Path(__file__).resolve().parents[1]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Bad JSONL in {path} line {line_number}: {exc}") from exc
    return rows


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def unique(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        value = value.strip()
        key = value.upper()
        if not value or key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def compact_text(value: str, limit: int = 900) -> str:
    text = " ".join((value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def safe_cell(value: Any) -> str:
    return str(value or "-").replace("|", "\\|").replace("\n", " ")


def load_units_by_id(lecture_dir: Path) -> dict[str, dict[str, Any]]:
    return {str(row.get("unit_id")): row for row in read_jsonl(lecture_dir / "source_multimodal_units.jsonl") if row.get("unit_id")}


def claim_text(claim: dict[str, Any]) -> str:
    return "\n".join(str(claim.get(field) or "") for field in CLAIM_TEXT_FIELDS if claim.get(field))


def full_blob(claim: dict[str, Any], units: list[dict[str, Any]]) -> str:
    parts = [claim_text(claim)]
    for unit in units:
        parts.append(str(unit.get("text") or ""))
        for frame in unit.get("frames") or []:
            parts.append(str(frame.get("ocr_text") or ""))
    return "\n".join(part for part in parts if part.strip())


def collect_frame_refs(root: Path, claim: dict[str, Any], units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refs = []
    for frame in (claim.get("source") or {}).get("frames") or []:
        refs.append({"path": str(frame.get("path") or "").replace("\\", "/"), "time": frame.get("time"), "unit_id": None})
    for unit in units:
        for frame in unit.get("frames") or []:
            refs.append(
                {
                    "path": str(frame.get("path") or "").replace("\\", "/"),
                    "time": frame.get("time"),
                    "unit_id": unit.get("unit_id"),
                    "ocr_line_count": frame.get("ocr_line_count", 0),
                }
            )
    seen = set()
    deduped = []
    for ref in refs:
        rel_path = ref["path"]
        if not rel_path or rel_path in seen:
            continue
        seen.add(rel_path)
        physical = root / "_new_lecture_corpus" / Path(rel_path)
        ref["physical_path"] = str((Path("_new_lecture_corpus") / Path(rel_path)).as_posix())
        ref["exists"] = physical.exists()
        deduped.append(ref)
    return deduped


def ocr_snippets(units: list[dict[str, Any]], limit: int = 6) -> list[dict[str, str]]:
    snippets = []
    for unit in units:
        for frame in unit.get("frames") or []:
            ocr = str(frame.get("ocr_text") or "").strip()
            if not ocr:
                continue
            snippets.append(
                {
                    "path": str(frame.get("path") or "").replace("\\", "/"),
                    "time": str(frame.get("time") or ""),
                    "ocr_excerpt": compact_text(ocr, 260),
                }
            )
            if len(snippets) >= limit:
                return snippets
    return snippets


def extract_instruments(blob: str, claim: dict[str, Any]) -> list[str]:
    instruments = []
    source = blob + "\n" + " ".join(str(claim.get(field) or "") for field in ("topic", "scenario"))
    for match in re.finditer(r"\b[A-Z]{2,12}(?:USDT\.P|USDTP|USDT|USD|PERP)?\b", source):
        token = match.group(0).upper()
        if token in LATIN_STOPWORDS:
            continue
        if token.endswith("USDT.P"):
            instruments.extend([token[:-6], token])
        elif token.endswith("USDTP"):
            instruments.extend([token[:-5], token])
        elif token.endswith("USDT") and len(token) > 4:
            instruments.extend([token[:-4], token])
        elif token.endswith("USD") and len(token) > 3:
            instruments.extend([token[:-3], token])
        else:
            instruments.append(token)
    lowered = source.lower().replace("ё", "е")
    for name, symbol in SYMBOL_NAME_MAP.items():
        if re.search(rf"(?<![a-zа-я0-9]){re.escape(name)}(?![a-zа-я0-9])", lowered):
            instruments.append(symbol)
    for field in ("topic", "scenario"):
        for token in re.split(r"[^a-z0-9]+", str(claim.get(field) or "").lower()):
            if token in SYMBOL_NAME_MAP:
                instruments.append(SYMBOL_NAME_MAP[token])
    return unique(instruments)[:14]


def extract_dates(blob: str) -> list[str]:
    dates = []
    for year, month, day in re.findall(r"\b(20\d{2})[-./](\d{1,2})[-./](\d{1,2})\b", blob):
        dates.append(f"{year}-{int(month):02d}-{int(day):02d}")
    for day, month, year in re.findall(r"\b(\d{1,2})[-./](\d{1,2})[-./](20\d{2})\b", blob):
        dates.append(f"{year}-{int(month):02d}-{int(day):02d}")
    month_pattern = "|".join(MONTHS_RU)
    for day, month_name, year in re.findall(rf"\b(\d{{1,2}})\s+({month_pattern})\s+(20\d{{2}})\b", blob.lower()):
        dates.append(f"{year}-{MONTHS_RU[month_name]}-{int(day):02d}")
    return unique(dates)


def extract_relative_date_cues(blob: str) -> list[str]:
    lowered = blob.lower().replace("ё", "е")
    cues = []
    if re.search(r"\b(сегодня|сегодняшн|today)\b", lowered):
        cues.append("today")
    if re.search(r"\b(вчера|вчерашн|yesterday)\b", lowered):
        cues.append("yesterday")
    if re.search(r"\b(завтра|tomorrow)\b", lowered):
        cues.append("tomorrow")
    if re.search(r"\b(следующ(?:ий|его|ая)|next day)\b", lowered):
        cues.append("next_day")
    if re.search(r"\b(прошл(?:ый|ого|ая)|previous day)\b", lowered):
        cues.append("previous_day")
    return cues


def extract_prices(blob: str) -> list[str]:
    prices = []
    context = r"уров|цена|level|price|close|entry|вход|стоп|stop|тейк|take|high|low|long|short|\$|центов|доллар|около|выше|ниже"
    pattern = re.compile(
        r"(?<![\w])\d{1,6}(?:[.,]\d{1,8})?(?:\s*[-/–]\s*\d{1,6}(?:[.,]\d{1,8})?)?(?:\s*(?:\$|доллар(?:ов|а)?|цент(?:ов|а)?))?",
        re.IGNORECASE,
    )
    for match in pattern.finditer(blob):
        raw = match.group(0).strip()
        nearby = blob[max(0, match.start() - 60): match.end() + 60]
        if not raw or re.fullmatch(r"20\d{2}", raw):
            continue
        if re.fullmatch(r"\d{1,2}", raw) and not re.search(context, nearby, re.IGNORECASE):
            continue
        if re.search(r"%|процент", nearby[: max(0, len(raw) + 20)], re.IGNORECASE):
            continue
        if not re.search(r"[.,\-/–$]", raw) and not re.search(context, nearby, re.IGNORECASE):
            continue
        prices.append(re.sub(r"\s+", "", raw.replace("–", "-")).replace(",", "."))
    return unique(prices)[:20]


def extract_timeframes(blob: str) -> list[str]:
    found = [match.group(0).upper() for match in re.finditer(r"\b(?:1M|1W|1D|D1|H4|H1|M15|M5|M1|5m|15m|daily|weekly)\b", blob, re.IGNORECASE)]
    lowered = blob.lower()
    if "днев" in lowered:
        found.append("D1")
    if "недель" in lowered:
        found.append("1W")
    if "месяц" in lowered or "месяч" in lowered:
        found.append("1M")
    if "часов" in lowered or "часовик" in lowered:
        found.append("H1")
    return unique(found)[:8]


def detect_direction(blob: str) -> str:
    lowered = blob.lower().replace("ё", "е")
    long_score = len(re.findall(r"\b(long|лонг\w*|покуп\w*|buy|вверх|пробой вверх|выше уровня)\b", lowered))
    short_score = len(re.findall(r"\b(short|шорт\w*|прода\w*|sell|вниз|пробой вниз|ниже уровня)\b", lowered))
    if long_score and short_score:
        return "both_or_branching" if abs(long_score - short_score) <= 1 else ("long" if long_score > short_score else "short")
    if long_score:
        return "long"
    if short_score:
        return "short"
    return "none"


def scenario_flags(blob: str) -> list[str]:
    lowered = blob.lower().replace("ё", "е")
    return [label for label, needles in SCENARIO_KEYWORDS.items() if any(needle in lowered for needle in needles)]


def expert_signals(blob: str, claim: dict[str, Any]) -> list[str]:
    lowered = blob.lower().replace("ё", "е")
    signals = []
    if re.search(r"\b(можно|допускает|план|сценарий|интересн|ожидать|вход|зайти|взять)\b", lowered):
        signals.append("actionable_or_watch")
    if re.search(r"\b(нельзя|не торг|лучше ничего|пропустить|skip|wait|ждать)\b", lowered):
        signals.append("invalid_or_wait")
    if re.search(r"\b(закрыл|выход|не исполн|ошиб|правильн|не нужен)\b", lowered):
        signals.append("trade_management_or_verdict")
    if str(claim.get("claim_type") or "") in TRADE_CLAIM_TYPES:
        signals.append("trade_like_claim_type")
    return unique(signals)


def readiness_score(features: dict[str, Any]) -> int:
    score = 0
    score += 2 if features["instrument_candidates"] else 0
    score += 2 if features["price_candidates"] else 0
    score += 2 if features["direction"] in {"long", "short"} else 1 if features["direction"] == "both_or_branching" else 0
    score += 2 if features["frame_count"] else 0
    score += 1 if features["existing_frame_count"] else 0
    score += 3 if features["exact_date_candidates"] else 0
    score += 1 if features["relative_date_cues"] else 0
    score += 1 if features["timeframes"] else 0
    score += 2 if features["expert_signals"] else 0
    score += 1 if any(flag in features["scenario_candidates"] for flag in ("breakout", "false_breakout", "retest", "compression")) else 0
    return score


def classify(features: dict[str, Any]) -> str:
    has_instrument = bool(features["instrument_candidates"])
    has_price = bool(features["price_candidates"])
    has_direction = features["direction"] in {"long", "short", "both_or_branching"}
    has_exact_date = bool(features["exact_date_candidates"])
    has_frames = features["frame_count"] > 0
    has_expert = bool(features["expert_signals"])
    trade_like = "trade_like_claim_type" in features["expert_signals"] or has_direction
    risk_only = "risk_management" in features["scenario_candidates"] and not (has_instrument and has_direction)
    if has_instrument and has_price and features["direction"] in {"long", "short"} and has_exact_date and has_frames and has_expert and features["direct_trade_case"]:
        return "calibration_ready_candidate"
    if has_instrument and has_price and has_frames and has_direction and trade_like:
        return "high_priority_visual_review"
    if risk_only:
        return "risk_management_only"
    if has_frames and (has_instrument or has_price) and (trade_like or features["scenario_candidates"]):
        return "needs_visual_frame_review"
    if has_frames:
        return "methodology_visual_reference"
    return "rule_only"


def blockers(features: dict[str, Any], classification: str) -> list[str]:
    result = []
    if not features["instrument_candidates"]:
        result.append("missing_instrument")
    if not features["exact_date_candidates"]:
        result.append("missing_exact_decision_date")
    if features["relative_date_cues"] and not features["exact_date_candidates"]:
        result.append("relative_date_requires_video/session_anchor")
    if not features["price_candidates"]:
        result.append("missing_level_or_price")
    if features["direction"] == "none":
        result.append("missing_direction")
    if features["direction"] == "both_or_branching":
        result.append("branching_direction_needs_verdict_side")
    if not features["direct_trade_case"]:
        result.append("claim_type_not_direct_trade_case")
    if not features["expert_signals"]:
        result.append("missing_expert_verdict_or_trade_plan")
    if not features["frame_count"]:
        result.append("missing_frame_reference")
    if classification in {"methodology_visual_reference", "rule_only", "risk_management_only"}:
        result.append("methodology_not_trade_case")
    return result


def extract_features(claim: dict[str, Any], units: list[dict[str, Any]], frame_refs: list[dict[str, Any]]) -> dict[str, Any]:
    blob = full_blob(claim, units)
    features = {
        "instrument_candidates": extract_instruments(blob, claim),
        "exact_date_candidates": extract_dates(blob),
        "relative_date_cues": extract_relative_date_cues(blob),
        "price_candidates": extract_prices(blob),
        "direction": detect_direction(blob),
        "scenario_candidates": scenario_flags(blob),
        "timeframes": extract_timeframes(blob),
        "expert_signals": expert_signals(blob, claim),
        "direct_trade_case": str(claim.get("claim_type") or "") in {"example", "case_study"},
        "frame_count": len(frame_refs),
        "existing_frame_count": sum(1 for frame in frame_refs if frame.get("exists")),
        "ocr_snippet_count": len(ocr_snippets(units)),
    }
    features["readiness_score"] = readiness_score(features)
    return features


def build_row(root: Path, lecture_dir: Path, claim: dict[str, Any], units_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    source = claim.get("source") or {}
    unit_ids = [str(unit_id) for unit_id in source.get("unit_ids") or []]
    units = [units_by_id[unit_id] for unit_id in unit_ids if unit_id in units_by_id]
    frame_refs = collect_frame_refs(root, claim, units)
    features = extract_features(claim, units, frame_refs)
    classification = classify(features)
    return {
        "candidate_id": f"LMM-{int(source.get('course_position') or 0):03d}-{claim.get('claim_id')}",
        "claim_id": claim.get("claim_id"),
        "classification": classification,
        "readiness_score": features["readiness_score"],
        "promotion_blockers": blockers(features, classification),
        "lecture_dir": lecture_dir.name,
        "lecture_title": source.get("lecture_title"),
        "course_position": source.get("course_position"),
        "time": source.get("time"),
        "topic": claim.get("topic"),
        "scenario": claim.get("scenario"),
        "claim_type": claim.get("claim_type"),
        "confidence": claim.get("confidence"),
        "maturity": claim.get("maturity"),
        "features": {key: value for key, value in features.items() if key != "readiness_score"},
        "source_unit_ids": unit_ids,
        "matched_source_unit_ids": [unit.get("unit_id") for unit in units],
        "frame_refs": frame_refs,
        "ocr_snippets": ocr_snippets(units),
        "statement_excerpt": compact_text(str(claim.get("statement") or ""), 700),
        "quote_excerpt": compact_text(str(claim.get("quote") or ""), 450),
        "visual_evidence_excerpt": compact_text(str(claim.get("visual_evidence") or ""), 450),
        "source_text_excerpt": compact_text(" ".join(str(unit.get("text") or "") for unit in units), 800),
    }


def known_anchor_hits(rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    anchors = {"APT": [], "LTC": [], "NVDA": [], "PYTH": [], "UBER": [], "AROT": [], "LINK": []}
    for row in rows:
        instruments = {instrument.upper() for instrument in row["features"].get("instrument_candidates", [])}
        for anchor in anchors:
            if anchor in instruments and len(anchors[anchor]) < 8:
                anchors[anchor].append(row["candidate_id"])
    return anchors


def write_report(path: Path, summary: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    lines = [
        "# Lecture Multimodal Calibration Candidates",
        "",
        f"Generated: `{summary['generated_at']}`",
        "",
        "## Summary",
        "",
        f"- Multimodal claims scanned: {summary['claim_count']}",
        f"- Lectures scanned: {summary['lecture_count']}",
        f"- Candidate rows: {summary['candidate_count']}",
        f"- Classification counts: `{summary['classification_counts']}`",
        f"- Readiness score counts: `{summary['readiness_score_counts']}`",
        "",
        "## Interpretation",
        "",
        "`calibration_ready_candidate` requires instrument, exact decision date, level/price, direction, frame refs, and an expert plan/verdict. `high_priority_visual_review` means the multimodal claim already has ticker/level/direction/frame evidence, but still needs visual date/close reconstruction before promotion to ground truth.",
        "",
        "## Top Review Queue",
        "",
        "| Rank | ID | Class | Ready | Lecture | Time | Direction | Instruments | Prices | Dates | Scenarios | Blockers |",
        "|---:|---|---|---:|---|---|---|---|---|---|---|---|",
    ]
    for row in rows[:120]:
        features = row["features"]
        lines.append(
            "| "
            f"{row['rank']} | `{row['candidate_id']}` | `{row['classification']}` | {row['readiness_score']} | "
            f"{safe_cell(row.get('lecture_title'))} | {safe_cell(row.get('time'))} | "
            f"{features['direction']} | {safe_cell(', '.join(features['instrument_candidates']) or '-')} | "
            f"{safe_cell(', '.join(features['price_candidates'][:8]) or '-')} | "
            f"{safe_cell(', '.join(features['exact_date_candidates']) or '-')} | "
            f"{safe_cell(', '.join(features['scenario_candidates'][:5]) or '-')} | "
            f"{safe_cell(', '.join(row['promotion_blockers'][:4]) or '-')} |"
        )
    lines.extend(["", "## Top Candidate Notes", ""])
    for row in rows[:35]:
        features = row["features"]
        frame_sample = ", ".join(frame["physical_path"] for frame in row["frame_refs"][:4]) or "none"
        lines.extend(
            [
                f"### {row['rank']}. {row['candidate_id']} - {row.get('lecture_title')} {row.get('time')}",
                "",
                f"- Class: `{row['classification']}`, readiness: {row['readiness_score']}, claim type: `{row.get('claim_type')}`",
                f"- Direction/scenario: `{features['direction']}` / `{', '.join(features['scenario_candidates']) or 'none'}`",
                f"- Instruments: `{', '.join(features['instrument_candidates']) or 'none'}`; prices: `{', '.join(features['price_candidates'][:12]) or 'none'}`",
                f"- Dates: `{', '.join(features['exact_date_candidates']) or 'none'}`; relative cues: `{', '.join(features['relative_date_cues']) or 'none'}`",
                f"- Frame refs: {len(row['frame_refs'])}; existing files: {features['existing_frame_count']}; sample: `{frame_sample}`",
                f"- Blockers: `{', '.join(row['promotion_blockers']) or 'none'}`",
                "",
                f"Statement: {safe_cell(row['statement_excerpt'])}",
                "",
                f"Visual: {safe_cell(row['visual_evidence_excerpt'])}",
                "",
            ]
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def mine(root: Path) -> dict[str, Any]:
    lecture_dirs = sorted(
        path
        for path in (root / LECTURE_PASS_DIR).glob("lecture_*")
        if path.is_dir() and (path / "claims_multimodal.jsonl").exists()
    )
    rows: list[dict[str, Any]] = []
    claim_count = 0
    for lecture_dir in lecture_dirs:
        claims = read_jsonl(lecture_dir / "claims_multimodal.jsonl")
        units_by_id = load_units_by_id(lecture_dir)
        claim_count += len(claims)
        rows.extend(build_row(root, lecture_dir, claim, units_by_id) for claim in claims)
    rows.sort(
        key=lambda row: (
            -int(row["readiness_score"]),
            row["classification"] != "calibration_ready_candidate",
            row["classification"] != "high_priority_visual_review",
            int(row.get("course_position") or 0),
            str(row.get("claim_id") or ""),
        )
    )
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
    output_dir = root / OUTPUT_DIR
    high_priority = [row for row in rows if row["classification"] in {"calibration_ready_candidate", "high_priority_visual_review"}]
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "lecture_count": len(lecture_dirs),
        "claim_count": claim_count,
        "candidate_count": len(rows),
        "classification_counts": dict(Counter(row["classification"] for row in rows)),
        "readiness_score_counts": dict(Counter(str(row["readiness_score"]) for row in rows)),
        "high_priority_count": len(high_priority),
        "calibration_ready_count": sum(1 for row in rows if row["classification"] == "calibration_ready_candidate"),
        "top_review_queue": [row["candidate_id"] for row in rows[:80]],
        "known_anchor_hits": known_anchor_hits(rows),
        "output_files": {
            "candidates_jsonl": str((output_dir / "candidates.jsonl").relative_to(root)).replace("\\", "/"),
            "high_priority_json": str((output_dir / "high_priority_visual_queue.json").relative_to(root)).replace("\\", "/"),
            "summary_json": str((output_dir / "summary.json").relative_to(root)).replace("\\", "/"),
            "review_report_md": str((output_dir / "review_report.md").relative_to(root)).replace("\\", "/"),
        },
    }
    write_jsonl(output_dir / "candidates.jsonl", rows)
    write_json(output_dir / "high_priority_visual_queue.json", high_priority[:250])
    write_json(output_dir / "summary.json", summary)
    write_report(output_dir / "review_report.md", summary, rows)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mine multimodal lecture claims for OHLC-ready calibration review candidates.")
    parser.add_argument("--root", type=Path, default=default_root())
    return parser.parse_args()


def main() -> int:
    configure_stdio()
    args = parse_args()
    summary = mine(args.root.resolve())
    print(
        f"[OK] scanned {summary['claim_count']} multimodal claims from {summary['lecture_count']} lectures; "
        f"classes={summary['classification_counts']}; high_priority={summary['high_priority_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())