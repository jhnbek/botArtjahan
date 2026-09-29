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
import math
from pathlib import Path
from typing import Any

import numpy as np

try:
    from .scenario_image_features import FEATURE_NAMES, FEATURE_SCHEMA_VERSION
    from .scenario_model import features_for_entry, validate_model
    from .scenario_training_rules import build_training_contract, scenario_training_policy
except ImportError:
    from scenario_image_features import FEATURE_NAMES, FEATURE_SCHEMA_VERSION
    from scenario_model import features_for_entry, validate_model
    from scenario_training_rules import build_training_contract, scenario_training_policy

REPO = Path(__file__).resolve().parents[1]
DEFAULT_COLLECTION = REPO / '_knowledge_base/manual_reviews/scenarios_dzhahan_20260925'
VERSION = 'entry_demonstration_post_daily_close_v2'


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
    if (isinstance(regularization, bool) or not isinstance(regularization, (int, float))
            or not math.isfinite(regularization) or regularization <= 0):
        raise ValueError('Regularization must be finite and positive')
    if names != list(FEATURE_NAMES):
        raise ValueError('Training requires the canonical feature order')
    if len(cases) < 5:
        raise ValueError('Insufficient training data')
    for case in cases:
        if len(case.get('states', [])) < 2:
            raise ValueError('Each scenario needs at least two states')
        for state in case['states']:
            if set(state) != set(names):
                raise ValueError('All states must use the same feature schema')
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in state.values()):
                raise ValueError('Features must be finite numeric values')
    states = np.asarray([[s[n] for n in names] for c in cases for s in c['states']], dtype=float)
    if not np.isfinite(states).all():
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


def fit_entry_readiness(cases: list[dict], names: list[str], regularization: float) -> dict:
    """Learn author-selected states versus observed post-D1 waiting states.

    Immediate entries supply positive demonstrations even when no legal earlier
    state exists. Earlier states are waits, never labels of losing trades.
    Classes are balanced and each episode contributes equal pre-balance weight.
    """
    if (isinstance(regularization, bool) or not isinstance(regularization, (int, float))
            or not math.isfinite(regularization) or regularization <= 0):
        raise ValueError('Regularization must be finite and positive')
    if names != list(FEATURE_NAMES) or len(cases) < 5:
        raise ValueError('Canonical features and at least five training cases required')
    values, labels, importance = [], [], []
    for case in cases:
        states = case.get('states', [])
        if not states:
            raise ValueError('Each scenario needs at least one admissible state')
        for i, state in enumerate(states):
            if set(state) != set(names) or any(
                isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                for v in state.values()
            ):
                raise ValueError('Invalid finite numeric feature schema')
            selected = i == len(states)-1
            values.append([state[n] for n in names])
            labels.append(float(selected))
            importance.append(1. if selected else 1. / (len(states)-1))
    x, y, sw = np.asarray(values, dtype=float), np.asarray(labels), np.asarray(importance)
    if set(labels) != {0., 1.}:
        raise ValueError('Need both author entries and post-D1 wait observations')
    mean, scale = x.mean(axis=0), x.std(axis=0)
    scale[scale < 1e-8] = 1.
    design = np.column_stack(((x-mean)/scale, np.ones(len(x))))
    for label in (0., 1.):
        selected = y == label
        sw[selected] *= .5 / sw[selected].sum()
    penalty = np.asarray([regularization]*len(names)+[0.])
    weights = np.zeros(len(names)+1)

    def loss(w):
        logits = design @ w
        return float(np.sum(sw*(np.logaddexp(0., logits)-y*logits)) + .5*np.sum(penalty*w*w))

    history = [loss(weights)]
    for _ in range(80):
        p = sigmoid(design @ weights)
        grad = design.T @ (sw*(p-y)) + penalty*weights
        hess = design.T @ ((sw*p*(1-p))[:, None]*design) + np.diag(penalty+1e-9)
        step = np.linalg.solve(hess, grad)
        rate = 1.
        while rate > 1e-8 and loss(weights-rate*step) > history[-1]:
            rate *= .5
        weights -= rate*step
        history.append(loss(weights))
        if np.max(abs(rate*step)) < 1e-7:
            break
    if not np.isfinite(weights).all() or not np.isfinite(history).all():
        raise ValueError('Non-finite optimization result')
    return dict(mean=mean.tolist(), scale=scale.tolist(), weights=weights[:-1].tolist(),
                bias=float(weights[-1]), regularization=regularization,
                optimization=dict(iterations=len(history)-1, initial_objective=history[0],
                                  final_objective=history[-1], objective_history=history))


