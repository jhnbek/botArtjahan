"""Research D1+H1 direction and predicted-side entry on all prepared episodes.

Both timeframes describe both sides of the supplied level. Author direction is
only a target, never an input transformation. This is historical label agreement,
not continuous-market detection, level discovery, or a profit backtest.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from .align_scenario_market import COLLECTION, digest
from .evaluate_market_scenario_pipeline import load_case_history, _verified_waits
from .prepare_market_training import DAY, HOUR, closed_prefix
from .prepare_scenario_training import load_reviews
from .scenario_model import READINESS_MODEL_KIND, features_for_entry, score_features, validate_model
from .scenario_prerequisites import PREREQUISITE_FEATURE_NAMES, prerequisite_features_from_ohlc
from .train_market_scenario_models import load_prepared_cases, validate_cases

FEATURE_NAMES = tuple(f'{tf}_{name}' for tf in ('d1', 'h1') for name in PREREQUISITE_FEATURE_NAMES)
MODEL_KIND = 'author_context_direction_logistic'
FEATURE_SCHEMA = 'closed_D1_H1_symmetric_prerequisites_v1'
OUTPUT = 'training/market_context_direction'
SPLITS = ('train', 'validation', 'test')
LIMITATIONS = [
    'Selected author episodes and supplied blue levels; historical level availability is not independently established.',
    'Agreement with author direction and demonstrated entry is not profit, win rate, or continuous-market detection.',
    'Pre-hour proxies exclude execution-hour OHLC and do not establish exact intrabar execution.',
    'The fixed instrument test groups have been observed previously; this is not a fresh independent test.',
    'Author-condition-derived and OR-branch labels preserve their documented conventions and timing limits.',
    'Sparse verified waiting states; unlabelled earlier hours are not negatives.',
    'Stops, targets, costs, slippage and autonomous discovery of levels are not learned or evaluated here.',
]


def context_features(daily, hourly, level, cutoff, signal):
    """Use only bars closed by this state; no direction label is accepted."""
    if (type(cutoff) is not int or type(signal) is not int or cutoff % HOUR
            or signal % DAY or cutoff < signal + DAY):
        raise ValueError('context_state_precedes_closed_D1_or_invalid_boundary')
    d = closed_prefix(daily, cutoff // DAY * DAY, DAY)
    h = closed_prefix(hourly, cutoff, HOUR)
    features = {f'{tf}_{name}': value for tf, bars in (('d1', d), ('h1', h))
                for name, value in prerequisite_features_from_ohlc(bars, level).items()}
    return features, dict(daily_close_time_ms=d[-1]['close_time_ms'],
                          hourly_close_time_ms=h[-1]['close_time_ms'])


def build_cases(base, collection, *, history_loader=load_case_history):
    cases, cache = [], {}
    for case in base:
        histories = {}
        for tf in ('daily', 'hourly'):
            key = (case[f'source_{tf}_cache'], case[f'source_{tf}_cache_sha256'],
                   case['market_symbol'], case['market_category'], tf)
            if key not in cache:
                cache[key] = history_loader(collection, case, tf)
            histories[tf] = cache[key]
        features, times = context_features(histories['daily'], histories['hourly'], case['level'],
                                           case['decision_time_ms'], case['signal_d1_open_time_ms'])
        if (times['daily_close_time_ms'] != case['latest_closed_d1_time_ms']
                or any(not math.isclose(features[f'd1_{name}'], value, rel_tol=1e-12, abs_tol=1e-12)
                       for name, value in case['direction_features'].items())):
            raise ValueError('context_recomputed_D1_disagrees_with_prepared_case')
        cases.append({**{k: case[k] for k in ('scenario_id', 'group', 'split', 'direction', 'level',
                       'decision_time_ms', 'signal_d1_open_time_ms', 'entry_resolution',
                       'direction_daily_only_eligible', 'source_daily_cache', 'source_daily_cache_sha256',
                       'source_hourly_cache', 'source_hourly_cache_sha256')},
                      'features': features, **times,
                      'author_exact_execution_hour_verified': case.get('author_exact_execution_hour_verified'),
                      'entry_label_source': case.get('entry_label_source', 'reviewed_author_entry_anchor')})
    return cases


def _matrix(cases):
    rows = []
    for case in cases:
        values = case['features']
        if set(values) != set(FEATURE_NAMES):
            raise ValueError('context_requires_exact_symmetric_48_feature_schema')
        if any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v)
               for v in values.values()):
            raise ValueError('context_features_must_be_finite')
        rows.append([values[name] for name in FEATURE_NAMES])
    return np.asarray(rows, dtype=float)


def _partitions(cases):
    seen, groups = set(), {}
    for c in cases:
        if c['scenario_id'] in seen or c['split'] not in SPLITS or c['direction'] not in ('short', 'long'):
            raise ValueError('invalid_or_duplicate_context_case')
        seen.add(c['scenario_id'])
        if c['group'] in groups and groups[c['group']] != c['split']:
            raise ValueError('context_instrument_group_leakage')
        groups[c['group']] = c['split']
    _matrix(cases)
    return {split: [c for c in cases if c['split'] == split] for split in SPLITS}


def _splits(cases):
    return {s: dict(scenarios=len(rows), scenario_ids=sorted(c['scenario_id'] for c in rows),
                    groups=sorted({c['group'] for c in rows})) for s, rows in _partitions(cases).items()}


def _sigmoid(values):
    return 1 / (1 + np.exp(-np.clip(values, -60, 60)))


def _fit(cases, regularization):
    if len(cases) < 4 or {c['direction'] for c in cases} != {'short', 'long'}:
        raise ValueError('context_fit_requires_both_sides_and_four_cases')
    x = _matrix(cases)
    y = np.asarray([c['direction'] == 'long' for c in cases], dtype=float)
    mean, scale = x.mean(axis=0), x.std(axis=0)
    scale[scale < 1e-8] = 1.
    design = np.column_stack(((x - mean) / scale, np.ones(len(x))))
    weights = np.zeros(design.shape[1])
    penalty = np.asarray([regularization] * x.shape[1] + [0.])

    def objective(values):
        logits = design @ values
        return float(np.mean(np.logaddexp(0, logits) - y * logits) + .5 * np.sum(penalty * values**2))

    history = [objective(weights)]
    for _ in range(80):
        p = _sigmoid(design @ weights)
        gradient = design.T @ (p - y) / len(x) + penalty * weights
        hessian = design.T @ ((p * (1 - p))[:, None] * design) / len(x)
        hessian += np.diag(penalty + 1e-9)
        step = np.linalg.solve(hessian, gradient)
        rate = 1.
        while rate > 1e-8 and objective(weights - rate * step) > history[-1]:
            rate *= .5
        weights -= rate * step
        history.append(objective(weights))
        if np.max(abs(rate * step)) < 1e-7:
            break
    return dict(mean=mean.tolist(), scale=scale.tolist(), weights=weights[:-1].tolist(),
                bias=float(weights[-1]), regularization=regularization,
                optimization=dict(iterations=len(history)-1, objective_history=history))


def probabilities(cases, model):
    logits = ((_matrix(cases) - np.asarray(model['mean'])) / np.asarray(model['scale'])) @ np.asarray(model['weights']) + model['bias']
    return _sigmoid(logits), logits


def metrics(cases, model, baseline):
    probabilities_, logits = probabilities(cases, model)
    labels = [c['direction'] for c in cases]
    predictions = ['long' if p >= .5 else 'short' for p in probabilities_]
    counts = Counter(labels)
    confusion = {a: {b: sum(y == a and p == b for y, p in zip(labels, predictions))
                     for b in ('short', 'long')} for a in ('short', 'long')}
    return dict(scenarios=len(cases), correct=sum(a == b for a, b in zip(labels, predictions)),
                accuracy=sum(a == b for a, b in zip(labels, predictions))/len(cases),
                balanced_accuracy=sum(confusion[s][s]/counts[s] for s in counts)/len(counts),
                log_loss=float(np.mean(np.logaddexp(0, logits) - np.asarray([s == 'long' for s in labels])*logits)),
                class_counts=dict(counts), confusion_true_rows_predicted_columns=confusion,
                majority_baseline=dict(direction_chosen_from_fit_partition=baseline, accuracy=counts[baseline]/len(cases)),
                details=[dict(scenario_id=c['scenario_id'], group=c['group'], author_direction=c['direction'],
                              predicted_direction=side, author_long_probability=float(p))
                         for c, side, p in zip(cases, predictions, probabilities_)])


def fit_context(cases, provenance):
    parts = _partitions(cases)
    if any(not rows for rows in parts.values()):
        raise ValueError('context_requires_three_nonempty_partitions')
    baseline = Counter(c['direction'] for c in parts['train']).most_common(1)[0][0]
    search = [dict(regularization=r, validation=metrics(parts['validation'], _fit(parts['train'], r), baseline))
              for r in (.01, .1, 1., 10.)]
    selected = min(search, key=lambda r: (-r['validation']['balanced_accuracy'], r['validation']['log_loss'], -r['regularization']))
    fitting = parts['train'] + parts['validation']
    fitted = _fit(fitting, selected['regularization'])
    model = dict(schema_version=1, model_kind=MODEL_KIND, feature_schema_version=FEATURE_SCHEMA,
                 feature_names=list(FEATURE_NAMES), classes=['short', 'long'], **fitted,
                 fitted_scenario_ids=sorted(c['scenario_id'] for c in fitting), splits=_splits(cases),
                 research_only=True, automatic_order_execution_allowed=False, provenance=provenance,
                 probability_meaning='author long-label agreement, never profit probability', limitations=LIMITATIONS)
    # Final weights and normalization are fixed before looking at test values.
    test = metrics(parts['test'], model, Counter(c['direction'] for c in fitting).most_common(1)[0][0])
    report = dict(trained=True, eligible_direction_scenarios=len(cases), splits=model['splits'],
                  fitted_scenario_ids=model['fitted_scenario_ids'], validation_search=search,
                  selected_regularization=selected['regularization'], held_out_test=test,
                  H1_dependent_scenario_ids=sorted(c['scenario_id'] for c in cases if not c['direction_daily_only_eligible']),
                  historical_test_was_previously_observed=True, profitability_evaluated=False,
                  automatic_order_execution_allowed=False, provenance=provenance, limitations=LIMITATIONS)
    return model, report


def validate_frozen_pair(base, context_model, entry, entry_report, provenance):
    if (context_model.get('model_kind') != MODEL_KIND or context_model.get('feature_schema_version') != FEATURE_SCHEMA
            or context_model.get('feature_names') != list(FEATURE_NAMES)
            or context_model.get('classes') != ['short', 'long']
            or context_model.get('research_only') is not True
            or context_model.get('automatic_order_execution_allowed') is not False):
        raise ValueError('invalid_context_model_schema')
    for key in ('weights', 'mean', 'scale'):
        values = context_model.get(key)
        if (not isinstance(values, list) or len(values) != len(FEATURE_NAMES)
                or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values)):
            raise ValueError('invalid_context_model_parameters')
    if any(v <= 0 for v in context_model['scale']) or not math.isfinite(context_model['bias']):
        raise ValueError('invalid_context_model_scale_or_bias')
    validate_model(entry)
    if (entry.get('model_kind') != READINESS_MODEL_KIND or entry.get('research_only') is not True
            or entry.get('automatic_order_execution_allowed') is not False):
        raise ValueError('context_pipeline_requires_frozen_research_entry_model')
    if (entry_report.get('trained') is not True or entry.get('provenance') != provenance
            or entry_report.get('provenance') != provenance
            or context_model.get('provenance', {}).get('base_market_v2') != provenance):
        raise ValueError('context_entry_preparation_provenance_mismatch')
    fit_ids = sorted(c['scenario_id'] for c in base if c['split'] != 'test')
    expected_splits = {s: dict(scenarios=sum(c['split'] == s for c in base),
                               scenario_ids=sorted(c['scenario_id'] for c in base if c['split'] == s),
                               groups=sorted({c['group'] for c in base if c['split'] == s})) for s in SPLITS}
    groups = {}
    for c in base:
        if c['group'] in groups and groups[c['group']] != c['split']:
            raise ValueError('context_instrument_group_leakage')
        groups[c['group']] = c['split']
    for artifact in (context_model, entry, entry_report.get('entry', {})):
        if artifact.get('fitted_scenario_ids') != fit_ids:
            raise ValueError('context_fitted_ids_disagree_with_frozen_partitions')
        for split, expected in expected_splits.items():
            recorded = artifact.get('splits', {}).get(split, {})
            if any(recorded.get(k) != v for k, v in expected.items()):
                raise ValueError('context_frozen_partition_mismatch')


def evaluate_pipeline(base, context_model, entry, entry_report, provenance, *, collection,
                      reviews, history_loader=load_case_history):
    validate_frozen_pair(base, context_model, entry, entry_report, provenance)
    details, waits, false_positives = [], 0, 0
    for case in base:
        if case['split'] != 'test':
            continue
        _verified_waits(case, reviews)
        daily = history_loader(collection, case, 'daily')
        hourly = history_loader(collection, case, 'hourly')
        states = []
        for i, cutoff in enumerate(case['state_close_times_ms']):
            features, times = context_features(daily, hourly, case['level'], cutoff, case['signal_d1_open_time_ms'])
            probability = float(probabilities([dict(features=features)], context_model)[0][0])
            side = 'long' if probability >= .5 else 'short'
            h = closed_prefix(hourly, cutoff, HOUR)
            score = score_features(features_for_entry(h, case['level'], side), entry)
            is_entry = i == len(case['state_close_times_ms']) - 1
            if not is_entry:
                waits += 1
                false_positives += int(score >= 0)
            states.append(dict(state_close_time_ms=cutoff, **times, predicted_direction=side,
                               author_long_probability=probability, raw_entry_score=score,
                               predicts_entry=score >= 0, label='entry' if is_entry else 'verified_wait'))
        last = states[-1]
        correct = last['predicted_direction'] == case['direction']
        details.append(dict(scenario_id=case['scenario_id'], group=case['group'], author_direction=case['direction'],
                            direction_correct=correct, joint_correct=correct and last['predicts_entry'],
                            direction_daily_only_eligible=case['direction_daily_only_eligible'],
                            entry_resolution=case['entry_resolution'], state_results=states))
    n = len(details)
    return dict(schema_version=1, model_pair='closed_D1_H1_context_direction_and_market_v2_entry',
                scenarios=n, scenario_ids=[d['scenario_id'] for d in details],
                direction=dict(correct=sum(d['direction_correct'] for d in details), total=n),
                entry_at_demonstrated_state=dict(positive=sum(d['state_results'][-1]['predicts_entry'] for d in details), total=n),
                joint=dict(correct=sum(d['joint_correct'] for d in details), total=n),
                verified_waits=dict(states=waits, false_positives=false_positives, true_negatives=waits-false_positives),
                threshold_selected_on_test=False, weights_fitted=False, author_direction_used_as_predictor=False,
                future_ohlc_used=False, entry_score_threshold=0., historical_test_was_previously_observed=True,
                profitability_evaluated=False, continuous_market_entry_detection_evaluated=False,
                limitations=LIMITATIONS, details=details)


def run(collection=COLLECTION):
    collection = Path(collection)
    base, preparation, base_provenance = load_prepared_cases(collection)
    validate_cases(base, preparation)
    entry_path = collection/'training/market_v2/scenario_model.json'
    entry_report_path = collection/'training/market_v2/training_report.json'
    entry_hash, report_hash = digest(entry_path), digest(entry_report_path)
    entry = json.loads(entry_path.read_text(encoding='utf-8'))
    entry_report = json.loads(entry_report_path.read_text(encoding='utf-8'))
    if entry_report.get('model_sha256', {}).get('scenario_model.json') != entry_hash:
        raise ValueError('context_frozen_entry_model_hash_mismatch')
    cases = build_cases(base, collection)
    cases_text = ''.join(json.dumps(c, ensure_ascii=False, allow_nan=False)+'\n' for c in cases)
    provenance = dict(base_market_v2=base_provenance,
                      context_cases_sha256=hashlib.sha256(cases_text.encode('utf-8')).hexdigest(),
                      code_sha256={name: digest(Path(__file__).with_name(name)) for name in
                                   ('train_market_context_direction.py', 'scenario_prerequisites.py', 'level_structure.py',
                                    'detector_prototype.py', 'scenario_model.py', 'scenario_image_features.py',
                                    'prepare_market_training.py', 'evaluate_market_scenario_pipeline.py', 'scenario_bybit_history.py')})
    model, report = fit_context(cases, provenance)
    reviews, review_hashes = load_reviews(collection)
    if review_hashes != base_provenance['review_sources_sha256']:
        raise ValueError('context_reviews_changed_during_run')
    evaluation = evaluate_pipeline(base, model, entry, entry_report, base_provenance,
                                   collection=collection, reviews=reviews)
    if (digest(entry_path) != entry_hash or digest(entry_report_path) != report_hash
            or load_prepared_cases(collection)[2] != base_provenance
            or any(digest(Path(__file__).with_name(name)) != expected
                   for name, expected in provenance['code_sha256'].items())):
        raise ValueError('context_sources_or_frozen_models_changed_during_run')
    timestamp = datetime.now(timezone.utc).isoformat()
    model['trained_at_utc'] = timestamp
    report.update(trained_at_utc=timestamp, target_pairs=preparation['target_pairs'], user_skipped=preparation['user_skipped'],
                  all_target_directions_prepared=len(cases) == preparation['target_pairs'])
    folder = collection/OUTPUT
    folder.mkdir(exist_ok=True)
    # Hash and persist exactly the same UTF-8 bytes on every platform. Windows
    # text-mode newline translation must not invalidate the frozen provenance.
    (folder/'cases.jsonl').write_bytes(cases_text.encode('utf-8'))
    (folder/'model.json').write_text(json.dumps(model, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    model_hash = digest(folder/'model.json')
    report['model_sha256'] = model_hash
    evaluation.update(evaluated_at_utc=timestamp, context_model_sha256=model_hash,
                      entry_model_sha256=entry_hash, entry_training_report_sha256=report_hash,
                      context_provenance=provenance, research_only=True, automatic_order_execution_allowed=False)
    for name, artifact in (('training_report.json', report), ('pipeline_evaluation.json', evaluation)):
        (folder/name).write_text(json.dumps(artifact, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    return report, evaluation


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--collection', type=Path, default=COLLECTION)
    report, pipeline = run(parser.parse_args().collection)
    print(json.dumps(dict(scenarios=report['eligible_direction_scenarios'],
                          fitted=len(report['fitted_scenario_ids']), held_out=report['held_out_test']['scenarios'],
                          direction= pipeline['direction'], joint=pipeline['joint'], waits=pipeline['verified_waits']), indent=2))
