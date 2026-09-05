from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import urllib.error
import urllib.request
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


GROUND_TRUTH_CATALOG = Path("_knowledge_base/structured/consolidation/calibration_ground_truth_catalog/ground_truth_catalog.json")
OUTPUT_DIR = Path("_knowledge_base/structured/consolidation/calibration_ohlc_bridge_crypto")
DATA_ROOT = Path("_historical_data/public/calibration_crypto_spot_1d_v1")
BASE_URL = "https://data.binance.vision/data/spot/monthly/klines"
INTERVAL = "1d"


@dataclass
class DownloadResult:
    status: str
    url: str
    path: str
    error: str = ""


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def default_root() -> Path:
    return Path(__file__).resolve().parents[1]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) for row in rows)
    path.write_text(payload + ("\n" if payload else ""), encoding="utf-8")


def month_for_date(date: str) -> str:
    return date[:7]


def archive_name(symbol: str, month: str) -> str:
    return f"{symbol}-{INTERVAL}-{month}.zip"


def archive_url(symbol: str, month: str) -> str:
    return f"{BASE_URL}/{symbol}/{INTERVAL}/{archive_name(symbol, month)}"


def archive_path(root: Path, symbol: str, month: str) -> Path:
    return root / DATA_ROOT / "archives" / symbol / INTERVAL / archive_name(symbol, month)


def download_archive(path: Path, url: str) -> DownloadResult:
    if path.exists() and path.stat().st_size > 0:
        return DownloadResult(status="already_present", url=url, path=path.as_posix())
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(url, timeout=45) as response:
            data = response.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        return DownloadResult(status="failed", url=url, path=path.as_posix(), error=str(exc))
    path.write_bytes(data)
    return DownloadResult(status="downloaded", url=url, path=path.as_posix())


def ms_to_iso(value: str) -> str:
    timestamp = datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    return timestamp.isoformat().replace("+00:00", "Z")


def date_from_open_ms(value: str) -> str:
    return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).date().isoformat()


def row_to_bar(symbol: str, row: list[str], ingested_at: str) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "timeframe": "D1",
        "open_time_utc": ms_to_iso(row[0]),
        "close_time_utc": ms_to_iso(row[6]),
        "open": float(row[1]),
        "high": float(row[2]),
        "low": float(row[3]),
        "close": float(row[4]),
        "volume": float(row[5]),
        "data_source_id": "binance_public_data_spot_monthly_klines",
        "ingested_at_utc": ingested_at,
    }


def extract_daily_bar(path: Path, symbol: str, target_date: str, ingested_at: str) -> tuple[dict[str, Any] | None, str]:
    try:
        with zipfile.ZipFile(path) as archive:
            csv_names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
            if not csv_names:
                return None, "archive_has_no_csv"
            with archive.open(csv_names[0]) as handle:
                reader = csv.reader(io.TextIOWrapper(handle, encoding="utf-8"))
                for row in reader:
                    if not row or row[0] == "open_time":
                        continue
                    if date_from_open_ms(row[0]) == target_date:
                        return row_to_bar(symbol, row, ingested_at), ""
    except (OSError, zipfile.BadZipFile, ValueError, IndexError) as exc:
        return None, str(exc)
    return None, "target_date_not_found"


def direction_condition(direction: str, close: float, level: float | None) -> bool | None:
    if level is None:
        return None
    if direction == "long":
        return close > level
    if direction == "short":
        return close < level
    return None


def level_touched(bar: dict[str, Any], level: float | None) -> bool | None:
    if level is None:
        return None
    return bar["low"] <= level <= bar["high"]


def directional_reach(direction: str, bar: dict[str, Any], level: float | None) -> bool | None:
    if level is None:
        return None
    if direction == "long":
        return bar["high"] >= level
    if direction == "short":
        return bar["low"] <= level
    return None


def catalog_close_tolerance(catalog_close: Any) -> float | None:
    if catalog_close is None:
        return None
    numeric_close = abs(float(catalog_close))
    return max(0.02, numeric_close * 0.00001)


