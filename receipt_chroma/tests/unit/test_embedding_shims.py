"""Old import paths re-export the relocated implementation objects."""

from __future__ import annotations

import receipt_embeddings.formatting as embeddings_formatting
import receipt_embeddings.formatting.line_format as embeddings_line_format
import receipt_embeddings.formatting.receipt_rows as embeddings_receipt_rows
import receipt_embeddings.formatting.word_format as embeddings_word_format
import receipt_embeddings.openai as embeddings_openai
import receipt_embeddings.openai.batch_status as embeddings_batch_status
import receipt_embeddings.openai.helpers as embeddings_helpers
import receipt_embeddings.openai.poll as embeddings_poll
import receipt_embeddings.openai.realtime as embeddings_realtime
import receipt_embeddings.openai.submit as embeddings_submit

import receipt_chroma.embedding.formatting as chroma_formatting
import receipt_chroma.embedding.formatting.line_format as chroma_line_format
import receipt_chroma.embedding.formatting.receipt_rows as chroma_receipt_rows
import receipt_chroma.embedding.formatting.word_format as chroma_word_format
import receipt_chroma.embedding.openai as chroma_openai
import receipt_chroma.embedding.openai.batch_status as chroma_batch_status
import receipt_chroma.embedding.openai.helpers as chroma_helpers
import receipt_chroma.embedding.openai.poll as chroma_poll
import receipt_chroma.embedding.openai.realtime as chroma_realtime
import receipt_chroma.embedding.openai.submit as chroma_submit


def _assert_same_exports(old, new) -> None:
    names = list(old.__all__)
    assert names, f"{old.__name__} has empty __all__"
    for name in names:
        assert getattr(old, name) is getattr(new, name), name


def test_formatting_package_shim_is_same_objects() -> None:
    _assert_same_exports(chroma_formatting, embeddings_formatting)


def test_formatting_submodule_shims_are_same_objects() -> None:
    _assert_same_exports(chroma_line_format, embeddings_line_format)
    _assert_same_exports(chroma_receipt_rows, embeddings_receipt_rows)
    _assert_same_exports(chroma_word_format, embeddings_word_format)


def test_openai_package_shim_is_same_objects() -> None:
    _assert_same_exports(chroma_openai, embeddings_openai)


def test_openai_submodule_shims_are_same_objects() -> None:
    _assert_same_exports(chroma_batch_status, embeddings_batch_status)
    _assert_same_exports(chroma_helpers, embeddings_helpers)
    _assert_same_exports(chroma_poll, embeddings_poll)
    _assert_same_exports(chroma_realtime, embeddings_realtime)
    _assert_same_exports(chroma_submit, embeddings_submit)
