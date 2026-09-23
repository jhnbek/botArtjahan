"""Continuous, read-only market scanner for the trader-brain layer.

The scanner gives the bot uninterrupted access to market data: it loops over a
watchlist, pulls fresh OHLC from the Binance public feed, runs each symbol
through the existing read-only KB chart-review pipeline, and appends a
*signal-mode* case to the casebook. Those signal cases land in the casebook
*unlabelled*, so they queue up in ``casebook.py pending`` and wait for the human
confirmation loop - the scanner never auto-confirms anything.

SAFETY INVARIANTS
-----------------
* Uses only the read-only Binance feed (public klines, no key, no orders).
* Builds only the read-only KB packet (no PnL, no winrate, no backtest).
* Signal cases are written with mode="signal" and every execution flag False;
  if a packet ever carried a True execution flag the scanner refuses to store it.
* No entry / stop / target is invented, so no descriptive outcome is attached -
  there is nothing to peek at, which keeps decision quality judgeable blind.
* A heartbeat/state file is written so a GUI can show "what the bot is doing"
  without the scanner ever touching execution.

Standard library only, so it runs inside a PyInstaller bundle.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Make sibling brain modules importable whether run as a script or imported.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import binance_feed
import casebook_store as store
from casebook import snapshot_from_packet, example_ids
from chart_review_packet import build_live_kb_chart_review_packet

try:
    from situation_analyzer import configure_stdio
except ModuleNotFoundError:  # pragma: no cover - packaging fallback
    from knowledge_bot.situation_analyzer import configure_stdio


ROOT = store.ROOT
SCANNER_DIR = ROOT / "_knowledge_base" / "live_scanner"
STATE_PATH = SCANNER_DIR / "scanner_state.json"
SCAN_LOG_PATH = SCANNER_DIR / "scan_log.jsonl"

# A small, liquid default watchlist. Override with --symbols on the CLI.
DEFAULT_WATCHLIST = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT",
]
INTRADAY_INTERVALS = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h"}

# The eight read-only flags that must stay False on every packet we store.
EXECUTION_FLAGS = (
    "execution_allowed",
    "runtime_signal_allowed",
    "order_generation_allowed",
    "pnl_computation_allowed",
    "aggregate_winrate_allowed",
    "paper_trading_allowed",
    "live_trading_allowed",
    "backtest_harness_allowed",
)


class ScannerError(RuntimeError):
    """Raised when the scanner cannot safely proceed."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def kb_context_interval(interval: str) -> str:
    return "1d" if interval in INTRADAY_INTERVALS else interval


def kb_higher_interval(context_interval: str) -> str:
    return "1w" if context_interval != "1w" else ""


def kb_limit(interval: str, requested: int) -> int:
    if interval == "1w":
        return min(260, binance_feed.MAX_LIMIT)
    if interval in {"1d", "3d"}:
        return min(max(requested, 365), binance_feed.MAX_LIMIT)
    return min(requested, binance_feed.MAX_LIMIT)


@dataclass
class ScannerConfig:
    """Configuration for a scan run."""

    watchlist: list[str] = field(default_factory=lambda: list(DEFAULT_WATCHLIST))
    market: str = "spot"
    interval: str = "1d"
    limit: int = 200
    quote: str = "USDT"
    atr_period: int = 14
    top_k: int = 3
    poll_seconds: float = 300.0
    max_cycles: int | None = None
    timeout: int = binance_feed.DEFAULT_TIMEOUT
    dry_run: bool = False
    source: str = "scanner_daemon"


def _packet_args(symbol: str, ohlc_path: Path, cfg: ScannerConfig,
                 *, context_ohlc_path: Path | None = None,
                 context_interval: str | None = None,
                 higher_ohlc_path: Path | None = None,
                 higher_interval: str = "") -> argparse.Namespace:
    """Build the Namespace the read-only packet builder expects.

    Signal scanning supplies no direction/entry/stop/target on purpose, so the
    packet never describes an outcome and stays judgeable blind to the result.
    """
    return argparse.Namespace(
        ohlc_file=ohlc_path,
        context_ohlc_file=context_ohlc_path,
        higher_ohlc_file=higher_ohlc_path,
        symbol=symbol,
        instrument=symbol,
        venue=f"binance_{cfg.market}",
        timeframe=cfg.interval,
        context_timeframe=context_interval,
        higher_timeframe=higher_interval,
        direction=None,
        date_session=None,
        level=None,
        entry=None,
        stop=None,
        target=None,
        trigger=None,
        atr_period=cfg.atr_period,
        top_k=cfg.top_k,
        no_describe_outcome=True,
    )


def _assert_read_only(packet: dict[str, Any]) -> None:
    for flag in EXECUTION_FLAGS:
        if packet.get(flag) is True:
            raise ScannerError(
                f"refusing to store a packet with {flag}=True; scanner is read-only"
            )


