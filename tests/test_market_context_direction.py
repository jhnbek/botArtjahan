"""Direction must use causal, symmetric D1/H1 geometry on every prepared case."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from knowledge_bot import train_market_context_direction as context
from tests.test_market_pipeline_evaluation import fixtures


def training_cases():
    rows = []
    for i in range(12):
        split = ('train', 'validation', 'test')[i//4]
        side = 'long' if i % 2 else 'short'
        features = dict.fromkeys(context.FEATURE_NAMES, 0.)
        features['h1_long_close_from_level_tr'] = (-1 if side == 'short' else 1)*(i+1)
        rows.append(dict(scenario_id=i+1, group=split.upper(), split=split, direction=side,
                         features=features, direction_daily_only_eligible=i != 8))
    return rows


def pipeline_fixture():
    data = fixtures()
    base, _, provenance, _, entry, report, reviews, daily, hourly = data
    base[-2]['direction_daily_only_eligible'] = False
    names = list(context.FEATURE_NAMES)
    model = dict(schema_version=1, model_kind=context.MODEL_KIND, feature_schema_version=context.FEATURE_SCHEMA,
                 feature_names=names, classes=['short', 'long'], mean=[0.]*len(names), scale=[1.]*len(names),
                 weights=[0.]*len(names), bias=-1., research_only=True, automatic_order_execution_allowed=False,
                 provenance=dict(base_market_v2=provenance), splits=deepcopy(entry['splits']),
                 fitted_scenario_ids=deepcopy(entry['fitted_scenario_ids']))
    return base, provenance, model, entry, report, reviews, daily, hourly


def evaluate(data):
    base, provenance, model, entry, report, reviews, daily, hourly = data
    return context.evaluate_pipeline(base, model, entry, report, provenance, collection=Path('.'), reviews=reviews,
                                    history_loader=lambda root, case, tf: daily if tf == 'daily' else hourly)


class ContextDirectionTests(unittest.TestCase):
    def test_author_side_and_entry_vectors_do_not_transform_context_features(self):
        base, _, _, _, _, _, daily, hourly = pipeline_fixture()
        loader = lambda root, case, tf: daily if tf == 'daily' else hourly
        before = context.build_cases(base, Path('.'), history_loader=loader)
        changed = deepcopy(base)
        for c in changed:
            c['direction'] = 'short' if c['direction'] == 'long' else 'long'
            c['states'] = [{'leaked_author_direction': 999}]
        after = context.build_cases(changed, Path('.'), history_loader=loader)
        self.assertEqual([c['features'] for c in before], [c['features'] for c in after])
        self.assertEqual(len(before), len(base))
        self.assertFalse(before[-2]['direction_daily_only_eligible'])
        self.assertEqual(set(before[0]['features']), set(context.FEATURE_NAMES))

    def test_future_D1_and_execution_hour_cannot_change_features_or_pipeline(self):
        data = pipeline_fixture()
        before = evaluate(data)
        changed = deepcopy(data)
        cutoff = changed[0][-1]['decision_time_ms']
        for b in changed[-2]:
            if b['close_time_ms'] > cutoff // context.DAY * context.DAY:
                b.update(open=-1, high=float('inf'), low=-1, close=-1)
        for b in changed[-1]:
            if b['close_time_ms'] > cutoff:
                b.update(open=-1, high=float('inf'), low=-1, close=-1)
        self.assertEqual(before, evaluate(changed))
        case = data[0][-1]
        with self.assertRaisesRegex(ValueError, 'precedes_closed_D1'):
            context.context_features(data[-2], data[-1], case['level'],
                                     case['signal_d1_open_time_ms'], case['signal_d1_open_time_ms'])

    def test_weights_and_normalization_never_fit_test_values_or_labels(self):
        cases = training_cases()
        original = deepcopy(cases)
        model, report = context.fit_context(cases, {})
        changed = deepcopy(cases)
        for c in changed:
            if c['split'] == 'test':
                c['direction'] = 'short' if c['direction'] == 'long' else 'long'
                c['features'] = dict.fromkeys(context.FEATURE_NAMES, 1e9)
        other, _ = context.fit_context(changed, {})
        self.assertEqual(cases, original)
        for key in ('mean', 'scale', 'weights', 'bias', 'regularization', 'fitted_scenario_ids'):
            self.assertEqual(model[key], other[key])
        expected = np.mean([[c['features'][n] for n in context.FEATURE_NAMES]
                            for c in cases if c['split'] != 'test'], axis=0)
        np.testing.assert_allclose(model['mean'], expected)
        self.assertEqual(report['held_out_test']['scenarios'], 4)
        self.assertEqual(model['fitted_scenario_ids'], list(range(1, 9)))

    def test_group_leakage_and_author_feature_in_schema_are_rejected(self):
        cases = training_cases()
        cases[-1]['group'] = cases[0]['group']
        with self.assertRaisesRegex(ValueError, 'group_leakage'):
            context.fit_context(cases, {})
        cases = training_cases()
        cases[0]['features']['author_direction'] = 1.
        with self.assertRaisesRegex(ValueError, '48_feature_schema'):
            context.fit_context(cases, {})

    def test_pipeline_includes_H1_dependent_test_and_recomputes_predicted_side(self):
        data = pipeline_fixture()
        original = deepcopy(data)
        with patch.object(context, '_fit', side_effect=AssertionError('evaluation cannot fit')):
            result = evaluate(data)
        self.assertEqual(data, original)
        self.assertEqual(result['scenario_ids'], [40, 41])
        self.assertEqual(result['direction'], dict(correct=1, total=2))
        self.assertEqual(result['joint'], dict(correct=0, total=2))
        changed = deepcopy(data)
        for c in changed[0]:
            c['states'] = [{k: 1e10 for k in state} for state in c['states']]
        self.assertEqual(result, evaluate(changed))
        self.assertTrue(all(s['predicted_direction'] == 'short' for d in result['details'] for s in d['state_results']))
        for d in result['details']:
            for s in d['state_results']:
                self.assertEqual(s['hourly_close_time_ms'], s['state_close_time_ms'])
                self.assertLessEqual(s['daily_close_time_ms'], s['state_close_time_ms'])

    def test_provenance_changed_fits_and_unverified_waits_are_rejected(self):
        data = pipeline_fixture()
        data[3]['provenance'] = {'stale': True}
        with self.assertRaisesRegex(ValueError, 'provenance_mismatch'):
            evaluate(data)
        data = pipeline_fixture()
        data[2]['fitted_scenario_ids'].append(40)
        with self.assertRaisesRegex(ValueError, 'fitted_ids'):
            evaluate(data)
        data = pipeline_fixture()
        data[5][40]['wait_interval_verified'] = False
        with self.assertRaisesRegex(ValueError, 'wait_states_not_explicitly_verified'):
            evaluate(data)

    def test_run_binds_saved_cases_provenance_to_actual_UTF8_bytes(self):
        base, provenance, _, entry, report, reviews, daily, hourly = pipeline_fixture()
        # Four fitting cases are required by the optimizer; preserve frozen
        # instrument groups and the existing validation/test fixture episodes.
        for original, sid in ((base[0], 3), (base[1], 4)):
            base.append(dict(deepcopy(original), scenario_id=sid))
        base.sort(key=lambda c: c['scenario_id'])
        fit_ids = sorted(c['scenario_id'] for c in base if c['split'] != 'test')
        splits = {s: dict(scenarios=sum(c['split'] == s for c in base),
                           scenario_ids=[c['scenario_id'] for c in base if c['split'] == s],
                           groups=sorted({c['group'] for c in base if c['split'] == s})) for s in context.SPLITS}
        entry.update(fitted_scenario_ids=fit_ids, splits=deepcopy(splits))
        report['entry'] = dict(fitted_scenario_ids=fit_ids, splits=deepcopy(splits))
        preparation = dict(target_pairs=407, eligible_pairs=len(base), user_skipped=[54, 85],
                           automatic_order_execution_allowed=False)
        loader = lambda root, case, tf: daily if tf == 'daily' else hourly
        actual_build, actual_evaluate = context.build_cases, context.evaluate_pipeline
        with tempfile.TemporaryDirectory() as directory:
            collection = Path(directory)
            market = collection/'training/market_v2'
            market.mkdir(parents=True)
            entry_path = market/'scenario_model.json'
            entry_path.write_text(json.dumps(entry), encoding='utf-8')
            report['model_sha256'] = {'scenario_model.json': context.digest(entry_path)}
            report_path = market/'training_report.json'
            report_path.write_text(json.dumps(report), encoding='utf-8')
            original_entry, original_report = entry_path.read_bytes(), report_path.read_bytes()
            with patch.object(context, 'load_prepared_cases', return_value=(base, preparation, provenance)), \
                 patch.object(context, 'load_reviews', return_value=(reviews, provenance['review_sources_sha256'])), \
                 patch.object(context, 'build_cases', side_effect=lambda b, c: actual_build(b, c, history_loader=loader)), \
                 patch.object(context, 'evaluate_pipeline', side_effect=lambda *a, **kw: actual_evaluate(*a, **kw, history_loader=loader)):
                context.run(collection)
            folder = collection/context.OUTPUT
            payload = (folder/'cases.jsonl').read_bytes()
            self.assertNotIn(b'\r\n', payload)
            expected = hashlib.sha256(payload).hexdigest()
            for name in ('model.json', 'training_report.json', 'pipeline_evaluation.json'):
                artifact = json.loads((folder/name).read_text(encoding='utf-8'))
                saved = artifact['context_provenance' if name == 'pipeline_evaluation.json' else 'provenance']
                self.assertEqual(saved['context_cases_sha256'], expected)
            self.assertEqual(len(payload.decode('utf-8').splitlines()), len(base))
            self.assertEqual(entry_path.read_bytes(), original_entry)
            self.assertEqual(report_path.read_bytes(), original_report)


if __name__ == '__main__':
    unittest.main()