def evaluate_readiness(cases: list[dict], names: list[str], model: dict) -> dict:
    """Report entry/wait agreement separately from optional within-event ranking."""
    confusion = dict(entry_as_entry=0, entry_as_wait=0, wait_as_entry=0, wait_as_wait=0)
    details = []
    for case in cases:
        x = np.asarray([[s[n] for n in names] for s in case['states']])
        scores = ((x-np.asarray(model['mean']))/np.asarray(model['scale'])) @ np.asarray(model['weights']) + model['bias']
        for i, score in enumerate(scores):
            actual = 'entry' if i == len(scores)-1 else 'wait'
            predicted = 'entry' if score >= 0 else 'wait'
            confusion[f'{actual}_as_{predicted}'] += 1
        details.append(dict(scenario_id=case['scenario_id'], group=case['group'],
                            state_offsets_before_entry=case['offsets'], scores=scores.tolist(),
                            selected_state_score=float(scores[-1])))
    entry_total = confusion['entry_as_entry']+confusion['entry_as_wait']
    wait_total = confusion['wait_as_entry']+confusion['wait_as_wait']
    recall_entry = confusion['entry_as_entry']/entry_total if entry_total else None
    recall_wait = confusion['wait_as_wait']/wait_total if wait_total else None
    balanced = (recall_entry+recall_wait)/2 if recall_entry is not None and recall_wait is not None else None
    multi = [c for c in cases if len(c['states']) >= 2]
    return dict(scenarios=len(cases), entry_recall=recall_entry, wait_recall=recall_wait,
                balanced_accuracy=balanced, confusion=confusion,
                ranking=evaluate(multi, names, model), details=details,
                metric_scope='author demonstrated entry vs post-D1 waiting states; not win rate or continuous-market detection')


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


def require_reviewed_audit(audit: dict) -> None:
    if audit.get('entry_training_review_complete') is not True:
        raise RuntimeError(
            'Training paused. Rebuild the extraction audit from manually reviewed '
            'entry anchors first; see TRAINING_HANDOFF.md. '
            'The automatic arrow audit must not be used as entry ground truth.'
        )


