"""Shared resolution of the data root for the trader-brain runtime.

When running from source the data root is the repository root (the parent of
the ``knowledge_bot`` package). When running as a PyInstaller-frozen desktop
client the package lives inside the bundle's ``_internal`` directory, which is
not a sensible place to read/write live data. In that case the data root is the
directory that contains the executable, so ``_knowledge_base`` sits next to the
``.exe`` and is shared by every runtime module.
"""

from __future__ import annotations

import sys
from pathlib import Path


def data_root(module_file: str) -> Path:
    """Return the directory that holds ``_knowledge_base`` for runtime data.

    ``module_file`` is the importing module's ``__file__`` and is used as the
    source-checkout fallback (its ``parents[1]`` is the repository root).
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(module_file).resolve().parents[1]
