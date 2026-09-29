import unittest
import hashlib
import json
from pathlib import Path
import tempfile

from knowledge_bot.scenario_market_metadata import build_metadata, parse_caption_dates


class CaptionDateTests(unittest.TestCase):
    def test_missing_year_is_not_inferred_from_adjacent_scenarios(self):
        result = parse_caption_dates("22 АПРЕЛЯ")
        self.assertEqual(result["date_precision"], "year_missing")
        self.assertEqual(result["caption_date_candidates"], [])

    def test_date_range_stays_two_search_candidates(self):
        result = parse_caption_dates("8–9 июля 2026")
        self.assertEqual(result["caption_date_candidates"], ["2026-07-08", "2026-07-09"])
        self.assertEqual(result["date_precision"], "day_range")

    def test_two_timeframes_and_abbreviated_year(self):
        result = parse_caption_dates("6 ИЮНЯ 26 (1D); 6 ИЮНЯ 2026 (1H)")
        self.assertEqual(result["caption_date_candidates"], ["2026-06-06"])
        self.assertEqual(parse_caption_dates("8 МАРТА 26")["caption_date_candidates"], ["2026-03-08"])

    def test_invalid_date_and_conflicting_years_are_unresolved(self):
        self.assertEqual(parse_caption_dates("31 февраля 2026")["date_precision"], "invalid_caption_date")
        self.assertEqual(parse_caption_dates("12 февраля 2025; 2026")["date_precision"], "conflicting_years")

    def test_central_user_scope_excludes_both_ids_and_is_fingerprinted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "images").mkdir()
            (root / "visual_analysis").mkdir()
            (root / "training").mkdir()
            rows = []
            for sid in (1, 54, 85):
                hashes = {}
                for timeframe in ("1D", "1H"):
                    relative = f"images/{timeframe}_{sid}.jpg"
                    (root / relative).write_bytes(b"immutable test image bytes")
                    hashes[relative] = hashlib.sha256((root / relative).read_bytes()).hexdigest()
                rows.append(dict(scenario_id=sid, instrument="BTC", date_label="8 мая 2026", image_sha256=hashes))
            (root / "visual_analysis/scenario_analysis.jsonl").write_text("\n".join(json.dumps(row) for row in rows), encoding="utf8")
            legacy, legacy_report = build_metadata(root)
            self.assertEqual([row["scenario_id"] for row in legacy], [1, 54])
            self.assertIsNone(legacy_report["user_scope_sha256"])
            scope = root / "training/user_scope.json"
            scope.write_text(json.dumps(dict(source_scenarios=409, target_scenarios=407, excluded_scenario_ids=[54, 85])), encoding="utf8")
            current, report = build_metadata(root)
            self.assertEqual([row["scenario_id"] for row in current], [1])
            self.assertEqual(report["user_authorized_skip"], [54, 85])
            self.assertEqual(report["user_scope_sha256"], hashlib.sha256(scope.read_bytes()).hexdigest())
            self.assertEqual(current[0]["user_scope_sha256"], report["user_scope_sha256"])


if __name__ == "__main__":
    unittest.main()
