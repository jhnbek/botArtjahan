from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = ROOT / "_knowledge_base" / "structured" / "consolidation" / "layer7_case_charts"
VERSION = "layer7_case_chart_renderer_v1"


COLORS = {
    "bg": (248, 247, 242),
    "panel": (255, 255, 252),
    "grid": (222, 218, 207),
    "axis": (80, 80, 74),
    "text": (34, 34, 31),
    "muted": (102, 101, 94),
    "up": (31, 132, 90),
    "down": (191, 72, 59),
    "wick": (83, 82, 75),
    "level_pass": (27, 91, 161),
    "level_reject": (174, 69, 90),
    "level_other": (118, 104, 65),
    "entry": (210, 124, 35),
    "stop": (142, 42, 50),
    "target": (45, 132, 71),
    "time_marker": (92, 78, 145),
}


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def rel_path(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
    ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def load_case(path: Path) -> dict[str, Any]:
    case = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(case, dict):
        raise ValueError("case file must contain a JSON object")
    return case


def parse_dt(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def price_range(bars: list[dict[str, Any]], levels: list[dict[str, Any]],
                extra_prices: list[float] | None = None) -> tuple[float, float]:
    values: list[float] = []
    for bar in bars:
        values.append(float(bar["high"]))
        values.append(float(bar["low"]))
    for level in levels:
        try:
            values.append(float(level["price"]))
        except (KeyError, TypeError, ValueError):
            continue
    values.extend(extra_prices or [])
    if not values:
        return 0.0, 1.0
    low = min(values)
    high = max(values)
    if high <= low:
        return low - 1.0, high + 1.0
    pad = (high - low) * 0.08
    return low - pad, high + pad


def y_for_price(price: float, top: int, bottom: int, low: float, high: float) -> int:
    return int(bottom - ((price - low) / (high - low)) * (bottom - top))


def draw_grid(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], low: float, high: float,
              font: ImageFont.ImageFont) -> None:
    left, top, right, bottom = box
    draw.rectangle(box, fill=COLORS["panel"], outline=COLORS["grid"], width=1)
    for index in range(5):
        ratio = index / 4
        y = int(top + ratio * (bottom - top))
        price = high - ratio * (high - low)
        draw.line((left, y, right, y), fill=COLORS["grid"], width=1)
        draw.text((right + 8, y - 8), f"{price:,.2f}", fill=COLORS["muted"], font=font)


def level_color(level: dict[str, Any]) -> tuple[int, int, int]:
    status = str(level.get("kb_status") or "").lower()
    if status == "pass":
        return COLORS["level_pass"]
    if status == "reject":
        return COLORS["level_reject"]
    return COLORS["level_other"]


def draw_levels(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], low: float, high: float,
                levels: list[dict[str, Any]], font: ImageFont.ImageFont) -> None:
    left, top, right, bottom = box
    for level in levels:
        try:
            price = float(level["price"])
        except (KeyError, TypeError, ValueError):
            continue
        y = y_for_price(price, top, bottom, low, high)
        color = level_color(level)
        if str(level.get("kb_status") or "").lower() == "reject":
            step = 14
            x = left
            while x < right:
                draw.line((x, y, min(x + 8, right), y), fill=color, width=2)
                x += step
        else:
            draw.line((left, y, right, y), fill=color, width=2)
        label = f"{price:,.2f} {level.get('side', '')} {level.get('kb_status', '')}".strip()
        label_w = int(draw.textlength(label, font=font))
        draw.rectangle((left + 6, y - 13, left + 14 + label_w, y + 5), fill=COLORS["panel"])
        draw.text((left + 10, y - 12), label, fill=color, font=font)


def marker_color(kind: str) -> tuple[int, int, int]:
    return COLORS.get(kind, COLORS["time_marker"])


def draw_price_markers(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], low: float, high: float,
                       markers: list[dict[str, Any]], font: ImageFont.ImageFont) -> None:
    left, top, right, bottom = box
    for marker in markers:
        try:
            price = float(marker["price"])
        except (KeyError, TypeError, ValueError):
            continue
        y = y_for_price(price, top, bottom, low, high)
        color = marker_color(str(marker.get("kind") or ""))
        x = left
        while x < right:
            draw.line((x, y, min(x + 10, right), y), fill=color, width=2)
            x += 18
        label = f"{marker.get('label', '')} {price:,.2f}".strip()
        label_w = int(draw.textlength(label, font=font))
        draw.rectangle((right - label_w - 14, y - 13, right - 6, y + 5), fill=COLORS["panel"])
        draw.text((right - label_w - 10, y - 12), label, fill=color, font=font)


def bar_time(row: dict[str, Any]) -> str:
    return str(row.get("open_time") or row.get("time") or "")


