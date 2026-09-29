"""Learn closed-D1 direction independently of unresolved hourly entry labels.

This extension preserves market_v2 and every pending author TVX request. A D1
observation timestamp is not an entry recommendation or a replacement entry.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from .align_scenario_market import COLLECTION, digest, read_jsonl
from .prepare_market_training import DAY, closed_prefix, choose_level, load_matched_history
from .scenario_market_match import time_at_x
from .scenario_prerequisites import prerequisite_features_from_ohlc, PREREQUISITE_FEATURE_NAMES
from .scenario_direction_model import (fit_direction_classifier, evaluate_direction,
                                       validate_direction_model, MODEL_KIND, FEATURE_SCHEMA_VERSION)
from .scenario_training_rules import H1_DEPENDENT_DIRECTION_IDS
from .train_market_scenario_models import load_prepared_cases, _features, _check_hashes, LIMITATIONS

REVIEW = 'training/market_direction_pending_reviews.jsonl'
OUTPUT = 'training/market_direction_expanded'


def supplemental_case(collection, review, annotation, daily, group_splits):
    """Only verified daily labels can be recovered; H1 labels remain absent."""
    sid = annotation['scenario_id']
    if review.get('daily_only_direction_verified') is not True or sid in H1_DEPENDENT_DIRECTION_IDS:
        raise ValueError('direction_requires_H1_or_further_review')
    if review.get('no_entry_label_created') is not True:
        raise ValueError('supplement_must_not_create_entry_label')
    if review.get('source_images_sha256') != annotation['image_sha256']:
        raise ValueError('direction_review_source_mismatch')
    if not review.get('source_review_files_sha256'):
        raise ValueError('direction_review_evidence_required')
    _check_hashes(collection, review['source_images_sha256'])
    _check_hashes(collection, review['source_review_files_sha256'])
    if not daily.get('accepted'):
        raise ValueError('supplement_requires_confirmed_daily_market')
    bars = load_matched_history(collection, daily, '1D')
    signal = time_at_x(daily['fingerprint'], daily, review['closed_daily_anchor_x'])
    observation = signal + DAY
    if observation != review['observation_close_time_ms']:
        raise ValueError('reviewed_daily_boundary_mismatch')
    prefix = closed_prefix(bars, observation, DAY)
    level, _, method = choose_level(daily, review, prefix[-1]['close'])
    direction = review['reviewed_direction']
    if direction not in ('long', 'short') or direction != annotation.get('direction'):
        raise ValueError('supplement_direction_requires_explicit_author_correction')
    group = daily['symbol'].removesuffix('USDT')
    if group not in group_splits:
        raise ValueError('instrument_missing_from_frozen_partition')
    return dict(scenario_id=sid, group=group, split=group_splits[group],
                direction=direction, features=prerequisite_features_from_ohlc(prefix, level),
                observation_close_time_ms=observation, signal_d1_open_time_ms=signal,
                observation_source='reviewed_closed_D1_without_entry_label', entry_label_created=False,
                hourly_entry_status='pending_author_or_semantic_clarification',
                level=level, level_method=method, market_symbol=daily['symbol'],
                source_images_sha256=annotation['image_sha256'], source_daily_cache=daily['cache_file'],
                source_daily_cache_sha256=daily['cache_file_sha256'], source_review_file=REVIEW)


def entry_ledger_snapshot(collection, base, previous):
    raw = (collection/'training/market_v2/ledger.jsonl').read_bytes()
    rows = [json.loads(line) for line in raw.decode('utf-8').splitlines() if line.strip()]
    expected = set(range(1,410))-set(previous['user_skipped'])
    if (len(rows) != previous['target_pairs'] or {r['scenario_id'] for r in rows} != expected
            or any(r.get('status') not in ('eligible','pending') for r in rows)):
        raise ValueError('entry_ledger_scope_mismatch')
    if {r['scenario_id'] for r in rows if r['status']=='eligible'} != {c['scenario_id'] for c in base}:
        raise ValueError('entry_ledger_eligible_cases_mismatch')
    if sum(r['status']=='pending' for r in rows) != previous['pending_pairs']:
        raise ValueError('entry_ledger_pending_count_mismatch')
    return rows, hashlib.sha256(raw).hexdigest()


def prepare(collection=COLLECTION):
    collection = Path(collection)
    base, previous, base_provenance = load_prepared_cases(collection)
    entry_rows, entry_ledger_digest = entry_ledger_snapshot(collection, base, previous)
    ledger = {r['scenario_id']: dict(scenario_id=r['scenario_id'], status='pending',
                                   reason='direction_depends_on_hourly_context')
              for r in entry_rows}
    cases = []
    for case in base:
        if not case['direction_daily_only_eligible']:
            continue
        sid = case['scenario_id']
        cases.append(dict(scenario_id=sid, group=case['group'], split=case['split'],
                          direction=case['direction'], features=case['direction_features'],
                          observation_close_time_ms=case['latest_closed_d1_time_ms'],
                          signal_d1_open_time_ms=case['signal_d1_open_time_ms'],
                          observation_source='existing_verified_market_v2_D1', entry_label_created=False,
                          level=case['level'], market_symbol=case['market_symbol'],
                          source_daily_cache=case['source_daily_cache'],
                          source_daily_cache_sha256=case['source_daily_cache_sha256'],
                          source_images_sha256=case['source_images_sha256']))
        ledger[sid].update(status='eligible', reason='existing_verified_D1_direction', split=case['split'])
    pending_entry = {r['scenario_id'] for r in entry_rows if r['status'] == 'pending'}
    review_digest = digest(collection/REVIEW)
    reviews = read_jsonl(collection/REVIEW)
    review_ids={r['scenario_id'] for r in reviews}
    if len(reviews) != len(review_ids) or not pending_entry.issubset(review_ids):
        raise ValueError('direction_reviews_must_account_for_all_pending_entries_once')
    annotations = {r['scenario_id']: r for r in read_jsonl(collection/'visual_analysis/scenario_analysis.jsonl')}
    daily = {r['scenario_id']: r for r in read_jsonl(collection/'training/market_daily_alignment.jsonl')}
    frozen = read_jsonl(collection/'training/direction_training_ledger.jsonl')
    group_splits = {str(r['instrument']).upper(): r['split'] for r in frozen if r.get('split')}
    for review in reviews:
        sid = review['scenario_id']
        if sid not in pending_entry:
            continue
        try:
            case = supplemental_case(collection, review, annotations[sid], daily[sid], group_splits)
            cases.append(case)
            ledger[sid].update(status='eligible', reason='closed_D1_direction_with_pending_TVX', split=case['split'])
        except (ValueError, KeyError, TypeError) as error:
            ledger[sid]['reason'] = str(error)
    cases.sort(key=lambda row: row['scenario_id'])
    if digest(collection/REVIEW) != review_digest:
        raise ValueError('direction_reviews_changed_during_preparation')
    if digest(collection/'training/market_v2/ledger.jsonl') != entry_ledger_digest:
        raise ValueError('entry_ledger_changed_during_preparation')
    provenance = dict(base_market_v2=base_provenance,
                      source_sha256={REVIEW: review_digest, 'training/market_v2/ledger.jsonl':entry_ledger_digest},
                      code_sha256={name: digest(Path(__file__).with_name(name)) for name in
                                   ('train_market_direction.py', 'scenario_direction_model.py',
                                    'scenario_prerequisites.py', 'scenario_market_match.py',
                                    'prepare_market_training.py')})
    report = dict(target_pairs=previous['target_pairs'], user_skipped=previous['user_skipped'],
                  eligible_direction_scenarios=len(cases), pending_direction_scenarios=len(ledger)-len(cases),
                  entry_eligible_scenarios=previous['eligible_pairs'],
                  entry_pending_scenarios=previous['pending_pairs'],
                  supplemental_direction_ids=[r['scenario_id'] for r in cases if r['scenario_id'] in pending_entry],
                  new_entry_labels_created=0, provenance=provenance)
    validate(cases, report)
    return cases, sorted(ledger.values(), key=lambda row: row['scenario_id']), report


def validate(cases, report):
    if not {54, 85}.issubset(report['user_skipped']) or report['target_pairs'] != 409-len(report['user_skipped']):
        raise ValueError('invalid_user_scope')
    if len(cases) != report['eligible_direction_scenarios']:
        raise ValueError('direction_coverage_mismatch')
    ids, groups = set(), {}
    for case in cases:
        sid, group, split = case['scenario_id'], case['group'], case['split']
        if sid in ids or sid in report['user_skipped'] or not 1 <= sid <= 409:
            raise ValueError('duplicate_or_excluded_scenario')
        ids.add(sid)
        if split not in ('train', 'validation', 'test') or not isinstance(group, str) or not group:
            raise ValueError('invalid_direction_partition')
        if group in groups and groups[group] != split:
            raise ValueError('instrument_group_leaked_across_partitions')
        groups[group] = split
        if case['direction'] not in ('long', 'short') or case.get('entry_label_created') is not False:
            raise ValueError('direction_only_labels_required')
        observation, signal = case['observation_close_time_ms'], case['signal_d1_open_time_ms']
        if any(type(t) is not int or t < 0 or t % DAY for t in (observation, signal)) or observation < signal+DAY:
            raise ValueError('daily_observation_not_closed')
        _features(case['features'], list(PREREQUISITE_FEATURE_NAMES), 'direction')


def fit(cases, preparation):
    validate(cases, preparation)
    parts = {s: [c for c in cases if c['split'] == s] for s in ('train', 'validation', 'test')}
    if any(not rows for rows in parts.values()) or {c['direction'] for c in parts['train']} != {'long', 'short'}:
        raise ValueError('direction_training_requires_partitions_and_both_sides')
    two_classes = {c['direction'] for c in parts['validation']} == {'long', 'short'}
    majority = Counter(c['direction'] for c in parts['train']).most_common(1)[0][0]
    candidates = []
    for regularization in ((.01, .1, 1., 10.) if two_classes else (.1,)):
        fitted = fit_direction_classifier(parts['train'], regularization)
        candidates.append(dict(regularization=regularization,
                               validation=evaluate_direction(parts['validation'], fitted, baseline_direction=majority)))
    selected = min(candidates, key=lambda r: (-(r['validation']['balanced_accuracy'] or 0.),
                                               r['validation']['log_loss'], -r['regularization']))
    training = parts['train']+parts['validation']
    fitted = fit_direction_classifier(training, selected['regularization'])
    # Freeze final weights before evaluating previously observed test groups.
    majority_final = Counter(c['direction'] for c in training).most_common(1)[0][0]
    test = evaluate_direction(parts['test'], fitted, baseline_direction=majority_final)
    splits = {s: dict(scenarios=len(rows), scenario_ids=[c['scenario_id'] for c in rows],
                      groups=sorted({c['group'] for c in rows})) for s, rows in parts.items()}
    model = dict(schema_version=1, model_kind=MODEL_KIND, feature_schema_version=FEATURE_SCHEMA_VERSION,
                 feature_names=list(PREREQUISITE_FEATURE_NAMES), classes=['short', 'long'], **fitted,
                 research_only=True, automatic_order_execution_allowed=False,
                 trained_at_utc=datetime.now(timezone.utc).isoformat(),
                 fitted_scenario_ids=sorted(c['scenario_id'] for c in training), splits=splits,
                 provenance=preparation['provenance'], limitations=LIMITATIONS,
                 direction_only=True, pending_hourly_entries_resolved=False,
                 trained_on_successful_examples_only=False,
                 probability_meaning='author long-label agreement, never profit probability')
    validate_direction_model(model)
    report = dict(**preparation, trained=True, fitted_scenario_ids=model['fitted_scenario_ids'], splits=splits,
                  validation_search=candidates, selected_regularization=selected['regularization'], held_out_test=test,
                  historical_test_was_previously_observed=True, profitability_evaluated=False,
                  automatic_order_execution_allowed=False, limitations=LIMITATIONS)
    return model, report


def run(collection=COLLECTION):
    collection = Path(collection)
    cases, ledger, preparation = prepare(collection)
    model, report = fit(cases, preparation)
    folder = collection/OUTPUT
    folder.mkdir(exist_ok=True)
    for name, rows in (('cases.jsonl', cases), ('ledger.jsonl', ledger)):
        (folder/name).write_text(''.join(json.dumps(r, ensure_ascii=False, allow_nan=False)+'\n' for r in rows), encoding='utf-8')
    report['cases_sha256'] = digest(folder/'cases.jsonl')
    model['provenance']['direction_cases_sha256'] = report['cases_sha256']
    (folder/'direction_model.json').write_text(json.dumps(model, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    report['model_sha256'] = digest(folder/'direction_model.json')
    (folder/'training_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--collection', type=Path, default=COLLECTION)
    result = run(parser.parse_args().collection)
    print(json.dumps({k: result[k] for k in ('eligible_direction_scenarios', 'pending_direction_scenarios',
                                           'entry_pending_scenarios', 'new_entry_labels_created')}, indent=2))
