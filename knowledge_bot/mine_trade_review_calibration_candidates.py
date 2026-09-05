from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from build_knowledge_inventory import STRONG_SCORE, load_taxonomy, score_topic
from search_lectures import load_chunks


TOPIC_ID = "trade_reviews_examples"
OUTPUT_DIR = Path("_knowledge_base/structured/consolidation/trade_review_calibration_candidates")

LONG_CUES = ("лонг", "long", "покуп", "купить", "buy", "вверх", "наверх")
SHORT_CUES = ("шорт", "short", "продаж", "sell", "вниз")
ENTRY_CUES = ("вход", "точка входа", "зашел", "заходил", "вошел", "выставил", "лимит", "маркет")
OUTCOME_CUES = (
    "получил стоп",
    "стоп-лосс",
    "тейк",
    "take profit",
    "забрал",
    "прибыль",
    "убыт",
    "5 к 1",
    "4 к 1",
    "3 к 1",
    "10 к 1",
    "не дал точку",
    "нельзя делать",
    "не трогал",
    "ошибка",
)
SCENARIO_CUES = {
    "breakout": ("пробой", "breakout", "закреп", "первый импульс"),
    "false_breakout": ("ложный пробой", "лп", "false breakout", "прокол"),
    "rebound": ("отбой", "rebound", "реакция от уровня"),
    "fixation_return": ("закреп", "возврат к уровню", "вернулись к уровню"),
    "bsu_bpu": ("бсу", "бпу", "бпу1", "бпу2"),
    "compression": ("поджат", "сжат", "compression"),
    "v_u": ("v-форма", "u-форма", "v форма", "u форма"),
}
COMPANY_CUES = ("dell", "mastercard", "nvidia", "tesla", "apple", "amazon", "meta", "microsoft", "netflix")
SYMBOL_STOP_WORDS = {"ATR", "USD", "TV", "OHLC", "KB", "API", "JSON", "OCR", "R", "D", "H", "M", "БСУ", "БПУ"}


def default_root() -> Path:
    return Path(__file__).resolve().parents[1]


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) for row in rows)
    path.write_text(payload + ("\n" if payload else ""), encoding="utf-8")