def find_bar_index_by_time(bars: list[dict[str, Any]], value: str) -> int | None:
    marker_dt = parse_dt(value)
    if marker_dt is not None:
        bar_dts = [parse_dt(bar_time(row)) for row in bars]
        valid = [(index, dt) for index, dt in enumerate(bar_dts) if dt is not None]
        if not valid:
            return None
        if marker_dt < valid[0][1] or marker_dt > valid[-1][1]:
            return None
        return min(valid, key=lambda item: abs((item[1] - marker_dt).total_seconds()))[0]
    for index, row in enumerate(bars):
        if bar_time(row).startswith(value):
            return index
    return None


def draw_time_markers(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], bars: list[dict[str, Any]],
                      markers: list[dict[str, Any]], font: ImageFont.ImageFont) -> None:
    left, top, right, bottom = box
    count = max(len(bars), 1)
    slot = (right - left) / count
    for marker in markers:
        index = find_bar_index_by_time(bars, str(marker.get("time") or ""))
        if index is None:
            continue
        x = int(left + index * slot + slot / 2)
        color = marker_color(str(marker.get("kind") or ""))
        draw.line((x, top, x, bottom), fill=color, width=1)
        label = str(marker.get("label") or "")
        label_w = int(draw.textlength(label, font=font))
        draw.rectangle((x + 4, top + 6, x + label_w + 12, top + 24), fill=COLORS["panel"])
        draw.text((x + 8, top + 5), label, fill=color, font=font)


def collect_packet_annotations(case: dict[str, Any]) -> dict[str, Any]:
    annotations: dict[str, Any] = {"price_markers": [], "time_markers": [], "summary": {}, "error": None}
    try:
        from validate_layer7_real_chart_cases import build_packet

        packet = build_packet(case)
    except Exception as exc:
        annotations["error"] = f"{type(exc).__name__}: {exc}"
        return annotations

    entry = ((packet.get("layer_reports") or {}).get("entry") or {})
    best = entry.get("best_entry") or {}
    detector = best.get("entry_detector") or {}
    features = detector.get("bsu_bpu_features") or {}
    scenario = entry.get("scenario") or {}
    verdict = scenario.get("verdict") or {}
    annotations["summary"] = {
        "review_status": packet.get("review_status"),
        "scenario_family": scenario.get("family"),
        "scenario_direction": scenario.get("direction"),
        "scenario_status": verdict.get("status"),
        "best_entry_model": best.get("model"),
        "best_entry_status": best.get("status"),
    }
    for key, label, kind in [
        ("entry_price", "entry", "entry"),
        ("stop_price", "stop", "stop"),
        ("target_price", "target", "target"),
    ]:
        if best.get(key) is not None:
            annotations["price_markers"].append({"price": best[key], "label": label, "kind": kind})
    for key, label in [("bsu_time", "BSU"), ("bpu1_time", "BPU1"), ("bpu2_time", "BPU2")]:
        if features.get(key):
            annotations["time_markers"].append({"time": features[key], "label": label, "kind": "time_marker"})
    return annotations