def _append_signal_case(packet: dict[str, Any], packet_path: Path | None,
                        cfg: ScannerConfig) -> tuple[dict[str, Any], bool]:
    """Append a signal-mode case mirroring casebook.cmd_append (no outcome)."""
    _assert_read_only(packet)
    snapshot = snapshot_from_packet(packet)
    analyzer = packet.get("analyzer_summary") or {}
    evidence = {
        "supporting_example_ids": example_ids(analyzer.get("supporting_examples")),
        "guardrail_example_ids": example_ids(analyzer.get("guardrail_examples")),
    }
    provenance = {
        "packet_path": packet_path.as_posix() if packet_path else None,
        "dataset_id": packet.get("dataset_id"),
        "code_version": packet.get("dataset_id"),
        "produced_by": "scanner_daemon",
    }
    read_only_flags = {flag: packet.get(flag) for flag in EXECUTION_FLAGS if flag in packet}
    return store.append_case(snapshot, cfg.source, "signal", evidence, provenance, read_only_flags)


def scan_symbol(symbol: str, cfg: ScannerConfig) -> dict[str, Any]:
    """Fetch fresh OHLC for one symbol, analyze it, and queue a signal case.

    Returns a per-symbol summary dict (never raises for a single symbol; the
    error is captured so one bad symbol cannot stop the loop)."""
    symbol = binance_feed.normalize_symbol(symbol)
    result: dict[str, Any] = {"symbol": symbol, "ok": False}
    try:
        binance_feed.get_ohlc(
            symbol,
            interval=cfg.interval,
            limit=cfg.limit,
            market=cfg.market,
            write_cache=True,
            timeout=cfg.timeout,
        )
        ohlc_path = binance_feed.cache_path(symbol, cfg.interval, cfg.market)
        context_interval = kb_context_interval(cfg.interval)
        context_path = ohlc_path
        if context_interval != cfg.interval:
            binance_feed.get_ohlc(
                symbol,
                interval=context_interval,
                limit=kb_limit(context_interval, cfg.limit),
                market=cfg.market,
                write_cache=True,
                timeout=cfg.timeout,
            )
            context_path = binance_feed.cache_path(symbol, context_interval, cfg.market)
        higher_interval = kb_higher_interval(context_interval)
        higher_path: Path | None = None
        if higher_interval:
            binance_feed.get_ohlc(
                symbol,
                interval=higher_interval,
                limit=kb_limit(higher_interval, cfg.limit),
                market=cfg.market,
                write_cache=True,
                timeout=cfg.timeout,
            )
            higher_path = binance_feed.cache_path(symbol, higher_interval, cfg.market)
        packet = build_live_kb_chart_review_packet(
            _packet_args(
                symbol,
                ohlc_path,
                cfg,
                context_ohlc_path=context_path,
                context_interval=context_interval,
                higher_ohlc_path=higher_path,
                higher_interval=higher_interval,
            )
        )
        review_state = (packet.get("review_state") or {}).get("state")
        verdict = ((packet.get("analyzer_summary") or {}).get("verdict") or {}).get("state")
        kb = packet.get("kb_analysis") or {}
        permission = kb.get("permission_summary") or {}
        level_summary = kb.get("level_summary") or {}
        result.update({
            "review_state": review_state,
            "analyzer_verdict": verdict,
            "kb_status": kb.get("status"),
            "kb_review_status": kb.get("review_status"),
            "advisor_status": permission.get("advisor_status"),
            "best_entry_model": permission.get("best_entry_model"),
            "best_entry_status": permission.get("best_entry_status"),
            "working_level_count": level_summary.get("working_level_count"),
            "context_timeframe": context_interval,
            "higher_timeframe": higher_interval,
            "ohlc_file": ohlc_path.as_posix(),
        })
        if cfg.dry_run:
            result.update({"ok": True, "stored": False, "dry_run": True})
            return result
        case, created = _append_signal_case(packet, ohlc_path, cfg)
        result.update({
            "ok": True,
            "stored": True,
            "created": created,
            "case_id": case["case_id"],
        })
    except Exception as exc:  # noqa: BLE001 - one symbol must not kill the loop
        result.update({
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
        })
    return result


def run_once(cfg: ScannerConfig, cycle: int = 1) -> dict[str, Any]:
    """Scan every symbol in the watchlist once and return a cycle summary."""
    started = now_iso()
    symbols = [binance_feed.normalize_symbol(s) for s in cfg.watchlist]
    results = [scan_symbol(symbol, cfg) for symbol in symbols]
    ok = [r for r in results if r.get("ok")]
    failed = [r for r in results if not r.get("ok")]
    new_cases = sum(1 for r in ok if r.get("created"))
    summary = {
        "cycle": cycle,
        "started_at": started,
        "finished_at": now_iso(),
        "market": cfg.market,
        "interval": cfg.interval,
        "symbols_scanned": len(results),
        "symbols_ok": len(ok),
        "symbols_failed": len(failed),
        "new_signal_cases": new_cases,
        "dry_run": cfg.dry_run,
        "read_only": True,
        "results": results,
    }
    return summary


