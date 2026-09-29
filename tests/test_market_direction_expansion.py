from copy import deepcopy
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from knowledge_bot import train_market_direction as trainer


def fixtures():
    cases = []
    for split, ids in [('train', range(1, 7)), ('validation', range(20, 22)), ('test', range(40, 42))]:
        for sid in ids:
            side = 'long' if sid % 2 else 'short'
            sign = 1 if side == 'long' else -1
            cases.append(dict(scenario_id=sid, group=split.upper(), split=split, direction=side,
                              features={n: sign*(i+1)*.03+(sid%3)*.01 for i,n in enumerate(trainer.PREREQUISITE_FEATURE_NAMES)},
                              entry_label_created=False, observation_close_time_ms=21*trainer.DAY,
                              signal_d1_open_time_ms=20*trainer.DAY))
    return cases, dict(target_pairs=407, user_skipped=[54,85], eligible_direction_scenarios=len(cases), provenance={})


class DirectionExpansionTests(unittest.TestCase):
    def test_test_mutation_never_changes_weights_or_normalization(self):
        cases, report = fixtures()
        first, _ = trainer.fit(cases, report)
        altered = deepcopy(cases)
        for c in altered:
            if c['split'] == 'test':
                c['features'] = {name: 1e7 for name in c['features']}
                c['direction'] = 'long'
        second, _ = trainer.fit(altered, report)
        for field in ('weights','bias','mean','scale','regularization'):
            self.assertEqual(first[field], second[field])
        self.assertEqual(set(first['fitted_scenario_ids']), {1,2,3,4,5,6,20,21})

    def test_invalid_scope_leakage_and_forming_daily_bar_are_rejected(self):
        cases, report = fixtures()
        for field, value, message in [('group','TRAIN','leaked'),
                                       ('observation_close_time_ms',20*trainer.DAY,'not_closed'),
                                       ('entry_label_created',True,'direction_only')]:
            altered = deepcopy(cases)
            altered[-1][field] = value
            with self.assertRaisesRegex(ValueError, message):
                trainer.validate(altered, report)
        cases[0]['scenario_id'] = 54
        with self.assertRaisesRegex(ValueError, 'excluded'):
            trainer.validate(cases, report)

    def test_pending_entry_can_supply_closed_D1_without_new_entry_label(self):
        annotation = dict(scenario_id=27, direction='long', image_sha256={'images/1D_27.jpg':'daily','images/1H_27.jpg':'hourly'})
        review = dict(scenario_id=27, daily_only_direction_verified=True, no_entry_label_created=True,
                      source_images_sha256=annotation['image_sha256'],source_review_files_sha256={'training/review.jsonl':'review'},
                      closed_daily_anchor_x=400,observation_close_time_ms=21*trainer.DAY,reviewed_direction='long')
        daily = dict(accepted=True,fingerprint={},symbol='ETHUSDT',cache_file='daily.json',cache_file_sha256='cache')
        bars = [dict(open_time_ms=i*trainer.DAY,close_time_ms=(i+1)*trainer.DAY,
                     open=100+i,high=104+i,low=98+i,close=102+i) for i in range(25)]
        with patch.object(trainer,'_check_hashes'), \
             patch.object(trainer,'load_matched_history',return_value=bars), \
             patch.object(trainer,'time_at_x',return_value=20*trainer.DAY), \
             patch.object(trainer,'choose_level',return_value=(120,300,'reviewed')):
            first = trainer.supplemental_case(Path('.'),review,annotation,daily,{'ETH':'train'})
            self.assertFalse(first['entry_label_created'])
            self.assertEqual(first['hourly_entry_status'],'pending_author_or_semantic_clarification')
            self.assertNotIn('decision_time_ms',first)
            for bar in bars[21:]: bar.update(open=-999,high=float('inf'),close=-999)
            self.assertEqual(first,trainer.supplemental_case(Path('.'),review,annotation,daily,{'ETH':'train'}))
            with self.assertRaisesRegex(ValueError,'boundary_mismatch'):
                trainer.supplemental_case(Path('.'),dict(review,observation_close_time_ms=20*trainer.DAY),annotation,daily,{})
            with self.assertRaisesRegex(ValueError,'frozen_partition'):
                trainer.supplemental_case(Path('.'),review,annotation,daily,{})

    def test_hourly_dependent_direction_cannot_be_promoted(self):
        with self.assertRaisesRegex(ValueError,'requires_H1'):
            trainer.supplemental_case(Path('.'),{'daily_only_direction_verified':True},{'scenario_id':307},{},{})
        with self.assertRaisesRegex(ValueError,'must_not_create_entry'):
            trainer.supplemental_case(Path('.'),{'daily_only_direction_verified':True},{'scenario_id':27},{},{})

    def test_ledger_snapshot_cannot_silently_change_pending_scope(self):
        cases, report = fixtures()
        report['pending_pairs']=407-len(cases)
        ids={c['scenario_id'] for c in cases}
        rows=[dict(scenario_id=i,status='eligible' if i in ids else 'pending')
              for i in range(1,410) if i not in (54,85)]
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            path=root/'training/market_v2/ledger.jsonl'
            path.parent.mkdir(parents=True)
            def write(values): path.write_text(''.join(json.dumps(r)+'\n' for r in values),encoding='utf-8')
            write(rows)
            snapshot,checksum=trainer.entry_ledger_snapshot(root,cases,report)
            self.assertEqual(snapshot,rows)
            self.assertEqual(checksum,trainer.digest(path))
            write(rows[:-1])
            with self.assertRaisesRegex(ValueError,'scope_mismatch'):
                trainer.entry_ledger_snapshot(root,cases,report)
            write([dict(r,status='pending') if r['scenario_id']==1 else r for r in rows])
            with self.assertRaisesRegex(ValueError,'eligible_cases_mismatch'):
                trainer.entry_ledger_snapshot(root,cases,report)
            write(rows)
            with self.assertRaisesRegex(ValueError,'pending_count_mismatch'):
                trainer.entry_ledger_snapshot(root,cases,dict(report,pending_pairs=1))


if __name__ == '__main__':
    unittest.main()
