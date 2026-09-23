"""Build situation-analyzer input from an OHLC file.

This is a read-only adapter. It parses CSV/JSON OHLC rows, summarizes market
context, checks whether stated levels/entry/stop/target were visible in the
window, can attach a single-window descriptive outcome, and optionally runs
situation_analyzer on the generated context. It does not calculate PnL,
backtest, place orders, or promote ground truth.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from situation_analyzer import analyze as analyze_situation
    from situation_analyzer import configure_stdio, default_root
    from build_visual_review_inventory_report import BASE, OBSERVATIONS, compact, read_jsonl
except ModuleNotFoundError:
    from knowledge_bot.situation_analyzer import analyze as analyze_situation
    from knowledge_bot.situation_analyzer import configure_stdio, default_root
    from knowledge_bot.build_visual_review_inventory_report import BASE, OBSERVATIONS, compact, read_jsonl


DEFAULT_CONTEXT_OUTPUT = BASE / "ohlc_situation_context_last.json"
DEFAULT_ANALYZER_OUTPUT = BASE / "ohlc_situation_analyzer_last.json"
TIME_KEYS = ("open_time", "open_time_utc", "time", "timestamp", "date", "datetime")
OPEN_KEYS = ("open", "o")
HIGH_KEYS = ("high", "h")
LOW_KEYS = ("low", "l")
CLOSE_KEYS = ("close", "c")
VOLUME_KEYS = ("volume", "vol", "v")


@dataclass(frozen=True)
class Bar:
    open_time: str
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None


def pick(row: dict[str, Any], keys: tuple[str, ...], required: bool = True) -> Any:
    normalized = {str(key).strip().lower(): value for key, value in row.items()}
    for key in keys:
        if key in normalized and normalized[key] not in {None, ""}:
            return normalized[key]
    if required:
        raise ValueError(f"Missing required OHLC column. Expected one of: {', '.join(keys)}")
    return None


def normalize_time(value: Any) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError("Empty OHLC timestamp")
    if re.fullmatch(r"\d{13}", text):
        return datetime.fromtimestamp(int(text) / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    if re.fullmatch(r"\d{10}", text):
        return datetime.fromtimestamp(int(text), tz=timezone.utc).isoformat().replace("+00:00", "Z")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return datetime.fromisoformat(text).replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def row_to_bar(row: dict[str, Any]) -> Bar:
    return Bar(
        open_time=normalize_time(pick(row, TIME_KEYS)),
        open=float(pick(row, OPEN_KEYS)),
        high=float(pick(row, HIGH_KEYS)),
        low=float(pick(row, LOW_KEYS)),
        close=float(pick(row, CLOSE_KEYS)),
        volume=None if pick(row, VOLUME_KEYS, required=False) is None else float(pick(row, VOLUME_KEYS, required=False)),
    )


def load_csv(path: Path) -> list[Bar]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError("CSV file has no header")
        return [row_to_bar(row) for row in reader]


def load_json(path: Path) -> list[Bar]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        for key in ("bars", "ohlc", "rows", "data"):
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break
    if not isinstance(payload, list):
        raise ValueError("JSON OHLC input must be a list or an object with bars/ohlc/rows/data list")
    return [row_to_bar(row) for row in payload]


def load_bars(path: Path) -> list[Bar]:
    bars = load_json(path) if path.suffix.lower() == ".json" else load_csv(path)
    bars = sorted(bars, key=lambda bar: bar.open_time)
    if not bars:
        raise ValueError("OHLC input has no rows")
    bad_rows = [bar for bar in bars if bar.high < max(bar.open, bar.close) or bar.low > min(bar.open, bar.close)]
    if bad_rows:
        raise ValueError(f"OHLC integrity error: {len(bad_rows)} rows have high/low inconsistent with open/close")
    return bars


def true_ranges(bars: list[Bar]) -> list[float]:
    ranges: list[float] = []
    previous_close: float | None = None
    for bar in bars:
        if previous_close is None:
            ranges.append(bar.high - bar.low)
        else:
            ranges.append(max(bar.high - bar.low, abs(bar.high - previous_close), abs(bar.low - previous_close)))
        previous_close = bar.close
    return ranges


def atr(bars: list[Bar], period: int) -> float | None:
    if not bars:
        return None
    ranges = true_ranges(bars)
    window = ranges[-period:] if len(ranges) >= period else ranges
    if not window:
        return None
    return sum(window) / len(window)


def price_touched(bars: list[Bar], price: float) -> list[dict[str, Any]]:
    touches = []
    for index, bar in enumerate(bars):
        if bar.low <= price <= bar.high:
            touches.append({
                "index": index,
                "time": bar.open_time,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
            })
    return touches


def first_directional_touch(bars: list[Bar], price: float, direction: str) -> dict[str, Any] | None:
    direction = direction.lower()
    for index, bar in enumerate(bars):
        if direction == "long" and bar.high >= price:
            return {"index": index, "time": bar.open_time, "price": price, "bar_high": bar.high, "bar_low": bar.low}
        if direction == "short" and bar.low <= price:
            return {"index": index, "time": bar.open_time, "price": price, "bar_high": bar.high, "bar_low": bar.low}
    return None


def close_position(close: float, level: float | None, tolerance: float) -> str:
    if level is None:
        return "unknown"
    if close > level + tolerance:
        return "above_level"
    if close < level - tolerance:
        return "below_level"
    return "at_level"


def numeric(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def build_descriptive_outcome(args: argparse.Namespace, bars: list[Bar], entry: float | None, stop: float | None, target: float | None) -> dict[str, Any] | None:
    if not getattr(args, "describe_outcome", False):
        return None
    if not args.direction or entry is None or stop is None or target is None:
        raise ValueError("--describe-outcome requires --direction, --entry, --stop, and --target")
    try:
        from outcome_descriptor import describe_outcome
    except ModuleNotFoundError:
        from knowledge_bot.outcome_descriptor import describe_outcome
    report = describe_outcome(bars, args.direction, entry, stop, target)
    report["source_path"] = args.ohlc_file.as_posix()
    return report


def build_context(args: argparse.Namespace, bars: list[Bar]) -> dict[str, Any]:
    latest = bars[-1]
    level = numeric(args.level)
    entry = numeric(args.entry)
    stop = numeric(args.stop)
    target = numeric(args.target)
    atr_value = atr(bars, args.atr_period)
    tolerance = max(abs(latest.close) * 0.0005, (atr_value or 0) * 0.02, 1e-12)
    level_touches = price_touched(bars, level) if level is not None else []
    entry_touch = first_directional_touch(bars, entry, args.direction or "") if entry is not None and args.direction else None
    stop_touch = first_directional_touch(bars, stop, "short" if args.direction == "long" else "long") if stop is not None and args.direction else None
    target_touch = first_directional_touch(bars, target, args.direction or "") if target is not None and args.direction else None
    descriptive_outcome = build_descriptive_outcome(args, bars, entry, stop, target)
    text_parts = [
        f"OHLC window {bars[0].open_time}..{latest.open_time}, {len(bars)} bars, timeframe {args.timeframe or 'unknown'}.",
        f"Latest OHLC: O={latest.open} H={latest.high} L={latest.low} C={latest.close}.",
    ]
    if atr_value is not None:
        text_parts.append(f"ATR{args.atr_period}={round(atr_value, 8)}.")
    if level is not None:
        text_parts.append(
            f"Level {level}: close is {close_position(latest.close, level, tolerance)}, touches={len(level_touches)}, latest_distance={round(latest.close - level, 8)}."
        )
    if entry is not None:
        text_parts.append(f"Entry {entry}: {'touched' if entry_touch else 'not touched'} in OHLC window.")
    if stop is not None:
        text_parts.append(f"Stop {stop}: {'touched' if stop_touch else 'not touched'} in OHLC window.")
    if target is not None:
        text_parts.append(f"Target {target}: {'touched' if target_touch else 'not touched'} in OHLC window.")
    if descriptive_outcome:
        text_parts.append(
            f"Descriptive outcome: {descriptive_outcome['outcome']}, MFE={descriptive_outcome['mfe_r']}R, MAE={descriptive_outcome['mae_r']}R."
        )

    if entry is not None and entry_touch is None:
        ohlc_verification_status = "ohlc_window_loaded_but_entry_not_touched"
    elif entry is not None:
        ohlc_verification_status = "ohlc_window_loaded_and_entry_touched"
    else:
        ohlc_verification_status = "ohlc_window_loaded_without_entry_check"

    payload = {
        "text": " ".join(text_parts),
        "instrument": args.instrument,
        "venue": args.venue,
        "timeframe": args.timeframe,
        "direction": args.direction,
        "date_session": args.date_session or latest.open_time[:10],
        "level_area": None if level is None else str(level),
        "entry": None if entry is None else str(entry),
        "stop": None if stop is None else str(stop),
        "target": None if target is None else str(target),
        "trigger": args.trigger,
        "ohlc": {
            "source_path": args.ohlc_file.as_posix(),
            "bar_count": len(bars),
            "start_time": bars[0].open_time,
            "end_time": latest.open_time,
            "latest_bar": latest.__dict__,
            "atr_period": args.atr_period,
            "atr": atr_value,
            "level": level,
            "level_touch_count": len(level_touches),
            "level_last_touch": level_touches[-1] if level_touches else None,
            "close_position_vs_level": close_position(latest.close, level, tolerance),
            "ohlc_verification_status": ohlc_verification_status,
            "entry_touch": entry_touch,
            "stop_touch": stop_touch,
            "target_touch": target_touch,
            "descriptive_outcome": descriptive_outcome,
            "read_only_flags": {
                "order_generation_allowed": False,
                "pnl_computation_allowed": False,
                "aggregate_winrate_allowed": False,
                "backtest_execution_allowed": False,
                "paper_live_execution_allowed": False,
            },
        },
    }
    if ohlc_verification_status != "ohlc_window_loaded_but_entry_not_touched":
        payload["ohlc_verification"] = ohlc_verification_status
    return {key: value for key, value in payload.items() if value is not None}


def render_context_markdown(context: dict[str, Any]) -> str:
    ohlc = context["ohlc"]
    lines = [
        "# OHLC Situation Context",
        "",
        f"Generated source: `{ohlc['source_path']}`",
        "",
        "## Summary",
        "",
        f"- Instrument: `{context.get('instrument', 'unknown')}`",
        f"- Venue: `{context.get('venue', 'unknown')}`",
        f"- Timeframe: `{context.get('timeframe', 'unknown')}`",
        f"- Direction: `{context.get('direction', 'unknown')}`",
        f"- Bars: `{ohlc['bar_count']}` from `{ohlc['start_time']}` to `{ohlc['end_time']}`",
        f"- ATR{ohlc['atr_period']}: `{ohlc['atr']}`",
        f"- Close vs level: `{ohlc['close_position_vs_level']}`",
        f"- OHLC verification status: `{ohlc['ohlc_verification_status']}`",
        f"- Level touches: `{ohlc['level_touch_count']}`",
        f"- Entry touch: `{bool(ohlc['entry_touch'])}`",
        f"- Stop touch: `{bool(ohlc['stop_touch'])}`",
        f"- Target touch: `{bool(ohlc['target_touch'])}`",
        "",
    ]
    if ohlc.get("descriptive_outcome"):
        outcome = ohlc["descriptive_outcome"]
        lines.extend([
            "## Descriptive Outcome",
            "",
            f"- Outcome: `{outcome['outcome']}`",
            f"- Bars to resolution: `{outcome['bars_to_resolution']}`",
            f"- MFE: `{outcome['mfe_r']}R`",
            f"- MAE: `{outcome['mae_r']}R`",
            "",
        ])
    lines.extend([
        "## Analyzer Input Text",
        "",
        context["text"],
        "",
        "Safety: read-only context; no PnL, no order generation, no backtest execution.",
        "",
    ])
    return "\n".join(lines)


def write_output(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build read-only situation analyzer context from OHLC CSV/JSON.")
    parser.add_argument("--root", type=Path, default=default_root())
    parser.add_argument("--ohlc-file", type=Path, required=True)
    parser.add_argument("--instrument")
    parser.add_argument("--venue")
    parser.add_argument("--timeframe")
    parser.add_argument("--direction", choices=["long", "short"])
    parser.add_argument("--date-session")
    parser.add_argument("--level", type=float)
    parser.add_argument("--entry", type=float)
    parser.add_argument("--stop", type=float)
    parser.add_argument("--target", type=float)
    parser.add_argument("--trigger")
    parser.add_argument("--atr-period", type=int, default=14)
    parser.add_argument("--describe-outcome", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_CONTEXT_OUTPUT)
    parser.add_argument("--markdown-output", type=Path)
    parser.add_argument("--run-analyzer", action="store_true")
    parser.add_argument("--analyzer-output", type=Path, default=DEFAULT_ANALYZER_OUTPUT)
    parser.add_argument("--top-k", type=int, default=7)
    return parser.parse_args()


def main() -> int:
    configure_stdio()
    args = parse_args()
    root = args.root.resolve()
    ohlc_path = args.ohlc_file if args.ohlc_file.is_absolute() else root / args.ohlc_file
    args.ohlc_file = ohlc_path
    bars = load_bars(ohlc_path)
    context = build_context(args, bars)
    output_path = args.output if args.output.is_absolute() else root / args.output
    write_output(output_path, json.dumps(context, ensure_ascii=False, indent=2) + "\n")

    markdown_path = args.markdown_output
    if markdown_path:
        markdown_path = markdown_path if markdown_path.is_absolute() else root / markdown_path
        write_output(markdown_path, render_context_markdown(context))

    analyzer_path = None
    analyzer_summary = None
    if args.run_analyzer:
        rows = read_jsonl(root / OBSERVATIONS)
        analyzer_report = analyze_situation(context, rows, top_k=max(1, args.top_k))
        analyzer_path = args.analyzer_output if args.analyzer_output.is_absolute() else root / args.analyzer_output
        write_output(analyzer_path, json.dumps(analyzer_report, ensure_ascii=False, indent=2) + "\n")
        analyzer_summary = {
            "verdict": analyzer_report["verdict"],
            "missing_fields": analyzer_report["trade_contract_completeness"]["missing_fields"],
            "supporting_examples": len(analyzer_report["supporting_examples"]),
            "guardrail_examples": len(analyzer_report["guardrail_examples"]),
        }

    print(json.dumps({
        "context_output": output_path.as_posix(),
        "markdown_output": None if markdown_path is None else markdown_path.as_posix(),
        "analyzer_output": None if analyzer_path is None else analyzer_path.as_posix(),
        "bar_count": len(bars),
        "context_summary": {
            "instrument": context.get("instrument"),
            "timeframe": context.get("timeframe"),
            "close_position_vs_level": context["ohlc"].get("close_position_vs_level"),
            "level_touch_count": context["ohlc"].get("level_touch_count"),
            "entry_touch": bool(context["ohlc"].get("entry_touch")),
            "stop_touch": bool(context["ohlc"].get("stop_touch")),
            "target_touch": bool(context["ohlc"].get("target_touch")),
            "descriptive_outcome": None if not context["ohlc"].get("descriptive_outcome") else {
                "outcome": context["ohlc"]["descriptive_outcome"].get("outcome"),
                "mfe_r": context["ohlc"]["descriptive_outcome"].get("mfe_r"),
                "mae_r": context["ohlc"]["descriptive_outcome"].get("mae_r"),
            },
        },
        "analyzer_summary": analyzer_summary,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())