"""Shadow-форвард пробойного правила «по тренду». Без денег и без ордеров.

Правило заморожено коммитом 541d2e7 (2026-07-15): вход на открытии бара после
закрытия за уровнем >= 0.25 ATR в направлении тренда, стоп за уровнем 0.5 ATR,
тейк 3R, таймаут 20 баров, комиссия 0.0004 на сторону. Логика сигналов — та же
функция study_symbol, что и в исследовании (один источник правды).

В зачёт идут только сделки с датой входа >= SHADOW_START_ENTRY — ни один бар,
на которых правило формулировалось, в форвард не попадает.

Трекер идемпотентен: каждый запуск пересчитывает состояние по закрытым дневным
свечам и догоняет пропущенные дни. Запускать раз в день (или реже — ничего не
теряется). Артефакты в _shadow_forward/:
  - shadow_ledger.jsonl  — все сигналы с исходами (пересобирается);
  - first_seen.jsonl     — append-only аудит: когда сигнал впервые увиден;
  - shadow_report.md     — накопительная сводка.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from analyze_breakout_by_trend import study_symbol
from link_scenarios_to_market import load_symbol_bars
from match_scenario_charts import UNIVERSE
import scn002_spec_driven_backtest as spec

TRACKER_VERSION = "shadow_breakout_v1"
SHADOW_START_ENTRY = "2026-07-16"   # заморожено; не менять без новой заморозки правила
BENCHMARK_R = 0.15                  # матожидание из бэктеста 2024-2026
WARMUP_DAYS = 500


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def default_root() -> Path:
    return Path(__file__).resolve().parents[1]


def signal_id(event: dict[str, Any]) -> str:
    trade = event["trade"]
    return f"{event['symbol']}:{trade['entry_date']}:{event['breakout_dir']}:{event['level']:.6g}"


def main() -> int:
    configure_stdio()
    parser = argparse.ArgumentParser(description="Shadow-форвард пробойного правила (без ордеров).")
    parser.add_argument("--since", default=SHADOW_START_ENTRY,
                        help="только для отладки; зачётная дата заморожена в скрипте")
    args = parser.parse_args()
    since = args.since

    root = default_root()
    shadow_root = root / "_shadow_forward"
    shadow_root.mkdir(exist_ok=True)
    ohlc_dir = root / "_scenario_archive" / "ohlc"
    now = datetime.now(timezone.utc)
    start_fetch = datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=timezone.utc) - timedelta(days=WARMUP_DAYS)

    p = spec.SpecParams()
    signals: list[dict[str, Any]] = []
    for ticker in UNIVERSE:
        symbol = f"{ticker}USDT"
        try:
            bars = load_symbol_bars(ohlc_dir, symbol, start_fetch, now)
        except Exception as exc:
            print(f"[warn] {symbol}: {str(exc)[:100]}")
            continue
        if len(bars) < 120:
            continue
        for event in study_symbol(symbol, bars, p):
            trade = event.get("trade")
            if event.get("aligned") is True and trade and trade["entry_date"] >= since:
                signals.append({
                    "id": signal_id(event),
                    "symbol": event["symbol"],
                    "touch_date": event["date"],
                    "level": event["level"],
                    "direction": event["breakout_dir"],
                    "trend": event["trend"],
                    **{k: trade[k] for k in ("entry_date", "entry", "stop", "target", "exit", "exit_date", "r")},
                    "tracker_version": TRACKER_VERSION,
                })
    signals.sort(key=lambda s: (s["entry_date"], s["symbol"]))

    if since != SHADOW_START_ENTRY:
        # отладочный прогон: считаем и печатаем, но зачётные файлы не трогаем
        closed = [s for s in signals if s["exit"] in ("stop", "target", "timeout")]
        wins = sum(1 for s in closed if s["r"] > 0)
        print(f"[dry-run since={since}] signals={len(signals)} closed={len(closed)}")
        if closed:
            tot = sum(s["r"] for s in closed)
            print(f"[dry-run] WR={100 * wins / len(closed):.0f}% totR={tot:+.1f}")
        return 0

    ledger_path = shadow_root / "shadow_ledger.jsonl"
    with ledger_path.open("w", encoding="utf-8") as handle:
        for s in signals:
            handle.write(json.dumps(s, ensure_ascii=False) + "\n")

    # append-only аудит первого появления сигнала
    seen_path = shadow_root / "first_seen.jsonl"
    seen_ids = set()
    if seen_path.exists():
        for raw in seen_path.read_text(encoding="utf-8").splitlines():
            if raw.strip():
                seen_ids.add(json.loads(raw)["id"])
    new_signals = [s for s in signals if s["id"] not in seen_ids]
    with seen_path.open("a", encoding="utf-8") as handle:
        for s in new_signals:
            handle.write(json.dumps({"id": s["id"], "first_seen": now.isoformat()},
                                    ensure_ascii=False) + "\n")

    closed = [s for s in signals if s["exit"] in ("stop", "target", "timeout")]
    open_pos = [s for s in signals if s["exit"] == "open"]
    wins = sum(1 for s in closed if s["r"] > 0)
    tot_r = sum(s["r"] for s in closed)
    days = (now - datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=timezone.utc)).days

    lines = [
        "# Shadow-форвард: пробой по тренду",
        "",
        f"Обновлено: {now.isoformat()}",
        f"Правило заморожено: коммит 541d2e7, зачёт входов с {since} (день {days})",
        f"Бенчмарк из бэктеста 2024-2026: +{BENCHMARK_R:.2f}R/сделку",
        "",
        f"- Закрытых сделок: {len(closed)}"
        + (f", WR {100 * wins / len(closed):.0f}%, totR {tot_r:+.1f}, "
           f"R/сделку {tot_r / len(closed):+.2f}" if closed else ""),
        f"- Открытых позиций: {len(open_pos)} (mark-to-market {sum(s['r'] for s in open_pos):+.1f}R)",
        f"- Новых сигналов в этом запуске: {len(new_signals)}",
        "",
        "| Вход | Символ | Напр. | Уровень | Статус | Выход | R |",
        "|---|---|---|---|---|---|---|",
    ]
    for s in signals[-60:]:
        lines.append(f"| {s['entry_date']} | {s['symbol']} | {s['direction']} "
                     f"| {s['level']:.6g} | {s['exit']} | {s.get('exit_date') or '—'} | {s['r']:+.2f} |")
    (shadow_root / "shadow_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"signals={len(signals)} closed={len(closed)} open={len(open_pos)} new_today={len(new_signals)}")
    if closed:
        print(f"WR={100 * wins / len(closed):.0f}% totR={tot_r:+.1f} R/trade={tot_r / len(closed):+.2f}")
    print(f"report: {shadow_root / 'shadow_report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
