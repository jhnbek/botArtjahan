from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TELEGRAM_CATALOG = Path("_knowledge_base/structured/supplemental_charts/telegram_2024-09-26/trade_catalog.json")
LECTURE_SEED_CATALOG = Path(
    "_knowledge_base/structured/consolidation/trade_review_calibration_candidates/visual_review_seed_catalog.json"
)
OUTPUT_DIR = Path("_knowledge_base/structured/consolidation/calibration_ground_truth_catalog")


def default_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_date(value: str) -> str:
    if not value:
        return value
    if "-" in value and len(value.split("-", 1)[0]) == 4:
        return value
    day, month, year = value.split(".")
    return f"{year}-{month.zfill(2)}-{day.zfill(2)}"


def market_kind(exchange: str) -> str:
    exchange_upper = (exchange or "").upper()
    if exchange_upper == "BINANCE":
        return "crypto_spot"
    if exchange_upper in {"NASDAQ", "NYSE"}:
        return "us_equity"
    if exchange_upper == "MOEX":
        return "moex_equity"
    return "unknown"


def data_symbol(ticker: str, exchange: str) -> str:
    if (exchange or "").upper() == "BINANCE":
        return f"{ticker.upper()}USDT"
    return ticker.upper()


def telegram_case(row: dict[str, Any], index: int, dataset_id: str) -> dict[str, Any]:
    exchange = row.get("exchange") or ""
    ticker = row.get("ticker") or ""
    return {
        "calibration_case_id": f"TG-{index:04d}",
        "source_type": "telegram_visual_trade_catalog",
        "source_dataset": dataset_id,
        "source_ref": row.get("source_photo"),
        "source_confidence": "high",
        "extraction_method": "manual_visual_review",
        "instrument": {
            "ticker": ticker,
            "name": row.get("name"),
            "exchange": exchange,
            "market_kind": market_kind(exchange),
            "data_symbol": data_symbol(ticker, exchange),
        },
        "decision": {
            "date": normalize_date(row.get("date")),
            "timeframe": "D1",
            "level": row.get("level"),
            "close": row.get("close"),
            "direction": row.get("direction"),
            "verdict_price": row.get("verdict_price"),
            "expert_verdict": f"valid_{row.get('direction')}_setup",
        },
        "readiness": {
            "ready_for_ohlc_bridge": True,
            "blockers": [],
            "notes": row.get("notes"),
        },
    }


def lecture_case(row: dict[str, Any], index: int, dataset_id: str) -> dict[str, Any]:
    exchange = row.get("exchange") or ""
    ticker = row.get("ticker") or ""
    return {
        "calibration_case_id": f"LC-{index:04d}",
        "source_type": "lecture_visual_trade_seed",
        "source_dataset": dataset_id,
        "source_ref": {
            "source_candidate_id": row.get("source_candidate_id"),
            "chunk_id": row.get("chunk_id"),
            "lecture_title": row.get("lecture_title"),
            "timecode": row.get("timecode"),
            "source_frames": row.get("source_frames") or [],
        },
        "source_confidence": row.get("confidence") or "high",
        "extraction_method": "manual_visual_review_of_mined_lecture_candidate",
        "instrument": {
            "ticker": ticker,
            "name": row.get("name"),
            "exchange": exchange,
            "market_kind": market_kind(exchange),
            "data_symbol": data_symbol(ticker, exchange),
        },
        "decision": {
            "date": normalize_date(row.get("date")),
            "timeframe": row.get("primary_timeframe") or "D1",
            "secondary_timeframe": row.get("secondary_timeframe"),
            "level": row.get("level"),
            "close": row.get("close"),
            "direction": row.get("direction"),
            "verdict_price": row.get("level"),
            "scenario": row.get("scenario"),
            "expert_verdict": row.get("expert_verdict"),
        },
        "readiness": {
            "ready_for_ohlc_bridge": True,
            "blockers": [],
            "notes": row.get("evidence"),
        },
    }


def build_catalog(root: Path) -> dict[str, Any]:
    telegram = load_json(root / TELEGRAM_CATALOG)
    lecture = load_json(root / LECTURE_SEED_CATALOG)

    cases = [telegram_case(row, idx, telegram["dataset_id"]) for idx, row in enumerate(telegram.get("trades") or [], start=1)]
    cases.extend(
        lecture_case(row, idx, lecture["dataset_id"])
        for idx, row in enumerate(lecture.get("promoted_trades") or [], start=1)
    )

    source_counts = Counter(case["source_type"] for case in cases)
    market_counts = Counter(case["instrument"]["market_kind"] for case in cases)
    exchange_counts = Counter(case["instrument"]["exchange"] for case in cases)
    catalog = {
        "dataset_id": "calibration_ground_truth_catalog_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "role": "engine_vs_expert_calibration_ground_truth_input",
        "source_inputs": {
            "telegram_catalog": TELEGRAM_CATALOG.as_posix(),
            "lecture_seed_catalog": LECTURE_SEED_CATALOG.as_posix(),
        },
        "case_count": len(cases),
        "source_counts": dict(source_counts),
        "market_kind_counts": dict(market_counts),
        "exchange_counts": dict(exchange_counts),
        "ready_for_ohlc_bridge_count": sum(1 for case in cases if case["readiness"]["ready_for_ohlc_bridge"]),
        "cases": cases,
        "bridge_notes": [
            "BINANCE cases can use public crypto OHLC adapters first.",
            "NASDAQ/NYSE and MOEX cases require an explicit vendor/adjustment policy before automated historical loading.",
            "Lecture partial reviews remain excluded until source/date/level are fully pinned.",
        ],
    }
    return catalog


def write_report(path: Path, catalog: dict[str, Any]) -> None:
    lines = [
        "# Calibration Ground Truth Catalog",
        "",
        f"Generated: `{catalog['generated_at']}`",
        "",
        "## Summary",
        "",
        f"- Cases: {catalog['case_count']}",
        f"- Ready for OHLC bridge: {catalog['ready_for_ohlc_bridge_count']}",
        f"- Source counts: `{catalog['source_counts']}`",
        f"- Market counts: `{catalog['market_kind_counts']}`",
        f"- Exchange counts: `{catalog['exchange_counts']}`",
        "",
        "## OHLC Bridge Order",
        "",
        "1. Binance crypto cases: public data is easiest to source and avoids equity adjustment ambiguity.",
        "2. US equities: require declared adjusted/raw OHLC policy and vendor choice.",
        "3. MOEX equities: require vendor and exchange calendar policy.",
        "",
        "## Cases",
        "",
        "| ID | Source | Market | Symbol | Date | Direction | Level | Close | Verdict price |",
        "|---|---|---|---|---|---|---:|---:|---:|",
    ]
    for case in catalog["cases"]:
        instrument = case["instrument"]
        decision = case["decision"]
        lines.append(
            "| "
            f"{case['calibration_case_id']} | {case['source_type']} | {instrument['market_kind']} | "
            f"{instrument['data_symbol']} | {decision['date']} | {decision['direction']} | "
            f"{decision.get('level')} | {decision.get('close')} | {decision.get('verdict_price')} |"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a normalized calibration ground-truth catalog from promoted sources.")
    parser.add_argument("--root", type=Path, default=default_root())
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    catalog = build_catalog(root)
    output_dir = root / OUTPUT_DIR
    write_json(output_dir / "ground_truth_catalog.json", catalog)
    write_report(output_dir / "ground_truth_catalog.md", catalog)
    print(
        f"[OK] built {catalog['case_count']} calibration cases; "
        f"markets={catalog['market_kind_counts']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())