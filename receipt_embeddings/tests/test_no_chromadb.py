"""Guard: the Python packages must never import chromadb or receipt_chroma.

``receipt_embeddings`` exists to be the backend-neutral home for embedding
interfaces and the relocated formatting/openai subpackages
(docs/chroma-removal/SPEC.md §6 F); a chromadb import anywhere in it would
defeat the split. The consumers that used to sit on Chroma
(``receipt_agent``, ``receipt_upload``, ``receipt_dynamo_stream``) are
covered by the same guard now that the ``receipt_chroma`` package is gone
(teardown PR #7).
"""

import ast
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FORBIDDEN_MODULES = ("chromadb", "receipt_chroma")
_GUARDED_PACKAGES = (
    _REPO_ROOT / "receipt_embeddings" / "receipt_embeddings",
    _REPO_ROOT / "receipt_agent" / "receipt_agent",
    _REPO_ROOT / "receipt_upload" / "receipt_upload",
    _REPO_ROOT / "receipt_dynamo_stream" / "receipt_dynamo_stream",
)


def _is_forbidden(name: str) -> bool:
    return any(
        name == module or name.startswith(f"{module}.")
        for module in _FORBIDDEN_MODULES
    )


def _forbidden_imports(package_root: Path) -> list[str]:
    violations = []
    for path in sorted(package_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if _is_forbidden(name):
                    violations.append(f"{path}:{node.lineno}")
    return violations


@pytest.mark.unit
@pytest.mark.parametrize(
    "package_root",
    _GUARDED_PACKAGES,
    ids=[root.name for root in _GUARDED_PACKAGES],
)
def test_no_chroma_imports_anywhere_in_package(package_root: Path) -> None:
    if not package_root.is_dir():
        pytest.skip(f"{package_root} not checked out")
    violations = _forbidden_imports(package_root)
    assert not violations, "chroma imports found: " + ", ".join(violations)
