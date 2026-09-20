"""Guard the glyph MCP Lambda image contract: the default glyphstudio
install must stay resolvable without local monorepo packages.

The Lambda Dockerfile (infra/glyph_mcp_lambda/lambdas/Dockerfile) runs
``pip install tools/glyph-studio/py`` against PyPI only. A mandatory
dependency on receipt-embeddings (a local package, not on PyPI) breaks
that build — it must stay behind the ``sections`` extra.
"""

import tomllib
from pathlib import Path

_PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _project() -> dict:
    return tomllib.loads(_PYPROJECT.read_text())["project"]


def test_receipt_embeddings_is_not_a_default_dependency():
    default_deps = " ".join(_project()["dependencies"]).replace("_", "-")
    assert "receipt-embeddings" not in default_deps


def test_receipt_embeddings_stays_available_via_sections_extra():
    extras = _project()["optional-dependencies"]["sections"]
    assert any("receipt-embeddings" in dep for dep in extras)
