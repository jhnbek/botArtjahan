"""Gated execution layer for the trader-brain (desktop client slice D).

This is the ONLY module in the project that models order execution. The
read-only brain (analyzer / packet / casebook / scanner / desktop_app) never
imports it, so analysis can never accidentally place an order. Execution lives
here, behind a deliberately strict ladder:

    paper  ->  shadow  ->  live

* ``paper``  - simulated fills against supplied prices. No network, no money.
* ``shadow`` - would-be orders are computed and audit-logged but NOT sent.
* ``live``   - real orders. HARD-DISABLED by default; refuses unless the
               operator explicitly arms it AND supplies a real adapter.

Every order request passes through a :class:`RiskGate` first (kill-switch,
daily loss limit, max position size, max open positions). Every decision -
accepted, rejected, simulated, or blocked - is written to an append-only audit
log. Nothing here is promotable into the read-only learning loop.

Standard library only.
"""
from __future__ import annotations

import json
import os
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable


from runtime_root import data_root

ROOT = data_root(__file__)
EXEC_DIR = ROOT / "_knowledge_base" / "live_execution"
AUDIT_LOG_PATH = EXEC_DIR / "execution_audit.jsonl"
KILL_SWITCH_PATH = EXEC_DIR / "KILL_SWITCH"
STATE_PATH = EXEC_DIR / "execution_state.json"

# Live trading stays off unless BOTH this env var is set to the exact opt-in
# token AND the caller passes a real live adapter. Two independent locks.
LIVE_ARM_ENV = "TRADER_BRAIN_ARM_LIVE"
LIVE_ARM_TOKEN = "I_UNDERSTAND_THE_RISK"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ExecutionMode(str, Enum):
    PAPER = "paper"
    SHADOW = "shadow"
    LIVE = "live"


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class ExecutionError(RuntimeError):
    """Raised when an order cannot be safely processed."""


class RiskRejection(ExecutionError):
    """Raised when the risk gate rejects an order."""


# --------------------------------------------------------------------------- #
# Order / fill data
# --------------------------------------------------------------------------- #
@dataclass
class OrderRequest:
    symbol: str
    side: Side
    quantity: float
    price: float                 # reference/limit price (also paper fill price)
    mode: ExecutionMode
    reason: str = ""             # why this order exists (e.g. confirmed case id)
    client_order_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def notional(self) -> float:
        return abs(self.quantity) * abs(self.price)


@dataclass
class OrderResult:
    accepted: bool
    mode: ExecutionMode
    symbol: str
    side: Side
    quantity: float
    price: float
    status: str                  # filled / simulated / shadow_logged / rejected / blocked
    client_order_id: str
    broker_order_id: str | None = None
    reason: str = ""
    detail: str = ""
    at: str = field(default_factory=now_iso)

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["side"] = self.side.value
        record["mode"] = self.mode.value
        return record


# --------------------------------------------------------------------------- #
# Risk configuration + gate
# --------------------------------------------------------------------------- #
@dataclass
class RiskLimits:
    max_notional_per_order: float = 100.0
    max_open_positions: int = 3
    max_daily_loss: float = 50.0        # absolute currency units; positive number
    max_position_qty_per_symbol: float = 1.0
    allow_when_kill_switch: bool = False  # never True in practice


@dataclass
class AccountState:
    realized_pnl_today: float = 0.0
    open_positions: dict[str, float] = field(default_factory=dict)  # symbol -> signed qty

    def open_position_count(self) -> int:
        return sum(1 for qty in self.open_positions.values() if abs(qty) > 1e-12)


class RiskGate:
    """Pre-trade risk checks. Pure, deterministic, no side effects."""

    def __init__(self, limits: RiskLimits, kill_switch_path: Path = KILL_SWITCH_PATH):
        self.limits = limits
        self.kill_switch_path = kill_switch_path

    def kill_switch_engaged(self) -> bool:
        return self.kill_switch_path.exists()

    def check(self, order: OrderRequest, account: AccountState) -> tuple[bool, str]:
        """Return (allowed, reason). reason explains a rejection."""
        if self.kill_switch_engaged() and not self.limits.allow_when_kill_switch:
            return False, "kill_switch_engaged"

        if order.quantity <= 0:
            return False, "non_positive_quantity"

        if order.price <= 0:
            return False, "non_positive_price"

        if order.notional() > self.limits.max_notional_per_order:
            return False, (
                f"notional_exceeds_limit:{order.notional():.4f}>"
                f"{self.limits.max_notional_per_order:.4f}"
            )

        if account.realized_pnl_today <= -abs(self.limits.max_daily_loss):
            return False, (
                f"daily_loss_limit_hit:{account.realized_pnl_today:.4f}<=-"
                f"{abs(self.limits.max_daily_loss):.4f}"
            )

        signed = order.quantity if order.side == Side.BUY else -order.quantity
        projected = account.open_positions.get(order.symbol, 0.0) + signed
        if abs(projected) > self.limits.max_position_qty_per_symbol:
            return False, (
                f"position_qty_exceeds_limit:{abs(projected):.6f}>"
                f"{self.limits.max_position_qty_per_symbol:.6f}"
            )

        opens_new = (
            abs(account.open_positions.get(order.symbol, 0.0)) <= 1e-12
            and abs(projected) > 1e-12
        )
        if opens_new and account.open_position_count() >= self.limits.max_open_positions:
            return False, (
                f"max_open_positions_reached:{account.open_position_count()}>="
                f"{self.limits.max_open_positions}"
            )

        return True, "ok"


