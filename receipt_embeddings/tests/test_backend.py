"""Backend-selection contract for the shared vector seam."""

from receipt_embeddings import DynamoVectorSearchClient, vector_search_client


class _InjectedVectorClient:
    def search(self, vector, index, top_k, filters=None):
        del vector, index, top_k, filters
        return []

    def get_vector(self, _key):
        return []


def test_dynamodb_is_the_only_backend(monkeypatch) -> None:
    expected = _InjectedVectorClient()
    monkeypatch.setattr(
        DynamoVectorSearchClient,
        "from_env",
        classmethod(lambda cls: expected),
    )
    assert vector_search_client() is expected


def test_injected_client_wins_over_environment(monkeypatch) -> None:
    expected = _InjectedVectorClient()

    def _never(_cls):
        raise AssertionError("from_env must not run when a client is given")

    monkeypatch.setattr(
        DynamoVectorSearchClient, "from_env", classmethod(_never)
    )
    assert vector_search_client(vector_client=expected) is expected


class _BotoLike:
    def search_vectors(self, **_kwargs):
        return {}


def test_dynamodb_backend_uses_threaded_client_and_table() -> None:
    """E3 review P1-3: a caller-provided table/client must win over the
    environment fallback so sessions never silently cross tables."""
    boto_like = _BotoLike()

    client = vector_search_client(
        dynamodb_client=boto_like, table_name="ReceiptsTable-x"
    )

    assert isinstance(client, DynamoVectorSearchClient)
    assert client.table_name == "ReceiptsTable-x"
    assert client._client is boto_like


def test_dynamodb_fallback_warns_and_names_the_table(
    monkeypatch, caplog
) -> None:
    fallback = _InjectedVectorClient()
    fallback.table_name = "ReceiptsTable-env"
    monkeypatch.setattr(
        DynamoVectorSearchClient,
        "from_env",
        classmethod(lambda _cls: fallback),
    )

    with caplog.at_level("WARNING", logger="receipt_embeddings.backend"):
        assert vector_search_client() is fallback

    assert "ReceiptsTable-env" in caplog.text
