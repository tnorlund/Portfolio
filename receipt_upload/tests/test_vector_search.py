"""Backend-selection contract for live-ingest vector consumers."""

import pytest
from receipt_embeddings import (
    ChromaVectorSearchClient,
    DynamoVectorSearchClient,
)

from receipt_upload.vector_search import vector_search_client


class _RawChroma:
    def query(self, **_kwargs):
        return {}


class _InjectedVectorClient:
    def search(self, vector, index, top_k, filters=None):
        del vector, index, top_k, filters
        return []

    def get_vector(self, _key):
        return []


def test_dynamodb_is_the_default_backend(monkeypatch) -> None:
    # Chroma teardown: an unset VECTOR_BACKEND now means DynamoDB.
    monkeypatch.delenv("VECTOR_BACKEND", raising=False)
    expected = _InjectedVectorClient()
    monkeypatch.setattr(
        DynamoVectorSearchClient,
        "from_env",
        classmethod(lambda cls: expected),
    )
    assert vector_search_client(_RawChroma()) is expected


def test_chroma_backend_is_still_selectable(monkeypatch) -> None:
    monkeypatch.setenv("VECTOR_BACKEND", "chroma")
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


def test_unknown_backend_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("VECTOR_BACKEND", "unknown")
    with pytest.raises(ValueError, match="VECTOR_BACKEND"):
        vector_search_client(_RawChroma())
