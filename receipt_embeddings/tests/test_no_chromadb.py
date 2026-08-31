"""Guard: receipt_embeddings must never import chromadb.

The package exists to be the backend-neutral home for embedding interfaces
and the relocated formatting/openai subpackages (docs/chroma-removal/SPEC.md
§6 F); a chromadb import anywhere in it would defeat the split.
"""

import ast
from pathlib import Path

import pytest

_PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "receipt_embeddings"


@pytest.mark.unit
def test_no_chromadb_imports_anywhere_in_package() -> None:
    violations = []
    for path in sorted(_PACKAGE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if name == "chromadb" or name.startswith("chromadb."):
                    violations.append(f"{path}:{node.lineno}")

    assert not violations, "chromadb imports found: " + ", ".join(violations)
