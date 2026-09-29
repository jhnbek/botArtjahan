"""Evaluate frozen D1 -> predicted-side H1 models on demonstrated test episodes.

This is label agreement on selected author scenarios, not continuous-market
detection, a profit backtest, or a win rate. No weights or labels are fitted.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Callable

from .align_scenario_market import COLLECTION, digest
from .prepare_market_training import DAY, HOUR, closed_prefix
from .prepare_scenario_training import load_reviews
from .scenario_bybit_history import _hash
from .scenario_direction_model import direction_advice_from_ohlc, validate_direction_model
from .scenario_model import READINESS_MODEL_KIND, features_for_entry, score_features, validate_model
from .train_market_scenario_models import load_prepared_cases, validate_cases

MODEL_FILES = {'direction': 'direction_model.json', 'entry': 'scenario_model.json'}
SPLITS = ('train', 'validation', 'test')
ENTRY_SCORE_THRESHOLD = 0.0
LIMITATIONS = [
    'Selected demonstrated episodes and supplied blue levels, not a continuous market stream.',
    'Agreement with author direction and entry labels is not profitability or a win rate.',
    'Pre-hour proxies exclude execution-hour final OHLC and do not identify exact intrabar execution.',
    'Caption-derived OR branches need not be the author\'s earliest selected execution.',
    'Verified waiting states are sparse; unlabelled earlier hours are never treated as negatives.',
    'The fixed instrument test groups have been observed previously; this is not a fresh independent test.',
    'The historical availability of supplied author levels is not independently established.',
    'Stop losses, take profits, costs, slippage and autonomous level discovery are not evaluated.',
]


def _contained(collection: Path, relative: str) -> Path:
    path = (collection / relative).resolve()
    if not path.is_relative_to(collection.resolve()):
        raise ValueError('market_source_path_outside_collection')
    return path


def load_case_history(collection: Path, case: dict, timeframe: str) -> list[dict]:
    """Use the prepared case cache, including explicit author-corrected pairs."""
    if timeframe not in ('daily', 'hourly'):
        raise ValueError('unsupported_market_timeframe')
    path = _contained(Path(collection), case[f'source_{timeframe}_cache'])
    if digest(path) != case[f'source_{timeframe}_cache_sha256']:
        raise ValueError('pipeline_market_cache_changed')
    payload = json.loads(path.read_text(encoding='utf-8'))
    checksum = payload.pop('cache_sha256', None)
    if checksum != _hash(payload):
        raise ValueError('pipeline_market_cache_checksum_invalid')
    expected = dict(symbol=case['market_symbol'], category=case['market_category'],
                    interval='1d' if timeframe == 'daily' else '1h')
    if any(payload.get('identity', {}).get(key) != value for key, value in expected.items()):
        raise ValueError('pipeline_market_cache_identity_mismatch')
    snapshot = payload.get('exchange_snapshot_ms')
    if isinstance(snapshot, bool) or not isinstance(snapshot, int) or snapshot < 0:
        raise ValueError('pipeline_market_snapshot_invalid')
    return [bar for bar in payload['bars'] if bar['close_time_ms'] <= snapshot]


def _scenario_ids(values: object) -> list[int]:
    if (not isinstance(values, list)
            or any(isinstance(value, bool) or not isinstance(value, int) for value in values)
            or len(values) != len(set(values))):
        raise ValueError('invalid_model_partition_scenario_ids')
    return sorted(values)


def validate_frozen_models(cases: list[dict], preparation: dict, provenance: dict,
                           direction: dict, entry: dict, training_report: dict) -> list[dict]:
    """Require exactly the prepared frozen partitions and train+validation fits."""
    validate_cases(cases, preparation)
    validate_direction_model(direction)
    validate_model(entry)
    if (entry.get('model_kind') != READINESS_MODEL_KIND or entry.get('research_only') is not True
            or entry.get('automatic_order_execution_allowed') is not False):
        raise ValueError('pipeline_requires_research_readiness_model')
    if training_report.get('trained') is not True:
        raise ValueError('pipeline_requires_completed_saved_training')
    if not provenance or any(artifact.get('provenance') != provenance
                             for artifact in (direction, entry, training_report)):
        raise ValueError('saved_models_preparation_provenance_mismatch')
    coverage = {key: preparation[key] for key in ('target_pairs', 'eligible_pairs', 'user_skipped')}
    if any(model.get('corpus_coverage') != coverage for model in (direction, entry)):
        raise ValueError('saved_model_coverage_mismatch')
    if any(training_report.get(key) != value for key, value in coverage.items()):
        raise ValueError('saved_training_coverage_mismatch')

    all_test_ids, all_fit_ids, all_test_groups, all_fit_groups = set(), set(), set(), set()
    tests = {}
    for kind, model in (('direction', direction), ('entry', entry)):
        source = [case for case in cases if kind == 'entry' or case.get('direction_daily_only_eligible') is True]
        expected_fit = sorted(case['scenario_id'] for case in source if case['split'] != 'test')
        if (_scenario_ids(model.get('fitted_scenario_ids')) != expected_fit
                or _scenario_ids(training_report.get(kind, {}).get('fitted_scenario_ids')) != expected_fit):
            raise ValueError('saved_model_fitted_ids_disagree_with_fixed_partitions')
        for split in SPLITS:
            subset = [case for case in source if case['split'] == split]
            expected_ids = sorted(case['scenario_id'] for case in subset)
            expected_groups = sorted({case['group'] for case in subset})
            for recorded in (model.get('splits', {}).get(split, {}),
                             training_report.get(kind, {}).get('splits', {}).get(split, {})):
                if (_scenario_ids(recorded.get('scenario_ids')) != expected_ids
                        or recorded.get('groups') != expected_groups
                        or recorded.get('scenarios') != len(subset)):
                    raise ValueError('saved_model_partition_changed')
            if split == 'test':
                tests[kind] = set(expected_ids)
                all_test_ids.update(expected_ids)
                all_test_groups.update(expected_groups)
            else:
                all_fit_ids.update(expected_ids)
                all_fit_groups.update(expected_groups)
    if all_test_ids & all_fit_ids or all_test_groups & all_fit_groups:
        raise ValueError('pipeline_test_overlaps_either_model_fit')
    intersection = tests['direction'] & tests['entry']
    if not intersection:
        raise ValueError('no_shared_frozen_test_episodes')
    return sorted((case for case in cases if case['scenario_id'] in intersection),
                  key=lambda case: case['scenario_id'])


def _verified_waits(case: dict, reviews: dict[int, dict]) -> None:
    if len(case['state_close_times_ms']) == 1:
        return
    review = reviews.get(case['scenario_id'], {})
    if (review.get('wait_interval_verified') is not True
            or review.get('no_other_valid_entries_in_wait_interval') is not True
            or review.get('entry_variant') == 'repeat_entry'
            or review.get('earlier_entry_labels_require_review')):
        raise ValueError('pipeline_wait_states_not_explicitly_verified')


def evaluate_pipeline(cases: list[dict], preparation: dict, provenance: dict,
                      direction_model: dict, entry_model: dict, training_report: dict,
                      *, collection: Path, reviews: dict[int, dict],
                      history_loader: Callable = load_case_history) -> dict:
    """Read closed OHLC, predict side, then recalculate H1 features for that side."""
    selected = validate_frozen_models(cases, preparation, provenance, direction_model,
                                      entry_model, training_report)
    details, cache = [], {}
    wait_count = wait_false_positives = 0
    for case in selected:
        _verified_waits(case, reviews)
        histories = {}
        for timeframe in ('daily', 'hourly'):
            key = (case[f'source_{timeframe}_cache'], case[f'source_{timeframe}_cache_sha256'],
                   case['market_symbol'], case['market_category'], timeframe)
            if key not in cache:
                cache[key] = history_loader(collection, case, timeframe)
            histories[timeframe] = cache[key]
        daily_cutoff = case['latest_closed_d1_time_ms']
        if daily_cutoff > min(case['state_close_times_ms']):
            raise ValueError('pipeline_daily_information_after_evaluated_state')
        daily = closed_prefix(histories['daily'], daily_cutoff, DAY)
        advice = direction_advice_from_ohlc(daily, case['level'], model=direction_model,
                                           daily_close_confirmed=True)
        if advice.get('status') != 'research_only' or advice.get('direction') not in ('long', 'short'):
            raise ValueError('pipeline_direction_inference_failed')
        actual_features = advice['prerequisites']['features']
        if (set(actual_features) != set(case['direction_features'])
                or any(not math.isclose(actual_features[name], expected, rel_tol=1e-12, abs_tol=1e-12)
                       for name, expected in case['direction_features'].items())):
            raise ValueError('pipeline_recomputed_daily_features_disagree')
        predicted_side = advice['direction']
        state_results = []
        for index, cutoff in enumerate(case['state_close_times_ms']):
            hourly = closed_prefix(histories['hourly'], cutoff, HOUR)
            # Crucial: do not reuse case['states']; those encode AUTHOR direction.
            features = features_for_entry(hourly, case['level'], predicted_side)
            score = score_features(features, entry_model)
            is_entry = index == len(case['state_close_times_ms']) - 1
            predicts_entry = score >= ENTRY_SCORE_THRESHOLD
            if not is_entry:
                wait_count += 1
                wait_false_positives += int(predicts_entry)
            state_results.append(dict(state_close_time_ms=cutoff,
                                      last_used_hourly_open_time_ms=hourly[-1]['open_time_ms'],
                                      last_used_hourly_close_time_ms=hourly[-1]['close_time_ms'],
                                      label='entry' if is_entry else 'verified_wait',
                                      raw_entry_score=score, predicts_entry=predicts_entry,
                                      features_direction=predicted_side))
        direction_correct = predicted_side == case['direction']
        entry_positive = state_results[-1]['predicts_entry']
        details.append(dict(scenario_id=case['scenario_id'], group=case['group'],
                            author_direction=case['direction'], predicted_direction=predicted_side,
                            author_long_probability=advice['author_long_probability'],
                            direction_correct=direction_correct, entry_score_nonnegative=entry_positive,
                            joint_correct=direction_correct and entry_positive,
                            daily_information_cutoff_ms=daily_cutoff,
                            last_used_daily_open_time_ms=daily[-1]['open_time_ms'],
                            decision_time_ms=case['decision_time_ms'],
                            entry_resolution=case['entry_resolution'],
                            entry_label_source=case.get('entry_label_source', 'reviewed_author_entry_anchor'),
                            author_exact_execution_hour_verified=case.get('author_exact_execution_hour_verified'),
                            earliest_OR_execution_verified=case.get('earliest_OR_execution_verified'),
                            state_results=state_results))
    total = len(details)
    direction_correct = sum(row['direction_correct'] for row in details)
    entry_positive = sum(row['entry_score_nonnegative'] for row in details)
    joint_correct = sum(row['joint_correct'] for row in details)
    warnings = ['Previously observed fixed test groups; no new independent test is claimed.']
    if wait_count < 20:
        warnings.append(f'Only {wait_count} verified waiting states; false-positive estimates are fragile.')
    return dict(schema_version=1, evaluated_at_utc=datetime.now(timezone.utc).isoformat(),
                metric_scope='selected_demonstrated_episodes_with_supplied_author_levels',
                evaluation_split='intersection_of_saved_direction_and_entry_test_partitions',
                model_pair='original_market_v2_direction_and_entry',
                expanded_direction_model_used=False,
                expanded_direction_model_scope='separate experiment; not evaluated by this paired pipeline',
                scenarios=total, scenario_ids=[row['scenario_id'] for row in details],
                groups=sorted({row['group'] for row in details}),
                direction=dict(correct=direction_correct, total=total, accuracy=direction_correct/total),
                entry_at_demonstrated_state=dict(score_greater_than_or_equal_to_zero=entry_positive,
                                                total=total, rate=entry_positive/total),
                joint=dict(correct_direction_and_nonnegative_entry_score=joint_correct,
                           total=total, accuracy=joint_correct/total),
                verified_waits=dict(states=wait_count, false_positives=wait_false_positives,
                                    true_negatives=wait_count-wait_false_positives,
                                    false_positive_rate=wait_false_positives/wait_count if wait_count else None),
                entry_score_threshold=ENTRY_SCORE_THRESHOLD, threshold_selected_on_test=False,
                hourly_features_use='predicted_direction_only_recomputed_from_closed_OHLC',
                resolution_counts=dict(Counter(row['entry_resolution'] for row in details)),
                entry_label_sources=dict(Counter(row['entry_label_source'] for row in details)),
                weights_fitted=False, labels_created=False, future_ohlc_used=False,
                profitability_evaluated=False, continuous_market_entry_detection_evaluated=False,
                historical_test_was_previously_observed=True, research_only=True,
                automatic_order_execution_allowed=False, preparation_provenance=provenance,
                limitations=LIMITATIONS, warnings=warnings, details=details)


def run(collection: Path = COLLECTION) -> dict:
    collection = Path(collection)
    cases, preparation, provenance = load_prepared_cases(collection)
    folder = collection / 'training/market_v2'
    training_path = folder / 'training_report.json'
    training_hash = digest(training_path)
    training_report = json.loads(training_path.read_text(encoding='utf-8'))
    models = {}
    hashes = {}
    for kind, filename in MODEL_FILES.items():
        path = folder / filename
        hashes[filename] = digest(path)
        if training_report.get('model_sha256', {}).get(filename) != hashes[filename]:
            raise ValueError(f'saved_model_hash_mismatch: {filename}')
        models[kind] = json.loads(path.read_text(encoding='utf-8'))
    reviews, review_hashes = load_reviews(collection)
    if review_hashes != provenance['review_sources_sha256']:
        raise ValueError('pipeline_reviews_changed_during_load')
    report = evaluate_pipeline(cases, preparation, provenance, models['direction'], models['entry'],
                               training_report, collection=collection, reviews=reviews)
    if (digest(training_path) != training_hash
            or any(digest(folder / name) != checksum for name, checksum in hashes.items())):
        raise ValueError('frozen_models_changed_during_pipeline_evaluation')
    report.update(model_sha256=hashes, training_report_sha256=training_hash,
                  evaluation_code_sha256=digest(Path(__file__)),
                  market_cache_validation_code_sha256=digest(Path(__file__).with_name('scenario_bybit_history.py')))
    # The sole output; weights, training reports, preparation and source labels are untouched.
    (folder / 'pipeline_evaluation.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--collection', type=Path, default=COLLECTION)
    result = run(parser.parse_args().collection)
    print(json.dumps({key: result[key] for key in ('scenarios', 'direction', 'entry_at_demonstrated_state',
                                                  'joint', 'verified_waits', 'warnings')}, indent=2))
