from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import zipfile
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from detector_prototype import DetectorInputError, build_output
from run_calibration_crypto_ohlc_bridge import (
    DATA_ROOT,
    GROUND_TRUTH_CATALOG,
    archive_path,
    archive_url,
    date_from_open_ms,
    download_archive,
    row_to_bar,
)


BRIDGE_RESULTS = Path("_knowledge_base/structured/consolidation/calibration_ohlc_bridge_crypto/case_results.json")
OUTPUT_DIR = Path("_knowledge_base/structured/consolidation/calibration_engine_compare_crypto_retest")
LOOKBACK_CALENDAR_DAYS = 120
MIN_CONTEXT_BARS = 20
ATR_PERIOD = 14


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


def month_key(day: date) -> str:
    return day.isoformat()[:7]


def iter_months(start: date, end: date) -> list[str]:
    current = date(start.year, start.month, 1)
    months: list[str] = []
    while current <= end:
        months.append(month_key(current))
        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)
    return months


def extract_month_bars(root: Path, symbol: str, month: str, ingested_at: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    path = archive_path(root, symbol, month)
    download = download_archive(path, archive_url(symbol, month))
    if download.status == "failed":
        return [], download.__dict__

    bars: list[dict[str, Any]] = []
    try:
        with zipfile.ZipFile(path) as archive:
            csv_names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
            if not csv_names:
                status = dict(download.__dict__)
                status["status"] = "archive_has_no_csv"
                return [], status
            with archive.open(csv_names[0]) as handle:
                reader = csv.reader(io.TextIOWrapper(handle, encoding="utf-8"))
                for row in reader:
                    if not row or row[0] == "open_time":
                        continue
                    bars.append(row_to_bar(symbol, row, ingested_at))
    except (OSError, zipfile.BadZipFile, ValueError, IndexError) as exc:
        status = dict(download.__dict__)
        status["status"] = "archive_read_failed"
        status["error"] = str(exc)
        return [], status
    return bars, download.__dict__


def load_context_bars(root: Path, symbol: str, target_date: str, ingested_at: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    end = date.fromisoformat(target_date)
    start = end - timedelta(days=LOOKBACK_CALENDAR_DAYS)
    bars: list[dict[str, Any]] = []
    downloads: list[dict[str, Any]] = []
    for month in iter_months(start, end):
        month_bars, download = extract_month_bars(root, symbol, month, ingested_at)
        downloads.append(download)
        bars.extend(
            bar
            for bar in month_bars
            if start.isoformat() <= str(bar["open_time_utc"])[:10] <= target_date
        )
    bars.sort(key=lambda bar: str(bar["open_time_utc"]))
    return bars, downloads


def detector_candles(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "time": bar["open_time_utc"],
            "open": bar["open"],
            "high": bar["high"],
            "low": bar["low"],
            "close": bar["close"],
        }
        for bar in bars
    ]


def average_true_range(bars: list[dict[str, Any]], period: int = ATR_PERIOD) -> float | None:
    if len(bars) < 2:
        return None
    true_ranges: list[float] = []
    previous_close = float(bars[0]["close"])
    for bar in bars[1:]:
        high = float(bar["high"])
        low = float(bar["low"])
        true_ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
        previous_close = float(bar["close"])
    window = true_ranges[-period:]
    if not window:
        return None
    return sum(window) / len(window)


def source_alignment_status(bridge_result: dict[str, Any]) -> str:
    if bridge_result.get("status") != "bar_found":
        return "bar_missing"
    if bridge_result.get("checks", {}).get("catalog_close_matches_binance_close") is False:
        return "source_close_mismatch"
    return "aligned"


def expert_trigger_scope(bridge_result: dict[str, Any]) -> str:
    checks = bridge_result.get("checks") or {}
    if checks.get("verdict_price_directional_reach_intraday") is True:
        return "verdict_price_reached_intraday"
    if checks.get("directional_reach_intraday") is True:
        return "level_reached_intraday"
    return "pending_trigger_not_reached_on_decision_day"


def expected_retest_bias(case: dict[str, Any]) -> str:
    decision = case.get("decision") or {}
    direction = str(decision.get("direction") or "").lower()
    level = decision.get("level")
    verdict_price = decision.get("verdict_price")
    if direction not in {"long", "short"} or level is None or verdict_price is None:
        return "unknown"
    if direction == "long" and float(verdict_price) >= float(level):
        return "breakout"
    if direction == "short" and float(verdict_price) <= float(level):
        return "breakout"
    return "false_breakout"


def comparison_scope(alignment: str, trigger_scope: str, expected_bias: str, context_bars: int, detector_status: str) -> tuple[bool, str]:
    if alignment != "aligned":
        return False, alignment
    if context_bars < MIN_CONTEXT_BARS:
        return False, "insufficient_context_bars"
    if expected_bias == "unknown":
        return False, "expert_bias_unknown"
    if trigger_scope == "pending_trigger_not_reached_on_decision_day":
        return False, "pending_trigger_not_reached"
    if detector_status != "ran":
        return False, detector_status
    return True, "scored"


def compact_detector_output(output: dict[str, Any] | None) -> dict[str, Any] | None:
    if output is None:
        return None
    return {
        "status": output.get("status"),
        "classification": output.get("classification"),
        "bias": output.get("bias"),
        "strength": output.get("strength"),
        "required_passed": output.get("required_passed"),
        "hard_rejects": output.get("hard_rejects"),
        "strength_factors": output.get("strength_factors"),
        "weakness_factors": output.get("weakness_factors"),
        "retest_features": output.get("retest_features"),
    }


def compare_case(root: Path, case: dict[str, Any], bridge_result: dict[str, Any], ingested_at: str) -> dict[str, Any]:
    symbol = str(case["instrument"]["data_symbol"])
    decision = case["decision"]
    target_date = str(decision["date"])
    level = decision.get("level")
    alignment = source_alignment_status(bridge_result)
    trigger_scope = expert_trigger_scope(bridge_result)
    expected_bias = expected_retest_bias(case)
    bars, downloads = load_context_bars(root, symbol, target_date, ingested_at)
    atr = average_true_range(bars)
    detector_status = "not_run"
    detector_error = ""
    detector_output: dict[str, Any] | None = None

    if level is None:
        detector_status = "missing_level"
    elif len(bars) < MIN_CONTEXT_BARS:
        detector_status = "insufficient_context_bars"
    elif atr is None or atr <= 0:
        detector_status = "invalid_atr"
    else:
        detector_input = {
            "symbol": symbol,
            "retests": [
                {
                    "timeframe": "1d",
                    "level_price": float(level),
                    "atr": atr,
                    "candles": detector_candles(bars),
                }
            ],
        }
        try:
            output = build_output(detector_input)
            detector_output = (output.get("detectors") or {}).get("near_far_retest", [None])[0]
            detector_status = "ran" if isinstance(detector_output, dict) else "missing_detector_output"
        except (DetectorInputError, KeyError, IndexError, TypeError, ValueError) as exc:
            detector_status = "detector_error"
            detector_error = str(exc)

    is_scored, scope_reason = comparison_scope(alignment, trigger_scope, expected_bias, len(bars), detector_status)
    actual_bias = detector_output.get("bias") if isinstance(detector_output, dict) else None
    comparison_result = "not_scored"
    if is_scored:
        comparison_result = "match" if actual_bias == expected_bias else "mismatch"

    return {
        "calibration_case_id": case["calibration_case_id"],
        "source_ref": case.get("source_ref"),
        "symbol": symbol,
        "date": target_date,
        "direction": decision.get("direction"),
        "level": level,
        "verdict_price": decision.get("verdict_price"),
        "expert_verdict": decision.get("expert_verdict"),
        "expected_retest_bias": expected_bias,
        "source_alignment_status": alignment,
        "expert_trigger_scope": trigger_scope,
        "detector_status": detector_status,
        "detector_error": detector_error,
        "context": {
            "lookback_calendar_days": LOOKBACK_CALENDAR_DAYS,
            "bar_count": len(bars),
            "start_date": None if not bars else str(bars[0]["open_time_utc"])[:10],
            "end_date": None if not bars else str(bars[-1]["open_time_utc"])[:10],
            "atr_period": ATR_PERIOD,
            "atr": None if atr is None else round(atr, 8),
            "download_status_counts": dict(Counter(download.get("status") for download in downloads)),
        },
        "bridge_checks": bridge_result.get("checks", {}),
        "detector_output": compact_detector_output(detector_output),
        "comparison": {
            "scored": is_scored,
            "scope_reason": scope_reason,
            "actual_bias": actual_bias,
            "result": comparison_result,
        },
    }


def render_report(summary: dict[str, Any], results: list[dict[str, Any]]) -> str:
    lines = [
        "# Crypto Retest Detector Calibration Compare",
        "",
        f"Generated: `{summary['generated_at']}`",
        "",
        "## Summary",
        "",
        f"- Cases: {summary['case_count']}",
        f"- Detector ran: {summary['detector_ran_count']}",
        f"- Source aligned: {summary['source_aligned_count']}",
        f"- Scored comparable cases: {summary['scored_case_count']}",
        f"- Matches: {summary['match_count']}",
        f"- Mismatches: {summary['mismatch_count']}",
        f"- Not scored: {summary['not_scored_count']}",
        "",
        "Scope note: this is only the `near_far_retest` prototype over Binance D1 context. It is not a full trade detector, PnL run, or full Gerchik setup verdict.",
        "",
        "## Case Results",
        "",
        "| Case | Symbol | Trigger scope | Source | Bars | ATR | Engine class | Engine bias | Expected | Scored | Result | Reason |",
        "|---|---|---|---|---:|---:|---|---|---|---|---|---|",
    ]
    for result in results:
        context = result["context"]
        detector = result.get("detector_output") or {}
        comparison = result["comparison"]
        atr = "" if context.get("atr") is None else f"{context['atr']:.8g}"
        lines.append(
            "| "
            f"{result['calibration_case_id']} | {result['symbol']} | {result['expert_trigger_scope']} | "
            f"{result['source_alignment_status']} | {context['bar_count']} | {atr} | "
            f"{detector.get('classification') or ''} | {detector.get('bias') or ''} | {result['expected_retest_bias']} | "
            f"{comparison['scored']} | {comparison['result']} | {comparison['scope_reason']} |"
        )
    notes = [result for result in results if result["comparison"]["result"] != "match"]
    if notes:
        lines.extend(["", "## Calibration Notes", ""])
        for result in notes:
            detector = result.get("detector_output") or {}
            features = detector.get("retest_features") or {}
            hard_rejects = detector.get("hard_rejects") or []
            weaknesses = detector.get("weakness_factors") or []
            details: list[str] = []
            if result["comparison"]["scope_reason"] == "pending_trigger_not_reached":
                details.append("source slide is aligned, but the Binance D1 bar did not reach the level/verdict trigger on that date")
            if result["comparison"]["scope_reason"] == "source_close_mismatch":
                details.append("screenshot close does not match Binance public D1 close within tolerance")
            if detector.get("classification"):
                details.append(f"engine_class={detector['classification']}")
            if detector.get("bias"):
                details.append(f"engine_bias={detector['bias']}")
            if hard_rejects:
                details.append("hard_rejects=" + ",".join(str(item) for item in hard_rejects))
            if weaknesses:
                details.append("weaknesses=" + ",".join(str(item) for item in weaknesses))
            if features.get("bars_since_contact") is not None:
                details.append(f"bars_since_contact={features['bars_since_contact']}")
            if features.get("contact_count") is not None:
                details.append(f"contact_count={features['contact_count']}")
            lines.append(f"- `{result['calibration_case_id']}` `{result['symbol']}`: " + "; ".join(details))
    return "\n".join(lines) + "\n"


def build_compare(root: Path) -> dict[str, Any]:
    catalog = read_json(root / GROUND_TRUTH_CATALOG)
    bridge_results = read_json(root / BRIDGE_RESULTS)
    cases = [case for case in catalog.get("cases") or [] if case.get("instrument", {}).get("market_kind") == "crypto_spot"]
    case_by_id = {case["calibration_case_id"]: case for case in cases}
    bridge_by_id = {result["calibration_case_id"]: result for result in bridge_results}
    ingested_at = datetime.now(timezone.utc).isoformat()
    results: list[dict[str, Any]] = []
    for case_id in sorted(case_by_id):
        if case_id not in bridge_by_id:
            continue
        results.append(compare_case(root, case_by_id[case_id], bridge_by_id[case_id], ingested_at))

    comparison_counts = Counter(result["comparison"]["result"] for result in results)
    source_counts = Counter(result["source_alignment_status"] for result in results)
    detector_counts = Counter(result["detector_status"] for result in results)
    scope_counts = Counter(result["comparison"]["scope_reason"] for result in results)
    bias_counts = Counter(
        (result.get("detector_output") or {}).get("bias") or "no_bias"
        for result in results
        if result["detector_status"] == "ran"
    )
    scored = [result for result in results if result["comparison"]["scored"]]
    output_dir = root / OUTPUT_DIR
    summary = {
        "dataset_id": "calibration_engine_compare_crypto_retest_v1",
        "generated_at": ingested_at,
        "source_ground_truth_catalog": GROUND_TRUTH_CATALOG.as_posix(),
        "source_crypto_bridge": BRIDGE_RESULTS.as_posix(),
        "detector": "near_far_retest",
        "detector_runner": "knowledge_bot/detector_prototype.py",
        "market_kind": "crypto_spot",
        "timeframe": "D1",
        "case_count": len(results),
        "detector_ran_count": detector_counts.get("ran", 0),
        "source_aligned_count": source_counts.get("aligned", 0),
        "scored_case_count": len(scored),
        "match_count": comparison_counts.get("match", 0),
        "mismatch_count": comparison_counts.get("mismatch", 0),
        "not_scored_count": comparison_counts.get("not_scored", 0),
        "source_alignment_counts": dict(source_counts),
        "detector_status_counts": dict(detector_counts),
        "scope_reason_counts": dict(scope_counts),
        "engine_bias_counts": dict(bias_counts),
        "scope_limits": [
            "near_far_retest detector only",
            "Binance public spot D1 context only",
            "no intraday trigger detector",
            "no stop, target, PnL, or order simulation",
            "source-mismatch and pending-trigger cases are not scored as detector accuracy",
        ],
        "output_files": {
            "summary_json": (OUTPUT_DIR / "summary.json").as_posix(),
            "case_results_json": (OUTPUT_DIR / "case_results.json").as_posix(),
            "case_results_jsonl": (OUTPUT_DIR / "case_results.jsonl").as_posix(),
            "report_md": (OUTPUT_DIR / "report.md").as_posix(),
        },
    }
    write_json(output_dir / "summary.json", summary)
    write_json(output_dir / "case_results.json", results)
    write_jsonl(output_dir / "case_results.jsonl", results)
    (output_dir / "report.md").write_text(render_report(summary, results), encoding="utf-8")
    return summary


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run crypto calibration compare for the OHLC-only near/far retest prototype.")
    parser.add_argument("--root", type=Path, default=default_root(), help="Workspace root.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    args = parse_args(sys.argv[1:] if argv is None else argv)
    summary = build_compare(args.root)
    print(
        "[OK] crypto retest compare "
        f"cases={summary['case_count']} ran={summary['detector_ran_count']} "
        f"scored={summary['scored_case_count']} matches={summary['match_count']} mismatches={summary['mismatch_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())