"""Test path setup for repository-level harness modules.

When pytest runs from the repo root, the OUTER ``receipt_embeddings/``
directory (which holds pyproject.toml, not code) is importable as an
empty namespace package and shadows the real package one level down. Put
the package root first on sys.path and evict any cached namespace stub
so both suites collect from the repo root in any order.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))
_PACKAGE_ROOT = str(REPOSITORY_ROOT / "receipt_embeddings")
if _PACKAGE_ROOT in sys.path:
    sys.path.remove(_PACKAGE_ROOT)
sys.path.insert(0, _PACKAGE_ROOT)

_cached = sys.modules.get("receipt_embeddings")
if _cached is not None and getattr(_cached, "__file__", None) is None:
    for _name in [
        name
        for name in list(sys.modules)
        if name == "receipt_embeddings"
        or name.startswith("receipt_embeddings.")
    ]:
        del sys.modules[_name]
