"""Проверка гипотезы: в тренде у уровня чаще реализуется пробой, чем ЛП.

Слой 1 (частоты): по дневным свечам всех кэшированных символов находим дни
касания подтверждённых уровней (механика scn002_spec_driven), классифицируем
исход за 20 дней (классификатор из build_scenario_calibration_report) и
раскладываем частоты по режиму тренда (фрактальные пивоты как в SCN-003,
с честным confirm по закрытию) и направлению потенциального пробоя.

Слой 2 (матожидание): простое пробойное правило — после касания ждём первого
закрытия за уровнем >= 0.25 ATR в направлении «от p0», вход по открытию
следующего бара, стоп за уровнем 0.5 ATR, тейк 3R, таймаут 20 баров,
комиссия 0.0004 на сторону. Считаем в R, отдельно по строкам матрицы
(тренд x направление). Read-only исследование, не торговый сигнал.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from build_scenario_calibration_report import HORIZON, classify_outcome
from link_scenarios_to_market import load_symbol_bars
from match_scenario_charts import UNIVERSE
from scn002_strict_kb_backtest import Bar, atr_at
from scn003_bsu_bpu_m5_backtest import confirmed_pivots, global_trend
import scn002_spec_driven_backtest as spec

STUDY_VERSION = "breakout_by_trend_v1"
TOUCH_COOLDOWN = 5      # дней: повторные касания того же уровня не считаем
ACCEPT_ATR = 0.25       # закрытие за уровнем для входа
STOP_ATR = 0.5          # стоп за уровнем
RR_TARGET = 3.0
FEE = 0.0004
ENTRY_WAIT = 10         # дней на появление закрытия за уровнем после касания


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def default_root() -> Path:
    return Path(__file__).resolve().parents[1]


def trend_at(pivots, confirm_times, t_ms: int, n: int = 4) -> str:
    import bisect
    idx = bisect.bisect_right(confirm_times, t_ms)
    return global_trend(pivots[:idx], n)


def simulate_breakout_trade(bars: list[Bar], touch_i: int, level: float, atr: float,
                            direction: str) -> dict[str, Any] | None:
    """Вход после первого закрытия за уровнем в заданном направлении."""
    entry_i = None
    for j in range(touch_i + 1, min(touch_i + 1 + ENTRY_WAIT, len(bars) - 1)):
        close = bars[j].close
        if direction == "long" and close > level + ACCEPT_ATR * atr:
            entry_i = j + 1
            break
        if direction == "short" and close < level - ACCEPT_ATR * atr:
            entry_i = j + 1
            break
    if entry_i is None or entry_i >= len(bars):
        return None
    entry = bars[entry_i].open
    stop = level - STOP_ATR * atr if direction == "long" else level + STOP_ATR * atr
    risk = abs(entry - stop)
    if risk <= 0 or risk < 0.05 * atr:
        return None
    target = entry + RR_TARGET * risk if direction == "long" else entry - RR_TARGET * risk
    fee_r = 2 * FEE * entry / risk
    base = {
        "entry_i": entry_i,
        "entry_date": bars[entry_i].dt.strftime("%Y-%m-%d"),
        "entry": entry, "stop": stop, "target": target, "risk": risk,
    }
    for j in range(entry_i, min(entry_i + HORIZON, len(bars))):
        bar = bars[j]
        hit_stop = bar.low <= stop if direction == "long" else bar.high >= stop
        hit_target = bar.high >= target if direction == "long" else bar.low <= target
        if hit_stop:  # консервативно: конфликт в одном баре = стоп
            return {**base, "r": -1.0 - fee_r, "exit": "stop",
                    "exit_date": bar.dt.strftime("%Y-%m-%d")}
        if hit_target:
            return {**base, "r": RR_TARGET - fee_r, "exit": "target",
                    "exit_date": bar.dt.strftime("%Y-%m-%d")}
    j = min(entry_i + HORIZON, len(bars)) - 1
    r = (bars[j].close - entry) / risk if direction == "long" else (entry - bars[j].close) / risk
    # горизонт ещё не истёк на имеющихся данных -> позиция открыта (mark-to-market)
    exit_kind = "timeout" if entry_i + HORIZON <= len(bars) else "open"
    return {**base, "r": r - fee_r, "exit": exit_kind,
            "exit_date": bars[j].dt.strftime("%Y-%m-%d")}


def study_symbol(symbol: str, bars: list[Bar], p) -> list[dict[str, Any]]:
    pivots = confirmed_pivots(bars, 3)
    confirm_times = [pv.confirm_ms for pv in pivots]
    resistances: list[spec.PivotLevel] = []
    supports: list[spec.PivotLevel] = []
    start = p.atr_period + p.pivot_k + 20
    recent: dict[tuple[str, float], int] = {}
    events: list[dict[str, Any]] = []
    for i in range(start, len(bars) - 5):
        atr = atr_at(bars, i, p.atr_period)
        if atr is None or atr <= 0:
            continue
        spec.update_confirmed_levels(bars, i, atr, resistances, supports, p)
        bar = bars[i]
        touches = ([("res", lv) for lv in resistances if bar.high >= lv.price >= bar.low - 2 * atr]
                   + [("sup", lv) for lv in supports if bar.low <= lv.price <= bar.high + 2 * atr])
        for side, lv in touches:
            if not (bar.low <= lv.price <= bar.high):
                continue
            key = (side, round(lv.price / max(atr * 0.3, 1e-9)))
            if i - recent.get(key, -10**9) < TOUCH_COOLDOWN:
                recent[key] = i
                continue
            recent[key] = i
            p0_above = bar.close >= lv.price
            breakout_dir = "short" if p0_above else "long"
            # ВАЖНО: классификатор смотрит вперёд, тренд и уровни — только назад
            trend = trend_at(pivots, confirm_times, bar.open_time)
            realized = classify_outcome(bars, i, lv.price, atr)
            aligned = (trend == breakout_dir) if trend in ("long", "short") else None
            trade = simulate_breakout_trade(bars, i, lv.price, atr, breakout_dir)
            events.append({
                "symbol": symbol,
                "date": bar.dt.strftime("%Y-%m-%d"),
                "level": lv.price,
                "atr": atr,
                "trend": trend,
                "breakout_dir": breakout_dir,
                "aligned": aligned,
                "outcome": realized.get("outcome"),
                "trade": trade,
            })
    return events


def main() -> int:
    configure_stdio()
    parser = argparse.ArgumentParser(description="Частоты исходов и матожидание пробоя по режиму тренда.")
    parser.add_argument("--archive-root", type=Path, default=None)
    args = parser.parse_args()

    root = default_root()
    out_root = args.archive_root or root / "_scenario_archive"
    ohlc_dir = out_root / "ohlc"
    p = spec.SpecParams()
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    end = datetime.now(timezone.utc)

    events: list[dict[str, Any]] = []
    for ticker in UNIVERSE:
        symbol = f"{ticker}USDT"
        try:
            bars = load_symbol_bars(ohlc_dir, symbol, start, end)
        except Exception as exc:
            print(f"[skip] {symbol}: {str(exc)[:100]}")
            continue
        if len(bars) < 120:
            continue
        symbol_events = study_symbol(symbol, bars, p)
        events.extend(symbol_events)
        print(f"{symbol}: {len(symbol_events)} touch events")

    from collections import Counter, defaultdict
    clean = [e for e in events if e["outcome"] in (
        "breakout", "false_breakout", "consolidation", "pullback", "range_mixed")]

    def bucket(e) -> str:
        if e["aligned"] is True:
            return "по тренду"
        if e["aligned"] is False:
            return "против тренда"
        return "рейндж"

    lines = [
        "# Исходы у уровня и матожидание пробоя по режиму тренда",
        "",
        f"Собрано: {datetime.now(timezone.utc).isoformat()}",
        f"Версия: {STUDY_VERSION}; символов: {len(UNIVERSE)}, событий-касаний: {len(clean)}",
        "",
        "## Частоты исходов (доля от строки)",
        "",
        "| Режим | N | breakout | false_breakout | consolidation | pullback | mixed |",
        "|---|---|---|---|---|---|---|",
    ]
    by_bucket: dict[str, list] = defaultdict(list)
    for e in clean:
        by_bucket[bucket(e)].append(e)
    for name in ("по тренду", "против тренда", "рейндж"):
        group = by_bucket.get(name, [])
        if not group:
            continue
        counts = Counter(e["outcome"] for e in group)
        n = len(group)
        lines.append(
            f"| {name} | {n} | " + " | ".join(
                f"{100 * counts.get(k, 0) / n:.0f}%" for k in
                ("breakout", "false_breakout", "consolidation", "pullback", "range_mixed")) + " |")

    lines += ["", "## Матожидание пробойного правила (вход на закрытии за уровнем, стоп 0.5 ATR, тейк 3R)", "",
              "| Режим | Сделок | WR | totR | R/сделку |", "|---|---|---|---|---|"]
    for name in ("по тренду", "против тренда", "рейндж"):
        trades = [e["trade"] for e in by_bucket.get(name, []) if e["trade"]]
        if not trades:
            continue
        wins = sum(1 for t in trades if t["r"] > 0)
        tot = sum(t["r"] for t in trades)
        lines.append(f"| {name} | {len(trades)} | {100 * wins / len(trades):.0f}% "
                     f"| {tot:+.1f}R | {tot / len(trades):+.2f}R |")
    lines += ["", "Замечания: касания дедуплицированы (кулдаун "
              f"{TOUCH_COOLDOWN}д на уровень); конфликт стоп/тейк в одном баре засчитан как стоп; "
              "исполнение без проскальзывания — оценка оптимистична; период один (2024-2026)."]

    report_path = root / "_exports" / "breakout_by_trend_report.md"
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    import json
    with (root / "_exports" / "breakout_by_trend_events.jsonl").open("w", encoding="utf-8") as handle:
        for e in clean:
            handle.write(json.dumps(e, ensure_ascii=False) + "\n")
    print("\n".join(lines[6:len(lines)]))
    print(f"\nreport: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