def draw_candles(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], bars: list[dict[str, Any]],
                 levels: list[dict[str, Any]], title: str, font: ImageFont.ImageFont,
                 small_font: ImageFont.ImageFont, price_markers: list[dict[str, Any]] | None = None,
                 time_markers: list[dict[str, Any]] | None = None) -> None:
    left, top, right, bottom = box
    extra_prices: list[float] = []
    for marker in price_markers or []:
        try:
            extra_prices.append(float(marker["price"]))
        except (KeyError, TypeError, ValueError):
            continue
    low, high = price_range(bars, levels, extra_prices)
    draw_grid(draw, box, low, high, small_font)
    draw.text((left, top - 28), title, fill=COLORS["text"], font=font)
    count = max(len(bars), 1)
    slot = (right - left) / count
    body_width = max(2, min(18, int(slot * 0.62)))
    for index, bar in enumerate(bars):
        open_price = float(bar["open"])
        high_price = float(bar["high"])
        low_price = float(bar["low"])
        close_price = float(bar["close"])
        x = int(left + index * slot + slot / 2)
        y_high = y_for_price(high_price, top, bottom, low, high)
        y_low = y_for_price(low_price, top, bottom, low, high)
        y_open = y_for_price(open_price, top, bottom, low, high)
        y_close = y_for_price(close_price, top, bottom, low, high)
        color = COLORS["up"] if close_price >= open_price else COLORS["down"]
        draw.line((x, y_high, x, y_low), fill=COLORS["wick"], width=1)
        body_top = min(y_open, y_close)
        body_bottom = max(y_open, y_close)
        if body_bottom == body_top:
            body_bottom += 1
        draw.rectangle((x - body_width // 2, body_top, x + body_width // 2, body_bottom), fill=color, outline=color)
    draw_levels(draw, box, low, high, levels, small_font)
    draw_price_markers(draw, box, low, high, price_markers or [], small_font)
    draw_time_markers(draw, box, bars, time_markers or [], small_font)
    if bars:
        first_dt = parse_dt(str(bars[0].get("open_time") or bars[0].get("time") or ""))
        last_dt = parse_dt(str(bars[-1].get("open_time") or bars[-1].get("time") or ""))
        first = first_dt.strftime("%Y-%m-%d %H:%M") if first_dt else str(bars[0].get("open_time") or "")
        last = last_dt.strftime("%Y-%m-%d %H:%M") if last_dt else str(bars[-1].get("open_time") or "")
        draw.text((left, bottom + 8), first, fill=COLORS["muted"], font=small_font)
        label_w = int(draw.textlength(last, font=small_font))
        draw.text((right - label_w, bottom + 8), last, fill=COLORS["muted"], font=small_font)


def render(case: dict[str, Any], out_path: Path, execution_tail_bars: int,
           blind: bool = False) -> dict[str, Any]:
    width = 1600
    height = 1080
    margin_left = 80
    margin_right = 160
    image = Image.new("RGB", (width, height), COLORS["bg"])
    draw = ImageDraw.Draw(image)
    title_font = load_font(24, bold=True)
    panel_font = load_font(18, bold=True)
    small_font = load_font(13)

    case_id = str(case.get("case_id") or "")
    symbol = str(case.get("symbol") or "")
    timeframes = case.get("timeframes") or {}
    human_review = case.get("human_review") or {}
    header = f"{case_id} | {symbol} | {timeframes.get('context')} / {timeframes.get('execution')} | human_review={human_review.get('ohlc_reviewed') is True}"
    draw.text((margin_left, 24), header, fill=COLORS["text"], font=title_font)

    bars = case.get("bars") or {}
    context_bars = bars.get("context") or []
    execution_bars_all = bars.get("execution") or []
    execution_bars = execution_bars_all[-execution_tail_bars:] if execution_tail_bars > 0 else execution_bars_all
    levels = case.get("levels") if isinstance(case.get("levels"), list) else []
    # Blind mode hides the deterministic engine verdict (entry/stop/target and
    # BSU/BPU markers) so a human reviewer can label the chart without anchoring.
    annotations = {"price_markers": [], "time_markers": [], "summary": {}, "error": None} if blind else collect_packet_annotations(case)

    top_box = (margin_left, 110, width - margin_right, 500)
    bottom_box = (margin_left, 610, width - margin_right, 1000)
    draw_candles(
        draw,
        top_box,
        context_bars,
        levels,
        f"Context candles ({timeframes.get('context')}) - full case window",
        panel_font,
        small_font,
    )
    draw_candles(
        draw,
        bottom_box,
        execution_bars,
        levels,
        f"Execution candles ({timeframes.get('execution')}) - last {len(execution_bars)} of {len(execution_bars_all)} bars",
        panel_font,
        small_font,
        annotations.get("price_markers") or [],
        annotations.get("time_markers") or [],
    )

    legend_y = 62
    legend = [
        (COLORS["up"], "up candle"),
        (COLORS["down"], "down candle"),
        (COLORS["level_pass"], "pass level"),
        (COLORS["level_reject"], "reject level dashed"),
    ]
    if not blind:
        legend.extend([
            (COLORS["entry"], "entry"),
            (COLORS["stop"], "stop"),
            (COLORS["target"], "target"),
        ])
    x = margin_left
    for color, label in legend:
        draw.rectangle((x, legend_y, x + 18, legend_y + 12), fill=color)
        draw.text((x + 24, legend_y - 3), label, fill=COLORS["muted"], font=small_font)
        x += 170

    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path)
    return {
        "version": VERSION,
        "case_id": case_id,
        "symbol": symbol,
        "output_file": rel_path(out_path),
        "context_bar_count": len(context_bars),
        "execution_bar_count": len(execution_bars_all),
        "rendered_execution_bar_count": len(execution_bars),
        "level_count": len(levels),
        "annotation_price_marker_count": len(annotations.get("price_markers") or []),
        "annotation_time_marker_count": len(annotations.get("time_markers") or []),
        "annotation_error": annotations.get("error"),
        "packet_summary": annotations.get("summary") or {},
        "human_review_complete": all(human_review.get(key) is True for key in ["ohlc_reviewed", "levels_reviewed", "expectations_reviewed"]),
    }


def default_output(case_path: Path) -> Path:
    name = case_path.name
    if name.endswith(".template.json"):
        stem = name[:-len(".template.json")]
    elif name.endswith(".json"):
        stem = name[:-len(".json")]
    else:
        stem = case_path.stem
    return DEFAULT_OUT_DIR / f"{stem}.png"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render a Layer 7 case/draft OHLC chart with reviewed levels")
    parser.add_argument("case_file", help="Path to a Layer 7 .template.json or .json case")
    parser.add_argument("--out-file", help="PNG output path")
    parser.add_argument("--execution-tail-bars", type=int, default=384)
    parser.add_argument("--blind", action="store_true", help="Hide engine entry/stop/target annotations for blind review")
    return parser


def main() -> int:
    configure_stdio()
    args = build_parser().parse_args()
    case_path = Path(args.case_file)
    out_path = Path(args.out_file) if args.out_file else default_output(case_path)
    case = load_case(case_path)
    status = render(case, out_path, args.execution_tail_bars, blind=args.blind)
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())