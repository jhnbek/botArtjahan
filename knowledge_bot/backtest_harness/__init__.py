"""Safe scaffold for a future read-only backtest harness.

This package is intentionally non-operational. It exposes safety helpers,
manifest helpers, and a prohibited-capability scanner, but it does not load
historical data, execute detectors over market history, compute PnL, or create
runtime trading signals.
"""

from .safety import SAFETY_FLAGS, blocked_operation, scaffold_safety_status

__all__ = ["SAFETY_FLAGS", "blocked_operation", "scaffold_safety_status"]