# --------------------------------------------------------------------------- #
# Audit log (append-only)
# --------------------------------------------------------------------------- #
class AuditLog:
    def __init__(self, path: Path = AUDIT_LOG_PATH):
        self.path = path

    def write(self, event: str, payload: dict[str, Any]) -> dict[str, Any]:
        record = {"at": now_iso(), "event": event, **payload}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record


# --------------------------------------------------------------------------- #
# Broker adapters
# --------------------------------------------------------------------------- #
class BrokerAdapter(ABC):
    """Interface every execution backend implements."""

    mode: ExecutionMode

    @abstractmethod
    def place(self, order: OrderRequest) -> OrderResult:  # pragma: no cover - interface
        ...


class PaperBrokerAdapter(BrokerAdapter):
    """Simulated fills at the order's reference price. No network, no money."""

    mode = ExecutionMode.PAPER

    def place(self, order: OrderRequest) -> OrderResult:
        return OrderResult(
            accepted=True,
            mode=self.mode,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            price=order.price,
            status="simulated",
            client_order_id=order.client_order_id,
            broker_order_id=f"paper-{order.client_order_id[:12]}",
            reason=order.reason,
            detail="paper fill at reference price",
        )


class ShadowBrokerAdapter(BrokerAdapter):
    """Computes the would-be order and logs it, but never sends it."""

    mode = ExecutionMode.SHADOW

    def place(self, order: OrderRequest) -> OrderResult:
        return OrderResult(
            accepted=True,
            mode=self.mode,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            price=order.price,
            status="shadow_logged",
            client_order_id=order.client_order_id,
            broker_order_id=None,
            reason=order.reason,
            detail="shadow mode: order computed and logged, NOT sent to any venue",
        )


class LiveBrokerAdapter(BrokerAdapter):
    """Placeholder for a real venue adapter.

    This base class intentionally refuses to trade. A real implementation must
    subclass it and override :meth:`_submit` to talk to a venue. Even then the
    :class:`ExecutionEngine` only routes here when live trading is armed.
    """

    mode = ExecutionMode.LIVE

    def place(self, order: OrderRequest) -> OrderResult:
        broker_order_id = self._submit(order)
        return OrderResult(
            accepted=True,
            mode=self.mode,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            price=order.price,
            status="filled",
            client_order_id=order.client_order_id,
            broker_order_id=broker_order_id,
            reason=order.reason,
            detail="live order submitted",
        )

    def _submit(self, order: OrderRequest) -> str:  # pragma: no cover - must override
        raise ExecutionError(
            "LiveBrokerAdapter is a stub: subclass it and implement _submit() "
            "against a real venue before any live order can be sent."
        )