def close_matches_catalog(bar: dict[str, Any], catalog_close: Any) -> bool | None:
    tolerance = catalog_close_tolerance(catalog_close)
    if tolerance is None:
        return None
    return abs(bar["close"] - float(catalog_close)) <= tolerance


def bridge_case(root: Path, case: dict[str, Any], ingested_at: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
    symbol = case["instrument"]["data_symbol"]
    target_date = case["decision"]["date"]
    month = month_for_date(target_date)
    path = archive_path(root, symbol, month)
    url = archive_url(symbol, month)
    download = download_archive(path, url)
    result = {
        "calibration_case_id": case["calibration_case_id"],
        "symbol": symbol,
        "date": target_date,
        "direction": case["decision"].get("direction"),
        "level": case["decision"].get("level"),
        "catalog_close": case["decision"].get("close"),
        "catalog_verdict_price": case["decision"].get("verdict_price"),
        "download": download.__dict__,
        "status": "pending",
        "bar": None,
        "checks": {},
    }
    if download.status == "failed":
        result["status"] = "download_failed"
        return result, None

    bar, error = extract_daily_bar(path, symbol, target_date, ingested_at)
    if bar is None:
        result["status"] = "bar_missing"
        result["error"] = error
        return result, None

    level = case["decision"].get("level")
    direction = case["decision"].get("direction")
    numeric_level = float(level) if level is not None else None
    condition = direction_condition(direction, bar["close"], numeric_level)
    catalog_close = case["decision"].get("close")
    catalog_verdict_price = case["decision"].get("verdict_price")
    numeric_verdict_price = float(catalog_verdict_price) if catalog_verdict_price is not None else None
    result["status"] = "bar_found"
    result["bar"] = bar
    result["checks"] = {
        "close_minus_level": None if numeric_level is None else bar["close"] - numeric_level,
        "direction_condition_close_vs_level": condition,
        "level_touched_intraday": level_touched(bar, numeric_level),
        "directional_reach_intraday": directional_reach(direction, bar, numeric_level),
        "verdict_price_touched_intraday": level_touched(bar, numeric_verdict_price),
        "verdict_price_directional_reach_intraday": directional_reach(direction, bar, numeric_verdict_price),
        "catalog_close_matches_binance_close": close_matches_catalog(bar, catalog_close),
        "catalog_close_match_tolerance": catalog_close_tolerance(catalog_close),
        "catalog_close_delta_abs": None if catalog_close is None else abs(bar["close"] - float(catalog_close)),
        "catalog_close_delta": None if catalog_close is None else bar["close"] - float(catalog_close),
        "catalog_verdict_price_delta": None
        if catalog_verdict_price is None
        else bar["close"] - float(catalog_verdict_price),
    }
    bar_with_case = dict(bar)
    bar_with_case["calibration_case_id"] = case["calibration_case_id"]
    return result, bar_with_case


def build_bridge(root: Path) -> dict[str, Any]:
    catalog = read_json(root / GROUND_TRUTH_CATALOG)
    crypto_cases = [case for case in catalog.get("cases") or [] if case.get("instrument", {}).get("market_kind") == "crypto_spot"]
    ingested_at = datetime.now(timezone.utc).isoformat()
    results = []
    bars = []
    for case in crypto_cases:
        result, bar = bridge_case(root, case, ingested_at)
        results.append(result)
        if bar is not None:
            bars.append(bar)

    status_counts = Counter(result["status"] for result in results)
    passed_direction_count = sum(1 for result in results if result.get("checks", {}).get("direction_condition_close_vs_level") is True)
    level_touch_count = sum(1 for result in results if result.get("checks", {}).get("level_touched_intraday") is True)
    directional_reach_count = sum(1 for result in results if result.get("checks", {}).get("directional_reach_intraday") is True)
    verdict_price_reach_count = sum(
        1 for result in results if result.get("checks", {}).get("verdict_price_directional_reach_intraday") is True
    )
    catalog_close_match_count = sum(
        1 for result in results if result.get("checks", {}).get("catalog_close_matches_binance_close") is True
    )
    catalog_close_checked_count = sum(
        1 for result in results if result.get("checks", {}).get("catalog_close_matches_binance_close") is not None
    )
    output_dir = root / OUTPUT_DIR
    write_jsonl(output_dir / "bars.jsonl", bars)
    summary = {
        "dataset_id": "calibration_ohlc_bridge_crypto_v1",
        "generated_at": ingested_at,
        "source_ground_truth_catalog": GROUND_TRUTH_CATALOG.as_posix(),
        "market_kind": "crypto_spot",
        "timeframe": "D1",
        "case_count": len(crypto_cases),
        "bar_count": len(bars),
        "status_counts": dict(status_counts),
        "passed_direction_close_vs_level_count": passed_direction_count,
        "level_touched_intraday_count": level_touch_count,
        "directional_reach_intraday_count": directional_reach_count,
        "verdict_price_directional_reach_intraday_count": verdict_price_reach_count,
        "catalog_close_match_count": catalog_close_match_count,
        "catalog_close_checked_count": catalog_close_checked_count,
        "data_source": "binance_public_data_spot_monthly_klines",
        "output_files": {
            "summary_json": (OUTPUT_DIR / "summary.json").as_posix(),
            "case_results_json": (OUTPUT_DIR / "case_results.json").as_posix(),
            "bars_jsonl": (OUTPUT_DIR / "bars.jsonl").as_posix(),
            "report_md": (OUTPUT_DIR / "report.md").as_posix(),
        },
        "scope_limits": [
            "crypto spot D1 only",
            "no detector execution",
            "no pnl computation",
            "no trading or order simulation",
        ],
    }
    write_json(output_dir / "summary.json", summary)
    write_json(output_dir / "case_results.json", results)
    write_report(output_dir / "report.md", summary, results)
    return summary


def write_report(path: Path, summary: dict[str, Any], results: list[dict[str, Any]]) -> None:
    lines = [
        "# Calibration Crypto OHLC Bridge",
        "",
        f"Generated: `{summary['generated_at']}`",
        "",
        "## Summary",
        "",
        f"- Cases: {summary['case_count']}",
        f"- Bars found: {summary['bar_count']}",
        f"- Status counts: `{summary['status_counts']}`",
        f"- Direction close-vs-level pass count: {summary['passed_direction_close_vs_level_count']}",
        f"- Level touched intraday count: {summary['level_touched_intraday_count']}",
        f"- Directional reach intraday count: {summary['directional_reach_intraday_count']}",
        f"- Verdict price directional reach count: {summary['verdict_price_directional_reach_intraday_count']}",
        f"- Catalog close matches Binance close: {summary['catalog_close_match_count']}/{summary['catalog_close_checked_count']}",
        "",
        "## Case Results",
        "",
        "| Case | Symbol | Date | Direction | Level | Verdict | Low | High | Close | Catalog Close OK | Close OK | Level Reach | Verdict Reach | Status |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---|---|---|---|---|",
    ]
    for result in results:
        bar = result.get("bar") or {}
        checks = result.get("checks") or {}
        lines.append(
            "| "
            f"{result['calibration_case_id']} | {result['symbol']} | {result['date']} | {result['direction']} | "
            f"{result.get('level')} | {result.get('catalog_verdict_price')} | {bar.get('low')} | {bar.get('high')} | "
            f"{bar.get('close')} | {checks.get('catalog_close_matches_binance_close')} | "
            f"{checks.get('direction_condition_close_vs_level')} | {checks.get('directional_reach_intraday')} | "
            f"{checks.get('verdict_price_directional_reach_intraday')} | {result['status']} |"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run crypto-only OHLC bridge for calibration ground-truth cases.")
    parser.add_argument("--root", type=Path, default=default_root())
    return parser.parse_args()


def main() -> int:
    configure_stdio()
    args = parse_args()
    summary = build_bridge(args.root.resolve())
    print(
        f"[OK] crypto bridge cases={summary['case_count']} bars={summary['bar_count']} "
        f"status={summary['status_counts']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())