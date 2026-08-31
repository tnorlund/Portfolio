"""Make the inner ``receipt_embeddings`` package importable under pytest.

When pytest runs from the repo root, the OUTER ``receipt_embeddings/``
directory (which holds pyproject.toml, not code) is importable as an empty
namespace package and shadows the real package one level down. Put the
package root first on sys.path and evict any cached namespace stub.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PKG_ROOT = str(Path(__file__).resolve().parent.parent)
if _PKG_ROOT in sys.path:
    sys.path.remove(_PKG_ROOT)
sys.path.insert(0, _PKG_ROOT)

_cached = sys.modules.get("receipt_embeddings")
if _cached is not None and getattr(_cached, "__file__", None) is None:
    for _name in [
        n
        for n in sys.modules
        if n == "receipt_embeddings" or n.startswith("receipt_embeddings.")
    ]:
        del sys.modules[_name]
