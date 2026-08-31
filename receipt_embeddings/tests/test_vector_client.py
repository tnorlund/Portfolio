"""Protocol surface: consumers need only search() and get_vector()."""

from __future__ import annotations

from typing import get_protocol_members

from receipt_embeddings import VectorSearchClient
from receipt_embeddings.testing import FakeVectorIndex
from receipt_embeddings.vector_client import (
    cosine_distance,
    normalize_index_name,
)


def test_protocol_exposes_only_search_and_get_vector() -> None:
    assert get_protocol_members(VectorSearchClient) == {
        "search",
        "get_vector",
    }


def test_fake_and_protocol_are_interchangeable() -> None:
    def consume(client: VectorSearchClient) -> int:
        hits = client.search([1.0, 0.0], "lines", top_k=1)
        _ = client.get_vector("q")
        return len(hits)

    fake = FakeVectorIndex()
    fake.upsert(key="q", vector=[1.0, 0.0], index="lines")
    assert consume(fake) == 1
    assert isinstance(fake, VectorSearchClient)


def test_normalize_index_name_aliases() -> None:
    assert normalize_index_name("line-embeddings") == "lines"
    assert normalize_index_name("words-vectors") == "words"
    try:
        normalize_index_name("fonts")
    except ValueError as exc:
        assert "fonts" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")


def test_cosine_distance_rejects_length_mismatch() -> None:
    try:
        cosine_distance([1.0, 0.0], [1.0])
    except ValueError as exc:
        assert "mismatch" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")
