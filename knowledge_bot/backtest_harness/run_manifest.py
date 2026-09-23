from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .safety import SAFETY_FLAGS, SAFE_SCAFFOLD_SCOPE


def create_scaffold_manifest(source: str = "manual") -> dict[str, Any]:
    generated_at = datetime.now(timezone.utc).isoformat()
    return {
        "run_id": f"SCFS-{generated_at.replace('-', '').replace(':', '').replace('+00:00', 'Z').replace('.', '')}-{uuid4().hex[:8]}",
        "generated_at": generated_at,
        "source": source,
        "mode": "safe_empty_scaffold",
        "scope": SAFE_SCAFFOLD_SCOPE,
        "frozen_artifact_versions": {},
        **SAFETY_FLAGS,
    }
