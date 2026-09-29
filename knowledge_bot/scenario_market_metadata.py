"""Normalize scenario search metadata without asserting market/date alignment.

The author's caption may name the signal day, entry day, or a date range. Dates
here are search hints, never a D1 close or an execution timestamp. Original
annotations and images are left unchanged.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import re

from .scenario_corpus_scope import user_scope


COLLECTION = Path(__file__).resolve().parents[1] / "_knowledge_base/manual_reviews/scenarios_dzhahan_20260925"
MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
    "мая": 5, "июня": 6, "июля": 7, "августа": 8,
    "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}
# Both originals were inspected for these exceptions on 2026-09-27.
VISUAL_EXCEPTIONS = {
    13: "D1 says ETH 22 АПРЕЛЯ; H1 has no date/year. Year remains unknown.",
    33: "D1 says 13 АВГУСТА 2025; neither image names an instrument.",
    51: "D1 says 14 января 2026; neither image names an instrument.",
    78: "D1 explicitly says 26 ДЕКАБРЯ 2026 SOL, later than audit date. Caption retained; actual market date requires independent shape matching.",
    83: "D1 says 24 февраля 2025; neither image names an instrument.",
    111: "D1 says 21 ноября 2025; neither image names an instrument.",
    174: "D1 says 27 августа 2025; neither image names an instrument.",
    255: "D1 explicitly says 2 октября 2026 link, later than audit date. Caption retained; actual market date requires independent shape matching.",
    277: "D1 says 23 апреля 2026 H/Н. HUSDT is a candidate, not a verified market identity.",
    404: "D1 explicitly says 30 сентября 2026 virtual, later than audit date. Caption retained; actual market date requires independent shape matching.",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_caption_dates(label: str) -> dict:
    """Extract only explicitly captioned dates; preserve uncertain years/ranges."""
    normalized = label.casefold().replace("–", "-").replace("—", "-")
    days = re.search(r"(?<!\d)(\d{1,2})(?:\s*-\s*(\d{1,2}))?\s+(" + "|".join(MONTHS) + r")\b", normalized)
    years = re.findall(r"(?<!\d)(20\d{2})(?!\d)", normalized)
    abbreviated = False
    if not years:
        short_year = re.search(r"(?:" + "|".join(MONTHS) + r")\s+(\d{2})(?!\d)", normalized)
        if short_year:
            years = [str(2000 + int(short_year.group(1)))]
            abbreviated = True
    result = {"caption_date_candidates": [], "date_precision": "unparsed", "year_abbreviation_expanded": abbreviated}
    if not days:
        return result
    day1, day2, month = int(days.group(1)), int(days.group(2) or days.group(1)), MONTHS[days.group(3)]
    result.update(caption_day=day1, caption_end_day=day2, caption_month=month)
    if not years:
        result["date_precision"] = "year_missing"
        return result
    if len(set(years)) != 1:
        result["date_precision"] = "conflicting_years"
        return result
    year = int(years[0])
    try:
        first, last = date(year, month, day1), date(year, month, day2)
    except ValueError:
        result["date_precision"] = "invalid_caption_date"
        return result
    if last < first:
        result["date_precision"] = "invalid_caption_range"
        return result
    result["caption_date_candidates"] = [(first + timedelta(days=i)).isoformat() for i in range((last - first).days + 1)]
    result["date_precision"] = "day" if first == last else "day_range"
    return result


def build_metadata(collection: Path = COLLECTION, as_of: date = date(2026, 9, 27)) -> tuple[list[dict], dict]:
    collection = Path(collection)
    skipped_ids, target = user_scope(collection)
    scope_path = collection / "training/user_scope.json"
    scope_hash = sha256(scope_path) if scope_path.is_file() else None
    annotation_path = collection / "visual_analysis/scenario_analysis.jsonl"
    annotations = [json.loads(line) for line in annotation_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    ids = [int(row["scenario_id"]) for row in annotations]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate scenario IDs")
    annotation_hash = sha256(annotation_path)
    records = []
    for row in sorted(annotations, key=lambda item: item["scenario_id"]):
        sid = int(row["scenario_id"])
        if sid in skipped_ids:
            continue
        instrument = (row.get("instrument") or "").strip().upper()
        status = "caption_transcribed" if instrument else "caption_missing"
        if instrument in {"H", "Н"}:
            status = "single_character_caption_needs_market_match"
        hashes = {}
        for timeframe in ("1D", "1H"):
            relative = f"images/{timeframe}_{sid}.jpg"
            digest = sha256(collection / relative)
            if row.get("image_sha256", {}).get(relative) != digest:
                raise ValueError(f"image hash mismatch: {relative}")
            hashes[relative] = digest
        dates = parse_caption_dates(row.get("date_label") or "")
        pending = ["market_category_unverified", "caption_date_role_unverified", "pixel_to_market_alignment_required"]
        if status != "caption_transcribed":
            pending.append("instrument_identity_unverified")
        if dates["date_precision"] not in {"day", "day_range"}:
            pending.append("caption_year_or_date_unresolved")
        if any(date.fromisoformat(value) > as_of for value in dates["caption_date_candidates"]):
            pending.append("caption_after_audit_date")
        records.append({
            "scenario_id": sid, "instrument_caption": row.get("instrument"),
            "instrument_candidate": instrument or None,
            "bybit_symbol_candidate": ("HUSDT" if instrument == "Н" else instrument + "USDT") if instrument else None,
            "instrument_status": status, "date_caption": row.get("date_label"), **dates,
            "date_role": "unverified_search_hint_not_signal_or_entry_timestamp",
            "market_verified": False, "market_category": None,
            "pending": pending,
            "source_annotations": "visual_analysis/scenario_analysis.jsonl",
            "source_annotations_sha256": annotation_hash,
            "user_scope_sha256": scope_hash,
            "source_image_sha256": hashes,
            "exception_images_reinspected": sid in VISUAL_EXCEPTIONS,
            "exception_review_note": VISUAL_EXCEPTIONS.get(sid),
        })
    summary = {
        "schema_version": 1, "as_of": as_of.isoformat(), "target_count": len(records),
        "original_annotation_count": len(annotations), "user_authorized_skip": sorted(skipped_ids),
        "requested_target_count": target, "user_scope_sha256": scope_hash,
        "source_annotations_sha256": annotation_hash, "metadata_builder_sha256": sha256(Path(__file__)),
        "date_precision_counts": dict(Counter(row["date_precision"] for row in records)),
        "instrument_status_counts": dict(Counter(row["instrument_status"] for row in records)),
        "missing_instrument_ids": [row["scenario_id"] for row in records if row["instrument_status"] == "caption_missing"],
        "ambiguous_instrument_ids": [row["scenario_id"] for row in records if row["instrument_status"] == "single_character_caption_needs_market_match"],
        "unresolved_date_ids": [row["scenario_id"] for row in records if row["date_precision"] not in {"day", "day_range"}],
        "future_caption_ids": [row["scenario_id"] for row in records if "caption_after_audit_date" in row["pending"]],
        "market_verified_count": 0,
        "meaning": f"{len(records)} source pairs inventoried for market search. Captions alone do not establish actual market, D1 close, or entry time.",
    }
    return records, summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", type=Path, default=COLLECTION)
    args = parser.parse_args()
    records, summary = build_metadata(args.collection)
    out = args.collection / "training"
    out.mkdir(parents=True, exist_ok=True)
    (out / "market_metadata.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records), encoding="utf-8")
    (out / "market_metadata_report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
