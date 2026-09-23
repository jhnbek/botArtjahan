from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ingest_raw_lectures import get_ocr_reader, ocr_frame, release_ocr_reader


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
INGEST_VERSION = "supplemental_chart_ingest_v1"


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def default_root() -> Path:
    return Path(__file__).resolve().parents[1]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def image_size(path: Path) -> tuple[int | None, int | None]:
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore

        raw = np.fromfile(str(path), dtype=np.uint8)
        image = cv2.imdecode(raw, cv2.IMREAD_COLOR)
        if image is None:
            return None, None
        height, width = image.shape[:2]
        return int(width), int(height)
    except Exception:
        return None, None


def parse_source_name(path: Path) -> dict[str, Any]:
    match = re.match(r"photo_(\d+)_(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})", path.stem)
    if not match:
        return {"photo_index": None, "series_id": "unknown"}
    return {"photo_index": int(match.group(1)), "series_id": match.group(2)}


def normalise_text(value: str) -> str:
    return " ".join((value or "").replace("\n", " ").split())


def detect_timeframes(text: str) -> list[str]:
    cleaned = text.lower().replace(" ", "")
    candidates = []
    patterns = {
        "1m": ["1m", "1м"],
        "5m": ["5m", "5м"],
        "15m": ["15m", "15м"],
        "30m": ["30m", "30м"],
        "1h": ["1h", "1ч", "h1"],
        "4h": ["4h", "4ч", "h4"],
        "1d": ["1d", "1д", "d1"],
        "1w": ["1w", "1н", "w1"],
    }
    for value, aliases in patterns.items():
        if any(alias in cleaned for alias in aliases):
            candidates.append(value)
    return candidates


def detect_ticker_candidates(text: str) -> list[str]:
    values: list[str] = []
    for raw in re.findall(r"\b[A-ZА-Я0-9]{2,15}\b", text.upper()):
        if raw in {"USD", "USDT", "PERPETUAL", "CONTRACT", "BINANCE", "NASDAQ", "NYSE", "RUS", "PINE"}:
            continue
        if any(token in raw for token in ["USDT", "USD", "BTC", "ETH", "TON", "APT", "WIF", "SHIB", "PEOPLE", "TAO"]):
            values.append(raw)
    return sorted(set(values))[:8]


def detect_keywords(text: str) -> list[str]:
    lowered = text.lower()
    checks = {
        "tradingview": "tradingview" in lowered or "tradingv" in lowered,
        "binance": "binance" in lowered,
        "bybit": "bybit" in lowered,
        "nasdaq": "nasdaq" in lowered,
        "nyse": "nyse" in lowered,
        "russian_market": "rus" in lowered or "moex" in lowered,
        "level_text": "уров" in lowered or "урокень" in lowered,
        "base_text": "основа" in lowered or "осн" in lowered,
        "confirmation_text": "подтверж" in lowered,
        "buy_sell_panel": "купить" in lowered or "продать" in lowered,
    }
    return [key for key, present in checks.items() if present]


def make_observation(record: dict[str, Any], ocr: dict[str, Any]) -> dict[str, Any]:
    text = normalise_text(ocr.get("text", ""))
    return {
        "image_id": record["image_id"],
        "series_id": record["series_id"],
        "photo_index": record["photo_index"],
        "path": record["path"],
        "source_name": record["source_name"],
        "width": record["width"],
        "height": record["height"],
        "ocr_text_preview": text[:500],
        "ocr_line_count": len(ocr.get("lines") or []),
        "timeframe_candidates": detect_timeframes(text),
        "ticker_candidates": detect_ticker_candidates(text),
        "visual_keywords": detect_keywords(text),
        "link_status": "unlinked_supplemental_example",
        "review_status": "needs_visual_review",
        "maturity": "supplemental_visual_observation",
    }


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) for row in rows) + "\n", encoding="utf-8")