def _write_state(summary: dict[str, Any]) -> None:
    SCANNER_DIR.mkdir(parents=True, exist_ok=True)
    state = {
        "updated_at": summary["finished_at"],
        "status": "idle",
        "last_cycle": summary["cycle"],
        "last_started_at": summary["started_at"],
        "last_finished_at": summary["finished_at"],
        "market": summary["market"],
        "interval": summary["interval"],
        "symbols_scanned": summary["symbols_scanned"],
        "symbols_ok": summary["symbols_ok"],
        "symbols_failed": summary["symbols_failed"],
        "new_signal_cases": summary["new_signal_cases"],
        "read_only": True,
        "latest": [
            {
                "symbol": r["symbol"],
                "ok": r.get("ok"),
                "review_state": r.get("review_state"),
                "analyzer_verdict": r.get("analyzer_verdict"),
                "case_id": r.get("case_id"),
                "error": r.get("error"),
            }
            for r in summary["results"]
        ],
    }
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _append_scan_log(summary: dict[str, Any]) -> None:
    SCANNER_DIR.mkdir(parents=True, exist_ok=True)
    record = {k: v for k, v in summary.items() if k != "results"}
    with SCAN_LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def run_loop(cfg: ScannerConfig) -> int:
    """Run scan cycles until max_cycles is reached (or forever if None)."""
    cycle = 0
    while True:
        cycle += 1
        summary = run_once(cfg, cycle=cycle)
        _append_scan_log(summary)
        _write_state(summary)
        print(json.dumps({
            "cycle": summary["cycle"],
            "symbols_ok": summary["symbols_ok"],
            "symbols_failed": summary["symbols_failed"],
            "new_signal_cases": summary["new_signal_cases"],
            "finished_at": summary["finished_at"],
        }, ensure_ascii=False), flush=True)
        if cfg.max_cycles is not None and cycle >= cfg.max_cycles:
            break
        try:
            time.sleep(max(0.0, cfg.poll_seconds))
        except KeyboardInterrupt:  # pragma: no cover - manual stop
            print(json.dumps({"stopped": "keyboard_interrupt", "cycle": cycle}), flush=True)
            break
    return 0


def _parse_symbols(raw: str | None, fallback: list[str]) -> list[str]:
    if not raw:
        return list(fallback)
    parts = [p.strip() for chunk in raw.split(",") for p in chunk.split()]
    return [p for p in parts if p] or list(fallback)


def _cfg_from_args(args: argparse.Namespace) -> ScannerConfig:
    return ScannerConfig(
        watchlist=_parse_symbols(args.symbols, DEFAULT_WATCHLIST),
        market=args.market,
        interval=args.interval,
        limit=args.limit,
        quote=args.quote,
        atr_period=args.atr_period,
        top_k=args.top_k,
        poll_seconds=args.poll_seconds,
        max_cycles=args.max_cycles,
        timeout=args.timeout,
        dry_run=args.dry_run,
        source=args.source,
    )


def cmd_scan_once(args: argparse.Namespace) -> int:
    cfg = _cfg_from_args(args)
    summary = run_once(cfg, cycle=1)
    _append_scan_log(summary)
    _write_state(summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    cfg = _cfg_from_args(args)
    return run_loop(cfg)


def cmd_status(args: argparse.Namespace) -> int:
    if not STATE_PATH.exists():
        print(json.dumps({"status": "no_state", "state_path": STATE_PATH.as_posix()}, ensure_ascii=False, indent=2))
        return 0
    print(STATE_PATH.read_text(encoding="utf-8"))
    return 0


def _add_common_scan_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--symbols", help="comma/space separated symbols (default: built-in watchlist)")
    parser.add_argument("--market", default="spot", choices=sorted(binance_feed.MARKET_BASES.keys()))
    parser.add_argument("--interval", default="1d")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--quote", default="USDT")
    parser.add_argument("--atr-period", type=int, default=14, dest="atr_period")
    parser.add_argument("--top-k", type=int, default=3, dest="top_k")
    parser.add_argument("--timeout", type=int, default=binance_feed.DEFAULT_TIMEOUT)
    parser.add_argument("--dry-run", action="store_true", help="analyze but do not write casebook cases")
    parser.add_argument("--source", default="scanner_daemon")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only continuous market scanner (signal-mode casebook).")
    sub = parser.add_subparsers(dest="command", required=True)

    once = sub.add_parser("scan-once", help="run a single scan cycle over the watchlist")
    _add_common_scan_args(once)
    once.set_defaults(func=cmd_scan_once)

    run = sub.add_parser("run", help="run continuous scan cycles")
    _add_common_scan_args(run)
    run.add_argument("--poll-seconds", type=float, default=300.0, dest="poll_seconds")
    run.add_argument("--max-cycles", type=int, default=None, dest="max_cycles",
                     help="stop after this many cycles (default: run forever)")
    run.set_defaults(func=cmd_run)

    status = sub.add_parser("status", help="print the latest scanner heartbeat/state")
    status.set_defaults(func=cmd_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    # scan-once / status do not define poll/max-cycles; give them defaults.
    if not hasattr(args, "poll_seconds"):
        args.poll_seconds = 300.0
    if not hasattr(args, "max_cycles"):
        args.max_cycles = None
    try:
        return args.func(args)
    except ScannerError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
