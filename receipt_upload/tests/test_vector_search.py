"""Backend-selection contract for live-ingest vector consumers."""

from receipt_embeddings import DynamoVectorSearchClient

from receipt_upload.vector_search import vector_search_client


class _InjectedVectorClient:
    def search(self, vector, index, top_k, filters=None):
        del vector, index, top_k, filters
        return []

    def get_vector(self, _key):
        return []


def test_dynamodb_backend_is_built_lazily(monkeypatch) -> None:
    expected = _InjectedVectorClient()
    monkeypatch.setattr(
        DynamoVectorSearchClient,
        "from_env",
        classmethod(lambda _cls: expected),
    )
    assert vector_search_client() is expected


def test_injected_vector_client_wins() -> None:
    injected = _InjectedVectorClient()
    assert vector_search_client(vector_client=injected) is injected
