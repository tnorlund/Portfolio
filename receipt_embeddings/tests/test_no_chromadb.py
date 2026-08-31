"""receipt_embeddings must not import chromadb (Round B gate)."""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "receipt_embeddings"


def test_package_has_no_chromadb_import() -> None:
    offenders: list[str] = []
    for path in PACKAGE_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "chromadb" or alias.name.startswith(
                        "chromadb."
                    ):
                        offenders.append(str(path.relative_to(PACKAGE_ROOT)))
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module == "chromadb" or module.startswith("chromadb."):
                    offenders.append(str(path.relative_to(PACKAGE_ROOT)))
    assert offenders == []
