from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ingest_raw_lectures import get_ocr_reader, ocr_frame, release_ocr_reader

INGEST_VERSION = "scenario_archive_ingest_v1"

# Имена файлов в архиве сценариев: 1D_392.jpg / 1H_36.jpg
NAME_RE = re.compile(r"^(1D|1H)_(\d+)\.jpe?g$", re.IGNORECASE)

RU_MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}
# OCR смешивает латиницу и кириллицу в русских словах ("МАРТA" с латинской A)
LATIN_TO_CYR = str.maketrans(
    "ABCEHKMOPTXYaBcexopy",
    "АВСЕНКМОРТХУаВсехору",
)
MONTH_WORD = r"([а-яё]{3,9})"
# Полная дата (год может быть двузначным), либо месяц+год без дня, либо день+месяц без года
DATE_FULL_RE = re.compile(r"(\d{1,2})\s+" + MONTH_WORD + r"\s+(\d{2}|\d{4})\b")
DATE_MONTH_YEAR_RE = re.compile(MONTH_WORD + r"\s+(\d{2}|\d{4})\b")
DATE_DAY_MONTH_RE = re.compile(r"(\d{1,2})\s+" + MONTH_WORD + r"\b")
# Обратный маппинг для тикеров, распознанных кириллицей ("етн" -> "ETH")
CYR_TO_LATIN = str.maketrans("АВСЕНКМОРТХУавсенкмортху", "ABCEHKMOPTXYabcehkmoptxy")


def levenshtein(a: str, b: str) -> int:
    if abs(len(a) - len(b)) > 2:
        return 99
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def resolve_month(word: str) -> int | None:
    word = word.lower()
    best_month, best_dist = None, 99
    for name, number in RU_MONTHS.items():
        dist = levenshtein(word, name)
        if dist < best_dist:
            best_month, best_dist = number, dist
    threshold = 1 if len(word) <= 4 else 2
    return best_month if best_dist <= threshold else None
# Ветка сценария: "1) ложный пробой", допускаем OCR-шум вместо скобки
BRANCH_RE = re.compile(r"^\s*(\d)\s*[)\].,:]?\s+(.{3,})$")
TICKER_RE = re.compile(r"\b([a-z]{3,6})\b", re.IGNORECASE)
# OCR-шум и подписи, похожие на латинские токены ("график ID", "IH", "ТВX" и т.п.)
TICKER_STOPWORDS = {
    "the", "and", "xlt", "hi", "ii", "iii", "it", "itt", "id", "ih", "ip",
    "atp", "tbx", "atr", "ho", "ha", "tk", "hbx",
}


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def default_root() -> Path:
    return Path(__file__).resolve().parents[1]


def compact_ocr(payload: dict[str, Any]) -> dict[str, Any]:
    lines = [
        {"text": line.get("text", ""), "conf": round(float(line.get("confidence", 0.0)), 3)}
        for line in payload.get("lines", [])
        if str(line.get("text", "")).strip()
    ]
    out: dict[str, Any] = {"text": payload.get("text", ""), "lines": lines}
    if payload.get("error"):
        out["error"] = payload["error"]
    return out


# Известные OCR-искажения тикеров на графиках ("DOGE" читается как "[UGE"/"[OGE")
TICKER_OCR_FIXES = {"UGE": "DOGE", "OGE": "DOGE"}


def ticker_candidates(text: str) -> list[str]:
    out = []
    for token in TICKER_RE.findall(text):
        if token.lower() in TICKER_STOPWORDS:
            continue
        # слово-месяц, распознанное латиницей ("MAPTA"), тикером не считается
        if resolve_month(token.translate(LATIN_TO_CYR).lower()):
            continue
        token = token.upper()
        out.append(TICKER_OCR_FIXES.get(token, token))
    return out


def normalize_year(raw: str) -> int:
    year = int(raw)
    return year + 2000 if year < 100 else year


def match_date(text: str) -> tuple[int | None, int | None, int | None] | None:
    """(day, month, year), любое поле может быть None у частичной даты."""
    normalized = text.translate(LATIN_TO_CYR).lower()
    for match in DATE_FULL_RE.finditer(normalized):
        day, month_word, year = match.groups()
        month = resolve_month(month_word)
        if month:
            return int(day), month, normalize_year(year)
    for match in DATE_MONTH_YEAR_RE.finditer(normalized):
        month_word, year = match.groups()
        month = resolve_month(month_word)
        if month:
            return None, month, normalize_year(year)
    for match in DATE_DAY_MONTH_RE.finditer(normalized):
        day, month_word = match.groups()
        month = resolve_month(month_word)
        if month:
            return int(day), month, None
    return None


