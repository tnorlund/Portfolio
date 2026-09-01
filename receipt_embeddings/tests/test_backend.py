"""Backend-selection contract for the shared vector seam."""

import pytest

from receipt_embeddings import (
    ChromaVectorSearchClient,
    DynamoVectorSearchClient,
    vector_search_client,
)


class _RawChroma:
    def query(self, **_kwargs):
        return {}


class _InjectedVectorClient:
    def search(self, vector, index, top_k, filters=None):
        del vector, index, top_k, filters
        return []

    def get_vector(self, _key):
        return []


def test_chroma_is_the_default_backend(monkeypatch) -> None:
    monkeypatch.delenv("VECTOR_BACKEND", raising=False)
    assert isinstance(
        vector_search_client(_RawChroma()), ChromaVectorSearchClient
    )


def test_dynamodb_backend_is_built_lazily(monkeypatch) -> None:
    expected = _InjectedVectorClient()
    monkeypatch.setenv("VECTOR_BACKEND", "dynamodb")
    monkeypatch.setattr(
        DynamoVectorSearchClient,
        "from_env",
        classmethod(lambda _cls: expected),
    )
    assert vector_search_client(_RawChroma()) is expected


def test_injected_client_wins_over_environment(monkeypatch) -> None:
    expected = _InjectedVectorClient()
    monkeypatch.setenv("VECTOR_BACKEND", "chroma")
    assert (
        vector_search_client(_RawChroma(), vector_client=expected) is expected
    )


def test_protocol_conformant_chroma_client_passes_through() -> None:
    client = _InjectedVectorClient()
    assert vector_search_client(client) is client


def test_unknown_backend_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("VECTOR_BACKEND", "unknown")
    with pytest.raises(ValueError, match="VECTOR_BACKEND"):
        vector_search_client(_RawChroma())
