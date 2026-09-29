"""Entry extraction must preserve author provenance and causal D1/H1 cutoffs."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from knowledge_bot.prepare_scenario_training import daily_alignment
from knowledge_bot import train_scenario_entry_model as trainer


class ScenarioPreparationTests(unittest.TestCase):
    def fixture(self, root, *, after_close=True, daily_x=230.):
        (root/'training').mkdir()
        (root/'images').mkdir()
        (root/'visual_analysis').mkdir()
        image = root/'images/1H_1.jpg'
        image.write_bytes(b'synthetic image; OHLC fixture supplied separately')
        rows = [dict(scenario_id=i, instrument='COIN', direction='long' if i == 1 else 'unclear',
                     image_files=[f'images/1H_{i}.jpg'], image_sha256={}) for i in range(1, 410)]
        rows[0]['image_sha256']['images/1H_1.jpg'] = trainer.digest(image)
        source = root/'visual_analysis/scenario_analysis.jsonl'
        source.write_text(''.join(json.dumps(r)+'\n' for r in rows), encoding='utf-8')
        review = dict(scenario_id=1, reviewed=True, usable_for_entry_training=True,
                      decision_timing='after_bar_close' if after_close else 'before_bar_open')
        review_path = root/'training/anchor_reviews_fixture.jsonl'
        review_path.write_text(json.dumps(review)+'\n', encoding='utf-8')
        bars = [dict(x=i*10, open=100.+i*.1, high=101.+i*.1, low=99.+i*.1, close=100.5+i*.1)
                for i in range(25)]
        module = Path(trainer.__file__).parent
        return dict(entry_training_review_complete=True, annotation_sha256=trainer.digest(source),
                    feature_code_sha256=trainer.digest(module/'scenario_image_features.py'),
                    preparation_code_sha256=trainer.digest(module/'prepare_scenario_training.py'),
                    review_sources_sha256={'training/anchor_reviews_fixture.jsonl': trainer.digest(review_path)},
                    records=[dict(scenario_id=1, timeframe='1H', usable=True,
                                  source_sha256=trainer.digest(image), anchor_review=review,
                                  daily_close_verified=True, daily_close_bar_x=daily_x,
                                  signal_x=240 if after_close else 250, bar_pitch_px=10.,
                                  bars=bars, level=102., entry_state_resolution='synthetic')])

    def test_post_daily_cutoff_and_after_close_inclusion(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            audit = self.fixture(root)
            cases, ledger = trainer.build_cases(root, audit)
            self.assertEqual(len(ledger), 409)
            self.assertEqual(cases[0]['offsets'], [1, 0])
            self.assertEqual(cases[0]['last_feature_bar_x'], 240)
            # Immediate D1-close entries provide one positive, not fabricated waits.
            audit['records'][0]['daily_close_bar_x'] = 240
            self.assertEqual(trainer.build_cases(root, audit)[0][0]['offsets'], [0])

    def test_intrabar_proxy_cannot_contain_the_entry_bar(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            audit = self.fixture(root, after_close=False)
            self.assertEqual(trainer.build_cases(root, audit)[0][0]['offsets'], [1, 0])
            audit['records'][0]['signal_x'] = 240
            with self.assertRaisesRegex(ValueError, 'future OHLC'):
                trainer.build_cases(root, audit)

    def test_unusable_records_are_exclusions_not_missing_hash_errors(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            audit = self.fixture(root)
            audit['records'] = [dict(scenario_id=1, timeframe='1H', usable=False,
                                     reason='entry_anchor_not_yet_read')]
            cases, ledger = trainer.build_cases(root, audit)
            self.assertEqual(cases, [])
            self.assertEqual(ledger[0]['reason'], 'entry_anchor_not_yet_read')

    def test_new_or_changed_review_and_changed_preparation_invalidate_audit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            audit = self.fixture(root)
            altered = deepcopy(audit)
            altered['preparation_code_sha256'] = 'stale'
            with self.assertRaisesRegex(ValueError, 'preparation code'):
                trainer.build_cases(root, altered)
            (root/'training/anchor_alignment_supplement_new.jsonl').write_text('{}\n', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'anchor reviews'):
                trainer.build_cases(root, audit)

    def test_explicit_failed_alignment_never_falls_back_to_legacy_text(self):
        review = dict(scenario_id=146, daily_close_verified=False, daily_close_bar_x=None,
                      decision_bar_x=564, decision_timing='after_bar_close')
        self.assertIsNone(daily_alignment(review)[0])


if __name__ == '__main__':
    unittest.main()