def compact_text(value: str, max_chars: int = 900) -> str:
    text = " ".join((value or "").split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def safe_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def frame_ocr_text(frames: list[dict[str, Any]]) -> str:
    return " ".join(str(frame.get("ocr_text") or "") for frame in frames)


def scenario_flags(text_lower: str) -> list[str]:
    return [name for name, cues in SCENARIO_CUES.items() if any(cue in text_lower for cue in cues)]


def exact_date_candidates(text: str) -> list[str]:
    candidates = set(re.findall(r"\b\d{1,2}[./-]\d{1,2}[./-](?:\d{2}|\d{4})\b", text))
    candidates.update(re.findall(r"\b20\d{2}[./-]\d{1,2}[./-]\d{1,2}\b", text))
    return sorted(candidates)


def price_candidates(text: str) -> list[str]:
    raw = re.findall(r"(?<!\w)(?:\d{1,5}[.,]\d{1,4}|\d{2,5})(?!\w)", text)
    filtered = []
    for item in raw:
        digits = re.sub(r"\D", "", item)
        if len(digits) >= 2 and item not in {"2020", "2021", "2022", "2023", "2024", "2025", "2026"}:
            filtered.append(item)
    return sorted(set(filtered))[:20]


def instrument_candidates(text: str) -> list[str]:
    found: set[str] = set()
    text_lower = text.lower()
    for cue in COMPANY_CUES:
        if cue in text_lower:
            found.add(cue.upper())
    for match in re.findall(r"\b[A-ZА-Я]{2,6}\b", text):
        if match not in SYMBOL_STOP_WORDS:
            found.add(match)
    for pattern in (
        r"акци[яюи]\s+([A-Za-zА-Яа-яЁё0-9.-]{2,20})",
        r"бумаг[ауы]\s+([A-Za-zА-Яа-яЁё0-9.-]{2,20})",
        r"на\s+([A-Z][A-Za-z]{2,12})\b",
    ):
        for match in re.findall(pattern, text):
            token = match.strip(" .,:;()[]{}\"'").upper()
            if len(token) >= 2 and token not in SYMBOL_STOP_WORDS:
                found.add(token)
    return sorted(found)[:15]


def relative_date_cues(text_lower: str) -> list[str]:
    return [
        cue
        for cue in ("сегодня", "вчера", "позавчера", "в пятницу", "на следующий день", "прошлый день")
        if cue in text_lower
    ]


def detect_direction(text_lower: str) -> str:
    has_long = any(cue in text_lower for cue in LONG_CUES)
    has_short = any(cue in text_lower for cue in SHORT_CUES)
    if has_long and has_short:
        return "both"
    if has_long:
        return "long"
    if has_short:
        return "short"
    return "none"


def classify_candidate(features: dict[str, Any]) -> str:
    if (
        features["instrument_candidates"]
        and features["exact_date_candidates"]
        and features["has_level_cue"]
        and features["direction"] != "none"
        and features["frame_count"] > 0
    ):
        return "calibration_ready"
    if (
        features["frame_count"] > 0
        and features["has_level_cue"]
        and (features["has_entry_cue"] or features["direction"] != "none")
        and (features["has_outcome_cue"] or features["scenario_candidates"])
    ):
        return "needs_visual_frame_review"
    if features["has_risk_reward_cue"] or (features["has_stop_cue"] and not features["has_level_cue"]):
        return "risk_management_only"
    if features["has_level_cue"] or features["scenario_candidates"]:
        return "rule_only"
    return "not_reconstructable"


def blockers(features: dict[str, Any], classification: str) -> list[str]:
    if classification == "calibration_ready":
        return []
    missing = []
    if not features["instrument_candidates"]:
        missing.append("instrument_identity_missing")
    if not features["exact_date_candidates"]:
        missing.append("only_relative_date_cues" if features["relative_date_cues"] else "decision_date_missing")
    if not features["has_level_cue"]:
        missing.append("level_not_explicit_in_text")
    if features["direction"] == "none":
        missing.append("direction_missing")
    if features["frame_count"] == 0:
        missing.append("no_frame_refs")
    if not features["has_outcome_cue"]:
        missing.append("expert_verdict_or_outcome_missing")
    return missing


def readiness_score(features: dict[str, Any]) -> int:
    checks = [
        bool(features["instrument_candidates"]),
        bool(features["exact_date_candidates"]),
        features["has_level_cue"],
        features["direction"] != "none",
        features["has_entry_cue"],
        features["has_stop_cue"],
        features["has_outcome_cue"],
        features["frame_count"] > 0,
        bool(features["price_candidates"]),
    ]
    return sum(1 for value in checks if value)


def extract_features(chunk: dict[str, Any]) -> dict[str, Any]:
    frames = chunk.get("frames") or []
    combined = f"{chunk.get('lecture_title', '')} {chunk.get('text', '')} {frame_ocr_text(frames)}"
    lower = combined.lower()
    features = {
        "direction": detect_direction(lower),
        "scenario_candidates": scenario_flags(lower),
        "instrument_candidates": instrument_candidates(combined),
        "exact_date_candidates": exact_date_candidates(combined),
        "relative_date_cues": relative_date_cues(lower),
        "price_candidates": price_candidates(combined),
        "has_level_cue": "уров" in lower or "level" in lower,
        "has_entry_cue": any(cue in lower for cue in ENTRY_CUES),
        "has_stop_cue": "стоп" in lower or "stop" in lower,
        "has_outcome_cue": any(cue in lower for cue in OUTCOME_CUES),
        "has_risk_reward_cue": bool(re.search(r"\b\d+\s*[кk:]\s*1\b", lower)) or "risk_reward" in chunk.get("keywords", []),
        "frame_count": len(frames),
        "ocr_frame_count": sum(1 for frame in frames if frame.get("ocr_text")),
    }
    features["readiness_score"] = readiness_score(features)
    features["classification"] = classify_candidate(features)
    features["promotion_blockers"] = blockers(features, features["classification"])
    return features


def frame_refs(frames: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    return [
        {
            "time": frame.get("time"),
            "path": frame.get("path"),
            "ocr_text_preview": compact_text(str(frame.get("ocr_text") or ""), 160),
        }
        for frame in frames[:limit]
    ]


def mine(root: Path) -> dict[str, Any]:
    chunks = load_chunks(root / "_knowledge_base" / "lecture_chunks.jsonl")
    taxonomy = load_taxonomy(root / "knowledge_bot" / "knowledge_taxonomy.json")
    topic = next(item for item in taxonomy if item.topic_id == TOPIC_ID)

    rows = []
    for chunk in chunks:
        score = score_topic(chunk, topic)
        if score < STRONG_SCORE:
            continue
        features = extract_features(chunk)
        rows.append(
            {
                "candidate_id": f"LTR-{len(rows) + 1:04d}",
                "topic_id": TOPIC_ID,
                "score": score,
                "classification": features.pop("classification"),
                "readiness_score": features.pop("readiness_score"),
                "promotion_blockers": features.pop("promotion_blockers"),
                "chunk_id": chunk.get("chunk_id"),
                "lecture_id": chunk.get("lecture_id"),
                "lecture_title": chunk.get("lecture_title"),
                "lecture_dir": chunk.get("lecture_dir"),
                "course_position": chunk.get("course_position"),
                "start_time": chunk.get("start_time"),
                "end_time": chunk.get("end_time"),
                "start_sec": chunk.get("start_sec"),
                "end_sec": chunk.get("end_sec"),
                "keywords": chunk.get("keywords", []),
                "features": features,
                "frame_refs": frame_refs(chunk.get("frames") or []),
                "text_excerpt": compact_text(str(chunk.get("text") or ""), 1100),
            }
        )

    rows.sort(key=lambda item: (-item["readiness_score"], -item["score"], str(item.get("lecture_id") or ""), item.get("start_sec") or 0))
    for idx, row in enumerate(rows, start=1):
        row["rank"] = idx

    output_dir = root / OUTPUT_DIR
    write_jsonl(output_dir / "candidates.jsonl", rows)
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "topic_id": TOPIC_ID,
        "strong_score_threshold": STRONG_SCORE,
        "candidate_count": len(rows),
        "classification_counts": dict(Counter(row["classification"] for row in rows)),
        "readiness_score_counts": dict(Counter(str(row["readiness_score"]) for row in rows)),
        "lecture_count": len({row["lecture_id"] for row in rows}),
        "top_review_queue": [row["candidate_id"] for row in rows[:60]],
        "output_files": {
            "candidates_jsonl": str((output_dir / "candidates.jsonl").relative_to(root)).replace("\\", "/"),
            "summary_json": str((output_dir / "summary.json").relative_to(root)).replace("\\", "/"),
            "review_report_md": str((output_dir / "review_report.md").relative_to(root)).replace("\\", "/"),
        },
    }
    write_json(output_dir / "summary.json", summary)
    write_report(output_dir / "review_report.md", summary, rows)
    return summary


def write_report(path: Path, summary: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    lines = [
        "# Lecture Trade-Review Calibration Candidates",
        "",
        f"Generated: `{summary['generated_at']}`",
        "",
        "## Summary",
        "",
        f"- Strong candidate chunks: {summary['candidate_count']}",
        f"- Lectures covered: {summary['lecture_count']}",
        f"- Classification counts: `{summary['classification_counts']}`",
        f"- Readiness score counts: `{summary['readiness_score_counts']}`",
        "",
        "## Interpretation",
        "",
        "`calibration_ready` means transcript/OCR already exposes enough to attempt OHLC reconstruction automatically. `needs_visual_frame_review` means the transcript has a real trade-review shape but still needs frame inspection for ticker/date/level precision. Other classes are useful methodology, but not direct ground truth yet.",
        "",
        "## Top Review Queue",
        "",
        "| Rank | ID | Class | Ready | Score | Lecture | Time | Direction | Scenarios | Instruments | Dates | Blockers |",
        "|---:|---|---|---:|---:|---|---|---|---|---|---|---|",
    ]
    for row in rows[:80]:
        features = row["features"]
        lines.append(
            "| "
            f"{row['rank']} | `{row['candidate_id']}` | `{row['classification']}` | {row['readiness_score']} | {row['score']} | "
            f"{safe_cell(row['lecture_title'])} | {row['start_time']}-{row['end_time']} | "
            f"{features['direction']} | {safe_cell(', '.join(features['scenario_candidates']) or '-')} | "
            f"{safe_cell(', '.join(features['instrument_candidates']) or '-')} | "
            f"{safe_cell(', '.join(features['exact_date_candidates']) or '-')} | "
            f"{safe_cell(', '.join(row['promotion_blockers'][:4]) or '-')} |"
        )

    lines.extend(["", "## Top Candidate Notes", ""])
    for row in rows[:25]:
        features = row["features"]
        lines.extend(
            [
                f"### {row['rank']}. {row['candidate_id']} - {row['lecture_title']} {row['start_time']}-{row['end_time']}",
                "",
                f"- Class: `{row['classification']}`, readiness: {row['readiness_score']}, score: {row['score']}",
                f"- Direction/scenario: `{features['direction']}` / `{', '.join(features['scenario_candidates']) or 'none'}`",
                f"- Instruments: `{', '.join(features['instrument_candidates']) or 'none'}`; dates: `{', '.join(features['exact_date_candidates']) or 'none'}`; relative dates: `{', '.join(features['relative_date_cues']) or 'none'}`",
                f"- Frame refs: {len(row['frame_refs'])}",
                f"- Blockers: `{', '.join(row['promotion_blockers']) or 'none'}`",
                "",
                row["text_excerpt"],
                "",
            ]
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mine lecture trade-review chunks for calibration ground-truth candidates.")
    parser.add_argument("--root", type=Path, default=default_root())
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary = mine(args.root.resolve())
    print(f"[OK] mined {summary['candidate_count']} candidates; classes={summary['classification_counts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())