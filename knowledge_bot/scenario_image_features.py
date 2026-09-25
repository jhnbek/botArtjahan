"""Conservative, read-only OHLC extraction from the Dzhahan screenshot corpus.

No caption, ticker, scenario ID, outcome or post-anchor OHLC becomes a feature.
The arrows and existing blue lines are used only as user annotations locating
the decision bar and supplied level. This is not a level-discovery model.
Pixel prices assume a linear vertical scale; they are never exchange prices.
Ambiguous images abstain instead of inventing a decision timestamp.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np

FEATURE_SCHEMA_VERSION = "scenario_ohlc_level_v1"
FEATURE_NAMES = tuple(
    f"bar_{age}_{key}_from_level_tr"
    for age in range(8) for key in ("open", "high", "low", "close")
) + (
    "signal_body_tr", "signal_range_tr", "signal_upper_wick_tr", "signal_lower_wick_tr",
    "prior_close_change_3_tr", "prior_close_change_8_tr", "prior_mean_body_5_tr",
    "prior_mean_range_5_tr", "prior_range_contraction", "prior_wick_touch_fraction",
    "prior_range_cross_fraction", "prior_body_cross_fraction", "prior_consecutive_range_crosses",
    "prior_close_above_fraction",
)


def features_from_ohlc(
    bars: Sequence[Mapping[str, float]], level: float, *, lookback: int = 8,
    atr_period: int = 14,
) -> dict[str, float]:
    """Same causal feature schema for pixels and live CLOSED OHLC bars.

    ``bars[-1]`` is the decision bar. Callers must remove all future bars.
    Scale is a simple mean TR, not Wilder ATR, computed BEFORE that bar.
    At least eight previous complete bars are required. The scale uses up to
    fourteen previous TR values and requires their previous close as well.
    """
    if len(bars) < max(lookback, 10):
        raise ValueError("At least ten complete closed bars are required")
    arr = np.asarray([[float(b[k]) for k in ("open", "high", "low", "close")]
                      for b in bars], dtype=float)
    if not np.isfinite(arr).all() or not np.isfinite(level):
        raise ValueError("Non-finite OHLC or level")
    o, h, l, c = arr.T
    if np.any(h < np.maximum(o, c)) or np.any(l > np.minimum(o, c)):
        raise ValueError("Invalid OHLC ordering")
    trs = np.maximum(h[1:] - l[1:], np.maximum(abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])))
    prior_tr = trs[:-1][-atr_period:]
    scale = float(np.mean(prior_tr))
    if scale <= 0:
        raise ValueError("Zero previous true range")
    out: dict[str, float] = {}
    for age in range(lookback):
        for col, key in enumerate(("open", "high", "low", "close")):
            out[f"bar_{age}_{key}_from_level_tr"] = float((arr[-1-age, col]-level)/scale)
    ranges = h-l
    body = abs(c-o)
    out.update(
        signal_body_tr=float(body[-1]/scale),
        signal_range_tr=float(ranges[-1]/scale),
        signal_upper_wick_tr=float((h[-1]-max(o[-1], c[-1]))/scale),
        signal_lower_wick_tr=float((min(o[-1], c[-1])-l[-1])/scale),
        prior_close_change_3_tr=float((c[-2]-c[-5])/scale),
        prior_close_change_8_tr=float((c[-2]-c[-9])/scale),
        prior_mean_body_5_tr=float(np.mean(body[-6:-1])/scale),
        prior_mean_range_5_tr=float(np.mean(ranges[-6:-1])/scale),
        prior_range_contraction=float(np.mean(ranges[-4:-1])/scale),
    )
    ph, pl, po, pc = h[-9:-1], l[-9:-1], o[-9:-1], c[-9:-1]
    cross = (ph > level) & (pl < level)
    body_cross = (po-level)*(pc-level) < 0
    # Touches and cuts are observations, not automatic confirmation labels.
    out["prior_wick_touch_fraction"] = float(np.mean(np.minimum(abs(ph-level), abs(pl-level)) <= .08*scale))
    out["prior_range_cross_fraction"] = float(np.mean(cross))
    out["prior_body_cross_fraction"] = float(np.mean(body_cross))
    run = best = 0
    for cut in cross:
        run = run+1 if cut else 0
        best = max(best, run)
    out["prior_consecutive_range_crosses"] = float(best)
    out["prior_close_above_fraction"] = float(np.mean(pc > level))
    return out


def _bands(values: np.ndarray, gap: int = 2) -> list[np.ndarray]:
    return list(np.split(values, np.where(np.diff(values) > gap)[0]+1)) if len(values) else []


def _masks(rgb: np.ndarray) -> dict[str, np.ndarray]:
    r, g, b = rgb.astype(np.int16).transpose(2, 0, 1)
    return {
        "red": (r > 160) & (g < 120) & (r > g+60) & (r > b+35),
        "green": (g > 85) & (r < 110) & (g > r+45) & (b > r+25) & (g > b+8),
        "blue": (b > 110) & (b > r+40) & (b > g+20) & (g < 175),
        "black": (r < 110) & (g < 110) & (b < 110),
    }


def _arrow_candidates(black: np.ndarray) -> list[dict]:
    from scipy import ndimage

    labels, _ = ndimage.label(black, structure=np.ones((3, 3)))
    result = []
    for label_id, box in enumerate(ndimage.find_objects(labels), 1):
        if box is None:
            continue
        yy, xx = box
        height, width = yy.stop-yy.start, xx.stop-xx.start
        # Covers thin and thick hollow vertical arrows; excludes text glyphs.
        if height < 29 or width < 8 or height < .65*width or width > 150:
            continue
        component = labels[box] == label_id
        ys, xs = np.where(component)
        top, bottom = xs[ys < 3], xs[ys >= height-3]
        if not len(top) or not len(bottom):
            continue
        top_width, bottom_width = np.ptp(top)+1, np.ptp(bottom)+1
        tip_width = min(top_width, bottom_width)
        if tip_width > width*.3 or max(top_width, bottom_width) < width*.35:
            continue
        direction = "down" if bottom_width < top_width else "up"
        tip_xs = bottom if direction == "down" else top
        tip_x = float(np.median(tip_xs)+xx.start)
        if abs(tip_x-(xx.start+(width-1)/2)) > max(3, width*.12):
            continue
        result.append({"tip_x": tip_x,
                       "tip_y": int(yy.stop-1 if direction == "down" else yy.start),
                       "orientation": direction,
                       "bbox": [xx.start, yy.start, width, height]})
    return result


def _bar_peaks(masks: dict[str, np.ndarray]) -> tuple[np.ndarray, float]:
    from scipy import signal

    projection = (masks["red"] | masks["green"]).sum(axis=0)
    peaks, _ = signal.find_peaks(projection, prominence=4, distance=5)
    if len(peaks) < 10:
        raise ValueError("too_few_colored_stems")
    diffs = np.diff(peaks)
    pitch = float(np.median(diffs))
    if pitch < 7:
        raise ValueError("bar_spacing_too_small")
    # White dashed cursors can split a single thick stem into two peaks.
    keep = []
    for group in _bands(peaks, gap=max(5, int(pitch*.55))):
        scores = projection[group]
        top = group[scores >= max(scores)*.95]
        keep.append(int(round(float(np.mean(top)))))
    peaks = np.asarray(keep)
    pitch = float(np.median(np.diff(peaks)))
    return peaks, pitch


def _decode_bar(masks: dict[str, np.ndarray], x: int, pitch: float) -> dict:
    height, width = masks["red"].shape
    color = max(("red", "green"), key=lambda name: int(masks[name][:, x].sum()))
    mask = masks[color]
    yy = np.flatnonzero(mask[:, x])
    if len(yy) < 4:
        raise ValueError("stem_too_short")
    high_y, low_y = int(yy[0]), int(yy[-1])
    if high_y <= 1 or low_y >= height-2:
        raise ValueError("bar_clipped_by_frame")
    if len(yy)/(low_y-high_y+1) < .65:
        raise ValueError("disconnected_colored_stem")
    ticks = []
    offset = max(3, int(round(pitch*.29)))
    for side in (-1, 1):
        sx = x+side*offset
        if sx < 0 or sx+1 >= width:
            raise ValueError("tick_clipped_by_frame")
        ys = np.flatnonzero(mask[:, sx:sx+2].any(axis=1))
        ys = ys[(ys >= high_y-2) & (ys <= low_y+2)]
        bands = _bands(ys)
        if len(bands) != 1:
            raise ValueError("ambiguous_open_close_tick")
        ticks.append(float((bands[0][0]+bands[0][-1])/2))
    oy, cy = ticks
    # A half-pixel stroke error can put a tick at an extremum just outside stem.
    high_y, low_y = min(high_y, oy, cy), max(low_y, oy, cy)
    if (color == "red" and oy > cy+3) or (color == "green" and cy > oy+3):
        raise ValueError("tick_color_order_mismatch")
    return {"x": int(x), "open": -oy, "high": -float(high_y),
            "low": -float(low_y), "close": -cy, "color": color}


def extract_image_features(
    image_path: str | Path, *, timeframe: str | None = None,
    anchor_override: Mapping | None = None,
    signal_x: float | None = None, signal_x_verified: bool = False,
    include_signal_bar: bool = False,
) -> dict:
    """Extract one image, or return explicit abstention diagnostics.

    An externally supplied x is used only when independently visually verified.
    ``usable`` is technical extraction quality, not confirmation of entry timing,
    hindsight-free level provenance, linear scale or target-label correctness.
    """
    path = Path(image_path)
    from PIL import Image

    tf = (timeframe or ("1h" if path.name.startswith("1H_") else "1d")).lower()
    if tf not in ("1h", "1d"):
        raise ValueError("Supported screenshot timeframes: 1d and 1h")
    if anchor_override is not None:
        signal_x = float(anchor_override["x"])
        signal_x_verified = bool(anchor_override.get("visually_verified", False))
    if include_signal_bar and not signal_x_verified:
        raise ValueError("Including H1 signal bar requires independently verified after-close anchor")
    data = path.read_bytes()
    rgb = np.asarray(Image.open(path).convert("RGB"))
    masks = _masks(rgb)
    result = {
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "image": path.name, "sha256": hashlib.sha256(data).hexdigest(),
        "width": int(rgb.shape[1]), "height": int(rgb.shape[0]),
        "usable": False, "features": None, "reason": None,
        "timeframe": tf, "status": "excluded", "bars": [], "level": None,
        "source_sha256": hashlib.sha256(data).hexdigest(),
        "confidence": "unverified_extraction", "cutoff_x": None,
        "scale": "pixel_mean_prior_true_range_linear_assumption",
        "model_inputs_exclude": ["caption", "ticker", "direction_label", "outcome", "future_bars", "arrow_shape"],
        "limitations": ["linear_price_scale_assumed", "user_level_not_independently_reconstructed",
                        "annotated_bar_close_not_verified_entry_time", "pixel_ohlc_approximate"],
    }
    try:
        peaks, pitch = _bar_peaks(masks)
        arrows = _arrow_candidates(masks["black"])
        result["bar_pitch_px"] = pitch
        result["arrow_candidates"] = arrows
        if signal_x is not None:
            if not signal_x_verified:
                raise ValueError("unverified_external_anchor")
            anchor = float(signal_x)
            result["anchor_method"] = "independently_visually_verified_x"
        else:
            near = []
            for arrow in arrows:
                idx = int(np.argmin(abs(peaks-arrow["tip_x"])))
                x = int(peaks[idx])
                distance = abs(x-arrow["tip_x"])
                if distance > pitch*.35:
                    continue
                stem_mask = masks["red"][:, x] | masks["green"][:, x]
                ys = np.flatnonzero(stem_mask)
                if not len(ys):
                    continue
                edge = int(ys[0] if arrow["orientation"] == "down" else ys[-1])
                if abs(arrow["tip_y"]-edge) <= max(18, pitch*.8):
                    near.append((x, arrow))
            result["plausible_arrow_anchors"] = [x for x, _ in near]
            if len(near) != 1:
                raise ValueError("ambiguous_or_missing_signal_arrow")
            anchor = float(near[0][0])
            result["anchor_method"] = "single_unambiguous_vertical_arrow"
        idx = int(np.argmin(abs(peaks-anchor)))
        signal_peak = int(peaks[idx])
        if abs(signal_peak-anchor) > pitch*.35:
            raise ValueError("anchor_between_bars")
        result["signal_x"] = signal_peak
        # H1 author entry can be intrabar: the arrow-target bar is NEVER decoded
        # into features. Daily setup is separately a closed-bar observation.
        cutoff_idx = idx-1 if tf == "1h" and not include_signal_bar else idx
        result["cutoff_x"] = int(peaks[cutoff_idx]) if cutoff_idx >= 0 else None
        result["arrow_target_excluded"] = tf == "1h" and not include_signal_bar
        result["decision_convention"] = (
            "after_independently_verified_hourly_bar_close" if tf == "1h" and include_signal_bar
            else "before_hourly_arrow_bar" if tf == "1h"
            else "after_daily_annotated_bar_close"
        )
        chosen = peaks[max(0, cutoff_idx-79):cutoff_idx+1]
        bars = []
        errors = []
        for x in reversed(chosen):
            try:
                bar = _decode_bar(masks, int(x), pitch)
                if bars and not .65*pitch <= bars[-1]["x"]-bar["x"] <= 1.4*pitch:
                    raise ValueError("missing_or_irregular_bar")
                bars.append(bar)
            except ValueError as exc:
                errors.append({"x": int(x), "reason": str(exc)})
                break  # Never silently bridge an unobserved bar.
        bars.reverse()
        result["decoded_bars"] = bars
        result["bars"] = bars
        result["decode_boundary_errors"] = errors
        result["n_prior_bars"] = max(0, len(bars)-1)
        if len(bars) < 16:
            raise ValueError("insufficient_complete_contiguous_prior_bars")
        # Read existing level annotations using columns at/before the anchor.
        stop = signal_peak+1
        blue_rows = np.flatnonzero(masks["blue"][:, :stop].sum(axis=1) >= max(25, stop*.5))
        levels = [-float(np.mean(band)) for band in _bands(blue_rows)]
        result["drawn_level_pixel_prices"] = levels
        if not levels:
            raise ValueError("no_blue_horizontal_level")
        level = min(levels, key=lambda price: abs(price-bars[-1]["close"]))
        result["selected_level_pixel_price"] = level
        result["level"] = level
        result["level_selection"] = "nearest_to_annotated_bar_close_without_direction_label"
        features = features_from_ohlc(bars, level)
        if abs(features["bar_0_close_from_level_tr"]) > 2:
            raise ValueError("annotated_bar_far_from_any_level")
        result["features"] = features
        result["usable"] = True
        result["status"] = "ok"
        result["confidence"] = "approximate_pixels_automatic_anchor_needs_review"
        result["reason"] = "technical_extraction_passed_requires_timing_and_label_review"
    except ValueError as exc:
        result["reason"] = str(exc)
    result["reasons"] = [result["reason"]]
    return result


def audit_collection(collection: str | Path) -> dict:
    root = Path(collection)
    records = []
    for tf in ("1D", "1H"):
        for i in range(1, 410):
            path = root / "images" / f"{tf}_{i}.jpg"
            if path.exists():
                row = extract_image_features(path)
                row.update(scenario_id=i, timeframe=tf)
                records.append(row)
    return {
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "source_images_modified": False,
        "all_images_processed": len(records),
        "feature_count": len(next((r["features"] for r in records if r["usable"]), {})),
        "usable_by_timeframe": dict(Counter(r["timeframe"] for r in records if r["usable"])),
        "reason_counts": dict(Counter(r["reason"] for r in records)),
        "visual_spot_checks": {
            "1D": [1, 22, 30, 54, 85, 109, 113, 128, 200, 211, 281, 362, 409],
            "1H": [1, 14, 30, 32, 46, 53, 54, 67, 68, 97, 104, 113, 136, 163, 362],
            "scope": "28 originals viewed; automatic decisions not all independently manually verified",
        },
        "semantic_anchor_audit": {
            "not_true_entry_when_auto_vertical_arrow_used": [14, 97, 104, 113, 136, 163],
            "why": "Vertical arrow marks daily close or contextual impulse; actual entry is later or horizontal arrow.",
            "visually_explicit_after_target_bar_close": [32, 46, 53, 67],
            "requirement": "Training must apply separate semantic timing labels; usable alone is NOT an entry label.",
        },
        "training_scope": "Only causal OHLC at the chosen annotated bar; no PnL or true entry-time guarantee",
        "records": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("collection", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    audit = audit_collection(args.collection)
    output = args.output or args.collection / "training" / "image_extraction_audit.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(audit, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in audit.items() if k != "records"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