def build_cases(collection: Path, audit: dict) -> tuple[list[dict], list[dict]]:
    require_reviewed_audit(audit)
    source = collection/'visual_analysis/scenario_analysis.jsonl'
    if audit.get('annotation_sha256') != digest(source):
        raise ValueError('Reviewed extraction no longer matches annotations')
    if audit.get('feature_code_sha256') != digest(Path(__file__).with_name('scenario_image_features.py')):
        raise ValueError('Rebuild extraction after feature code changes')
    if audit.get('preparation_code_sha256') != digest(Path(__file__).with_name('prepare_scenario_training.py')):
        raise ValueError('Rebuild extraction after preparation code changes')
    sources = audit.get('review_sources_sha256')
    current_reviews = {p.relative_to(collection).as_posix(): digest(p)
                       for pattern in ('anchor_reviews_*.jsonl', 'anchor_alignment_supplement_*.jsonl')
                       for p in (collection/'training').glob(pattern)}
    if not sources or sources != current_reviews:
        raise ValueError('Reviewed extraction no longer matches anchor reviews')
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
        if not ext.get('usable', False):
            entry['reason'] = ext.get('reason', 'image_extraction_abstention')
            continue
        image = collection/'images'/f'1H_{sid}.jpg'
        actual_hash = digest(image)
        if actual_hash != ext.get('source_sha256', ext.get('sha256')):
            raise ValueError(f'Extraction no longer matches source image #{sid}')
        if actual_hash != row['image_sha256'][f'images/1H_{sid}.jpg']:
            raise ValueError(f'Image differs from visually reviewed original #{sid}')
        entry['hourly_source_sha256'] = actual_hash
        entry['extraction_reason'] = ext.get('reason')
        review = ext.get('anchor_review', {})
        if (review.get('reviewed') is not True or review.get('usable_for_entry_training') is not True
                or ext.get('daily_close_verified') is not True):
            raise ValueError(f'Missing reviewed entry or daily close #{sid}')
        bars = ext.get('bars', ext.get('decoded_bars'))
        level = ext.get('level', ext.get('selected_level_pixel_price'))
        anchor = ext['signal_x']
        after_close = review.get('decision_timing') == 'after_bar_close'
        if not bars or any(b['x'] > anchor or (b['x'] == anchor and not after_close) for b in bars):
            raise ValueError(f'Entry bar or future OHLC leaked into scenario #{sid}')
        if any(a['x'] >= b['x'] for a, b in zip(bars, bars[1:])):
            raise ValueError(f'Unordered OHLC #{sid}')
        daily_close_x = float(ext['daily_close_bar_x'])
        tolerance = float(ext['bar_pitch_px'])*.35
        states, offsets = [], []
        # Full 14 prior TR observations plus their previous close at every state.
        for earlier in range(6, -1, -1):
            prefix = bars[:-earlier] if earlier else bars
            if len(prefix) < 16:
                continue
            if prefix[-1]['x'] < daily_close_x-tolerance:
                continue
            features = features_for_entry(prefix, level, row['direction'])
            states.append(features)
            offsets.append(earlier)
        if not states or offsets[-1] != 0:
            entry['reason'] = 'no_entry_state_after_confirmed_daily_close'
            continue
        case = {'scenario_id': sid, 'group': group, 'direction': row['direction'],
                'hourly_source_sha256': actual_hash, 'anchor_x': anchor,
                'last_feature_bar_x': bars[-1]['x'], 'level_pixel_price': level,
                'states': states, 'offsets': offsets,
                'daily_close_bar_x': daily_close_x, 'decision_timing': review['decision_timing'],
                'entry_state_resolution': ext['entry_state_resolution'],
                'target': 'author_entry_state_vs_admissible_post_daily_close_wait_states',
                'earlier_states_are_failed_trades': False}
        cases.append(case)
        entry.update(status='eligible', reason='hourly_pre_entry_geometry_available',
                     comparison_states=len(states)-1, anchor_x=anchor,
                     last_feature_bar_x=bars[-1]['x'])
    return cases, ledger


