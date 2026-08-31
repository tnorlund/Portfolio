"""Real botocore-model contract tests for Dynamo vector search."""

from __future__ import annotations

import boto3
import pytest
from botocore.exceptions import ClientError
from botocore.stub import Stubber
from receipt_dynamo.entities import EMBEDDING_DIMENSIONS, ReceiptLineEmbedding

from receipt_embeddings.dynamo_client import DynamoVectorSearchClient

TABLE = "ReceiptsTable-dc5be22"
IMAGE_ID = "2f1e7204-84f1-4ab3-9b05-7dc6edebc1b7"


def _vector() -> list[float]:
    return [0.01] * EMBEDDING_DIMENSIONS


def _client():
    return boto3.client(
        "dynamodb",
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )


@pytest.mark.unit
def test_search_vectors_wire_shape_and_response_contract() -> None:
    boto_client = _client()
    adapter = DynamoVectorSearchClient(boto_client, TABLE)
    entity = ReceiptLineEmbedding(
        image_id=IMAGE_ID,
        receipt_id=1,
        line_id=2,
        text="COFFEE",
        merchant_name="Fixture Mart",
        place_id="place-1",
        row_line_ids=[2],
        section_type="ITEMS",
        line_vector=_vector(),
    )
    expected_vector = [{"N": "0.00999999978"}] * EMBEDDING_DIMENSIONS
    expected = {
        "TableName": TABLE,
        "IndexName": "line-embeddings",
        "SearchVector": expected_vector,
        "TopK": 10,
        "ReturnConsumedCapacity": "TOTAL",
        "SearchConditionExpression": "#f0 = :f0",
        "ExpressionAttributeNames": {"#f0": "section_type"},
        "ExpressionAttributeValues": {":f0": {"S": "ITEMS"}},
    }
    response = {
        "SearchResults": [{"Item": entity.to_item(), "Score": 0.125}],
        "ConsumedCapacity": {
            "VectorSearchRequestBytes": 40960,
        },
    }

    with Stubber(boto_client) as stubber:
        stubber.add_response("search_vectors", response, expected)
        results = adapter.search(
            _vector(), "lines-vectors", 10, {"section_type": "ITEMS"}
        )

    assert [result.key for result in results] == [entity.canonical_key]
    assert results[0].distance == pytest.approx(0.125)
    assert results[0].metadata["merchant_name"] == "Fixture Mart"
    assert adapter.last_request_bytes == 40960
    assert adapter.get_last_search_metrics()["estimated_usd"] == pytest.approx(
        40960 / 1_000_000_000 * 0.002
    )


@pytest.mark.unit
def test_search_throttle_propagates_for_caller_degradation_policy() -> None:
    boto_client = _client()
    adapter = DynamoVectorSearchClient(boto_client, TABLE)
    with Stubber(boto_client) as stubber:
        stubber.add_client_error(
            "search_vectors",
            service_error_code="ProvisionedThroughputExceededException",
            service_message="throttled",
            http_status_code=400,
        )
        with pytest.raises(ClientError, match="throttled"):
            adapter.search(_vector(), "line-embeddings", 1)


@pytest.mark.unit
def test_missing_vector_is_a_key_error() -> None:
    boto_client = _client()
    adapter = DynamoVectorSearchClient(boto_client, TABLE)
    key = f"IMAGE#{IMAGE_ID}#RECEIPT#00001#LINE#00002"
    expected = {
        "TableName": TABLE,
        "Key": {
            "PK": {"S": f"IMAGE#{IMAGE_ID}"},
            "SK": {"S": "RECEIPT#00001#LINE#00002#EMBEDDING"},
        },
        "ProjectionExpression": "#vector",
        "ExpressionAttributeNames": {"#vector": "line_vector"},
        "ConsistentRead": True,
    }
    with Stubber(boto_client) as stubber:
        stubber.add_response("get_item", {}, expected)
        with pytest.raises(KeyError, match="unknown vector key"):
            adapter.get_vector(key)


@pytest.mark.unit
def test_constructor_rejects_sdk_without_search_vectors() -> None:
    with pytest.raises(RuntimeError, match="boto3 >= 1.43.64"):
        DynamoVectorSearchClient(object(), TABLE)


@pytest.mark.unit
def test_invalid_filter_is_rejected_before_network() -> None:
    adapter = DynamoVectorSearchClient(_client(), TABLE)
    with pytest.raises(ValueError, match="supports only equality filters"):
        adapter.search(
            _vector(), "line-embeddings", 1, {"label_status": "none"}
        )
