"""Frozen pipeline must infer H1 side from D1 and preserve test causality."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from knowledge_bot import evaluate_market_scenario_pipeline as pipeline
from knowledge_bot import scenario_direction_model as direction_module
from knowledge_bot import scenario_image_features as image_features
from knowledge_bot import scenario_prerequisites as prerequisites
from knowledge_bot import train_market_scenario_models as trainer


def bars(start, count, interval):
    return [dict(open_time_ms=start+i*interval, close_time_ms=start+(i+1)*interval,
                 open=99+i*.1, high=99.3+i*.1, low=98.8+i*.1, close=99.1+i*.1)
            for i in range(count)]


def fixtures():
    """Hand-authored weights, not fitted models, isolate evaluation behavior."""
    day, hour = pipeline.DAY, pipeline.HOUR
    daily = bars(0, 28, day)
    hourly = bars(24*day-30*hour, 60, hour)
    cutoff = 24*day+3*hour
    level = 100.
    cases = []
    for sid, split, group, side in [(1, 'train', 'BTC', 'long'), (2, 'train', 'BTC', 'short'),
                                   (10, 'validation', 'ETH', 'long'),
                                   (40, 'test', 'SOL', 'long'), (41, 'test', 'SOL', 'short')]:
        times = [cutoff-hour, cutoff]
        cases.append(dict(scenario_id=sid, split=split, group=group, direction=side,
                          direction_daily_only_eligible=True, level=level,
                          direction_features=prerequisites.prerequisite_features_from_ohlc(
                              pipeline.closed_prefix(daily, 24*day, day), level),
                          states=[pipeline.features_for_entry(pipeline.closed_prefix(hourly,t,hour),level,side)
                                  for t in times],
                          state_close_times_ms=times, offsets=[1, 0], decision_time_ms=cutoff,
                          signal_d1_open_time_ms=23*day, latest_closed_d1_time_ms=24*day,
                          entry_resolution='pre_hour_proxy_not_exact_intrabar_entry',
                          earlier_states_are_failed_trades=False,
                          market_symbol=group+'USDT', market_category='linear',
                          source_daily_cache=f'{group}_daily.json', source_daily_cache_sha256='fixture_daily',
                          source_hourly_cache=f'{group}_hourly.json', source_hourly_cache_sha256='fixture_hourly'))
    preparation = dict(target_pairs=407, eligible_pairs=len(cases), user_skipped=[54, 85],
                       automatic_order_execution_allowed=False)
    provenance = dict(synthetic_fixture='fixed', review_sources_sha256={})
    coverage = {key:preparation[key] for key in ('target_pairs','eligible_pairs','user_skipped')}
    common = dict(schema_version=1, research_only=True, automatic_order_execution_allowed=False,
                  provenance=provenance, corpus_coverage=coverage, fitted_scenario_ids=[1, 2, 10])
    names = list(prerequisites.PREREQUISITE_FEATURE_NAMES)
    direction = dict(**common, model_kind=direction_module.MODEL_KIND,
                     feature_schema_version=prerequisites.FEATURE_SCHEMA_VERSION,
                     feature_names=names, classes=['short','long'], mean=[0.]*len(names),
                     scale=[1.]*len(names), weights=[0.]*len(names), bias=-1.)
    names = list(image_features.FEATURE_NAMES)
    weights = [float(name == 'bar_0_close_from_level_tr') for name in names]
    entry = dict(**common, model_kind=pipeline.READINESS_MODEL_KIND,
                 feature_schema_version=image_features.FEATURE_SCHEMA_VERSION,
                 feature_names=names, mean=[0.]*len(names), scale=[1.]*len(names), weights=weights, bias=0.,
                 threshold=None)
    report = dict(trained=True, **coverage, provenance=provenance)
    for kind, model in (('direction',direction),('entry',entry)):
        model['splits'] = {split:dict(scenario_ids=[c['scenario_id'] for c in cases if c['split']==split],
                                      groups=sorted({c['group'] for c in cases if c['split']==split}),
                                      scenarios=sum(c['split']==split for c in cases))
                           for split in pipeline.SPLITS}
        report[kind] = dict(fitted_scenario_ids=[1,2,10], splits=deepcopy(model['splits']))
    reviews = {case['scenario_id']:dict(wait_interval_verified=True,
                                        no_other_valid_entries_in_wait_interval=True) for case in cases}
    return cases, preparation, provenance, direction, entry, report, reviews, daily, hourly


def evaluate(data, **kwargs):
    cases, prep, provenance, direction, entry, report, reviews, daily, hourly = data
    return pipeline.evaluate_pipeline(cases, prep, provenance, direction, entry, report,
        collection=Path('.'), reviews=reviews,
        history_loader=lambda root,case,timeframe: daily if timeframe=='daily' else hourly, **kwargs)


class MarketPipelineTests(unittest.TestCase):
    def test_predicted_side_not_author_features_drives_entry_score(self):
        data = fixtures()
        cases, _, _, _, entry, _, _, _, hourly = data
        original = deepcopy(data)
        with patch.object(trainer,'fit_direction_classifier',side_effect=AssertionError('no fitting')), \
             patch.object(trainer,'fit_entry_readiness',side_effect=AssertionError('no fitting')):
            result = evaluate(data)
        self.assertEqual(data, original)
        self.assertEqual(result['scenario_ids'], [40,41])
        self.assertEqual(result['direction']['correct'], 1)
        self.assertEqual(result['entry_at_demonstrated_state']['score_greater_than_or_equal_to_zero'],0)
        self.assertEqual(result['joint']['correct_direction_and_nonnegative_entry_score'],0)
        case = cases[-2]
        self.assertGreater(pipeline.score_features(case['states'][-1],entry),0)
        expected = pipeline.score_features(pipeline.features_for_entry(
            pipeline.closed_prefix(hourly,case['decision_time_ms'],pipeline.HOUR),case['level'],'short'),entry)
        detail = result['details'][0]
        self.assertLess(expected,0)
        self.assertEqual(detail['state_results'][-1]['raw_entry_score'], expected)
        self.assertTrue(all(s['features_direction']=='short' for d in result['details'] for s in d['state_results']))
        # The stored author-side vectors cannot silently become inference input.
        changed = deepcopy(data)
        for c in changed[0]:
            c['states'] = [{name:1e9 for name in image_features.FEATURE_NAMES} for _ in c['states']]
        self.assertEqual(evaluate(changed)['details'], result['details'])

    def test_future_daily_and_execution_hour_ohlc_cannot_change_results(self):
        data = fixtures()
        before = evaluate(data)
        changed = deepcopy(data)
        decision = changed[0][-1]['decision_time_ms']
        for bar in changed[-2]:
            if bar['close_time_ms']>24*pipeline.DAY:
                bar.update(open=1e8,high=1e10,low=.001,close=1e9)
        for bar in changed[-1]:
            if bar['close_time_ms']>decision:
                bar.update(open=1e8,high=1e10,low=.001,close=1e9)
        after = evaluate(changed)
        self.assertEqual(before['details'], after['details'])
        self.assertEqual(before['joint'], after['joint'])
        for detail in before['details']:
            for state in detail['state_results']:
                self.assertEqual(state['last_used_hourly_close_time_ms'],state['state_close_time_ms'])
                self.assertLess(state['last_used_hourly_open_time_ms'],state['state_close_time_ms'])

    def test_zero_score_is_entry_and_joint_and_wait_metrics_are_separate(self):
        data = fixtures()
        data[4]['weights'] = [0.]*len(data[4]['weights'])
        result = evaluate(data)
        self.assertEqual(result['direction']['correct'],1)
        self.assertEqual(result['entry_at_demonstrated_state']['score_greater_than_or_equal_to_zero'],2)
        self.assertEqual(result['joint']['correct_direction_and_nonnegative_entry_score'],1)
        self.assertEqual(result['verified_waits'],dict(states=2,false_positives=2,true_negatives=0,false_positive_rate=1.))
        self.assertFalse(result['expanded_direction_model_used'])
        self.assertFalse(result['weights_fitted'])
        self.assertFalse(result['profitability_evaluated'])

    def test_invented_waits_or_changed_daily_vectors_are_rejected(self):
        data = fixtures()
        data[6][40]['no_other_valid_entries_in_wait_interval'] = False
        with self.assertRaisesRegex(ValueError,'wait_states_not_explicitly_verified'):
            evaluate(data)
        data = fixtures()
        data[0][-1]['direction_features'][prerequisites.PREREQUISITE_FEATURE_NAMES[0]] += 1
        with self.assertRaisesRegex(ValueError,'recomputed_daily_features_disagree'):
            evaluate(data)

    def test_changed_provenance_fitted_ids_partition_or_instrument_leak_is_rejected(self):
        data = fixtures()
        data[3]['provenance'] = {'other':'experiment'}
        with self.assertRaisesRegex(ValueError,'provenance_mismatch'):
            evaluate(data)
        data = fixtures()
        data[4]['fitted_scenario_ids'] = [1,2,10,40]
        with self.assertRaisesRegex(ValueError,'fitted_ids'):
            evaluate(data)
        data = fixtures()
        data[3]['splits']['test']['scenario_ids'] = [40]
        with self.assertRaisesRegex(ValueError,'partition_changed'):
            evaluate(data)
        data = fixtures()
        data[0][-1]['group'] = 'BTC'
        with self.assertRaisesRegex(ValueError,'leaked_across_partitions'):
            evaluate(data)

    def test_test_intersection_is_fixed_by_both_saved_models(self):
        data = fixtures()
        data[0][-1]['direction_daily_only_eligible'] = False
        for artifact in (data[3],data[5]['direction']):
            artifact['splits']['test']['scenario_ids'] = [40]
            artifact['splits']['test']['scenarios'] = 1
        result = evaluate(data)
        self.assertEqual(result['scenario_ids'],[40])
        self.assertEqual(result['verified_waits']['states'],1)

    def test_market_cache_hash_checksum_identity_and_paths_are_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = fixtures()[0][-1]
            path = root / case['source_hourly_cache']
            payload = dict(identity=dict(symbol=case['market_symbol'],category='linear',interval='1h'),
                           bars=fixtures()[-1],exchange_snapshot_ms=30*pipeline.DAY)
            def write_payload():
                payload.pop('cache_sha256',None)
                payload['cache_sha256'] = pipeline._hash(payload)
                path.write_text(json.dumps(payload),encoding='utf8')
                case['source_hourly_cache_sha256'] = pipeline.digest(path)
            write_payload()
            self.assertEqual(pipeline.load_case_history(root,case,'hourly'),payload['bars'])
            path.write_text('{}',encoding='utf8')
            with self.assertRaisesRegex(ValueError,'cache_changed'):
                pipeline.load_case_history(root,case,'hourly')
            write_payload()
            corrupted = json.loads(path.read_text(encoding='utf8'))
            corrupted['bars'][0]['close'] += .001
            path.write_text(json.dumps(corrupted),encoding='utf8')
            case['source_hourly_cache_sha256'] = pipeline.digest(path)
            with self.assertRaisesRegex(ValueError,'checksum_invalid'):
                pipeline.load_case_history(root,case,'hourly')
            payload['identity']['symbol'] = 'OTHERUSDT'
            write_payload()
            with self.assertRaisesRegex(ValueError,'identity_mismatch'):
                pipeline.load_case_history(root,case,'hourly')
            case['source_hourly_cache'] = '../outside.json'
            with self.assertRaisesRegex(ValueError,'outside_collection'):
                pipeline.load_case_history(root,case,'hourly')

    def test_run_rejects_tampered_saved_weights_and_only_writes_evaluation(self):
        data = fixtures()
        cases,prep,provenance,direction,entry,report,reviews,_,_ = data
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root/'training/market_v2'
            folder.mkdir(parents=True)
            for name,artifact in [('direction_model.json',direction),('scenario_model.json',entry)]:
                (folder/name).write_text(json.dumps(artifact),encoding='utf8')
            report['model_sha256'] = {name:pipeline.digest(folder/name) for name in pipeline.MODEL_FILES.values()}
            (folder/'training_report.json').write_text(json.dumps(report),encoding='utf8')
            originals = {p.name:p.read_bytes() for p in folder.iterdir()}
            with patch.object(pipeline,'load_prepared_cases',return_value=(cases,prep,provenance)), \
                 patch.object(pipeline,'load_reviews',return_value=(reviews,{})), \
                 patch.object(pipeline,'evaluate_pipeline',return_value={'synthetic_evaluation':True}):
                pipeline.run(root)
                self.assertEqual({p.name for p in folder.iterdir()},set(originals)|{'pipeline_evaluation.json'})
                self.assertTrue(all((folder/name).read_bytes()==raw for name,raw in originals.items()))
                entry['bias'] += 1
                (folder/'scenario_model.json').write_text(json.dumps(entry),encoding='utf8')
                with self.assertRaisesRegex(ValueError,'saved_model_hash_mismatch'):
                    pipeline.run(root)


if __name__ == '__main__':
    unittest.main()
