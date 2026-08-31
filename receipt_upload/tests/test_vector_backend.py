"""VECTOR_BACKEND selection for merchant resolution retrieval."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from receipt_embeddings.vector_client import ScoredItem
from receipt_upload.merchant_resolution.vector_backend import (
    ChromaVectorSearchClient,
    vector_backend_name,
    vector_search_client,
)


@pytest.mark.unit
def test_default_backend_is_chroma(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VECTOR_BACKEND", raising=False)
    assert vector_backend_name() == "chroma"


@pytest.mark.unit
def test_chroma_adapter_calls_query() -> None:
    chroma = MagicMock()
    chroma.query.return_value = {
        "ids": [["IMAGE#a#RECEIPT#00001#LINE#00001"]],
        "distances": [[0.1]],
        "metadatas": [[{"image_id": "a", "receipt_id": 1}]],
    }
    client = ChromaVectorSearchClient(chroma)
    results = client.search([0.1] * 8, "lines-vectors", 20)
    chroma.query.assert_called_once()
    kwargs = chroma.query.call_args.kwargs
    assert kwargs["collection_name"] == "lines"
    assert kwargs["n_results"] == 20
    assert [item.distance for item in results] == [pytest.approx(0.1)]


@pytest.mark.unit
def test_dynamo_alias_selects_dynamodb(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VECTOR_BACKEND", "dynamo")
    assert vector_backend_name() == "dynamodb"


@pytest.mark.unit
def test_dynamodb_backend_constructs_dynamo_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VECTOR_BACKEND", "dynamodb")
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", "ReceiptsTable-dc5be22")
    fake = MagicMock()
    fake.search.return_value = [ScoredItem("k", 0.2, {"merchant_name": "A"})]
    with patch(
        "receipt_embeddings.dynamo_client.DynamoVectorSearchClient.from_env",
        return_value=fake,
    ):
        client = vector_search_client(MagicMock())
    assert client is fake
