from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "knowledge_bot"))

from broker_adapter import (  # noqa: E402
    AuditLog,
    ExecutionEngine,
    ExecutionMode,
    OrderRequest,
    Side,
)


class ExecutionEnginePathTests(unittest.TestCase):
    def test_custom_kill_switch_and_state_paths_are_used(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            kill_switch_path = temp_root / "controls" / "KILL_SWITCH"
            state_path = temp_root / "state" / "execution_state.json"
            audit_path = temp_root / "audit" / "execution_audit.jsonl"
            engine = ExecutionEngine(
                mode=ExecutionMode.PAPER,
                audit=AuditLog(audit_path),
                kill_switch_path=kill_switch_path,
                env=lambda _name: None,
                state_path=state_path,
            )
            order = OrderRequest(
                symbol="BTCUSDT",
                side=Side.BUY,
                quantity=0.001,
                price=50_000.0,
                mode=ExecutionMode.PAPER,
                reason="path regression test",
            )

            engine.engage_kill_switch("test")
            self.assertTrue(kill_switch_path.is_file())
            self.assertTrue(engine.gate.kill_switch_engaged())

            blocked = engine.submit(order)
            self.assertFalse(blocked.accepted)
            self.assertEqual(blocked.reason, "kill_switch_engaged")

            engine.release_kill_switch()
            self.assertFalse(kill_switch_path.exists())
            self.assertFalse(engine.gate.kill_switch_engaged())

            accepted = engine.submit(order)
            self.assertTrue(accepted.accepted)
            self.assertTrue(state_path.is_file())
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["mode"], "paper")
            self.assertFalse(state["kill_switch_engaged"])


if __name__ == "__main__":
    unittest.main()
