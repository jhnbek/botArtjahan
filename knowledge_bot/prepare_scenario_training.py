"""Rebuild entry observations from reviewed author anchors, preserving originals."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from .scenario_image_features import FEATURE_SCHEMA_VERSION, extract_image_features

DEFAULT_COLLECTION = Path(__file__).resolve().parents[1] / '_knowledge_base/manual_reviews/scenarios_dzhahan_20260925'
REVIEWED_AUDIT_NAME = 'reviewed_entry_extraction.json'

# Structured transcription of the earlier saved visual reviews. These numbers
# are explicitly present in their reasons; no market time is inferred from x.
LEGACY_DAILY_CLOSE_X = {
    146: 552, 160: 406, 165: 242, 166: 410, 167: 456, 173: 606,
    176: 501, 180: 362, 182: 307, 188: 555, 197: 444, 205: 343,
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def load_reviews(collection: Path) -> tuple[dict[int, dict], dict[str, str]]:
    reviews, fingerprints = {}, {}
    for path in sorted((collection / 'training').glob('anchor_reviews_*.jsonl')):
        fingerprints[path.relative_to(collection).as_posix()] = sha256(path)
        for row in read_jsonl(path):
            sid = int(row['scenario_id'])
            if sid in reviews:
                raise ValueError(f'Duplicate entry review #{sid}')
            reviews[sid] = {**row, 'review_file': path.relative_to(collection).as_posix()}
    for path in sorted((collection / 'training').glob('anchor_alignment_supplement_*.jsonl')):
        fingerprints[path.relative_to(collection).as_posix()] = sha256(path)
        for supplement in read_jsonl(path):
            sid = int(supplement['scenario_id'])
            if sid not in reviews:
                raise ValueError(f'Alignment without entry review #{sid}')
            reviews[sid].update(supplement)
            reviews[sid]['alignment_review_file'] = path.relative_to(collection).as_posix()
    return reviews, fingerprints


def daily_alignment(review: dict) -> tuple[float | None, str]:
    """Convert explicit saved visual descriptions into a machine-readable gate."""
    if 'daily_close_verified' in review and review['daily_close_verified'] is not True:
        return None, 'daily_close_not_verified'
    if review.get('daily_close_bar_x') is not None:
        if review.get('daily_close_verified') is not True:
            return None, 'daily_close_not_verified'
        return float(review['daily_close_bar_x']), 'structured_visual_review'
    sid = int(review['scenario_id'])
    if sid in LEGACY_DAILY_CLOSE_X:
        return float(LEGACY_DAILY_CLOSE_X[sid]), 'coordinate_in_saved_visual_review'
    reason = review.get('reason', '').lower()
    immediate = ('сразу' in reason or 'немедлен' in reason or 'дополнительных условий ожидания нет' in reason)
    if immediate and 'днев' in reason and review.get('decision_timing') == 'after_bar_close':
        return float(review['decision_bar_x']), 'explicit_immediate_entry_at_daily_close'
    return None, 'daily_close_alignment_needs_reading'


def prepare(collection: Path = DEFAULT_COLLECTION) -> dict:
    collection = Path(collection)
    annotations_path = collection / 'visual_analysis/scenario_analysis.jsonl'
    annotations = read_jsonl(annotations_path)
    if {r['scenario_id'] for r in annotations} != set(range(1, 410)) or len(annotations) != 409:
        raise ValueError('Expected all 409 author scenarios')
    reviews, fingerprints = load_reviews(collection)
    records = []
    for row in annotations:
        sid = int(row['scenario_id'])
        review = reviews.get(sid)
        record = dict(scenario_id=sid, timeframe='1H', usable=False, reason='entry_anchor_not_yet_read',
                      author_scenario_validity='accepted_user_demonstration')
        records.append(record)
        if review is None:
            continue
        record['anchor_review'] = review
        if review.get('reviewed') is not True or review.get('usable_for_entry_training') is not True:
            record['reason'] = 'entry_anchor_reading_not_numerically_resolved'
            continue
        if review.get('decision_timing') not in ('after_bar_close', 'before_bar_open'):
            record['reason'] = 'entry_decision_convention_unresolved'
            continue
        daily_x, alignment_source = daily_alignment(review)
        if daily_x is None:
            record['reason'] = alignment_source
            continue
        image = collection / 'images' / f'1H_{sid}.jpg'
        expected = row['image_sha256'].get(f'images/1H_{sid}.jpg')
        if not image.is_file() or sha256(image) != expected:
            raise ValueError(f'Author image missing or modified #{sid}')
        # New reviews carry their own source hash; legacy ones are bound by the
        # already-reviewed annotation inventory plus immutable review hash.
        reviewed_hash = review.get('source_sha256', review.get('image_sha256'))
        if isinstance(reviewed_hash, str) and reviewed_hash != expected:
            raise ValueError(f'Review no longer matches source #{sid}')
        extracted = extract_image_features(
            image, timeframe='1h', signal_x=float(review['decision_bar_x']),
            signal_x_verified=True, include_signal_bar=review['decision_timing'] == 'after_bar_close',
            level_y_override=review.get('reference_level_y'))
        record.update(extracted, scenario_id=sid, timeframe='1H', anchor_review=review,
                      daily_close_bar_x=daily_x, daily_close_verified=True,
                      daily_alignment_source=alignment_source,
                      entry_state_resolution=('closed_hourly_bar' if review['decision_timing'] == 'after_bar_close'
                                              else 'pre_hourly_bar_proxy_for_marked_intrabar_entry'))
        if not extracted['usable']:
            continue
        # Use a tolerance only to match the manually read center of a stem;
        # adjacent bars are at least .65*pitch apart in the extractor.
        tolerance = float(extracted['bar_pitch_px']) * .35
        eligible_ends = [b for b in extracted['bars'] if b['x'] >= daily_x - tolerance]
        if not eligible_ends:
            record.update(usable=False, reason='entry_state_precedes_confirmed_daily_close')
        else:
            record['admissible_closed_states_after_d1'] = len(eligible_ends)
            record['reason'] = 'reviewed_entry_and_daily_close_extracted'
    result = dict(
        schema_version=1, feature_schema_version=FEATURE_SCHEMA_VERSION,
        entry_training_review_complete=True,
        review_completion_scope='Every admitted numerical entry has a reviewed anchor and D1 alignment; excluded scenarios remain in ledger',
        all_scenarios_accounted_for=len(records), source_images_modified=False,
        annotation_sha256=sha256(annotations_path), review_sources_sha256=fingerprints,
        feature_code_sha256=sha256(Path(__file__).with_name('scenario_image_features.py')),
        preparation_code_sha256=sha256(Path(__file__)),
        usable_scenarios=sum(r['usable'] for r in records),
        reason_counts=dict(Counter(r['reason'] for r in records)), records=records,
    )
    output = collection / 'training' / REVIEWED_AUDIT_NAME
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--collection', type=Path, default=DEFAULT_COLLECTION)
    args = parser.parse_args()
    result = prepare(args.collection)
    print(json.dumps({k: v for k, v in result.items() if k != 'records'}, ensure_ascii=False, indent=2))
