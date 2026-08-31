"""Contract tests for the relocated embedding.formatting/.openai shims.

``receipt_chroma.embedding.formatting`` and ``receipt_chroma.embedding.openai``
moved to ``receipt_embeddings`` (docs/chroma-removal/SPEC.md §6 F). The old
paths are back-compat shims; these tests pin the shim contract: every old-path
import resolves to the very same objects as the new path.
"""

from importlib import import_module

import pytest

_SUBMODULE_PAIRS = [
    (
        "receipt_chroma.embedding.formatting.line_format",
        "receipt_embeddings.formatting.line_format",
    ),
    (
        "receipt_chroma.embedding.formatting.receipt_rows",
        "receipt_embeddings.formatting.receipt_rows",
    ),
    (
        "receipt_chroma.embedding.formatting.word_format",
        "receipt_embeddings.formatting.word_format",
    ),
    (
        "receipt_chroma.embedding.openai.batch_status",
        "receipt_embeddings.openai.batch_status",
    ),
    (
        "receipt_chroma.embedding.openai.helpers",
        "receipt_embeddings.openai.helpers",
    ),
    (
        "receipt_chroma.embedding.openai.poll",
        "receipt_embeddings.openai.poll",
    ),
    (
        "receipt_chroma.embedding.openai.realtime",
        "receipt_embeddings.openai.realtime",
    ),
    (
        "receipt_chroma.embedding.openai.submit",
        "receipt_embeddings.openai.submit",
    ),
]

_PACKAGE_PAIRS = [
    (
        "receipt_chroma.embedding.formatting",
        "receipt_embeddings.formatting",
    ),
    (
        "receipt_chroma.embedding.openai",
        "receipt_embeddings.openai",
    ),
]


@pytest.mark.parametrize(("old_path", "new_path"), _SUBMODULE_PAIRS)
def test_old_submodule_is_new_submodule(old_path: str, new_path: str) -> None:
    """Old submodule paths resolve to the identical relocated modules."""
    assert import_module(old_path) is import_module(new_path)


@pytest.mark.parametrize(("old_path", "new_path"), _PACKAGE_PAIRS)
def test_shim_reexports_every_public_name(
    old_path: str, new_path: str
) -> None:
    """The shim packages re-export the relocated packages' full public API."""
    old_package = import_module(old_path)
    new_package = import_module(new_path)

    assert set(old_package.__all__) == set(new_package.__all__)
    for name in new_package.__all__:
        assert getattr(old_package, name) is getattr(new_package, name)
