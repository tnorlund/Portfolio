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


class _BotoLike:
    def search_vectors(self, **_kwargs):
        return {}


def test_dynamodb_backend_uses_threaded_client_and_table(
    monkeypatch,
) -> None:
    """E3 review P1-3: a caller-provided table/client must win over the
    environment fallback so sessions never silently cross tables."""
    monkeypatch.setenv("VECTOR_BACKEND", "dynamodb")
    boto_like = _BotoLike()

    client = vector_search_client(
        None, dynamodb_client=boto_like, table_name="ReceiptsTable-x"
    )

    assert isinstance(client, DynamoVectorSearchClient)
    assert client.table_name == "ReceiptsTable-x"
    assert client._client is boto_like


def test_dynamodb_fallback_warns_and_names_the_table(
    monkeypatch, caplog
) -> None:
    monkeypatch.setenv("VECTOR_BACKEND", "dynamodb")
    fallback = _InjectedVectorClient()
    fallback.table_name = "ReceiptsTable-env"
    monkeypatch.setattr(
        DynamoVectorSearchClient,
        "from_env",
        classmethod(lambda _cls: fallback),
    )

    with caplog.at_level("WARNING", logger="receipt_embeddings.backend"):
        assert vector_search_client(None) is fallback

    assert "ReceiptsTable-env" in caplog.text
