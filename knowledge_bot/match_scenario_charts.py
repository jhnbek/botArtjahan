"""Определение тикера/даты сценария по «отпечатку» свечей на скриншоте.

Из D1-скриншота извлекается последовательность свечей (цвет + вертикальные
границы в пикселях) и сопоставляется с дневными свечами Binance из кэша
_scenario_archive/ohlc/. Последовательность из 40+ красно-зелёных баров
практически уникальна, поэтому совпадение восстанавливает символ и точную
календарную привязку окна графика.

Режимы:
  --validate N   проверить метод на N уже связанных сценариях (ответ известен)
  --recover      восстановить тикер/год для нераспознанных сценариев
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np

from link_scenarios_to_market import load_symbol_bars
from scn002_strict_kb_backtest import Bar

MATCH_VERSION = "chart_match_v1"

# Вселенная кандидатов: тикеры, встречающиеся в архиве (все на Binance spot)
UNIVERSE = [
    "BTC", "ETH", "ZEC", "XRP", "XLM", "SOL", "NEAR", "DOGE", "LINK", "ADA",
    "AAVE", "BNB", "ENA", "AVAX", "ONDO", "UNI", "WLD", "LTC", "SUI", "TAO", "WLFI",
]

MIN_CANDLES = 18
MIN_SIGN_SCORE = 0.97
MIN_MARGIN = 0.04  # отрыв лучшего кандидата от второго места


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def default_root() -> Path:
    return Path(__file__).resolve().parents[1]


def read_image(path: Path) -> np.ndarray | None:
    import cv2
    raw = np.fromfile(str(path), dtype=np.uint8)
    img = cv2.imdecode(raw, cv2.IMREAD_COLOR)
    return img


def extract_candles(img: np.ndarray) -> list[dict[str, Any]]:
    """Свечи как вертикальные красные/зелёные прогоны столбцов."""
    b = img[:, :, 0].astype(np.int16)
    g = img[:, :, 1].astype(np.int16)
    r = img[:, :, 2].astype(np.int16)
    red_mask = (r > 180) & (g < 120) & (b < 120)
    green_mask = (g > 110) & (r < 90) & (b > 60) & (b < 180)
    mask = red_mask | green_mask
    col_counts = mask.sum(axis=0)
    active = col_counts >= 2
    candles: list[dict[str, Any]] = []
    x = 0
    width = img.shape[1]
    while x < width:
        if not active[x]:
            x += 1
            continue
        x0 = x
        while x < width and active[x]:
            x += 1
        run = slice(x0, x)
        red_px = int(red_mask[:, run].sum())
        green_px = int(green_mask[:, run].sum())
        if red_px + green_px < 8:
            continue
        ys, _ = np.where(mask[:, run])
        candles.append({
            "x0": x0,
            "x1": x - 1,
            "color": "red" if red_px >= green_px else "green",
            "purity": max(red_px, green_px) / (red_px + green_px),
            "y_top": int(ys.min()),
            "y_bot": int(ys.max()),
        })
    if not candles:
        return []
    # отбрасываем аномально широкие прогоны (слипшиеся элементы разметки)
    widths = sorted(c["x1"] - c["x0"] + 1 for c in candles)
    median_width = widths[len(widths) // 2]
    return [c for c in candles if c["x1"] - c["x0"] + 1 <= max(3, median_width * 3)]


def candle_signs(candles: list[dict[str, Any]]) -> np.ndarray:
    return np.array([1 if c["color"] == "green" else -1 for c in candles], dtype=np.int8)


def bar_signs_and_wildcards(bars: list[Bar]) -> tuple[np.ndarray, np.ndarray]:
    signs = np.empty(len(bars), dtype=np.int8)
    wild = np.zeros(len(bars), dtype=bool)
    for i, bar in enumerate(bars):
        body = bar.close - bar.open
        rng = max(bar.high - bar.low, 1e-12)
        signs[i] = 1 if body >= 0 else -1
        if abs(body) < 0.08 * rng:
            wild[i] = True
    return signs, wild


def price_correlation(candles: list[dict[str, Any]], window: list[Bar]) -> float:
    """Корреляция середин пиксельных диапазонов с (high+low)/2 (ось y инвертирована)."""
    px = np.array([-(c["y_top"] + c["y_bot"]) / 2 for c in candles], dtype=np.float64)
    pr = np.array([(bar.high + bar.low) / 2 for bar in window], dtype=np.float64)
    if px.std() < 1e-9 or pr.std() < 1e-9:
        return 0.0
    return float(np.corrcoef(px, pr)[0, 1])


def match_against(candles: list[dict[str, Any]], bars: list[Bar]) -> dict[str, Any] | None:
    n = len(candles)
    if n < MIN_CANDLES or len(bars) < n:
        return None
    csigns = candle_signs(candles)
    bsigns, wild = bar_signs_and_wildcards(bars)
    best: dict[str, Any] | None = None
    second_score = 0.0
    for offset in range(0, len(bars) - n + 1):
        seg_signs = bsigns[offset:offset + n]
        seg_wild = wild[offset:offset + n]
        hits = (seg_signs == csigns) | seg_wild
        score = float(hits.mean())
        if best is None or score > best["sign_score"]:
            if best is not None:
                second_score = max(second_score, best["sign_score"])
            best = {"offset": offset, "sign_score": score}
        elif score > second_score:
            second_score = score
    if best is None:
        return None
    window = bars[best["offset"]:best["offset"] + n]
    best["corr"] = price_correlation(candles, window)
    best["margin"] = best["sign_score"] - second_score
    best["first_bar_date"] = window[0].dt.strftime("%Y-%m-%d")
    best["last_bar_date"] = window[-1].dt.strftime("%Y-%m-%d")
    best["n_candles"] = n
    return best


def accept(match: dict[str, Any] | None) -> bool:
    return (match is not None
            and match["sign_score"] >= MIN_SIGN_SCORE
            and match["corr"] >= 0.85
            and match["margin"] >= 0.0)


def load_index(out_root: Path) -> dict[int, dict[str, Any]]:
    records = {}
    with (out_root / "scenario_index.jsonl").open("r", encoding="utf-8") as handle:
        for raw in handle:
            if raw.strip():
                record = json.loads(raw)
                records[record["scenario_id"]] = record
    return records


def load_links(out_root: Path) -> dict[int, dict[str, Any]]:
    path = out_root / "market_link.jsonl"
    records = {}
    if path.exists():
        for raw in path.read_text(encoding="utf-8").splitlines():
            if raw.strip():
                record = json.loads(raw)
                records[record["scenario_id"]] = record
    return records


def get_bars(ohlc_dir: Path, symbol: str, start: datetime, end: datetime,
             cache: dict[str, list[Bar]]) -> list[Bar]:
    if symbol not in cache:
        try:
            cache[symbol] = load_symbol_bars(ohlc_dir, symbol, start, end)
        except Exception as exc:
            print(f"  [warn] {symbol}: {str(exc)[:120]}")
            cache[symbol] = []
    return cache[symbol]


def candles_for(out_root: Path, record: dict[str, Any]) -> list[dict[str, Any]]:
    if not record.get("d1_image"):
        return []
    img = read_image(out_root / record["d1_image"])
    if img is None:
        return []
    return extract_candles(img)


def run_validate(out_root: Path, ohlc_dir: Path, sample_size: int) -> int:
    links = load_links(out_root)
    index = load_index(out_root)
    linked = [r for r in links.values() if r["status"] == "linked"]
    linked.sort(key=lambda r: r["scenario_id"])
    step = max(1, len(linked) // sample_size)
    sample = linked[::step][:sample_size]
    fetch_start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    fetch_end = datetime.now(timezone.utc)
    cache: dict[str, list[Bar]] = {}
    ok = wrong_symbol = no_extract = rejected = 0
    for link in sample:
        sid = link["scenario_id"]
        candles = candles_for(out_root, index[sid])
        if len(candles) < MIN_CANDLES:
            no_extract += 1
            print(f"#{sid}: candles={len(candles)} — мало для матчинга")
            continue
        date = datetime.strptime(link["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        results = []
        for ticker in UNIVERSE:
            symbol = f"{ticker}USDT"
            bars = get_bars(ohlc_dir, symbol, fetch_start, fetch_end, cache)
            near = [b for b in bars if abs((b.dt - date).days) <= 200]
            match = match_against(candles, near)
            if match:
                results.append((symbol, match))
        results.sort(key=lambda x: (x[1]["sign_score"], x[1]["corr"]), reverse=True)
        if not results:
            rejected += 1
            continue
        top_symbol, top = results[0]
        expected = link["symbol"]
        in_window = top["first_bar_date"] <= link["date"] <= top["last_bar_date"]
        verdict = "OK" if (top_symbol == expected and accept(top) and in_window) else (
            "WRONG" if top_symbol != expected else "REJECTED")
        if verdict == "OK":
            ok += 1
        elif verdict == "WRONG":
            wrong_symbol += 1
        else:
            rejected += 1
        print(f"#{sid} {expected} {link['date']}: top={top_symbol} "
              f"sign={top['sign_score']:.3f} corr={top['corr']:.3f} margin={top['margin']:.3f} "
              f"window={top['first_bar_date']}..{top['last_bar_date']} n={top['n_candles']} -> {verdict}")
    total = ok + wrong_symbol + rejected + no_extract
    print(f"\nvalidation: OK={ok} WRONG={wrong_symbol} REJECTED={rejected} NO_EXTRACT={no_extract} of {total}")
    return 0


def run_recover(out_root: Path, ohlc_dir: Path) -> int:
    links = load_links(out_root)
    index = load_index(out_root)
    targets = [r for r in links.values() if r["status"] in (
        "skipped_no_ticker", "skipped_ambiguous_ticker", "no_symbol_data",
        "date_not_in_history", "skipped_partial_date", "skipped_no_date")
        # перепроверяем и связанные через OCR-алиас: алиас мог быть неверен
        or (r["status"] == "linked" and r.get("ticker_alias"))]
    targets.sort(key=lambda r: r["scenario_id"])
    fetch_start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    fetch_end = datetime.now(timezone.utc)
    cache: dict[str, list[Bar]] = {}
    out_path = out_root / "chart_match.jsonl"
    recovered = 0
    with out_path.open("w", encoding="utf-8") as handle:
        for link in targets:
            sid = link["scenario_id"]
            record = index[sid]
            candles = candles_for(out_root, record)
            result: dict[str, Any] = {
                "scenario_id": sid,
                "prior_status": link["status"],
                "n_candles": len(candles),
                "match_version": MATCH_VERSION,
            }
            if len(candles) < MIN_CANDLES:
                result["status"] = "not_enough_candles"
            else:
                results = []
                for ticker in UNIVERSE:
                    symbol = f"{ticker}USDT"
                    bars = get_bars(ohlc_dir, symbol, fetch_start, fetch_end, cache)
                    match = match_against(candles, bars)
                    if match:
                        results.append((symbol, match))
                results.sort(key=lambda x: (x[1]["sign_score"], x[1]["corr"]), reverse=True)
                if results and accept(results[0][1]):
                    symbol, top = results[0]
                    runner = results[1] if len(results) > 1 else None
                    cross_margin = top["sign_score"] - runner[1]["sign_score"] if runner else 1.0
                    if cross_margin >= MIN_MARGIN or (runner and runner[0] == symbol):
                        result["status"] = "matched"
                        result["symbol"] = symbol
                        result["window"] = [top["first_bar_date"], top["last_bar_date"]]
                        result["sign_score"] = round(top["sign_score"], 4)
                        result["corr"] = round(top["corr"], 4)
                        result["cross_margin"] = round(cross_margin, 4)
                        recovered += 1
                    else:
                        result["status"] = "ambiguous_between_symbols"
                        result["top2"] = [
                            {"symbol": s, "sign_score": round(m["sign_score"], 4)}
                            for s, m in results[:2]
                        ]
                else:
                    result["status"] = "no_confident_match"
            handle.write(json.dumps(result, ensure_ascii=False) + "\n")
            if sid % 10 == 0:
                print(f"#{sid}: {result['status']}")
    print(f"\nrecovered symbol for {recovered} of {len(targets)} scenarios")
    print(f"matches: {out_path}")
    return 0


def main() -> int:
    configure_stdio()
    parser = argparse.ArgumentParser(description="Матчинг скриншотов сценариев с историческими свечами.")
    parser.add_argument("--archive-root", type=Path, default=None)
    parser.add_argument("--validate", type=int, default=0, metavar="N")
    parser.add_argument("--recover", action="store_true")
    args = parser.parse_args()

    root = default_root()
    out_root = args.archive_root or root / "_scenario_archive"
    ohlc_dir = out_root / "ohlc"
    if args.validate:
        return run_validate(out_root, ohlc_dir, args.validate)
    if args.recover:
        return run_recover(out_root, ohlc_dir)
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
