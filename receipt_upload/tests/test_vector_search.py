"""Backend-selection contract for live-ingest vector consumers."""

from unittest.mock import Mock, create_autospec

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


def test_words_worker_uses_keyword_only_factory_for_its_own_table(monkeypatch):
    import receipt_dynamo

    from receipt_upload import vector_search
    from receipt_upload.merchant_resolution import embedding_processor

    dynamo = Mock()
    dynamo.get_receipt_sections_from_receipt.return_value = []
    monkeypatch.setattr(receipt_dynamo, "DynamoClient", lambda table: dynamo)
    factory = create_autospec(
        vector_search_client, return_value=_InjectedVectorClient()
    )
    monkeypatch.setattr(vector_search, "vector_search_client", factory)
    result = embedding_processor._run_words_pipeline_worker(
        words_data=[],
        word_labels_data=[],
        word_embeddings_list=[],
        image_id="00000000-0000-4000-8000-000000000001",
        receipt_id=1,
        table_name="test-table",
    )
    assert result["success"] is True
    factory.assert_called_once_with(
        dynamodb_client=dynamo._client, table_name="test-table"
    )
