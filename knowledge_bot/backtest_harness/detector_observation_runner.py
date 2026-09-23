from __future__ import annotations

from typing import Any

from .safety import blocked_operation


def component_contract() -> dict[str, Any]:
    return {
        "component": "detector_observation_runner",
        "implemented": False,
        "allowed_now": "metadata description only",
        "blocked_operation": blocked_operation("detector_observation"),
    }
