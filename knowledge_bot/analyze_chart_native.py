"""Пиксельный анализ сценарных скриншотов — без привязки к историческим данным.

Из каждого D1-скриншота извлекаются:
  - псевдо-OHLC: тело свечи -> open/close, фитили -> high/low (в пиксельных
    координатах; вся детекторная механика работает на отношениях к ATR и
    потому не требует абсолютных цен);
  - нарисованные вручную уровни (синие горизонтальные линии);
  - стрелка, указывающая бар сценария.

Дальше механика scn002_spec_driven_backtest запускается прямо на пиксельных
барах: подтверждённые уровни, кандидаты детекторов на баре со стрелкой,
сравнение нарисованных уровней с механическими. Работает для всех сценариев,
включая те, где тикер/дата не распознаны.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from link_scenarios_to_market import classify_branch_intents, compact_signal
from match_scenario_charts import load_index, read_image
from scn002_strict_kb_backtest import Bar, atr_at
import scn002_spec_driven_backtest as spec

NATIVE_VERSION = "chart_native_v1"
DAY_MS = 86_400_000
LEVEL_MATCH_ATR = 0.30  # нарисованный уровень «подтверждён», если механический ближе


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def default_root() -> Path:
    return Path(__file__).resolve().parents[1]


def extract_chart(img: np.ndarray) -> dict[str, Any]:
    """Свечи (тело+фитиль), синие уровни и стрелка из скриншота."""
    import cv2
    b = img[:, :, 0].astype(np.int16)
    g = img[:, :, 1].astype(np.int16)
    r = img[:, :, 2].astype(np.int16)
    height, width = b.shape
    red_mask = (r > 180) & (g < 120) & (b < 120)
    green_mask = (g > 110) & (r < 90) & (b > 60) & (b < 180)
    mask = red_mask | green_mask

    candles: list[dict[str, Any]] = []
    col_active = mask.sum(axis=0) >= 2
    x = 0
    while x < width:
        if not col_active[x]:
            x += 1
            continue
        x0 = x
        while x < width and col_active[x]:
            x += 1
        cols = list(range(x0, x))
        col_data: list[tuple[int, int, int]] = []  # (cx, ytop, ybot)
        red_px = green_px = 0
        for cx in cols:
            ys = np.where(mask[:, cx])[0]
            if ys.size == 0:
                continue
            col_data.append((cx, int(ys.min()), int(ys.max())))
            red_px += int(red_mask[:, cx].sum())
            green_px += int(green_mask[:, cx].sum())
        if red_px + green_px < 8 or not col_data:
            continue
        full_top = min(t for _, t, _ in col_data)
        full_bot = max(b_ for _, _, b_ in col_data)
        color = "green" if green_px > red_px else "red"
        # Бар в стиле HLOC: вертикальная линия + тик слева (open) и справа (close).
        # Центральная линия = столбцы с почти полным диапазоном; тики — короткие
        # столбцы слева/справа от неё.
        full_h = max(full_bot - full_top, 1)
        line_cols = [cd for cd in col_data if (cd[2] - cd[1]) >= 0.75 * full_h]
        line_x = (line_cols[len(line_cols) // 2][0] if line_cols
                  else col_data[len(col_data) // 2][0])
        left = [cd for cd in col_data if cd[0] < line_x and (cd[2] - cd[1]) < 0.75 * full_h]
        right = [cd for cd in col_data if cd[0] > line_x and (cd[2] - cd[1]) < 0.75 * full_h]
        # пиксельная ось y растёт вниз -> цена = -y
        high, low = -float(full_top), -float(full_bot)
        open_y = float(np.median([(t + b_) / 2 for _, t, b_ in left])) if left else None
        close_y = float(np.median([(t + b_) / 2 for _, t, b_ in right])) if right else None
        if open_y is not None and close_y is not None:
            open_, close = -open_y, -close_y
            # цвет надёжнее пиксельных тиков: направление должно совпадать
            if (close >= open_) != (color == "green"):
                open_, close = close, open_
        else:
            # тики не видны (тонкий бар): open/close по цвету из крайних цен
            open_, close = (low, high) if color == "green" else (high, low)
        candles.append({
            "x_center": (x0 + x - 1) / 2,
            "color": color,
            "open": open_, "high": high, "low": low, "close": close,
        })
    if candles:
        widths_px = sorted(c2["x_center"] - c1["x_center"]
                           for c1, c2 in zip(candles, candles[1:]))
        # аномально широкие прогоны уже слиты в один — отфильтруем выбросы по высоте
        heights = sorted(c["high"] - c["low"] for c in candles)
        max_height = heights[len(heights) // 2] * 12 + 1e-9
        candles = [c for c in candles if c["high"] - c["low"] <= max_height]

    # «доминирующий синий»: линии уровней бывают и яркими (208,96,48), и тёмными (160,96,64)
    blue = (b > 130) & ((b - np.maximum(g, r)) > 40)
    row_counts = blue.sum(axis=1)
    drawn_levels: list[float] = []
    run: list[int] = []
    for y in range(height):
        if row_counts[y] > 0.3 * width:
            run.append(y)
        elif run:
            drawn_levels.append(-float(np.mean(run)))
            run = []
    if run:
        drawn_levels.append(-float(np.mean(run)))

    dark = ((b < 100) & (g < 100) & (r < 100)).astype(np.uint8)
    n_comp, _, stats, centroids = cv2.connectedComponentsWithStats(dark, 8)
    arrows = [
        {"x": float(centroids[i][0]), "y": float(centroids[i][1]),
         "area": int(stats[i, 4]), "h": int(stats[i, 3])}
        for i in range(1, n_comp)
        if stats[i, 4] > 80 and stats[i, 3] > 30 and stats[i, 2] < 90
    ]
    arrows.sort(key=lambda a: a["area"], reverse=True)
    return {"candles": candles, "drawn_levels": drawn_levels, "arrows": arrows}


def to_bars(candles: list[dict[str, Any]]) -> list[Bar]:
    return [
        Bar(i * DAY_MS, c["open"], c["high"], c["low"], c["close"], 0.0)
        for i, c in enumerate(candles)
    ]


def nearest_candle_index(candles: list[dict[str, Any]], x: float) -> int:
    return min(range(len(candles)), key=lambda i: abs(candles[i]["x_center"] - x))


def analyze_scenario(extract: dict[str, Any]) -> dict[str, Any]:
    import dataclasses
    candles = extract["candles"]
    bars = to_bars(candles)
    # окно скриншота короткое (~25-50 баров), полный ATR-период съел бы всю историю
    p = dataclasses.replace(spec.SpecParams(), atr_period=7)
    start = p.atr_period + p.pivot_k + 2
    if len(bars) <= start + 2:
        return {"status": "not_enough_candles", "n_candles": len(bars)}

    scenario_i = None
    bar_source = "none"
    if extract["arrows"]:
        scenario_i = nearest_candle_index(candles, extract["arrows"][0]["x"])
        bar_source = "arrow"
    if scenario_i is None or scenario_i < start:
        # запасной вариант: последний бар, касающийся нарисованного уровня
        for i in range(len(bars) - 1, start - 1, -1):
            if any(bars[i].low <= lv <= bars[i].high for lv in extract["drawn_levels"]):
                scenario_i = i
                bar_source = "last_level_touch"
                break
    if scenario_i is None or scenario_i < start:
        scenario_i = len(bars) - 1
        bar_source = "last_bar"

    # кандидатов ищем на баре стрелки и в допуске +-2 бара (дрожание разметки)
    last_i = min(len(bars) - 1, scenario_i + 2)
    window_js = [j for j in range(max(start, scenario_i - 2), last_i + 1)]
    resistances: list[spec.PivotLevel] = []
    supports: list[spec.PivotLevel] = []
    setup_atr = None
    scenario_atr = None
    candidates_by_bar: dict[int, list[dict[str, Any]]] = {}
    for i in range(start, last_i + 1):
        atr = atr_at(bars, i, p.atr_period)
        if atr is None or atr <= 0:
            continue
        setup_atr = atr
        spec.update_confirmed_levels(bars, i, atr, resistances, supports, p)
        if i == scenario_i:
            scenario_atr = atr
        if i in window_js:
            bar = bars[i]
            found = []
            res_touch = sorted([lv for lv in resistances if bar.high >= lv.price],
                               key=lambda lv: abs(bar.close - lv.price))[:3]
            sup_touch = sorted([lv for lv in supports if bar.low <= lv.price],
                               key=lambda lv: abs(bar.close - lv.price))[:3]
            for side, levels, opposite in (("short", res_touch, supports), ("long", sup_touch, resistances)):
                for level in levels:
                    signal = spec.build_signal("PIXELS", bars, i, level, opposite,
                                               side, atr, atr, "1d", p)
                    if signal is not None:
                        found.append(compact_signal(signal))
            if found:
                candidates_by_bar[i] = found
    if setup_atr is None:
        return {"status": "no_atr", "n_candles": len(bars)}
    scenario_atr = scenario_atr or setup_atr

    mech_prices = [lv.price for lv in resistances + supports]
    level_match = []
    for drawn in extract["drawn_levels"]:
        if mech_prices:
            dist = min(abs(drawn - mp) for mp in mech_prices)
            level_match.append({
                "dist_atr": round(dist / scenario_atr, 3),
                "confirmed": dist / scenario_atr <= LEVEL_MATCH_ATR,
            })
        else:
            level_match.append({"dist_atr": None, "confirmed": False})

    best_j = min(candidates_by_bar, key=lambda j: abs(j - scenario_i)) if candidates_by_bar else None
    return {
        "status": "analyzed",
        "n_candles": len(bars),
        "scenario_bar": scenario_i,
        "bar_source": bar_source,
        "candidate_bar": best_j,
        "n_drawn_levels": len(extract["drawn_levels"]),
        "level_match": level_match,
        "mech_levels": {"resistances": len(resistances), "supports": len(supports)},
        "candidates": candidates_by_bar.get(best_j, []),
    }


def write_report(out_root: Path, rows: list[dict[str, Any]]) -> None:
    from collections import Counter
    statuses = Counter(r["status"] for r in rows)
    analyzed = [r for r in rows if r["status"] == "analyzed"]
    arrow_found = sum(1 for r in analyzed if r["bar_source"] == "arrow")
    drawn_total = sum(r["n_drawn_levels"] for r in analyzed)
    confirmed = sum(1 for r in analyzed for m in r["level_match"] if m["confirmed"])
    with_cand = [r for r in analyzed if r["candidates"]]
    fb = [r for r in analyzed if "false_breakout" in r.get("branch_intents", [])]
    fb_cand = [r for r in fb if r["candidates"]]
    fb_agree = sum(1 for r in fb_cand if any(
        c.get("sweep_mode") or c.get("retest_bias") == "false_breakout" for c in r["candidates"]))
    lines = [
        "# Пиксельный анализ сценариев (без исторических данных)",
        "",
        f"Собрано: {datetime.now(timezone.utc).isoformat()}",
        f"Версия: {NATIVE_VERSION}",
        "",
        "## Статусы",
        "",
    ]
    lines += [f"- {status}: {count}" for status, count in statuses.most_common()]
    lines += [
        "",
        "## Сводка",
        "",
        f"- Проанализировано: {len(analyzed)} из {len(rows)}",
        f"- Бар сценария найден по стрелке: {arrow_found}",
        f"- Нарисованных уровней: {drawn_total}, подтверждено механикой (<= {LEVEL_MATCH_ATR} ATR): "
        f"{confirmed} ({100 * confirmed / max(drawn_total, 1):.0f}%)",
        f"- Сценариев с кандидатами детекторов на баре сценария: {len(with_cand)}",
        f"- «Ложный пробой» и детектор видит sweep/ЛП-bias: {fb_agree} из {len(fb_cand)} (у кого есть кандидаты)",
        "",
        "Замечание: статусы кандидатов почти всегда reject из-за D1-артефакта",
        "technical_stop_gt_13pct_atr — смысл несут фичи, а не статус.",
    ]
    (out_root / "chart_native_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    configure_stdio()
    parser = argparse.ArgumentParser(description="Пиксельный анализ D1-скриншотов сценариев.")
    parser.add_argument("--archive-root", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    root = default_root()
    out_root = args.archive_root or root / "_scenario_archive"
    index = load_index(out_root)
    out_path = out_root / "chart_native.jsonl"
    rows: list[dict[str, Any]] = []
    with out_path.open("w", encoding="utf-8") as handle:
        for sid in sorted(index):
            record = index[sid]
            result: dict[str, Any] = {
                "scenario_id": sid,
                "native_version": NATIVE_VERSION,
                "branch_intents": classify_branch_intents(record.get("parsed", {}).get("branches", [])),
            }
            if not record.get("d1_image"):
                result["status"] = "no_image"
            else:
                img = read_image(out_root / record["d1_image"])
                if img is None:
                    result["status"] = "unreadable_image"
                else:
                    result.update(analyze_scenario(extract_chart(img)))
            rows.append(result)
            handle.write(json.dumps(result, ensure_ascii=False) + "\n")
            if len(rows) % 50 == 0:
                print(f"[{len(rows)}/{len(index)}]")
            if args.limit and len(rows) >= args.limit:
                break
    write_report(out_root, rows)
    from collections import Counter
    print("statuses:", dict(Counter(r["status"] for r in rows).most_common()))
    print(f"out: {out_path}")
    print(f"report: {out_root / 'chart_native_report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
