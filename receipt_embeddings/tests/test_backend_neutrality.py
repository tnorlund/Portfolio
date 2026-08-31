"""Architectural tests for the backend-neutral embedding package."""

from __future__ import annotations

import ast
import subprocess
import sys
import tomllib
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PACKAGE_ROOT / "receipt_embeddings"
REPOSITORY_ROOT = PACKAGE_ROOT.parent


def test_package_has_no_chromadb_imports_or_dependency() -> None:
    """The relocated package must remain usable without ChromaDB."""

    violations: list[str] = []
    for path in SOURCE_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            if any(
                module.split(".", 1)[0] == "chromadb" for module in modules
            ):
                violations.append(
                    f"{path.relative_to(PACKAGE_ROOT)}:{node.lineno}"
                )

    project = tomllib.loads(
        (PACKAGE_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    dependencies = project["project"]["dependencies"]

    assert not violations, "chromadb imports: " + ", ".join(violations)
    assert not any(
        dependency.lower().startswith("chromadb")
        for dependency in dependencies
    )


def test_relocated_modules_do_not_load_receipt_chroma() -> None:
    """New import paths are independent of the compatibility package."""

    code = """
import sys
import receipt_embeddings.formatting
import receipt_embeddings.openai
assert not any(
    name == "receipt_chroma" or name.startswith("receipt_chroma.")
    for name in sys.modules
)
"""
    subprocess.run([sys.executable, "-c", code], check=True)


def test_receipt_chroma_container_installs_include_local_package() -> None:
    """Container builds cannot resolve the unpublished sibling from PyPI."""

    failures: list[str] = []
    for path in (REPOSITORY_ROOT / "infra").rglob("Dockerfile*"):
        source = path.read_text(encoding="utf-8")
        if "COPY receipt_chroma/" not in source:
            continue
        if "COPY receipt_embeddings/ /tmp/receipt_embeddings/" not in source:
            failures.append(
                f"{path.relative_to(REPOSITORY_ROOT)}: missing COPY"
            )
        if "pip install --no-cache-dir /tmp/receipt_embeddings" not in source:
            failures.append(
                f"{path.relative_to(REPOSITORY_ROOT)}: missing install"
            )

    assert not failures, ", ".join(failures)
