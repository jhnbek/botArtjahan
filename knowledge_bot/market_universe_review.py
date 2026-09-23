"""Read-only market universe review scanner.

This CLI runs the existing Layer 6 chart review packet over a list of symbols
and writes review artifacts. It is deliberately observation-only: no PnL, no
outcome labels, no orders, no runtime signals, no paper trading, and no live
trading.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from chart_review_packet import (
    SOURCE_ARTIFACTS,
    ChartReviewParams,
    build_chart_review_packet_from_data_source,
)
from permission_context import load_manual_context


ROOT = Path(__file__).resolve().parents[1]
VERSION = "market_universe_review_v1"
DEFAULT_OUTPUT_DIR = ROOT / "_exports" / "market_universe_review_current"

SAFETY_FLAGS: dict[str, bool] = {
    "execution_allowed": False,
    "runtime_signal_allowed": False,
    "order_generation_allowed": False,
    "pnl_computation_allowed": False,
    "paper_trading_allowed": False,
    "live_trading_allowed": False,
    "backtest_harness_allowed": False,
}


@dataclass(frozen=True)
class ScanConfig:
    symbols: list[str]
    context_interval: str
    execution_interval: str
    higher_interval: str
    start: str
    end: str
    breakout_direction: str
    execution_lookback_bars: int
    output_dir: Path | None


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def split_symbol_text(text: str) -> list[str]:
    chunks = text.replace(",", " ").replace(";", " ").split()
    return [chunk.strip().upper() for chunk in chunks if chunk.strip()]


def load_symbols(symbols_arg: str | None, symbols_file: str | None) -> list[str]:
    raw_symbols: list[str] = []
    if symbols_arg:
        raw_symbols.extend(split_symbol_text(symbols_arg))
    if symbols_file:
        path = Path(symbols_file)
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                raw_symbols.extend(split_symbol_text(line))

    seen: set[str] = set()
    symbols: list[str] = []
    for symbol in raw_symbols:
        if symbol.startswith("#") or symbol in seen:
            continue
        seen.add(symbol)
        symbols.append(symbol)
    return symbols


def safe_file_stem(symbol: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_", "."} else "_" for char in symbol)


def relative_path(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except (OSError, ValueError):
        return path.as_posix()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) for row in rows)
    path.write_text(text + ("\n" if text else ""), encoding="utf-8")


def table_cell(value: Any) -> str:
    text = " ".join(str(value if value is not None else "").split()).replace("|", "\\|")
    return text or "-"


def checklist_counts(packet: dict[str, Any]) -> dict[str, int]:
    summary = ((packet.get("checklist_matrix") or {}).get("summary") or {})
    counts = summary.get("status_counts") or {}
    return {str(key): int(value) for key, value in counts.items()}


def first_queue_items(items: list[dict[str, Any]], limit: int = 5) -> list[str]:
    result: list[str] = []
    for item in items[:limit]:
        item_id = item.get("item_id") or item.get("checklist_id") or "item"
        text = " ".join(str(item.get("text") or item.get("evidence") or "").split())
        result.append(f"{item_id}: {text}" if text else str(item_id))
    return result


def packet_summary(symbol: str, packet: dict[str, Any], packet_path: Path | None) -> dict[str, Any]:
    counts = checklist_counts(packet)
    permission = packet.get("permission_summary") or {}
    level_summary = packet.get("level_summary") or {}
    entry_report = (packet.get("layer_reports") or {}).get("entry") or {}
    scenario = entry_report.get("scenario") or {}
    nearest_level = entry_report.get("nearest_level") or {}
    blockers = packet.get("blockers") or []
    manual_queue = packet.get("manual_review_queue") or []
    return {
        "symbol": symbol,
        "scan_status": "ok",
        "review_status": packet.get("review_status"),
        "scenario_family": scenario.get("family"),
        "scenario_direction": scenario.get("direction"),
        "scenario_valid": scenario.get("valid"),
        "nearest_level_price": nearest_level.get("price"),
        "nearest_level_status": nearest_level.get("kb_status"),
        "nearest_level_score": nearest_level.get("kb_score"),
        "candidate_level_count": level_summary.get("candidate_count"),
        "working_level_count": level_summary.get("working_level_count"),
        "rejected_level_count": level_summary.get("rejected_count"),
        "hard_gate_status": permission.get("hard_gate_status"),
        "advisor_status": permission.get("advisor_status"),
        "best_entry_model": permission.get("best_entry_model"),
        "best_entry_status": permission.get("best_entry_status"),
        "checklist_pass": counts.get("pass", 0),
        "checklist_manual_review": counts.get("manual_review", 0),
        "checklist_block": counts.get("block", 0),
        "manual_review_count": len(manual_queue),
        "blocker_count": len(blockers),
        "hard_rejects": permission.get("hard_rejects", []),
        "missing_inputs": permission.get("missing_inputs", []),
        "top_blockers": first_queue_items(blockers),
        "top_manual_review": first_queue_items(manual_queue),
        "packet_path": relative_path(packet_path),
        **SAFETY_FLAGS,
    }


def error_summary(symbol: str, exc: Exception) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "scan_status": "error",
        "review_status": "data_or_pipeline_error",
        "error_type": type(exc).__name__,
        "error": str(exc),
        "manual_review_count": 0,
        "blocker_count": 0,
        **SAFETY_FLAGS,
    }


def record_rank(record: dict[str, Any]) -> tuple[int, int, int, str]:
    if record.get("scan_status") != "ok":
        return (9, 0, 0, str(record.get("symbol")))
    status_order = {
        "checklist_complete_review_only": 0,
        "manual_review_required": 1,
        "blocked_review_only": 2,
    }
    return (
        status_order.get(str(record.get("review_status")), 5),
        int(record.get("blocker_count") or 0),
        int(record.get("manual_review_count") or 0),
        str(record.get("symbol")),
    )


def scan_symbol(config: ScanConfig, symbol: str, manual_context: dict[str, Any]) -> dict[str, Any]:
    packet = build_chart_review_packet_from_data_source(
        symbol=symbol,
        context_timeframe=config.context_interval,
        execution_timeframe=config.execution_interval,
        start=config.start,
        end=config.end,
        higher_timeframe=config.higher_interval,
        breakout_direction_arg=config.breakout_direction,
        manual_context=manual_context,
        params=ChartReviewParams(execution_lookback_bars=config.execution_lookback_bars),
    )
    packet_path: Path | None = None
    if config.output_dir is not None:
        packet_path = config.output_dir / "packets" / f"{safe_file_stem(symbol)}_chart_review_packet.json"
        write_json(packet_path, packet)
    return packet_summary(symbol, packet, packet_path)


def aggregate_summary(config: ScanConfig, records: list[dict[str, Any]], generated_at: str) -> dict[str, Any]:
    review_status_counts: dict[str, int] = {}
    scan_status_counts: dict[str, int] = {}
    for record in records:
        review_status = str(record.get("review_status") or "unknown")
        scan_status = str(record.get("scan_status") or "unknown")
        review_status_counts[review_status] = review_status_counts.get(review_status, 0) + 1
        scan_status_counts[scan_status] = scan_status_counts.get(scan_status, 0) + 1
    ok_records = [record for record in records if record.get("scan_status") == "ok"]
    return {
        "version": VERSION,
        "generated_at": generated_at,
        "mode": "read_only_market_universe_review",
        "scope_note": "review packets only; no PnL, labels, orders, runtime signals, paper trading, or live trading",
        "data_source": "existing Layer 6 public Binance monthly klines loader",
        "source_artifacts": SOURCE_ARTIFACTS,
        "symbols": config.symbols,
        "timeframes": {
            "higher": config.higher_interval,
            "context": config.context_interval,
            "execution": config.execution_interval,
        },
        "window": {"start": config.start, "end": config.end},
        "breakout_direction": config.breakout_direction,
        "execution_lookback_bars": config.execution_lookback_bars,
        "output_dir": relative_path(config.output_dir),
        "scan_status_counts": scan_status_counts,
        "review_status_counts": review_status_counts,
        "total_manual_review_items": sum(int(record.get("manual_review_count") or 0) for record in ok_records),
        "total_blockers": sum(int(record.get("blocker_count") or 0) for record in ok_records),
        "records": sorted(records, key=record_rank),
        **SAFETY_FLAGS,
    }


def write_markdown_report(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# Market Universe Review",
        "",
        "## Verdict",
        "",
        f"- Generated: `{summary['generated_at']}`",
        f"- Mode: `{summary['mode']}`",
        f"- Symbols: {len(summary['symbols'])}",
        f"- Window: `{summary['window']['start']}..{summary['window']['end']}`",
        f"- Timeframes: `{summary['timeframes']['higher']}` / `{summary['timeframes']['context']}` / `{summary['timeframes']['execution']}`",
        f"- Execution allowed: `{str(summary['execution_allowed']).lower()}`",
        f"- Runtime signal allowed: `{str(summary['runtime_signal_allowed']).lower()}`",
        f"- PnL computation allowed: `{str(summary['pnl_computation_allowed']).lower()}`",
        f"- Backtest harness allowed: `{str(summary['backtest_harness_allowed']).lower()}`",
        "",
        "This artifact ranks review packets for human analysis. It does not produce trade signals or performance claims.",
        "",
        "## Status Counts",
        "",
        f"- Scan status: `{summary['scan_status_counts']}`",
        f"- Review status: `{summary['review_status_counts']}`",
        f"- Total manual-review items: {summary['total_manual_review_items']}",
        f"- Total blockers: {summary['total_blockers']}",
        "",
        "## Symbols",
        "",
        "| Symbol | Scan | Review | Pass | Manual | Block | Levels | Best entry | Hard gate | Packet |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- | --- |",
    ]
    for record in summary["records"]:
        best_entry = f"{record.get('best_entry_model') or '-'} / {record.get('best_entry_status') or '-'}"
        lines.append(
            "| "
            + " | ".join([
                table_cell(record.get("symbol")),
                table_cell(record.get("scan_status")),
                table_cell(record.get("review_status")),
                table_cell(record.get("checklist_pass")),
                table_cell(record.get("checklist_manual_review")),
                table_cell(record.get("checklist_block")),
                table_cell(record.get("working_level_count")),
                table_cell(best_entry),
                table_cell(record.get("hard_gate_status")),
                table_cell(record.get("packet_path")),
            ])
            + " |"
        )
    lines.extend(["", "## Review Queue Hints", ""])
    for record in summary["records"]:
        hints = record.get("top_blockers") or record.get("top_manual_review") or []
        if record.get("scan_status") != "ok":
            hints = [f"{record.get('error_type')}: {record.get('error')}"]
        if not hints:
            continue
        lines.extend([f"### {record.get('symbol')}", ""])
        for hint in hints:
            lines.append(f"- {hint}")
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def write_outputs(config: ScanConfig, summary: dict[str, Any]) -> None:
    if config.output_dir is None:
        return
    write_json(config.output_dir / "market_universe_review_summary.json", summary)
    write_jsonl(config.output_dir / "market_universe_review_rows.jsonl", summary["records"])
    write_markdown_report(config.output_dir / "market_universe_review_summary.md", summary)


def print_text_summary(summary: dict[str, Any]) -> None:
    print("=" * 78)
    print("MARKET UNIVERSE REVIEW - READ ONLY")
    print("=" * 78)
    print(f"window: {summary['window']['start']}..{summary['window']['end']}")
    print(f"timeframes: {summary['timeframes']['higher']} / {summary['timeframes']['context']} / {summary['timeframes']['execution']}")
    print(f"symbols: {len(summary['symbols'])}")
    print("execution_allowed: false")
    print("runtime_signal_allowed: false")
    print("pnl_computation_allowed: false")
    print("-" * 78)
    for record in summary["records"]:
        if record.get("scan_status") != "ok":
            print(f"{record['symbol']}: ERROR {record.get('error_type')} - {record.get('error')}")
            continue
        print(
            f"{record['symbol']}: {record.get('review_status')} "
            f"pass={record.get('checklist_pass', 0)} "
            f"manual={record.get('checklist_manual_review', 0)} "
            f"block={record.get('checklist_block', 0)} "
            f"levels={record.get('working_level_count')} "
            f"best={record.get('best_entry_model')}/{record.get('best_entry_status')}"
        )
    if summary.get("output_dir"):
        print("-" * 78)
        print(f"output_dir: {summary['output_dir']}")
    print("=" * 78)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only Layer 6 scanner across a market universe")
    parser.add_argument("--symbols", help="Comma/space separated symbols, e.g. BTCUSDT,ETHUSDT,SOLUSDT")
    parser.add_argument("--symbols-file", help="Optional UTF-8 file with symbols")
    parser.add_argument("--context-interval", default="1d")
    parser.add_argument("--execution-interval", default="15m")
    parser.add_argument("--higher-interval", default="1w")
    parser.add_argument("--start", required=True, help="Start month in YYYY-MM format")
    parser.add_argument("--end", required=True, help="End month in YYYY-MM format")
    parser.add_argument("--breakout-direction", choices=["auto", "long", "short"], default="auto")
    parser.add_argument("--execution-lookback-bars", type=int, default=ChartReviewParams.execution_lookback_bars)
    parser.add_argument("--manual-context-json", help="Optional path to one shared manual-context JSON object")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--no-write", action="store_true", help="Do not write summary or packet artifacts")
    parser.add_argument("--limit", type=int, help="Optional max symbol count after de-duplication")
    parser.add_argument("--strict-exit-code", action="store_true", help="Return non-zero when any symbol fails")
    parser.add_argument("--output-format", choices=["text", "json"], default="text")
    return parser


def main() -> int:
    configure_stdio()
    parser = build_parser()
    args = parser.parse_args()
    symbols = load_symbols(args.symbols, args.symbols_file)
    if args.limit is not None:
        symbols = symbols[:max(0, args.limit)]
    if not symbols:
        parser.error("provide --symbols and/or --symbols-file")

    manual_context = load_manual_context(args.manual_context_json)
    config = ScanConfig(
        symbols=symbols,
        context_interval=args.context_interval,
        execution_interval=args.execution_interval,
        higher_interval=args.higher_interval,
        start=args.start,
        end=args.end,
        breakout_direction=args.breakout_direction,
        execution_lookback_bars=args.execution_lookback_bars,
        output_dir=None if args.no_write else Path(args.out_dir),
    )

    records: list[dict[str, Any]] = []
    for index, symbol in enumerate(config.symbols, start=1):
        print(f"[{index}/{len(config.symbols)}] scanning {symbol} ...", file=sys.stderr)
        try:
            records.append(scan_symbol(config, symbol, manual_context))
        except Exception as exc:
            records.append(error_summary(symbol, exc))

    summary = aggregate_summary(config, records, utc_now())
    write_outputs(config, summary)
    if args.output_format == "json":
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print_text_summary(summary)

    if args.strict_exit_code and summary["scan_status_counts"].get("error"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())