# --------------------------------------------------------------------------- #
# Execution engine: gate -> route -> audit
# --------------------------------------------------------------------------- #
class ExecutionEngine:
    """Routes orders through the risk gate to the active adapter, audit-logging
    every decision and updating paper/shadow account state.

    Live routing requires TWO independent locks:
      1. environment opt-in: ``TRADER_BRAIN_ARM_LIVE=I_UNDERSTAND_THE_RISK``
      2. a real (non-stub) :class:`LiveBrokerAdapter` passed as live_adapter.
    """

    def __init__(
        self,
        mode: ExecutionMode = ExecutionMode.PAPER,
        limits: RiskLimits | None = None,
        live_adapter: LiveBrokerAdapter | None = None,
        audit: AuditLog | None = None,
        kill_switch_path: Path = KILL_SWITCH_PATH,
        env: Callable[[str], str | None] = os.environ.get,
        state_path: Path = STATE_PATH,
    ):
        self.mode = ExecutionMode(mode)
        self.limits = limits or RiskLimits()
        self.gate = RiskGate(self.limits, kill_switch_path=kill_switch_path)
        self.audit = audit or AuditLog()
        self.account = AccountState()
        self._env = env
        self.state_path = state_path

        self.adapters: dict[ExecutionMode, BrokerAdapter] = {
            ExecutionMode.PAPER: PaperBrokerAdapter(),
            ExecutionMode.SHADOW: ShadowBrokerAdapter(),
        }
        if live_adapter is not None:
            self.adapters[ExecutionMode.LIVE] = live_adapter

    # -- arming -------------------------------------------------------------- #
    def live_armed(self) -> bool:
        return self._env(LIVE_ARM_ENV) == LIVE_ARM_TOKEN

    def _live_routable(self) -> tuple[bool, str]:
        if self.mode != ExecutionMode.LIVE:
            return True, "ok"
        if not self.live_armed():
            return False, "live_not_armed_env"
        adapter = self.adapters.get(ExecutionMode.LIVE)
        if adapter is None or type(adapter) is LiveBrokerAdapter:
            return False, "no_real_live_adapter"
        return True, "ok"

    # -- account bookkeeping (paper/shadow only) ---------------------------- #
    def _apply_fill(self, order: OrderRequest) -> None:
        signed = order.quantity if order.side == Side.BUY else -order.quantity
        current = self.account.open_positions.get(order.symbol, 0.0)
        self.account.open_positions[order.symbol] = current + signed

    def engage_kill_switch(self, reason: str = "manual") -> None:
        kill_switch_path = self.gate.kill_switch_path
        kill_switch_path.parent.mkdir(parents=True, exist_ok=True)
        kill_switch_path.write_text(
            json.dumps({"engaged_at": now_iso(), "reason": reason}, ensure_ascii=False),
            encoding="utf-8",
        )
        self.audit.write("kill_switch_engaged", {"reason": reason})

    def release_kill_switch(self) -> None:
        kill_switch_path = self.gate.kill_switch_path
        if kill_switch_path.exists():
            kill_switch_path.unlink()
        self.audit.write("kill_switch_released", {})

    # -- main entry point --------------------------------------------------- #
    def submit(self, order: OrderRequest) -> OrderResult:
        # The order's own mode must match the engine mode (no silent upgrades).
        if order.mode != self.mode:
            result = OrderResult(
                accepted=False, mode=self.mode, symbol=order.symbol, side=order.side,
                quantity=order.quantity, price=order.price, status="rejected",
                client_order_id=order.client_order_id, reason="mode_mismatch",
                detail=f"engine={self.mode.value} order={order.mode.value}",
            )
            self.audit.write("order_rejected", result.to_record())
            return result

        # Live double-lock check before anything else.
        routable, route_reason = self._live_routable()
        if not routable:
            result = OrderResult(
                accepted=False, mode=self.mode, symbol=order.symbol, side=order.side,
                quantity=order.quantity, price=order.price, status="blocked",
                client_order_id=order.client_order_id, reason=route_reason,
                detail="live trading is locked; order not routed",
            )
            self.audit.write("order_blocked", result.to_record())
            return result

        # Risk gate.
        allowed, reason = self.gate.check(order, self.account)
        if not allowed:
            result = OrderResult(
                accepted=False, mode=self.mode, symbol=order.symbol, side=order.side,
                quantity=order.quantity, price=order.price, status="rejected",
                client_order_id=order.client_order_id, reason=reason,
                detail="risk gate rejected the order",
            )
            self.audit.write("order_rejected", result.to_record())
            return result

        adapter = self.adapters.get(self.mode)
        if adapter is None:
            result = OrderResult(
                accepted=False, mode=self.mode, symbol=order.symbol, side=order.side,
                quantity=order.quantity, price=order.price, status="blocked",
                client_order_id=order.client_order_id, reason="no_adapter_for_mode",
                detail=f"no adapter registered for {self.mode.value}",
            )
            self.audit.write("order_blocked", result.to_record())
            return result

        result = adapter.place(order)
        # Only paper/shadow update local bookkeeping; live PnL comes from venue.
        if result.accepted and self.mode in (ExecutionMode.PAPER, ExecutionMode.SHADOW):
            self._apply_fill(order)
        self.audit.write("order_executed", result.to_record())
        self._write_state()
        return result

    def _write_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps({
            "updated_at": now_iso(),
            "mode": self.mode.value,
            "live_armed": self.live_armed(),
            "kill_switch_engaged": self.gate.kill_switch_engaged(),
            "realized_pnl_today": self.account.realized_pnl_today,
            "open_positions": self.account.open_positions,
            "limits": asdict(self.limits),
        }, ensure_ascii=False, indent=2), encoding="utf-8")


__all__ = [
    "ExecutionMode", "Side", "OrderRequest", "OrderResult",
    "RiskLimits", "AccountState", "RiskGate", "AuditLog",
    "BrokerAdapter", "PaperBrokerAdapter", "ShadowBrokerAdapter", "LiveBrokerAdapter",
    "ExecutionEngine", "ExecutionError", "RiskRejection",
    "LIVE_ARM_ENV", "LIVE_ARM_TOKEN",
]