def parse_d1_annotation(ocr_lines: list[dict[str, Any]]) -> dict[str, Any]:
    parsed: dict[str, Any] = {"date": None, "date_partial": None, "ticker": None, "branches": []}
    date_line: str | None = None
    for line in ocr_lines:
        text = str(line.get("text", "")).strip()
        found = match_date(text) if (parsed["date"] is None and parsed["date_partial"] is None) else None
        if found:
            day, month, year = found
            if day and month and year:
                parsed["date"] = f"{year:04d}-{month:02d}-{day:02d}"
            else:
                parsed["date_partial"] = {"day": day, "month": month, "year": year}
            date_line = text
            continue
        branch_match = BRANCH_RE.match(text)
        if branch_match and not any(ch.isdigit() for ch in branch_match.group(2)[:2]):
            parsed["branches"].append(branch_match.group(2).strip())
    # Тикер: сначала ищем в строке с датой (может стоять до или после даты),
    # затем — отдельной короткой строкой ("BTC").
    if date_line:
        candidates = ticker_candidates(date_line)
        if not candidates:
            # тикер мог распознаться кириллицей ("етн" -> ETH); слова-месяцы не тикеры
            for token in re.findall(r"\b([а-яё]{3,6})\b", date_line.lower()):
                if resolve_month(token):
                    continue
                latin = token.translate(CYR_TO_LATIN)
                if latin.isascii() and latin.isalpha() and latin.lower() not in TICKER_STOPWORDS:
                    candidates = [TICKER_OCR_FIXES.get(latin.upper(), latin.upper())]
                    break
        if candidates:
            parsed["ticker"] = candidates[0]
    if parsed["ticker"] is None:
        for line in ocr_lines:
            text = str(line.get("text", "")).strip()
            if len(text) <= 8:
                candidates = ticker_candidates(text)
                if candidates:
                    parsed["ticker"] = candidates[0]
                    break
    return parsed


def load_done_ids(index_path: Path) -> dict[int, dict[str, Any]]:
    done: dict[int, dict[str, Any]] = {}
    if not index_path.exists():
        return done
    with index_path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            raw = raw.strip()
            if not raw:
                continue
            try:
                record = json.loads(raw)
            except json.JSONDecodeError:
                continue
            done[int(record["scenario_id"])] = record
    return done


def discover_scenarios(images_dir: Path) -> dict[int, dict[str, Path]]:
    scenarios: dict[int, dict[str, Path]] = {}
    for path in sorted(images_dir.iterdir()):
        match = NAME_RE.match(path.name)
        if not match:
            continue
        timeframe, number = match.group(1).upper(), int(match.group(2))
        scenarios.setdefault(number, {})[timeframe] = path
    return scenarios


def display_date(parsed: dict[str, Any]) -> str:
    if parsed.get("date"):
        return parsed["date"]
    partial = parsed.get("date_partial")
    if partial:
        year = f"{partial['year']:04d}" if partial.get("year") else "????"
        month = f"{partial['month']:02d}" if partial.get("month") else "??"
        day = f"{partial['day']:02d}" if partial.get("day") else "??"
        return f"~{year}-{month}-{day}"
    return "—"


