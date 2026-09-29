"""Coordinate geometry for matching annotated screenshots to exchange bars.

These helpers establish a historical coordinate transform only. They do not
select a trading signal or expose the complete chart to a trained model.
"""
from __future__ import annotations

import numpy as np


def chart_color_masks(rgb: np.ndarray) -> dict[str, np.ndarray]:
    """Retain JPEG-compressed turquoise thin strokes when matching geometry.

    Tiny H1 stems sometimes have almost equal green/blue channels. Requiring
    green to exceed blue by eight loses their lower or upper wick. Dark red
    strokes also fall below the old red threshold. The dark-channel ceilings
    continue excluding the pastel volume bars and neutral caption background.
    This does not change the legacy approximate-OHLC training extractor.
    """
    from .scenario_image_features import _masks

    masks = _masks(rgb)
    r, g, b = rgb.astype(np.int16).transpose(2, 0, 1)
    masks['green'] = (g > 80) & (r < 110) & (g > r+30) & (b > r+20) & (g > b-8)
    masks['red'] = (r > 140) & (g < 120) & (r > g+40) & (r > b+25)
    return masks


def chart_stem_peaks(masks: dict[str, np.ndarray]) -> tuple[np.ndarray, float]:
    """Locate narrow stems for geometry even when OHLC ticks are unreadable.

    The legacy extractor requires seven pixels between stems because it must
    read open/close ticks too. A historical range/color fingerprint needs only
    separate vertical stems. Preserve legacy results, falling back solely when
    that seven-pixel spacing restriction is the rejection reason.
    """
    from scipy.signal import find_peaks
    from .scenario_image_features import _bar_peaks

    try:
        return _bar_peaks(masks)
    except ValueError as exc:
        if str(exc) != 'bar_spacing_too_small':
            raise
    projection = (masks['red'] | masks['green']).sum(axis=0)
    peaks, _ = find_peaks(projection, prominence=4, distance=3)
    if len(peaks) < 12:
        raise ValueError('too_few_narrow_chart_stems')
    pitch = float(np.median(np.diff(peaks)))
    if not 3 <= pitch < 7:
        raise ValueError('unresolved_narrow_chart_spacing')
    return peaks, pitch


def fit_chart_lattice(peaks: np.ndarray, initial_pitch: float) -> tuple[float, float, np.ndarray]:
    """Fit stem slots without accumulating the rounding error of a pixel pitch.

    Pixel spacing need not be an integer. Inferring slots by rounding absolute
    distance using the median integer gap inserts false missing bars on long
    charts. Adjacent gaps identify elapsed slots before the fractional spacing
    is fitted. Real missing stems retain their elapsed time slots.
    """
    peaks = np.asarray(peaks, dtype=float)
    if (peaks.ndim != 1 or len(peaks) < 3 or not np.isfinite(peaks).all()
            or not np.isfinite(initial_pitch) or initial_pitch <= 0
            or np.any(np.diff(peaks) <= 0)):
        raise ValueError('Invalid chart stem coordinates or spacing')
    gaps = np.diff(peaks)
    steps = np.maximum(1, np.rint(gaps / initial_pitch)).astype(int)
    slots = np.concatenate(([0], np.cumsum(steps)))
    design = np.column_stack((np.ones(len(slots)), slots))
    origin, pitch = np.linalg.lstsq(design, peaks, rcond=None)[0]
    if pitch <= 0:
        raise ValueError('Nonpositive fitted chart spacing')
    return float(origin), float(pitch), slots


def fit_price_geometry(pixels: np.ndarray, prices: np.ndarray, scale_px: float) -> dict:
    """Compare linear and logarithmic axes using residuals in pixel units.

    Both transforms have two fitted parameters. The better geometric fit is
    selected per historical candidate; it is not a second timestamp candidate.
    The same residual checks must subsequently apply regardless of axis type.
    """
    pixels, prices = np.asarray(pixels, dtype=float), np.asarray(prices, dtype=float)
    if (pixels.ndim != 1 or prices.shape != pixels.shape or len(pixels) < 4
            or not np.isfinite(pixels).all() or not np.isfinite(prices).all()
            or not np.isfinite(scale_px) or scale_px <= 0 or np.ptp(pixels) <= 0):
        raise ValueError('Invalid geometry observations')
    design = np.column_stack((pixels, np.ones(len(pixels))))
    transforms = [('linear', prices)]
    if np.all(prices > 0):
        transforms.append(('log', np.log(prices)))
    candidates = []
    for name, coordinate in transforms:
        slope, intercept = np.linalg.lstsq(design, coordinate, rcond=None)[0]
        if slope <= 0:
            continue
        residual = abs((coordinate - (pixels*slope+intercept))/slope)/scale_px
        inliers = residual <= max(.12, float(np.quantile(residual, .85)))
        if inliers.sum() >= 20:
            slope, intercept = np.linalg.lstsq(design[inliers], coordinate[inliers], rcond=None)[0]
        if slope <= 0:
            continue
        residual = abs((coordinate - (pixels*slope+intercept))/slope)/scale_px
        median, p90 = float(np.median(residual)), float(np.quantile(residual, .9))
        candidates.append(dict(price_axis_scale=name, price_axis_slope=float(slope),
                               price_axis_intercept=float(intercept),
                               median_range_error=median, p90_range_error=p90,
                               geometry_error=median+.5*p90))
    if not candidates:
        raise ValueError('No positive price-axis transform')
    return min(candidates, key=lambda candidate: candidate['geometry_error'])


def price_from_chart_y(geometry: dict, y: float) -> float:
    """Invert the selected chart axis; y increases downwards on screenshots."""
    coordinate = geometry['price_axis_intercept'] - geometry['price_axis_slope']*float(y)
    axis = geometry['price_axis_scale']
    if axis == 'linear':
        return float(coordinate)
    if axis == 'log':
        return float(np.exp(coordinate))
    raise ValueError('Unsupported price-axis scale')
