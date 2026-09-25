"""Fit a local entry-timing preference model from annotated D1/H1 examples.

The target is the author's selected H1 entry, not profitability. Each comparison
asks whether the observed state immediately BEFORE that entry is preferred over
an earlier state in the same episode. Unselected states are not failed trades.
Text, future bars, ticker and scenario numbers never enter the feature vector.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

try:
    from .scenario_image_features import FEATURE_SCHEMA_VERSION
    from .scenario_model import features_for_entry
    from .scenario_training_rules import build_training_contract, scenario_training_policy
except ImportError:
    from scenario_image_features import FEATURE_SCHEMA_VERSION
    from scenario_model import features_for_entry
    from scenario_training_rules import build_training_contract, scenario_training_policy

REPO = Path(__file__).resolve().parents[1]
DEFAULT_COLLECTION = REPO / '_knowledge_base/manual_reviews/scenarios_dzhahan_20260925'
VERSION = 'entry_demonstration_pairwise_v1'


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def assign_splits(cases: list[dict]) -> dict[int, str]:
    """Entire instruments stay together, including multiple views of one event.

    The fixed grouping is set before fitting and never optimized for metrics.
    This is instrument holdout, not a chronological forward trading backtest.
    """
    groups = sorted({c['group'] for c in cases},
                    key=lambda s: hashlib.sha256(('entry-v1/'+s).encode()).hexdigest())
    if len(groups) < 5:
        raise ValueError('Need at least five instrument groups for independent validation/test')
    n_test = max(1, len(groups)//5)
    n_val = max(1, len(groups)//5)
    split = {g: ('test' if i < n_test else 'validation' if i < n_test+n_val else 'train')
             for i, g in enumerate(groups)}
    return {c['scenario_id']: split[c['group']] for c in cases}


def sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0/(1.0+np.exp(-np.clip(x, -60, 60)))


def fit_ranker(cases: list[dict], names: list[str], regularization: float) -> dict:
    """Newton optimization of weighted pairwise logistic loss plus L2.

    Each scenario has equal total weight irrespective of available alternatives.
    Standardization and all fitted parameters use only these supplied cases.
    """
    states = np.asarray([[s[n] for n in names] for c in cases for s in c['states']], dtype=float)
    if len(cases) < 5 or not np.isfinite(states).all():
        raise ValueError('Insufficient or non-finite training data')
    mean = states.mean(axis=0)
    scale = states.std(axis=0)
    scale[scale < 1e-8] = 1.0
    diffs, importance = [], []
    for c in cases:
        x = (np.asarray([[s[n] for n in names] for s in c['states']])-mean)/scale
        diffs.extend(x[-1]-x[:-1])
        importance.extend([1.0/(len(cases)*(len(x)-1))]*(len(x)-1))
    d, sw = np.asarray(diffs), np.asarray(importance)
    w = np.zeros(len(names), dtype=float)

    def loss(v: np.ndarray) -> float:
        return float(np.sum(sw*np.logaddexp(0, -(d@v))) + .5*regularization*(v@v))

    initial = loss(w)
    history = [initial]
    for iteration in range(60):
        pr = sigmoid(d@w)
        grad = d.T@(sw*(pr-1)) + regularization*w
        hess = d.T@((sw*pr*(1-pr))[:, None]*d) + np.eye(len(w))*(regularization+1e-9)
        step = np.linalg.solve(hess, grad)
        rate = 1.0
        before = loss(w)
        while rate > 1e-8 and loss(w-rate*step) > before:
            rate *= .5
        w -= rate*step
        history.append(loss(w))
        if np.max(abs(rate*step)) < 1e-7:
            break
    return {'mean': mean.tolist(), 'scale': scale.tolist(), 'weights': w.tolist(),
            'bias': 0.0, 'regularization': regularization,
            'optimization': {'iterations': len(history)-1, 'initial_objective': initial,
                             'final_objective': history[-1], 'objective_history': history}}


def evaluate(cases: list[dict], names: list[str], model: dict) -> dict:
    details = []
    mean, scale, weights = (np.asarray(model[k]) for k in ('mean', 'scale', 'weights'))
    for c in cases:
        x = np.asarray([[s[n] for n in names] for s in c['states']])
        scores = ((x-mean)/scale)@weights
        delta = scores[-1]-scores[:-1]
        pair_score = float(np.mean((delta > 1e-10)+.5*(abs(delta) <= 1e-10)))
        rank = 1+int(np.sum(scores[:-1] > scores[-1]+1e-10))
        tie_count = int(np.sum(abs(delta) <= 1e-10))
        details.append({'scenario_id': c['scenario_id'], 'instrument_group': c['group'],
                        'state_offsets_before_entry': c['offsets'],
                        'scores': scores.tolist(), 'author_state_rank': rank,
                        'tied_earlier_states': tie_count, 'pairwise_preference_agreement': pair_score})
    if not details:
        return {'scenarios': 0, 'pairwise_preference_agreement': None, 'details': []}
    return {'scenarios': len(details),
            'pairwise_preference_agreement': float(np.mean([d['pairwise_preference_agreement'] for d in details])),
            'strict_top1_fraction': float(np.mean([d['author_state_rank']==1 and not d['tied_earlier_states'] for d in details])),
            'mean_author_state_rank': float(np.mean([d['author_state_rank'] for d in details])),
            'metric_scope': 'conditional preference for demonstrated entry vs earlier states; not trade accuracy or win rate',
            'details': details}


def build_cases(collection: Path, audit: dict) -> tuple[list[dict], list[dict]]:
    source = collection/'visual_analysis/scenario_analysis.jsonl'
    rows = [json.loads(s) for s in source.read_text(encoding='utf-8').splitlines() if s.strip()]
    if len(rows) != 409 or len({r['scenario_id'] for r in rows}) != 409:
        raise ValueError('The source corpus must contain all 409 unique scenarios')
    images = {(int(r['scenario_id']), r['timeframe'].upper()): r for r in audit['records']}
    cases, ledger = [], []
    for row in rows:
        sid = row['scenario_id']
        policy = scenario_training_policy(row)
        entry = {'scenario_id': sid, 'instrument': row['instrument'], 'direction': row['direction'],
                 'policy': policy, 'status': 'excluded', 'reason': None}
        ledger.append(entry)
        allowed = policy.get('eligible', policy.get('eligible_for_direction_training', True))
        if not allowed:
            entry['reason'] = 'label_policy_exclusion'
            continue
        if row['direction'] not in ('long', 'short'):
            entry['reason'] = 'unknown_direction_context'
            continue
        group = (row['instrument'] or '').strip().upper()
        if not group or group in ('H', 'Н'):
            entry['reason'] = 'unknown_instrument_cannot_prevent_group_leakage'
            continue
        ext = images.get((sid, '1H'))
        if ext is None:
            entry['reason'] = 'missing_hourly_image'
            continue
        image = collection/'images'/f'1H_{sid}.jpg'
        actual_hash = digest(image)
        if actual_hash != ext.get('source_sha256', ext.get('sha256')):
            raise ValueError(f'Extraction no longer matches source image #{sid}')
        if actual_hash != row['image_sha256'][f'images/1H_{sid}.jpg']:
            raise ValueError(f'Image differs from visually reviewed original #{sid}')
        entry['hourly_source_sha256'] = actual_hash
        entry['extraction_reason'] = ext.get('reason')
        if not ext.get('usable', False):
            entry['reason'] = 'image_extraction_abstention'
            continue
        bars = ext.get('bars', ext.get('decoded_bars'))
        level = ext.get('level', ext.get('selected_level_pixel_price'))
        anchor = ext['signal_x']
        if not bars or any(b['x'] >= anchor for b in bars):
            raise ValueError(f'Entry bar or future OHLC leaked into scenario #{sid}')
        states, offsets = [], []
        # Full 14 prior TR observations plus their previous close at every state.
        for earlier in range(6, -1, -1):
            prefix = bars[:-earlier] if earlier else bars
            if len(prefix) < 16:
                continue
            features = features_for_entry(prefix, level, row['direction'])
            states.append(features)
            offsets.append(earlier)
        if len(states) < 3:
            entry['reason'] = 'less_than_two_earlier_comparison_states'
            continue
        case = {'scenario_id': sid, 'group': group, 'direction': row['direction'],
                'hourly_source_sha256': actual_hash, 'anchor_x': anchor,
                'last_feature_bar_x': bars[-1]['x'], 'level_pixel_price': level,
                'states': states, 'offsets': offsets,
                'target': 'last_pre_entry_state_preferred_to_earlier_states',
                'earlier_states_are_failed_trades': False}
        cases.append(case)
        entry.update(status='eligible', reason='hourly_pre_entry_geometry_available',
                     comparison_states=len(states)-1, anchor_x=anchor,
                     last_feature_bar_x=bars[-1]['x'])
    return cases, ledger


def run(collection: Path, output: Path | None = None) -> dict:
    output = output or collection/'training'
    output.mkdir(parents=True, exist_ok=True)
    audit_path = collection/'training/image_extraction_audit.json'
    audit = json.loads(audit_path.read_text(encoding='utf-8'))
    if audit.get('entry_training_review_complete') is not True:
        raise RuntimeError(
            'Training paused for PC handoff. Rebuild the extraction audit from '
            'manually reviewed entry anchors first; see TRAINING_HANDOFF.md. '
            'The automatic arrow audit must not be used as entry ground truth.'
        )
    cases, ledger = build_cases(collection, audit)
    splits = assign_splits(cases)
    for c in cases:
        c['split'] = splits[c['scenario_id']]
    for row in ledger:
        row['split'] = splits.get(row['scenario_id'])
    names = sorted(cases[0]['states'][0])
    partitions = {s: [c for c in cases if c['split']==s] for s in ('train', 'validation', 'test')}
    candidates = []
    for regularization in (.01, .1, 1.0):
        fitted = fit_ranker(partitions['train'], names, regularization)
        validation = evaluate(partitions['validation'], names, fitted)
        candidates.append((validation['pairwise_preference_agreement'], regularization, fitted, validation))
    _, best_regularization, _, _ = max(candidates, key=lambda p: (p[0], p[1]))
    # Refit on train+validation once; the test instruments stay completely unseen.
    final_train = partitions['train']+partitions['validation']
    model = fit_ranker(final_train, names, best_regularization)
    test = evaluate(partitions['test'], names, model)
    train = evaluate(final_train, names, model)
    contract = build_training_contract()
    model.update(schema_version=1, model_kind='entry_pairwise_ranker',
                 feature_schema_version=FEATURE_SCHEMA_VERSION, feature_names=names,
                 version=VERSION, created_utc=datetime.now(timezone.utc).isoformat(),
                 purpose='rank H1 pre-entry states for a supplied D1 direction and level',
                 input_timeframe='1h', execution_mode='research_only',
                 threshold=None, profit_probability_model=False,
                 trained_on_successful_examples_only=True, stop_take_regression_trained=False,
                 level_discovery_trained=False,
                 training={'source_scenarios': 409, 'eligible_scenarios': len(cases),
                           'fitted_scenario_ids': sorted(c['scenario_id'] for c in final_train),
                           'test_scenario_ids': sorted(c['scenario_id'] for c in partitions['test']),
                           'split_method': 'whole instruments held out; fixed SHA256 order',
                           'source_annotations_sha256': digest(collection/'visual_analysis/scenario_analysis.jsonl'),
                           'image_audit_sha256': digest(audit_path),
                           'training_code_sha256': digest(Path(__file__)),
                           'feature_code_sha256': digest(Path(__file__).with_name('scenario_image_features.py')),
                           'rules_contract': contract},
                 metrics={'test_pairwise_preference_agreement': test['pairwise_preference_agreement'],
                          'test_scenarios': test['scenarios'], 'profitability_evaluated': False,
                          'live_entry_detection_evaluated': False})
    report = {'version': VERSION, 'trained': True, 'source_scenarios': 409,
              'eligible_scenarios': len(cases), 'excluded_scenarios': 409-len(cases),
              'excluded_reasons': dict(Counter(r['reason'] for r in ledger if r['status']=='excluded')),
              'split_counts': {s: len(v) for s, v in partitions.items()},
              'split_instruments': {s: sorted({c['group'] for c in v}) for s, v in partitions.items()},
              'final_fit_scenarios': len(final_train), 'selected_regularization': best_regularization,
              'validation_search': [{'regularization': x[1], 'evaluation': x[3]} for x in candidates],
              'train_evaluation': train, 'test_evaluation': test,
              'baselines': {'random_pair_preference': .5,
                            'most_recent_state_on_these_preselected_windows': 1.0,
                            'limitation': 'Entry endpoint is known when constructing examples. No time/index feature is supplied, but this conditional metric is not proof of finding entries in a continuous market stream.'},
              'limitations': ['Approximate pixel OHLC and automatic anchor extraction require review.',
                             'Author chosen entries are preferences, earlier moments are not labeled losses.',
                             'Only successful scenarios were supplied; no success probability is learned.',
                             'User levels are supplied; drawing them with hindsight was not independently ruled out.',
                             'D1/H1 exact time alignment is absent; D1 direction is supplied context, not inferred future data.',
                             'Absolute entry/SL/TP cannot be learned from unscaled images; KB risk rules remain.',
                             'This model has not been validated as a live entry trigger.']}
    write_json(output/'scenario_model.json', model)
    write_json(output/'training_report.json', report)
    (output/'training_ledger.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in ledger),encoding='utf-8')
    (output/'entry_training_cases.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in cases),encoding='utf-8')
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--collection', type=Path, default=DEFAULT_COLLECTION)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = run(args.collection, args.output)
    print(json.dumps({k: report[k] for k in ('trained','eligible_scenarios','excluded_scenarios','split_counts','final_fit_scenarios')},ensure_ascii=False))
    print(json.dumps(report['test_evaluation'],ensure_ascii=False))


if __name__ == '__main__':
    main()
