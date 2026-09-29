"""Align author chart geometry to public exchange history, with abstention.

The whole screenshot may establish a historical coordinate transform, but it
must never be used wholesale as model input. Consumers cut the matched market
history at independently reviewed D1/entry anchors before computing features.
Caption dates narrow the search only; they do not establish a signal timestamp.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
from PIL import Image

from .scenario_chart_geometry import fit_chart_lattice, fit_price_geometry, price_from_chart_y, chart_color_masks, chart_stem_peaks

MATCHER_SOURCE_SHA256 = hashlib.sha256(b''.join(
    Path(__file__).with_name(name).read_bytes() for name in
    ('scenario_market_match.py','scenario_chart_geometry.py','scenario_image_features.py')
)).hexdigest()


def chart_fingerprint(path: str | Path) -> dict:
    path = Path(path)
    rgb = np.asarray(Image.open(path).convert('RGB'))
    masks = chart_color_masks(rgb)
    peaks, pitch = chart_stem_peaks(masks)
    # Fit a bar lattice; missing detected stems preserve their time slots.
    origin, pitch, slots = fit_chart_lattice(peaks, pitch)
    records = []
    height = rgb.shape[0]
    for x, slot in zip(peaks, slots):
        if abs(x-(origin+slot*pitch)) > .22*pitch:
            continue
        color = max(('red', 'green'), key=lambda key: masks[key][:, x].sum())
        ys = np.flatnonzero(masks[color][:, x])
        if len(ys) < 4:
            continue
        records.append(dict(x=int(x), slot=int(slot), color=color,
                            high=-float(ys[0]) if ys[0] > 1 else None,
                            low=-float(ys[-1]) if ys[-1] < height-2 else None))
    if len(records) < 12 or len({r['slot'] for r in records}) != len(records):
        raise ValueError('insufficient_unique_chart_stems')
    blue = np.flatnonzero(masks['blue'].sum(axis=1) >= max(25, rgb.shape[1]*.5))
    bands = np.split(blue, np.where(np.diff(blue) > 2)[0]+1) if len(blue) else []
    return dict(source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                width=int(rgb.shape[1]), height=int(height),
                origin_x=float(origin), pitch=float(pitch),
                levels_y=[float(np.mean(b)) for b in bands], bars=records)


def match_fingerprint(fingerprint: dict, bars: list[dict]) -> dict:
    """Require independently agreeing color and affine OHLC range geometry.

    Candidate rejection thresholds are geometric, never fitted to trade labels
    or held-out trading model scores. Candidate timestamps must be contiguous.
    """
    stems = fingerprint['bars']
    slots = np.array([s['slot'] for s in stems], dtype=int)
    minslot, maxslot = int(min(slots)), int(max(slots))
    slots = slots-minslot
    count = maxslot-minslot+1
    if len(bars) < count or len(stems) < 12:
        return dict(accepted=False, reason='insufficient_history_or_chart')
    times = np.array([b['open_time_ms'] for b in bars], dtype=np.int64)
    step = int(np.median(np.diff(times))) if len(times) > 1 else 0
    ranges = [s['high']-s['low'] for s in stems if s['high'] is not None and s['low'] is not None]
    scale_px = float(np.median(ranges)) if ranges else 0
    if scale_px <= 0:
        return dict(accepted=False, reason='insufficient_visible_range')
    pixels, stem_indices, columns = [], [], []
    for i, stem in enumerate(stems):
        for column in ('high', 'low'):
            if stem[column] is not None:
                pixels.append(stem[column]); stem_indices.append(i); columns.append(column)
    pixels = np.asarray(pixels)
    candidates = []
    for offset in range(len(bars)-count+1):
        if step <= 0 or not np.all(np.diff(times[offset:offset+count]) == step):
            continue
        segment = [bars[offset+int(s)] for s in slots]
        hits = []
        for stem, bar in zip(stems, segment):
            body = bar['close']-bar['open']
            doji = abs(body) < .06*(bar['high']-bar['low'])
            hits.append(doji or ((body >= 0) == (stem['color'] == 'green')))
        color_score = float(np.mean(hits))
        if color_score < .75:
            continue
        prices = np.array([segment[i][col] for i, col in zip(stem_indices, columns)])
        try:
            geometry = fit_price_geometry(pixels, prices, scale_px)
        except ValueError:
            continue
        quality = geometry['geometry_error'] + (1-color_score)
        candidates.append(dict(offset=offset, first_slot=minslot,
                               first_open_time_ms=int(times[offset]), interval_ms=step,
                               **geometry, color_agreement=color_score,
                               quality=quality, matched_stems=len(stems)))
    candidates.sort(key=lambda row: row['quality'])
    if not candidates:
        return dict(accepted=False, reason='no_color_compatible_window')
    best = candidates[0]
    margin = candidates[1]['quality']-best['quality'] if len(candidates)>1 else None
    accepted = (best['matched_stems'] >= 16 and best['color_agreement'] >= .9
                and best['median_range_error'] <= .12 and best['p90_range_error'] <= .35
                and (margin is None or margin >= .15))
    return dict(best, accepted=bool(accepted), reason='matched_geometry' if accepted else 'geometry_or_uniqueness_not_confirmed',
                quality_margin=margin, candidates_checked=len(bars)-count+1,
                geometry_only=True, training_eligible=False,
                needs_reviewed_decision_anchors=True)


def time_at_x(fingerprint: dict, match: dict, x: float) -> int:
    if not match.get('accepted'):
        raise ValueError('Unconfirmed market alignment')
    slot = int(round((x-fingerprint['origin_x'])/fingerprint['pitch']))
    slots = [bar['slot'] for bar in fingerprint['bars']]
    if not min(slots) <= slot <= max(slots):
        raise ValueError('Anchor outside matched chart')
    if abs(x-(fingerprint['origin_x']+slot*fingerprint['pitch'])) > .35*fingerprint['pitch']:
        raise ValueError('Anchor between bars')
    return int(match['first_open_time_ms']+(slot-match['first_slot'])*match['interval_ms'])


def price_at_y(match: dict, y: float) -> float:
    if not match.get('accepted'):
        raise ValueError('Unconfirmed market alignment')
    return price_from_chart_y(match, y)