def run(collection: Path, output: Path | None = None) -> dict:
    output = output or collection/'training'
    audit_path = collection/'training/reviewed_entry_extraction.json'
    if not audit_path.is_file():
        audit_path = collection/'training/image_extraction_audit.json'
    audit = json.loads(audit_path.read_text(encoding='utf-8'))
    require_reviewed_audit(audit)
    cases, ledger = build_cases(collection, audit)
    annotations = [json.loads(s) for s in (collection/'visual_analysis/scenario_analysis.jsonl').read_text(encoding='utf-8').splitlines() if s.strip()]
    split_cases = [dict(scenario_id=r['scenario_id'], group=(r.get('instrument') or '').strip().upper()) for r in annotations]
    splits = assign_splits([c for c in split_cases if c['group'] and c['group'] not in ('H', 'Н')])
    for c in cases:
        c['split'] = splits[c['scenario_id']]
    for row in ledger:
        row['split'] = splits.get(row['scenario_id'])
    names = list(FEATURE_NAMES)
    partitions = {s: [c for c in cases if c['split']==s] for s in ('train', 'validation', 'test')}
    if any(not partitions[s] for s in partitions):
        raise ValueError('Every fixed instrument partition must contain entry observations')
    validation_has_waits = any(len(c['states']) > 1 for c in partitions['validation'])
    # Fix the fallback before inspecting any test score. Never reshuffle held-out
    # instruments just to obtain a convenient validation result.
    regularizations = (.01, .1, 1.0) if validation_has_waits else (.1,)
    selection_method = ('validation_balanced_accuracy' if validation_has_waits
                        else 'fixed_default_no_two_class_validation')
    candidates = []
    for regularization in regularizations:
        fitted = fit_entry_readiness(partitions['train'], names, regularization)
        validation = evaluate_readiness(partitions['validation'], names, fitted)
        selection_score = validation['balanced_accuracy'] if validation_has_waits else 0.
        candidates.append((selection_score, regularization, fitted, validation))
    _, best_regularization, _, _ = max(candidates, key=lambda p: (p[0], p[1]))
    # Refit on train+validation once; the test instruments stay completely unseen.
    final_train = partitions['train']+partitions['validation']
    model = fit_entry_readiness(final_train, names, best_regularization)
    test = evaluate_readiness(partitions['test'], names, model)
    train = evaluate_readiness(final_train, names, model)
    contract = build_training_contract()
    model.update(schema_version=1, model_kind='entry_demonstration_readiness',
                 feature_schema_version=FEATURE_SCHEMA_VERSION, feature_names=names,
                 version=VERSION, created_utc=datetime.now(timezone.utc).isoformat(),
                 purpose='compare author entry geometry to waiting states after confirmed D1, for a known direction and level',
                 input_timeframe='1h', execution_mode='research_only',
                 threshold=None, profit_probability_model=False,
                 trained_on_successful_examples_only=True, stop_take_regression_trained=False,
                 level_discovery_trained=False,
                 training={'source_scenarios': 409, 'eligible_scenarios': len(cases),
                           'fitted_scenario_ids': sorted(c['scenario_id'] for c in final_train),
                           'test_scenario_ids': sorted(c['scenario_id'] for c in partitions['test']),
                           'split_method': 'whole instruments held out; fixed SHA256 order',
                           'regularization_selection_method': selection_method,
                           'source_annotations_sha256': digest(collection/'visual_analysis/scenario_analysis.jsonl'),
                           'image_audit_sha256': digest(audit_path),
                           'training_code_sha256': digest(Path(__file__)),
                           'feature_code_sha256': digest(Path(__file__).with_name('scenario_image_features.py')),
                           'rules_contract': contract},
                 metrics={'test_pairwise_preference_agreement': test['ranking']['pairwise_preference_agreement'],
                          'test_entry_wait_balanced_accuracy': test['balanced_accuracy'],
                          'test_scenarios': test['scenarios'], 'profitability_evaluated': False,
                          'live_entry_detection_evaluated': False})
    report = {'version': VERSION, 'trained': True, 'source_scenarios': 409,
              'eligible_scenarios': len(cases), 'excluded_scenarios': 409-len(cases),
              'excluded_reasons': dict(Counter(r['reason'] for r in ledger if r['status']=='excluded')),
              'split_counts': {s: len(v) for s, v in partitions.items()},
              'split_instruments': {s: sorted({c['group'] for c in v}) for s, v in partitions.items()},
              'final_fit_scenarios': len(final_train), 'selected_regularization': best_regularization,
              'regularization_selection_method': selection_method,
              'state_counts': {s: {'entries': len(v), 'waits_after_d1': sum(len(c['states'])-1 for c in v)} for s, v in partitions.items()},
              'entry_resolution_counts': dict(Counter(c.get('entry_state_resolution', 'synthetic') for c in cases)),
              'validation_search': [{'regularization': x[1], 'evaluation': x[3]} for x in candidates],
              'train_evaluation': train, 'test_evaluation': test,
              'baselines': {'random_pair_preference': .5,
                            'most_recent_state_on_these_preselected_windows': 1.0,
                            'limitation': 'Entry endpoint is known when constructing examples. No time/index feature is supplied, but this conditional metric is not proof of finding entries in a continuous market stream.'},
              'limitations': ['Approximate pixel OHLC; accepted anchors are manually reviewed.',
                             'Author chosen entries are preferences, earlier moments are not labeled losses.',
                             'Only successful scenarios were supplied; no success probability is learned.',
                             'User levels are supplied; drawing them with hindsight was not independently ruled out.',
                             'D1/H1 alignment follows author arrows, not independently verified market timestamps.',
                             'Marked intrabar entries use a pre-hour proxy, without the future OHLC of the execution hour.',
                             'Absolute entry/SL/TP cannot be learned from unscaled images; KB risk rules remain.',
                             'This model has not been validated as a live entry trigger.']}
    validate_model(model)
    output.mkdir(parents=True, exist_ok=True)
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
