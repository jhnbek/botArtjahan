from __future__ import annotations

from typing import Any


SAFETY_FLAGS: dict[str, bool] = {
    "historical_data_loading_allowed": False,
    "detector_execution_allowed": False,
    "pnl_computation_allowed": False,
    "execution_allowed": False,
    "runtime_signal_allowed": False,
    "paper_trading_allowed": False,
    "live_trading_allowed": False,
    "backtest_harness_allowed": False,
}

SAFE_SCAFFOLD_SCOPE = (
    "package boundaries, CLI help, run manifest helpers, audit helpers, "
    "prohibited-capability scanning, and fixture-only synthetic data validation only"
)


def scaffold_safety_status() -> dict[str, Any]:
    return {
        "scope": SAFE_SCAFFOLD_SCOPE,
        "implemented_mode": "safe_scaffold_with_fixture_only_validation",
        **SAFETY_FLAGS,
    }


def blocked_operation(operation: str) -> dict[str, Any]:
    return {
        "operation": operation,
        "implemented": False,
        "blocked": True,
        "reason": "safe scaffold only; this operation requires a separate review gate",
        **SAFETY_FLAGS,
    }
