from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from .safety import SAFETY_FLAGS


PROHIBITED_IMPORT_ROOTS = {
    "alpaca",
    "binance",
    "ccxt",
    "ib_insync",
    "krakenex",
    "metatrader5",
    "oandapyv20",
    "websocket",
    "websockets",
}

PROHIBITED_IDENTIFIER_PARTS = {
    "broker_client",
    "exchange_client",
    "paper_trading_loop",
    "live_trading_loop",
    "runtime_signal_service",
    "send_runtime_signal",
    "create_market_order",
    "submit_market_order",
}


def _python_files(root: Path) -> list[Path]:
    if root.is_file() and root.suffix == ".py":
        return [root]
    return sorted(path for path in root.rglob("*.py") if path.is_file() and "__pycache__" not in path.parts)


def _import_root(name: str) -> str:
    return name.split(".", 1)[0].lower()


def scan_path(root: Path) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    files = _python_files(root)
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            findings.append({"path": path.as_posix(), "kind": "syntax_error", "detail": str(exc)})
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if _import_root(alias.name) in PROHIBITED_IMPORT_ROOTS:
                        findings.append({"path": path.as_posix(), "kind": "prohibited_import", "detail": alias.name})
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module and _import_root(module) in PROHIBITED_IMPORT_ROOTS:
                    findings.append({"path": path.as_posix(), "kind": "prohibited_import", "detail": module})
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                lowered = node.name.lower()
                for part in PROHIBITED_IDENTIFIER_PARTS:
                    if part in lowered:
                        findings.append({"path": path.as_posix(), "kind": "prohibited_identifier", "detail": node.name})

    passed = not findings
    return {
        "scanned_path": root.as_posix(),
        "file_count": len(files),
        "finding_count": len(findings),
        "findings": findings,
        "broker_api_import": False,
        "exchange_api_import": False,
        "order_creation": False,
        "paper_trading_loop": False,
        "live_trading_loop": False,
        "runtime_signal_service": False,
        "passed": passed,
        **SAFETY_FLAGS,
    }
