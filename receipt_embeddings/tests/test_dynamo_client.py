"""Contract tests for DynamoVectorSearchClient wire format."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from receipt_embeddings.dynamo_client import (
    DynamoVectorSearchClient,
    create_client_from_env,
)
from receipt_embeddings.quotas import (
    DEV_TABLE_NAME,
    EMBEDDING_DIMENSIONS,
    LINE_EMBEDDING_INDEX,
    PROTOCOL_LINE_INDEX,
    VECTOR_SEARCH_REQUEST_BYTES_PER_1536,
)
from receipt_embeddings.vector_client import VectorSearchClient

IMAGE_ID = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"


def _vector(peak: float = 1.0) -> list[float]:
    values = [0.0] * EMBEDDING_DIMENSIONS
    values[0] = peak
    return values


def _client(stub: MagicMock | None = None) -> DynamoVectorSearchClient:
    return DynamoVectorSearchClient(
        table_name=DEV_TABLE_NAME, client=stub or MagicMock()
    )


@pytest.mark.unit
def test_implements_protocol() -> None:
    assert isinstance(_client(), VectorSearchClient)


@pytest.mark.unit
def test_from_env_refuses_prod(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", "ReceiptsTable-d7ff76a")
    with pytest.raises(ValueError, match="refusing to query DynamoDB"):
        create_client_from_env()


@pytest.mark.unit
def test_search_sends_attributevalue_list_not_l_wrapped() -> None:
    stub = MagicMock()
    stub.search_vectors.return_value = {
        "SearchResults": [
            {
                "Item": {
                    "image_id": {"S": IMAGE_ID},
                    "receipt_id": {"N": "1"},
                    "line_id": {"N": "3"},
                    "merchant_name": {"S": "Sprouts"},
                    "section_type": {"S": "ITEMS"},
                },
                "Score": 0.0023,
            }
        ],
        "ConsumedCapacity": {
            "VectorSearchRequestBytes": VECTOR_SEARCH_REQUEST_BYTES_PER_1536
        },
    }
    client = _client(stub)
    results = client.search(_vector(), PROTOCOL_LINE_INDEX, 10)

    request = stub.search_vectors.call_args.kwargs
    assert request["IndexName"] == LINE_EMBEDDING_INDEX
    assert request["TableName"] == DEV_TABLE_NAME
    assert "Items" not in stub.search_vectors.return_value
    search_vector = request["SearchVector"]
    assert isinstance(search_vector, list)
    assert search_vector[0] == {"N": "1.0"}
    assert "L" not in search_vector[0]
    assert request["ReturnConsumedCapacity"] == "TOTAL"
    assert [item.key for item in results] == [
        f"IMAGE#{IMAGE_ID}#RECEIPT#00001#LINE#00003"
    ]
    assert results[0].distance == pytest.approx(0.0023)
    assert results[0].metadata["merchant_name"] == "Sprouts"
    assert client.last_request_units == VECTOR_SEARCH_REQUEST_BYTES_PER_1536


@pytest.mark.unit
def test_search_builds_equality_condition() -> None:
    stub = MagicMock()
    stub.search_vectors.return_value = {"SearchResults": []}
    _client(stub).search(
        _vector(),
        PROTOCOL_LINE_INDEX,
        5,
        filters={"section_type": "ITEMS"},
    )
    request = stub.search_vectors.call_args.kwargs
    assert request["SearchConditionExpression"] == "#f0 = :f0"
    assert request["ExpressionAttributeNames"] == {"#f0": "section_type"}
    assert request["ExpressionAttributeValues"] == {":f0": {"S": "ITEMS"}}


@pytest.mark.unit
def test_get_vector_reads_l_of_n() -> None:
    stub = MagicMock()
    stored = _vector(0.5)
    stub.get_item.return_value = {
        "Item": {
            "line_vector": {
                "L": [{"N": format(value, ".17g")} for value in stored]
            }
        }
    }
    got = _client(stub).get_vector(
        f"IMAGE#{IMAGE_ID}#RECEIPT#00001#LINE#00003"
    )
    assert got[0] == pytest.approx(0.5)
    key = stub.get_item.call_args.kwargs["Key"]
    assert key["SK"]["S"].endswith("#EMBEDDING")


@pytest.mark.unit
def test_search_key_comes_from_pk_sk_even_without_projected_ids() -> None:
    stub = MagicMock()
    stub.search_vectors.return_value = {
        "SearchResults": [
            {
                "Item": {
                    "PK": {"S": f"IMAGE#{IMAGE_ID}"},
                    "SK": {"S": "RECEIPT#00001#LINE#00003#EMBEDDING"},
                    "merchant_name": {"S": "Sprouts"},
                },
                "Score": 0.01,
            }
        ]
    }
    results = _client(stub).search(_vector(), PROTOCOL_LINE_INDEX, 10)
    assert [item.key for item in results] == [
        f"IMAGE#{IMAGE_ID}#RECEIPT#00001#LINE#00003"
    ]


@pytest.mark.unit
def test_requires_search_vectors_support() -> None:
    class _Legacy:
        pass

    with pytest.raises(RuntimeError, match="SearchVectors"):
        DynamoVectorSearchClient(table_name=DEV_TABLE_NAME, client=_Legacy())
