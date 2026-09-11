"""Synthetic coverage and work-budget checks for evidence graph retrieval."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from knowledge_core.db import canonical_json, connect
from knowledge_core.importer import build_core
from knowledge_core.query import trace
from knowledge_core.sources import read_json
from test_knowledge_core_importer import source_fixture
from test_knowledge_core_legacy import write_jsonl


class KnowledgeQueryTests(unittest.TestCase):
    def test_complete_connected_graph_has_bounded_database_work(self):
        # Every frame belongs to its own segment and OCR record. One source
        # unit references them all, so tracing it must retain the whole graph.
        count = 160
        with tempfile.TemporaryDirectory(prefix="knowledge-core-query-test-") as temp:
            parent = Path(temp).resolve()
            source = parent / "source"
            lecture = source_fixture(source)
            transcript = read_json(lecture / "transcript.json")
            transcript["segments"] = [
                {"id": i, "start": i, "end": i + 1, "text": f"Synthetic segment {i}"}
                for i in range(count)
            ]
            (lecture / "transcript.json").write_text(canonical_json(transcript), encoding="utf-8")
            frames = []
            for i in range(count):
                relative = f"frames/{i + 1}.jpg"
                (lecture / relative).write_bytes(f"synthetic-image-{i}".encode())
                frames.append({"path": relative, "time": i + 0.5,
                               "linked_segment_index": i, "ocr_text": f"Synthetic annotation {i}"})
            (lecture / "visual_index.json").write_text(canonical_json({"frames": frames}), encoding="utf-8")
            unit = {"unit_id": "U1", "lecture_title": "lecture", "segment_index": 0,
                    "start_sec": 0, "end_sec": count, "text": "Synthetic graph source",
                    "frames": [{"path": "lecture/" + frame["path"]} for frame in frames]}
            write_jsonl(source / "_knowledge_base/structured/lecture_pass/lectures/lecture_001/source_multimodal_units.jsonl",
                        [unit])
            dataset = parent / "dataset"
            build_core(source, dataset)

            progress_calls = 0

            def progress():
                nonlocal progress_calls
                progress_calls += 1
                # SQLite instruction counts avoid wall-clock assertions and
                # catch repeated full-table scans as this graph grows.
                return int(progress_calls > 1000)

            def instrumented_connect(*args, **kwargs):
                conn = connect(*args, **kwargs)
                conn.set_progress_handler(progress, 1000)
                return conn

            with patch("knowledge_core.query.connect", instrumented_connect):
                result = trace(dataset, "R1")
            self.assertLess(progress_calls, 1000)
            self.assertEqual(len(result["evidence"]), 1 + 3 * count)
            self.assertEqual(len(result["evidence_links"]), 1 + 3 * count)
            for kind in ("frame", "ocr", "transcript_segment"):
                self.assertEqual(sum(e["kind"] == kind for e in result["evidence"]), count)
            self.assertEqual(len({e["evidence_id"] for e in result["evidence"]}), len(result["evidence"]))
            self.assertEqual({node["legacy_id"] for node in result["knowledge"]}, {"R1", "C1", "G1"})


if __name__ == "__main__":
    unittest.main()