def create_contact_sheets(dataset_dir: Path, records: list[dict[str, Any]], columns: int, thumb_width: int) -> list[dict[str, Any]]:
    import cv2  # type: ignore
    import numpy as np  # type: ignore

    sheets_dir = dataset_dir / "contact_sheets"
    sheets_dir.mkdir(parents=True, exist_ok=True)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[record["series_id"]].append(record)

    outputs = []
    for series_id, items in sorted(groups.items()):
        sorted_items = sorted(items, key=lambda item: (item.get("photo_index") is None, item.get("photo_index") or 0, item["source_name"]))
        tiles = []
        max_tile_height = 0
        for record in sorted_items:
            path = dataset_dir / record["path"]
            raw = np.fromfile(str(path), dtype=np.uint8)
            image = cv2.imdecode(raw, cv2.IMREAD_COLOR)
            if image is None:
                image = np.full((thumb_width, thumb_width, 3), 245, dtype=np.uint8)
            height, width = image.shape[:2]
            scale = thumb_width / max(1, width)
            resized_height = max(1, int(height * scale))
            resized = cv2.resize(image, (thumb_width, resized_height), interpolation=cv2.INTER_AREA)
            label_height = 42
            tile = np.full((resized_height + label_height, thumb_width, 3), 255, dtype=np.uint8)
            tile[:resized_height, :thumb_width] = resized
            label = f"{record['image_id']} | p{record.get('photo_index')}"
            cv2.putText(tile, label, (8, resized_height + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (0, 0, 0), 1, cv2.LINE_AA)
            cv2.putText(tile, record["source_name"][:34], (8, resized_height + 36), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (50, 50, 50), 1, cv2.LINE_AA)
            tiles.append(tile)
            max_tile_height = max(max_tile_height, tile.shape[0])

        if not tiles:
            continue
        padded_tiles = []
        for tile in tiles:
            if tile.shape[0] < max_tile_height:
                pad = np.full((max_tile_height - tile.shape[0], thumb_width, 3), 255, dtype=np.uint8)
                tile = np.vstack([tile, pad])
            padded_tiles.append(tile)

        rows = []
        for start in range(0, len(padded_tiles), columns):
            row_tiles = padded_tiles[start:start + columns]
            while len(row_tiles) < columns:
                row_tiles.append(np.full((max_tile_height, thumb_width, 3), 255, dtype=np.uint8))
            rows.append(np.hstack(row_tiles))
        sheet = np.vstack(rows)
        output = sheets_dir / f"series_{series_id}.jpg"
        ok, encoded = cv2.imencode(".jpg", sheet, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
        if not ok:
            raise RuntimeError(f"failed to encode contact sheet: {output}")
        encoded.tofile(str(output))
        outputs.append({"series_id": series_id, "path": output.relative_to(dataset_dir).as_posix(), "image_count": len(sorted_items)})
    return outputs


def write_review(dataset_dir: Path, manifest: dict[str, Any], observations: list[dict[str, Any]]) -> None:
    series_counts = Counter(item["series_id"] for item in observations)
    keyword_counts = Counter(keyword for item in observations for keyword in item["visual_keywords"])
    timeframe_counts = Counter(tf for item in observations for tf in item["timeframe_candidates"])
    ticker_counts = Counter(ticker for item in observations for ticker in item["ticker_candidates"])
    lines = [
        "# Supplemental Chart Review: Telegram 2024-09-26",
        "",
        "## Scope",
        "",
        f"- Dataset ID: `{manifest['dataset_id']}`",
        f"- Source directory: `{manifest['source_dir']}`",
        f"- Images copied: {manifest['image_count']}",
        f"- Total bytes: {manifest['total_bytes']}",
        "- Source role: supplemental visual examples, not lecture-transcript claims.",
        "- Link policy: keep images unlinked unless a later pass can connect them to a lecture claim with strong evidence.",
        "",
        "## Series",
        "",
    ]
    for series_id, count in sorted(series_counts.items()):
        lines.append(f"- `{series_id}`: {count} images")
    lines.extend([
        "",
        "## OCR/Visual Index Signals",
        "",
        f"- OCR nonempty images: {sum(1 for item in observations if item['ocr_line_count'] > 0)} / {len(observations)}",
        f"- Top keywords: {dict(keyword_counts.most_common(12))}",
        f"- Timeframe candidates: {dict(timeframe_counts.most_common())}",
        f"- Ticker candidates: {dict(ticker_counts.most_common(20))}",
        "",
        "## Preliminary Interpretation",
        "",
        "This layer is a visual supplement to the lecture-derived knowledge base. It should be used as example material for chart-level concepts such as levels, bases, confirmations, exchange-specific charts, and TradingView screenshots, but it should not create executable rules without lecture-source support.",
        "",
        "## Files",
        "",
        "- `manifest.json`: copied image inventory with hashes and dimensions.",
        "- `ocr.json`: EasyOCR output per image.",
        "- `observations.jsonl`: one lightweight visual observation per image.",
        "- `contact_sheets/`: review sheets grouped by Telegram timestamp series.",
    ])
    (dataset_dir / "supplemental_review.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest standalone chart images as a supplemental visual corpus.")
    parser.add_argument("--source-dir", type=Path, required=True, help="Directory containing chart images.")
    parser.add_argument("--root", type=Path, default=default_root(), help="Workspace root.")
    parser.add_argument("--dataset-id", default="telegram_2024-09-26", help="Supplemental dataset identifier.")
    parser.add_argument("--languages", default="ru,en", help="EasyOCR languages, comma-separated.")
    parser.add_argument("--min-conf", type=float, default=0.25, help="Minimum OCR confidence.")
    parser.add_argument("--gpu", action="store_true", help="Use EasyOCR GPU mode.")
    parser.add_argument("--max-dimension", type=int, default=1400, help="Resize OCR input to this max side; 0 keeps original.")
    parser.add_argument("--overwrite", action="store_true", help="Recreate existing dataset directory.")
    parser.add_argument("--thumb-width", type=int, default=420, help="Contact sheet thumbnail width.")
    parser.add_argument("--columns", type=int, default=3, help="Contact sheet columns.")
    return parser.parse_args()


def main() -> int:
    configure_stdio()
    args = parse_args()
    root = args.root.resolve()
    source_dir = args.source_dir.resolve()
    dataset_dir = root / "_knowledge_base" / "structured" / "supplemental_charts" / args.dataset_id
    if not source_dir.exists():
        print(f"error: source directory not found: {source_dir}", file=sys.stderr)
        return 2
    if dataset_dir.exists() and args.overwrite:
        shutil.rmtree(dataset_dir)
    dataset_dir.mkdir(parents=True, exist_ok=True)
    images_dir = dataset_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    source_images = sorted([path for path in source_dir.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES], key=lambda path: path.name.lower())
    if not source_images:
        print("no source images found")
        return 0

    records: list[dict[str, Any]] = []
    ocr_records: list[dict[str, Any]] = []
    languages = [item.strip() for item in args.languages.split(",") if item.strip()]
    reader = get_ocr_reader(languages, args.gpu)
    max_dimension = args.max_dimension or None
    try:
        for idx, source_path in enumerate(source_images, start=1):
            parsed = parse_source_name(source_path)
            image_id = f"SCI-{idx:04d}"
            dest_name = f"{image_id}_{source_path.name}"
            dest_path = images_dir / dest_name
            if not dest_path.exists() or args.overwrite:
                shutil.copy2(source_path, dest_path)
            width, height = image_size(dest_path)
            record = {
                "image_id": image_id,
                "dataset_id": args.dataset_id,
                "source_name": source_path.name,
                "source_path": str(source_path),
                "path": dest_path.relative_to(dataset_dir).as_posix(),
                "series_id": parsed["series_id"],
                "photo_index": parsed["photo_index"],
                "size_bytes": dest_path.stat().st_size,
                "sha256": sha256_file(dest_path),
                "width": width,
                "height": height,
            }
            print(f"ocr {idx}/{len(source_images)} {source_path.name}", flush=True)
            ocr = ocr_frame(reader, dest_path, args.min_conf, max_dimension=max_dimension)
            ocr_record = {
                "image_id": image_id,
                "path": record["path"],
                "source_name": source_path.name,
                "series_id": record["series_id"],
                "text": ocr.get("text", ""),
                "lines": ocr.get("lines", []),
                **({"error": ocr["error"]} if ocr.get("error") else {}),
            }
            records.append(record)
            ocr_records.append(ocr_record)
    finally:
        release_ocr_reader()

    observations = [make_observation(record, ocr) for record, ocr in zip(records, ocr_records)]
    contact_sheets = create_contact_sheets(dataset_dir, records, args.columns, args.thumb_width)
    manifest = {
        "dataset_id": args.dataset_id,
        "ingest_version": INGEST_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_dir": str(source_dir),
        "output_dir": str(dataset_dir),
        "image_count": len(records),
        "total_bytes": sum(record["size_bytes"] for record in records),
        "ocr_engine": "easyocr",
        "ocr_languages": languages,
        "ocr_min_conf": args.min_conf,
        "ocr_max_dimension": max_dimension,
        "contact_sheets": contact_sheets,
        "images": records,
    }
    write_json(dataset_dir / "manifest.json", manifest)
    write_json(dataset_dir / "ocr.json", {"dataset_id": args.dataset_id, "frames": ocr_records})
    write_jsonl(dataset_dir / "observations.jsonl", observations)
    write_review(dataset_dir, manifest, observations)
    print(
        "supplemental_charts_ingested="
        f"images={len(records)} "
        f"ocr_nonempty={sum(1 for item in observations if item['ocr_line_count'] > 0)} "
        f"series={len(set(item['series_id'] for item in records))} "
        f"output={dataset_dir}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())