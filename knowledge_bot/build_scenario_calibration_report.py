"""Итоговая калибровка: прогноз из сценария vs механика vs фактический исход.

Объединяет три слоя _scenario_archive/:
  - scenario_index.jsonl  — OCR: дата, тикер, ветки прогноза;
  - market_link.jsonl     — привязка к свечам Binance, кандидаты детекторов;
  - chart_native.jsonl    — пиксельный разбор скриншота.

Для связанных сценариев классифицирует по реальным барам, что реализовалось
у опорного уровня в следующие 20 дней (breakout / false_breakout /
consolidation / pullback), и считает: попал ли фактический исход в одну из
веток сценария, и как коррелируют фичи детекторов с исходом.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from link_scenarios_to_market import load_symbol_bars
from scn002_strict_kb_backtest import Bar

CALIB_VERSION = "scenario_calibration_v1"
HORIZON = 20

# соответствие намерений веток классам исхода
INTENT_TO_OUTCOME = {
    "false_breakout": "false_breakout",
    "breakout": "breakout",
    "acceptance": "breakout",
    "consolidation": "consolidation",
    "pullback": "pullback",
}


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def default_root() -> Path:
    return Path(__file__).resolve().parents[1]


def read_jsonl(path: Path) -> dict[int, dict[str, Any]]:
    records: dict[int, dict[str, Any]] = {}
    if path.exists():
        for raw in path.read_text(encoding="utf-8").splitlines():
            if raw.strip():
                record = json.loads(raw)
                records[record["scenario_id"]] = record
    return records


def pick_reference_level(link: dict[str, Any]) -> float | None:
    """Опорный уровень сценария: уровень кандидата, иначе ближайший механический."""
    market = link.get("market", {})
    candidates = market.get("candidates", [])
    if candidates:
        return float(candidates[0]["level"])
    close = market.get("close")
    options = []
    for key in ("nearest_level_up", "nearest_level_down"):
        lv = market.get(key)
        if lv and lv["dist_atr"] <= 1.5:
            options.append((lv["dist_atr"], lv["price"]))
    if options:
        return float(min(options)[1])
    return None


def classify_outcome(bars: list[Bar], idx: int, level: float, atr: float) -> dict[str, Any]:
    """Что произошло у уровня за HORIZON баров после idx (по закрытым барам)."""
    p0_above = bars[idx].close >= level
    swept = False
    window = bars[idx + 1: idx + 1 + HORIZON]
    if len(window) < 5:
        return {"outcome": "insufficient_forward", "detail": f"only {len(window)} bars"}
    beyond_closes = 0
    for i, bar in enumerate(window):
        pierced = (bar.low < level - 0.1 * atr) if p0_above else (bar.high > level + 0.1 * atr)
        closed_beyond = (bar.close < level - 0.25 * atr) if p0_above else (bar.close > level + 0.25 * atr)
        closed_back = (bar.close >= level) if p0_above else (bar.close <= level)
        if closed_beyond:
            beyond_closes += 1
            if beyond_closes >= 2:
                return {"outcome": "breakout", "detail": f"2 closes beyond at +{i + 1}d",
                        "swept_before": swept}
        else:
            if beyond_closes == 1 and closed_back:
                swept = True
            beyond_closes = 0
            if pierced and closed_back:
                swept = True
        if swept:
            away = (bar.close - level) if p0_above else (level - bar.close)
            if away >= 1.0 * atr:
                return {"outcome": "false_breakout", "detail": f"sweep+return >=1 ATR at +{i + 1}d"}
    closes = [bar.close for bar in window[:10]]
    if all(abs(c - level) <= 1.0 * atr for c in closes):
        return {"outcome": "consolidation", "detail": "10 closes within 1 ATR of level"}
    touched = any((bar.low <= level <= bar.high) or
                  abs(min(bar.low - level, level - bar.high, key=abs)) <= 0.3 * atr
                  for bar in window)
    if not touched:
        return {"outcome": "pullback", "detail": "price left the level without contact"}
    return {"outcome": "range_mixed", "detail": "no clean class"}


def main() -> int:
    configure_stdio()
    parser = argparse.ArgumentParser(description="Сводная калибровка сценариев по трём слоям.")
    parser.add_argument("--archive-root", type=Path, default=None)
    args = parser.parse_args()

    root = default_root()
    out_root = args.archive_root or root / "_scenario_archive"
    index = read_jsonl(out_root / "scenario_index.jsonl")
    links = read_jsonl(out_root / "market_link.jsonl")
    native = read_jsonl(out_root / "chart_native.jsonl")
    ohlc_dir = out_root / "ohlc"

    bars_cache: dict[str, list[Bar]] = {}
    rows: list[dict[str, Any]] = []
    for sid in sorted(index):
        link = links.get(sid, {})
        nat = native.get(sid, {})
        record: dict[str, Any] = {
            "scenario_id": sid,
            "date": link.get("date"),
            "symbol": link.get("symbol"),
            "branches": link.get("branches") or index[sid].get("parsed", {}).get("branches", []),
            "branch_intents": link.get("branch_intents") or nat.get("branch_intents", []),
            "pixel": {
                "status": nat.get("status"),
                "bar_source": nat.get("bar_source"),
                "levels_confirmed": sum(1 for m in nat.get("level_match", []) if m.get("confirmed")),
                "levels_drawn": nat.get("n_drawn_levels"),
                "candidates": nat.get("candidates", []),
            },
            "market_candidates": link.get("market", {}).get("candidates", []),
            "forward": link.get("forward"),
            "calib_version": CALIB_VERSION,
        }
        if link.get("status") == "linked":
            level = pick_reference_level(link)
            if level is not None:
                symbol = link["symbol"]
                date = datetime.strptime(link["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                bars = bars_cache.get(symbol)
                if bars is None:
                    try:
                        bars = load_symbol_bars(ohlc_dir, symbol,
                                                date - timedelta(days=460),
                                                date + timedelta(days=40))
                    except Exception:
                        bars = []
                    bars_cache[symbol] = bars
                target_ms = int(date.timestamp() * 1000)
                idx_bar = next((i for i, b in enumerate(bars) if b.open_time == target_ms), None)
                atr = link.get("market", {}).get("setup_atr")
                if idx_bar is not None and atr:
                    record["reference_level"] = level
                    record["realized"] = classify_outcome(bars, idx_bar, level, atr)
            else:
                record["realized"] = {"outcome": "no_reference_level"}
        rows.append(record)

    with (out_root / "calibration.jsonl").open("w", encoding="utf-8") as handle:
        for record in rows:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    # --- агрегаты ---
    from collections import Counter
    classified = [r for r in rows if r.get("realized", {}).get("outcome") in (
        "breakout", "false_breakout", "consolidation", "pullback", "range_mixed")]
    outcome_counts = Counter(r["realized"]["outcome"] for r in classified)

    evaluable = []
    for r in classified:
        expected = {INTENT_TO_OUTCOME[i] for i in r["branch_intents"] if i in INTENT_TO_OUTCOME}
        if expected and r["realized"]["outcome"] != "range_mixed":
            evaluable.append((r, expected))
    hits = [r for r, expected in evaluable if r["realized"]["outcome"] in expected]
    n_branch_stats = Counter(len({INTENT_TO_OUTCOME[i] for i in r["branch_intents"]
                                  if i in INTENT_TO_OUTCOME}) for r, _ in evaluable)

    # детектор: sweep-сигнатура на баре сценария -> реализовался ли ЛП
    det_fb = [r for r in classified
              if any(c.get("sweep_mode") for c in r["market_candidates"])]
    det_fb_hit = [r for r in det_fb if r["realized"]["outcome"] == "false_breakout"]

    lines = [
        "# Калибровка сценариев: прогноз vs механика vs исход",
        "",
        f"Собрано: {datetime.now(timezone.utc).isoformat()}",
        f"Версия: {CALIB_VERSION} (горизонт {HORIZON} дневных баров)",
        "",
        "## Исходы у опорного уровня",
        "",
        f"Классифицировано: {len(classified)} сценариев",
        "",
    ]
    lines += [f"- {outcome}: {count} ({100 * count / len(classified):.0f}%)"
              for outcome, count in outcome_counts.most_common()]
    lines += [
        "",
        "## Попадание веток сценария",
        "",
        f"- Оцениваемых сценариев (есть распознанные ветки и чистый исход): {len(evaluable)}",
        f"- Исход попал в одну из веток: {len(hits)} ({100 * len(hits) / max(len(evaluable), 1):.0f}%)",
        f"- Распределение числа различных веток-исходов: {dict(sorted(n_branch_stats.items()))}",
        "",
        "Замечание: ветки сценария — перечень альтернатив, а не один прогноз;",
        "попадание = реализовалась хотя бы одна из перечисленных альтернатив.",
        "",
        "## Детектор как предсказатель",
        "",
        f"- Сценариев с sweep-сигнатурой у детектора в день сценария: {len(det_fb)}",
        f"- Из них реализовался ложный пробой: {len(det_fb_hit)} "
        f"({100 * len(det_fb_hit) / max(len(det_fb), 1):.0f}%)",
        f"- Базовая частота ложного пробоя по всем классифицированным: "
        f"{100 * outcome_counts.get('false_breakout', 0) / len(classified):.0f}%",
        "",
        "## Таблица (классифицированные сценарии)",
        "",
        "| # | Дата | Символ | Ветки | Исход | Попал | ret20d |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in classified:
        expected = {INTENT_TO_OUTCOME[i] for i in r["branch_intents"] if i in INTENT_TO_OUTCOME}
        hit = ("да" if r["realized"]["outcome"] in expected else "нет") if expected else "—"
        fwd = r.get("forward") or {}
        lines.append(
            f"| {r['scenario_id']} | {r['date']} | {r['symbol']} "
            f"| {'; '.join(r['branch_intents']) or '—'} | {r['realized']['outcome']} "
            f"| {hit} | {fwd.get('ret_20d_pct', '—')}% |"
        )
    (out_root / "calibration_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"classified: {len(classified)}, evaluable: {len(evaluable)}, hits: {len(hits)}")
    print("outcomes:", dict(outcome_counts.most_common()))
    print(f"report: {out_root / 'calibration_report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
