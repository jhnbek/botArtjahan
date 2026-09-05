"""Describe what price did after an entry, as factual excursion data.

This is a read-only descriptive layer. Given an OHLC window plus a stated
entry/stop/target and direction, it reports which level was reached first
(stop or target), the maximum favorable/adverse excursion expressed in R, and
how many bars it took to resolve. It describes a single window only. It does
NOT compute aggregate PnL/winrate over many trades, does NOT rank or promote
setups, does NOT generate orders, and does NOT run a backtest.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    from ohlc_situation_adapter import Bar, load_bars, numeric
    from situation_analyzer import configure_stdio, default_root
    from build_visual_review_inventory_report import BASE
except ModuleNotFoundError:
    from knowledge_bot.ohlc_situation_adapter import Bar, load_bars, numeric
    from knowledge_bot.situation_analyzer import configure_stdio, default_root
    from knowledge_bot.build_visual_review_inventory_report import BASE


DEFAULT_OUTPUT = BASE / "outcome_descriptor_last.json"


def entry_index(bars: list[Bar], entry: float, direction: str) -> int | None:
    """First bar whose range reaches the entry price in the trade direction."""
    direction = direction.lower()
    for index, bar in enumerate(bars):
        if direction == "long" and bar.high >= entry:
            return index
        if direction == "short" and bar.low <= entry:
            return index
    return None


def resolution(
    bars: list[Bar],
    start: int,
    entry: float,
    stop: float,
    target: float,
    direction: str,
) -> dict[str, Any]:
    """Scan forward from the entry bar to find which level resolves first.

    A bar that touches both stop and target in the same bar is reported as
    ``ambiguous_same_bar`` because intrabar order is unknown from OHLC alone.
    """
    direction = direction.lower()
    for offset, bar in enumerate(bars[start:]):
        if direction == "long":
            hit_stop = bar.low <= stop
            hit_target = bar.high >= target
        else:
            hit_stop = bar.high >= stop
            hit_target = bar.low <= target
        if hit_stop and hit_target:
            return {"outcome": "ambiguous_same_bar", "resolution_index": start + offset, "bars_to_resolution": offset}
        if hit_target:
            return {"outcome": "hit_target_first", "resolution_index": start + offset, "bars_to_resolution": offset}
        if hit_stop:
            return {"outcome": "hit_stop_first", "resolution_index": start + offset, "bars_to_resolution": offset}
    return {"outcome": "none_reached", "resolution_index": None, "bars_to_resolution": None}


def excursions(
    bars: list[Bar],
    start: int,
    end: int | None,
    entry: float,
    risk: float,
    direction: str,
) -> dict[str, float | None]:
    """Maximum favorable/adverse excursion in R over the resolved window."""
    direction = direction.lower()
    last = len(bars) - 1 if end is None else end
    window = bars[start : last + 1]
    if not window or risk <= 0:
        return {"mfe_r": None, "mae_r": None}
    if direction == "long":
        best = max(bar.high for bar in window)
        worst = min(bar.low for bar in window)
        mfe = (best - entry) / risk
        mae = (worst - entry) / risk
    else:
        best = min(bar.low for bar in window)
        worst = max(bar.high for bar in window)
        mfe = (entry - best) / risk
        mae = (entry - worst) / risk
    return {"mfe_r": round(mfe, 4), "mae_r": round(mae, 4)}


def describe_outcome(
    bars: list[Bar],
    direction: str,
    entry: float,
    stop: float,
    target: float,
) -> dict[str, Any]:
    risk = abs(entry - stop)
    if risk == 0:
        raise ValueError("Entry and stop are equal; risk distance is zero")
    if direction == "long" and not (stop < entry < target):
        raise ValueError("For a long, expected stop < entry < target")
    if direction == "short" and not (target < entry < stop):
        raise ValueError("For a short, expected target < entry < stop")

    start = entry_index(bars, entry, direction)
    reward = abs(target - entry)
    base: dict[str, Any] = {
        "kind": "descriptive_outcome",
        "direction": direction,
        "entry": entry,
        "stop": stop,
        "target": target,
        "risk_distance": round(risk, 8),
        "reward_distance": round(reward, 8),
        "planned_reward_risk": round(reward / risk, 4),
        "bar_count": len(bars),
        "read_only_flags": {
            "order_generation_allowed": False,
            "pnl_computation_allowed": False,
            "aggregate_winrate_allowed": False,
            "backtest_execution_allowed": False,
            "paper_live_execution_allowed": False,
        },
        "disclaimer": (
            "Single-window price description only. Not a PnL result, not a "
            "winrate, not a setup endorsement, not an executable signal."
        ),
    }
    if start is None:
        base.update({
            "entry_touched": False,
            "entry_index": None,
            "outcome": "entry_not_reached",
            "resolution_index": None,
            "bars_to_resolution": None,
            "mfe_r": None,
            "mae_r": None,
            "entry_time": None,
            "resolution_time": None,
        })
        return base

    res = resolution(bars, start, entry, stop, target, direction)
    exc = excursions(bars, start, res["resolution_index"], entry, risk, direction)
    res_index = res["resolution_index"]
    base.update({
        "entry_touched": True,
        "entry_index": start,
        "entry_time": bars[start].open_time,
        "outcome": res["outcome"],
        "resolution_index": res_index,
        "resolution_time": None if res_index is None else bars[res_index].open_time,
        "bars_to_resolution": res["bars_to_resolution"],
        "mfe_r": exc["mfe_r"],
        "mae_r": exc["mae_r"],
    })
    return base


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Descriptive Outcome",
        "",
        "_Single-window price description. Not PnL, not winrate, not a signal._",
        "",
        "## Plan",
        "",
        f"- Direction: `{report['direction']}`",
        f"- Entry / Stop / Target: `{report['entry']}` / `{report['stop']}` / `{report['target']}`",
        f"- Risk distance: `{report['risk_distance']}`",
        f"- Planned reward/risk: `{report['planned_reward_risk']}R`",
        "",
        "## What price did",
        "",
        f"- Entry touched: `{report['entry_touched']}`",
        f"- Outcome: `{report['outcome']}`",
        f"- Bars to resolution: `{report['bars_to_resolution']}`",
        f"- MFE: `{report['mfe_r']}R`",
        f"- MAE: `{report['mae_r']}R`",
        f"- Entry time: `{report['entry_time']}`",
        f"- Resolution time: `{report['resolution_time']}`",
        "",
        "Safety: read-only description; no PnL, no aggregate winrate, no orders, no backtest.",
        "",
    ]
    return "\n".join(lines)


def write_output(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Describe post-entry price excursion from an OHLC window (read-only)."
    )
    parser.add_argument("--root", type=Path, default=default_root())
    parser.add_argument("--ohlc-file", type=Path, required=True)
    parser.add_argument("--direction", choices=["long", "short"], required=True)
    parser.add_argument("--entry", type=float, required=True)
    parser.add_argument("--stop", type=float, required=True)
    parser.add_argument("--target", type=float, required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--markdown-output", type=Path)
    return parser.parse_args()


def main() -> int:
    configure_stdio()
    args = parse_args()
    root = args.root.resolve()
    ohlc_path = args.ohlc_file if args.ohlc_file.is_absolute() else root / args.ohlc_file
    bars = load_bars(ohlc_path)
    report = describe_outcome(
        bars,
        args.direction,
        float(numeric(args.entry)),
        float(numeric(args.stop)),
        float(numeric(args.target)),
    )
    report["source_path"] = ohlc_path.as_posix()

    output_path = args.output if args.output.is_absolute() else root / args.output
    write_output(output_path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    markdown_path = args.markdown_output
    if markdown_path:
        markdown_path = markdown_path if markdown_path.is_absolute() else root / markdown_path
        write_output(markdown_path, render_markdown(report))

    print(json.dumps({
        "output": output_path.as_posix(),
        "markdown_output": None if markdown_path is None else markdown_path.as_posix(),
        "entry_touched": report["entry_touched"],
        "outcome": report["outcome"],
        "bars_to_resolution": report["bars_to_resolution"],
        "mfe_r": report["mfe_r"],
        "mae_r": report["mae_r"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
