"""Связка архива ручных сценариев с рынком и детекторами.

Read-only слой: для каждого сценария из _scenario_archive/scenario_index.jsonl
с распознанной датой и тикером подтягивает дневные свечи Binance (публичный
endpoint, с файловым кэшем), восстанавливает состояние уровней на дату сценария
через механику scn002_spec_driven_backtest, снимает кандидатов детекторов на
этот день и описывает фактическое движение цены после даты. Ничего не торгует
и не считает PnL — только сопоставление для калибровки.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from binance_feed import FeedError, fetch_klines
from scn002_strict_kb_backtest import Bar, atr_at
import scn002_spec_driven_backtest as spec

LINK_VERSION = "scenario_market_link_v1"

# Уверенные исправления OCR-тикеров; неоднозначные (SUL: SOL или SUI?) не мапим
TICKER_ALIASES = {
    "WNLD": "WLD",
    "ONDU": "ONDO",
    "WLI": "WLFI",
    "WILI": "WLFI",
    "IILI": "WLFI",
    "ILTI": "WLFI",
    "ITIL": "WLFI",
}
AMBIGUOUS_TICKERS = {"SUL", "UMI"}

# Паттерны терпимы к типичным OCR-искажениям ("прабой", "накоплление", "робой")
BRANCH_INTENTS = [
    ("false_breakout", re.compile(r"ложн\w*\s+п?р[оа]б|лп\b", re.IGNORECASE)),
    ("breakout", re.compile(r"п?р[оа]б[оia]й|пробит", re.IGNORECASE)),
    ("consolidation", re.compile(r"накопл|поджат|консолид|распил", re.IGNORECASE)),
    ("pullback", re.compile(r"откат|отбой|отскок", re.IGNORECASE)),
    ("acceptance", re.compile(r"закреп", re.IGNORECASE)),
]

WARMUP_DAYS = 420
FORWARD_BARS = 20


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def default_root() -> Path:
    return Path(__file__).resolve().parents[1]


def classify_branch_intents(branches: list[str]) -> list[str]:
    intents: list[str] = []
    for branch in branches:
        matched = None
        for intent, pattern in BRANCH_INTENTS:
            if pattern.search(branch):
                matched = intent
                break
        intents.append(matched or "other")
    return intents


def load_scenarios(index_path: Path) -> list[dict[str, Any]]:
    records = []
    with index_path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            raw = raw.strip()
            if raw:
                records.append(json.loads(raw))
    return records


def cache_file(ohlc_dir: Path, symbol: str) -> Path:
    return ohlc_dir / f"{symbol}_1d.json"


def load_symbol_bars(ohlc_dir: Path, symbol: str, start: datetime, end: datetime) -> list[Bar]:
    """Дневные свечи [start, end] с файловым кэшем; докачивает при нехватке диапазона."""
    path = cache_file(ohlc_dir, symbol)
    cached: dict[str, Any] | None = None
    if path.exists():
        cached = json.loads(path.read_text(encoding="utf-8"))
        if cached["start_ms"] <= int(start.timestamp() * 1000) and cached["end_ms"] >= int(end.timestamp() * 1000):
            return [Bar(b["open_time_ms"], b["open"], b["high"], b["low"], b["close"], b["volume"]) for b in cached["bars"]]
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    if cached:
        start_ms = min(start_ms, cached["start_ms"])
        end_ms = max(end_ms, cached["end_ms"])
    raw = fetch_klines(symbol, interval="1d", limit=5000, start_ms=start_ms, end_ms=end_ms)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "symbol": symbol,
        "interval": "1d",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "start_ms": start_ms,
        "end_ms": end_ms,
        "bars": raw,
    }, ensure_ascii=False), encoding="utf-8")
    return [Bar(b["open_time_ms"], b["open"], b["high"], b["low"], b["close"], b["volume"]) for b in raw]


def compact_signal(signal: Any) -> dict[str, Any]:
    return {
        "side": signal.side,
        "status": signal.status,
        "score": signal.score,
        "level": signal.level,
        "entry": signal.entry,
        "stop": signal.stop,
        "target": signal.target,
        "hard_rejects": signal.hard_rejects,
        "strength_factors": signal.strength_factors,
        "weakness_factors": signal.weakness_factors,
        "manual_review_count": len(signal.manual_review_needed),
        "retest_classification": signal.retest_features.get("classification"),
        "retest_bias": signal.retest_features.get("bias"),
        "sweep_mode": signal.false_breakout_features.get("sweep_mode"),
        "risk_status": signal.risk_validation.get("status"),
    }


def evaluate_at_date(bars: list[Bar], idx: int, symbol: str, p: spec.SpecParams) -> dict[str, Any]:
    """Состояние уровней и кандидаты детекторов на баре idx (без заглядывания вперёд)."""
    resistances: list[spec.PivotLevel] = []
    supports: list[spec.PivotLevel] = []
    start = p.atr_period + p.pivot_k + 20
    if idx < start:
        return {"error": "insufficient_history"}
    setup_atr = None
    for i in range(start, idx + 1):
        setup_atr = atr_at(bars, i, p.atr_period)
        if setup_atr is None or setup_atr <= 0:
            continue
        spec.update_confirmed_levels(bars, i, setup_atr, resistances, supports, p)
    if setup_atr is None or setup_atr <= 0:
        return {"error": "no_atr"}
    bar = bars[idx]
    ups = sorted((lv.price for lv in resistances if lv.price >= bar.close), key=lambda x: x - bar.close)
    downs = sorted((lv.price for lv in supports if lv.price <= bar.close), key=lambda x: bar.close - x)
    candidates: list[dict[str, Any]] = []
    res_touch = sorted([lv for lv in resistances if bar.high >= lv.price], key=lambda lv: abs(bar.close - lv.price))[:3]
    sup_touch = sorted([lv for lv in supports if bar.low <= lv.price], key=lambda lv: abs(bar.close - lv.price))[:3]
    for side, levels, opposite in (("short", res_touch, supports), ("long", sup_touch, resistances)):
        for level in levels:
            signal = spec.build_signal(symbol, bars, idx, level, opposite, side, setup_atr, setup_atr, "1d", p)
            if signal is not None:
                candidates.append(compact_signal(signal))
    return {
        "close": bar.close,
        "setup_atr": setup_atr,
        "levels_total": {"resistances": len(resistances), "supports": len(supports)},
        "nearest_level_up": {"price": ups[0], "dist_atr": round((ups[0] - bar.close) / setup_atr, 2)} if ups else None,
        "nearest_level_down": {"price": downs[0], "dist_atr": round((bar.close - downs[0]) / setup_atr, 2)} if downs else None,
        "candidates": candidates,
    }


def forward_stats(bars: list[Bar], idx: int, setup_atr: float | None) -> dict[str, Any]:
    base = bars[idx].close
    out: dict[str, Any] = {}
    for horizon in (5, 10, 20):
        j = idx + horizon
        if j < len(bars):
            out[f"ret_{horizon}d_pct"] = round((bars[j].close / base - 1) * 100, 2)
    window = bars[idx + 1: idx + 1 + FORWARD_BARS]
    if window:
        max_high = max(b.high for b in window)
        min_low = min(b.low for b in window)
        out["bars_available"] = len(window)
        out["max_up_pct"] = round((max_high / base - 1) * 100, 2)
        out["max_down_pct"] = round((min_low / base - 1) * 100, 2)
        if setup_atr:
            out["max_up_atr"] = round((max_high - base) / setup_atr, 2)
            out["max_down_atr"] = round((base - min_low) / setup_atr, 2)
    return out


def write_report(out_root: Path, rows: list[dict[str, Any]]) -> None:
    from collections import Counter
    statuses = Counter(r["status"] for r in rows)
    linked = [r for r in rows if r["status"] == "linked"]
    with_candidates = [r for r in linked if r["market"].get("candidates")]
    intent_counts = Counter(i for r in rows for i in r.get("branch_intents", []))
    agree_fb = sum(
        1 for r in with_candidates
        if "false_breakout" in r.get("branch_intents", [])
        and any(c.get("sweep_mode") or (c.get("retest_bias") == "false_breakout") for c in r["market"]["candidates"])
    )
    fb_total = sum(1 for r in with_candidates if "false_breakout" in r.get("branch_intents", []))
    lines = [
        "# Связка сценариев с рынком и детекторами",
        "",
        f"Собрано: {datetime.now(timezone.utc).isoformat()}",
        f"Версия: {LINK_VERSION}",
        "",
        "## Статусы",
        "",
    ]
    lines += [f"- {status}: {count}" for status, count in statuses.most_common()]
    lines += [
        "",
        "## Сводка по связанным",
        "",
        "Замечание: кандидаты снимаются с D1-баров, а спека рассчитана на исполнение H1,",
        "поэтому статус почти всегда reject из-за technical_stop_gt_13pct_atr — это артефакт",
        "таймфрейма (как и в историческом D1-бэктесте: 0 сигналов). Смысловую нагрузку несут",
        "фичи кандидатов (retest_classification, sweep_mode, bias), а не статус.",
        "",
        f"- Связано с рынком: {len(linked)}",
        f"- Из них с кандидатами детекторов в день сценария: {len(with_candidates)}",
        f"- Ветки-намерения (по всем сценариям): {dict(intent_counts.most_common())}",
        f"- Сценарии «ложный пробой» с кандидатом, где детектор тоже видит sweep/ЛП-bias: {agree_fb} из {fb_total}",
        "",
        "| # | Дата | Символ | Ветки | Кандидаты (side/status/score) | Движение 20д |",
        "|---|---|---|---|---|---|",
    ]
    for r in sorted(linked, key=lambda x: x["scenario_id"]):
        cands = "; ".join(f"{c['side']}/{c['status']}/{c['score']:.1f}" for c in r["market"]["candidates"]) or "—"
        fwd = r.get("forward", {})
        move = f"+{fwd.get('max_up_pct', '?')}% / {fwd.get('max_down_pct', '?')}%" if fwd else "—"
        lines.append(
            f"| {r['scenario_id']} | {r['date']} | {r['symbol']} "
            f"| {'; '.join(r.get('branch_intents', [])) or '—'} | {cands} | {move} |"
        )
    (out_root / "market_link_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    configure_stdio()
    parser = argparse.ArgumentParser(description="Связка сценариев с дневными свечами Binance и детекторами.")
    parser.add_argument("--archive-root", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=0, help="только первые N пригодных сценариев (для проверки)")
    parser.add_argument("--refresh", action="store_true", help="пересчитать даже уже связанные сценарии")
    args = parser.parse_args()

    root = default_root()
    out_root = args.archive_root or root / "_scenario_archive"
    index_path = out_root / "scenario_index.jsonl"
    ohlc_dir = out_root / "ohlc"
    link_path = out_root / "market_link.jsonl"

    # Уверенные совпадения по «отпечатку» свечей (match_scenario_charts.py --recover):
    # восстановленный символ + календарное окно графика для доопределения даты
    overrides: dict[int, dict[str, Any]] = {}
    match_path = out_root / "chart_match.jsonl"
    if match_path.exists():
        for raw in match_path.read_text(encoding="utf-8").splitlines():
            if raw.strip():
                m = json.loads(raw)
                if m.get("status") == "matched":
                    overrides[m["scenario_id"]] = m

    scenarios = load_scenarios(index_path)
    done: dict[int, dict[str, Any]] = {}
    if link_path.exists() and not args.refresh:
        for raw in link_path.read_text(encoding="utf-8").splitlines():
            if raw.strip():
                record = json.loads(raw)
                if record.get("status"):
                    done[record["scenario_id"]] = record

    p = spec.SpecParams()
    rows: list[dict[str, Any]] = []
    processed = 0
    for scenario in sorted(scenarios, key=lambda s: s["scenario_id"]):
        sid = scenario["scenario_id"]
        if sid in done:
            rows.append(done[sid])
            continue
        parsed = scenario.get("parsed", {})
        branches = parsed.get("branches", [])
        record: dict[str, Any] = {
            "scenario_id": sid,
            "date": parsed.get("date"),
            "ticker": parsed.get("ticker"),
            "branch_intents": classify_branch_intents(branches),
            "branches": branches,
            "link_version": LINK_VERSION,
        }
        ticker = parsed.get("ticker")
        date_str = parsed.get("date")
        symbol: str | None = None
        override = overrides.get(sid)
        if override:
            symbol = override["symbol"]
            record["recovery"] = {
                "source": "chart_match",
                "sign_score": override.get("sign_score"),
                "corr": override.get("corr"),
                "window": override.get("window"),
            }
            w0 = datetime.strptime(override["window"][0], "%Y-%m-%d").date()
            w1 = datetime.strptime(override["window"][1], "%Y-%m-%d").date()
            partial = parsed.get("date_partial") or {}
            if not date_str and partial.get("day") and partial.get("month"):
                # год не распознан — берём тот, при котором дата попадает в окно графика
                for year in range(w0.year, w1.year + 1):
                    try:
                        candidate = datetime(year, partial["month"], partial["day"]).date()
                    except ValueError:
                        continue
                    if w0 <= candidate <= w1:
                        date_str = candidate.isoformat()
                        record["recovery"]["date_from_window"] = date_str
                        break
            elif date_str:
                # описка в годе на графике (дата "в будущем"): чиним по окну
                parsed_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                if not (w0 <= parsed_date <= w1):
                    for year in range(w0.year, w1.year + 1):
                        try:
                            candidate = parsed_date.replace(year=year)
                        except ValueError:
                            continue
                        if w0 <= candidate <= w1:
                            date_str = candidate.isoformat()
                            record["recovery"]["date_year_fixed"] = date_str
                            break
        if not date_str:
            record["status"] = ("matched_symbol_no_day" if override
                                else "skipped_partial_date" if parsed.get("date_partial")
                                else "skipped_no_date")
        elif symbol is None and not ticker:
            record["status"] = "skipped_no_ticker"
        elif symbol is None and ticker in AMBIGUOUS_TICKERS:
            record["status"] = "skipped_ambiguous_ticker"
        else:
            if symbol is None:
                mapped = TICKER_ALIASES.get(ticker, ticker)
                symbol = f"{mapped}USDT"
                if mapped != ticker:
                    record["ticker_alias"] = mapped
            record["symbol"] = symbol
            record["date"] = date_str
            date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            try:
                bars = load_symbol_bars(ohlc_dir, symbol,
                                        date - timedelta(days=WARMUP_DAYS),
                                        date + timedelta(days=FORWARD_BARS + 10))
            except (FeedError, OSError) as exc:
                bars = []
                record["status"] = "no_symbol_data"
                record["error"] = str(exc)[:200]
            if not bars:
                # пустой ответ без исключения: символ есть, но данных в окне нет
                record.setdefault("status", "no_symbol_data")
            else:
                target_ms = int(date.timestamp() * 1000)
                idx = next((i for i, b in enumerate(bars) if b.open_time == target_ms), None)
                if idx is None:
                    record["status"] = "date_not_in_history"
                else:
                    market = evaluate_at_date(bars, idx, symbol, p)
                    if "error" in market:
                        record["status"] = market["error"]
                    else:
                        record["status"] = "linked"
                        record["market"] = market
                        record["forward"] = forward_stats(bars, idx, market.get("setup_atr"))
        rows.append(record)
        processed += 1
        if processed % 20 == 0:
            print(f"[{processed}] scenario {sid}: {record['status']}")
        if args.limit and processed >= args.limit:
            rows.extend(done[s["scenario_id"]] for s in scenarios
                        if s["scenario_id"] in done and all(r["scenario_id"] != s["scenario_id"] for r in rows))
            break

    with link_path.open("w", encoding="utf-8") as handle:
        for record in sorted(rows, key=lambda r: r["scenario_id"]):
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    write_report(out_root, rows)
    from collections import Counter
    print("statuses:", dict(Counter(r["status"] for r in rows).most_common()))
    print(f"index: {link_path}")
    print(f"report: {out_root / 'market_link_report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