def write_summary(out_root: Path, records: list[dict[str, Any]]) -> None:
    parsed_dates = sum(1 for r in records if r["parsed"].get("date"))
    partial_dates = sum(1 for r in records if r["parsed"].get("date_partial"))
    parsed_tickers = sum(1 for r in records if r["parsed"].get("ticker"))
    with_branches = sum(1 for r in records if r["parsed"].get("branches"))
    ocr_errors = sum(
        1 for r in records
        if r.get("d1_ocr", {}).get("error") or r.get("h1_ocr", {}).get("error")
    )
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    future = [r["scenario_id"] for r in records if (r["parsed"].get("date") or "") > today]
    lines = [
        "# Индекс архива сценариев",
        "",
        f"Собрано: {datetime.now(timezone.utc).isoformat()}",
        f"Версия: {INGEST_VERSION}",
        "",
        f"- Сценариев: {len(records)}",
        f"- Полная дата: {parsed_dates}, частичная: {partial_dates}, без даты: {len(records) - parsed_dates - partial_dates}",
        f"- Распознан тикер: {parsed_tickers}",
        f"- Есть ветки сценария: {with_branches}",
        f"- Сценариев с ошибками OCR: {ocr_errors}",
        f"- Даты в будущем (вероятна описка в годе на графике): {future or 'нет'}",
        "",
        "| # | Дата | Тикер | Ветки |",
        "|---|---|---|---|",
    ]
    for r in sorted(records, key=lambda x: x["scenario_id"]):
        branches = "; ".join(r["parsed"].get("branches", [])) or "—"
        lines.append(
            f"| {r['scenario_id']} | {display_date(r['parsed'])} "
            f"| {r['parsed'].get('ticker') or '—'} | {branches} |"
        )
    (out_root / "scenario_index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    configure_stdio()
    parser = argparse.ArgumentParser(description="OCR-индексация архива ручных сценариев (пары 1D/1H JPG).")
    parser.add_argument("--images-dir", type=Path, default=None)
    parser.add_argument("--out-root", type=Path, default=None)
    parser.add_argument("--languages", default="ru,en")
    parser.add_argument("--min-conf", type=float, default=0.30)
    parser.add_argument("--no-gpu", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="обработать только первые N сценариев (для проверки)")
    parser.add_argument("--reparse", action="store_true",
                        help="пересобрать parsed-поля и сводку из уже сохранённого OCR, без запуска OCR")
    args = parser.parse_args()

    root = default_root()
    out_root = args.out_root or root / "_scenario_archive"
    images_dir = args.images_dir or out_root / "images"
    if not images_dir.is_dir():
        print(f"images dir not found: {images_dir}")
        return 2

    index_path = out_root / "scenario_index.jsonl"
    if args.reparse:
        done = load_done_ids(index_path)
        for record in done.values():
            record["parsed"] = parse_d1_annotation(record.get("d1_ocr", {}).get("lines", []))
        with index_path.open("w", encoding="utf-8") as handle:
            for number in sorted(done):
                handle.write(json.dumps(done[number], ensure_ascii=False) + "\n")
        write_summary(out_root, list(done.values()))
        parsed_dates = sum(1 for r in done.values() if r["parsed"].get("date"))
        print(f"reparsed {len(done)} records, full dates: {parsed_dates}")
        print(f"summary: {out_root / 'scenario_index.md'}")
        return 0

    scenarios = discover_scenarios(images_dir)
    done = load_done_ids(index_path)
    todo = [n for n in sorted(scenarios) if n not in done]
    if args.limit:
        todo = todo[: args.limit]
    print(f"scenarios: {len(scenarios)} total, {len(done)} already indexed, {len(todo)} to process")
    if todo:
        languages = [x.strip() for x in args.languages.split(",") if x.strip()]
        reader = get_ocr_reader(languages, use_gpu=not args.no_gpu)
        try:
            with index_path.open("a", encoding="utf-8") as handle:
                for pos, number in enumerate(todo, 1):
                    pair = scenarios[number]
                    d1_ocr = compact_ocr(ocr_frame(reader, pair["1D"], args.min_conf)) if "1D" in pair else {}
                    h1_ocr = compact_ocr(ocr_frame(reader, pair["1H"], args.min_conf)) if "1H" in pair else {}
                    record = {
                        "scenario_id": number,
                        "d1_image": str(pair["1D"].relative_to(out_root)) if "1D" in pair else None,
                        "h1_image": str(pair["1H"].relative_to(out_root)) if "1H" in pair else None,
                        "d1_ocr": d1_ocr,
                        "h1_ocr": h1_ocr,
                        "parsed": parse_d1_annotation(d1_ocr.get("lines", [])),
                        "ingest_version": INGEST_VERSION,
                    }
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                    handle.flush()
                    done[number] = record
                    if pos % 20 == 0 or pos == len(todo):
                        print(f"[{pos}/{len(todo)}] scenario {number}: "
                              f"date={record['parsed'].get('date')} ticker={record['parsed'].get('ticker')}")
        finally:
            release_ocr_reader()

    write_summary(out_root, list(done.values()))
    print(f"index: {index_path}")
    print(f"summary: {out_root / 'scenario_index.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
