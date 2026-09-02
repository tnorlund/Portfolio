"""Real botocore-model contract tests for Dynamo vector search."""

from __future__ import annotations

import boto3
import pytest
from botocore.exceptions import ClientError
from botocore.stub import Stubber
from receipt_embeddings import ChromaVectorSearchClient
from receipt_embeddings.dynamo_client import (
    _LINE_JOIN_ATTRIBUTES,
    _WORD_LABEL_JOIN_ATTRIBUTES,
    DynamoVectorSearchClient,
)

from receipt_dynamo.constants import CORE_LABEL_NAMES, ValidationStatus
from receipt_dynamo.entities import (
    EMBEDDING_DIMENSIONS,
    ReceiptLineEmbedding,
    ReceiptWordEmbedding,
)

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


def _join_names() -> dict[str, str]:
    return {
        f"#j{position}": name
        for position, name in enumerate(_LINE_JOIN_ATTRIBUTES)
    }


def _expected_join_request(keys: list[dict]) -> dict:
    names = _join_names()
    return {
        "RequestItems": {
            TABLE: {
                "Keys": keys,
                "ProjectionExpression": ", ".join(names),
                "ExpressionAttributeNames": names,
                "ConsistentRead": True,
            }
        },
        "ReturnConsumedCapacity": "TOTAL",
    }


def _expected_word_join_request(keys: list[dict]) -> dict:
    names = {
        f"#w{position}": name
        for position, name in enumerate(_WORD_LABEL_JOIN_ATTRIBUTES)
    }
    return {
        "RequestItems": {
            TABLE: {
                "Keys": keys,
                "ProjectionExpression": ", ".join(names),
                "ExpressionAttributeNames": names,
                "ConsistentRead": True,
            }
        },
        "ReturnConsumedCapacity": "TOTAL",
    }


def _line_key(receipt_id: int, line_id: int) -> dict:
    return {
        "PK": {"S": f"IMAGE#{IMAGE_ID}"},
        "SK": {"S": f"RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#EMBEDDING"},
    }


def _base_item_without_vector(entity: ReceiptLineEmbedding) -> dict:
    return {
        name: value
        for name, value in entity.to_item().items()
        if name not in {"PK", "SK", "TYPE", "line_vector"}
    }


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

    join_response = {
        "Responses": {TABLE: [_base_item_without_vector(entity)]},
        "ConsumedCapacity": [{"TableName": TABLE, "CapacityUnits": 4.0}],
    }

    with Stubber(boto_client) as stubber:
        stubber.add_response("search_vectors", response, expected)
        stubber.add_response(
            "batch_get_item",
            join_response,
            _expected_join_request([_line_key(1, 2)]),
        )
        results = adapter.search(
            _vector(), "lines-vectors", 10, {"section_type": "ITEMS"}
        )

    assert [result.key for result in results] == [entity.canonical_key]
    assert results[0].distance == pytest.approx(0.125)
    assert results[0].metadata["merchant_name"] == "Fixture Mart"
    assert adapter.last_request_bytes == 40960
    assert adapter.last_join_read_units == pytest.approx(4.0)
    metrics = adapter.get_last_search_metrics()
    assert metrics["estimated_usd"] == pytest.approx(
        40960 / 1_000_000_000 * 0.002
    )
    assert metrics["join_read_units"] == pytest.approx(4.0)
    assert metrics["request_units"] == pytest.approx(4.0)


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


def _projection_item(entity: ReceiptLineEmbedding) -> dict:
    """The line index's INCLUDE projection: no normalized_* attributes."""
    projected = {
        "text",
        "merchant_name",
        "place_id",
        "image_id",
        "receipt_id",
        "line_id",
        "row_line_ids",
        "section_type",
        "PK",
        "SK",
        "line_vector",
    }
    return {
        name: value
        for name, value in entity.to_item().items()
        if name in projected
    }


def _anchored_entity() -> ReceiptLineEmbedding:
    return ReceiptLineEmbedding(
        image_id=IMAGE_ID,
        receipt_id=1,
        line_id=2,
        text="CALL US 555-123-4567",
        merchant_name="Fixture Mart",
        place_id="place-1",
        row_line_ids=[2],
        section_type="HEADER",
        line_vector=_vector(),
        normalized_phone_10="5551234567",
        normalized_full_address="123 MAIN ST HENDERSON NV 89014",
    )


def _word_entity(label_status: str = "validated") -> ReceiptWordEmbedding:
    return ReceiptWordEmbedding(
        image_id=IMAGE_ID,
        receipt_id=1,
        line_id=2,
        word_id=3,
        text="COFFEE",
        merchant_name="Fixture Mart",
        label_status=label_status,
        word_vector=_vector(),
    )


class _ChromaWordClient:
    def query(self, **_kwargs):
        return {
            "ids": [[_word_entity().canonical_key]],
            "metadatas": [
                [
                    {
                        "image_id": IMAGE_ID,
                        "receipt_id": 1,
                        "line_id": 2,
                        "word_id": 3,
                        "label_status": "validated",
                        "valid_labels_array": ["PRODUCT_NAME"],
                    }
                ]
            ],
            "distances": [[0.25]],
        }


@pytest.mark.unit
def test_line_search_fetch_joins_unprojected_resolver_metadata() -> None:
    """SearchVectors -> BatchGetItem join surfaces the anchor fields the
    index projection omits (Round C fetch-join ruling)."""
    boto_client = _client()
    adapter = DynamoVectorSearchClient(boto_client, TABLE)
    entity = _anchored_entity()
    search_response = {
        "SearchResults": [{"Item": _projection_item(entity), "Score": 0.125}]
    }
    join_response = {"Responses": {TABLE: [_base_item_without_vector(entity)]}}

    with Stubber(boto_client) as stubber:
        stubber.add_response("search_vectors", search_response)
        stubber.add_response(
            "batch_get_item",
            join_response,
            _expected_join_request([_line_key(1, 2)]),
        )
        results = adapter.search(_vector(), "line-embeddings", 10)

    assert results[0].metadata["normalized_phone_10"] == "5551234567"
    assert (
        results[0].metadata["normalized_full_address"]
        == "123 MAIN ST HENDERSON NV 89014"
    )
    assert results[0].metadata["merchant_name"] == "Fixture Mart"


@pytest.mark.unit
def test_fetch_join_keeps_projection_metadata_for_missing_item() -> None:
    """A neighbor deleted between indexing and the join degrades to its
    projection metadata instead of being dropped or crashing."""
    boto_client = _client()
    adapter = DynamoVectorSearchClient(boto_client, TABLE)
    entity = _anchored_entity()
    search_response = {
        "SearchResults": [{"Item": _projection_item(entity), "Score": 0.125}]
    }

    with Stubber(boto_client) as stubber:
        stubber.add_response("search_vectors", search_response)
        stubber.add_response("batch_get_item", {"Responses": {TABLE: []}})
        results = adapter.search(_vector(), "line-embeddings", 10)

    assert [result.key for result in results] == [entity.canonical_key]
    assert results[0].metadata["merchant_name"] == "Fixture Mart"
    assert "normalized_phone_10" not in results[0].metadata


@pytest.mark.unit
def test_fetch_join_failure_degrades_to_projection_metadata() -> None:
    """A throttled join keeps the healthy SearchVectors results whole."""
    boto_client = _client()
    adapter = DynamoVectorSearchClient(
        boto_client, TABLE, sleep=lambda _: None
    )
    entity = _anchored_entity()
    search_response = {
        "SearchResults": [{"Item": _projection_item(entity), "Score": 0.125}]
    }

    with Stubber(boto_client) as stubber:
        stubber.add_response("search_vectors", search_response)
        stubber.add_client_error(
            "batch_get_item",
            service_error_code="ProvisionedThroughputExceededException",
            service_message="throttled",
            http_status_code=400,
        )
        results = adapter.search(_vector(), "line-embeddings", 10)

    assert [result.key for result in results] == [entity.canonical_key]
    assert results[0].metadata["merchant_name"] == "Fixture Mart"


@pytest.mark.unit
def test_fetch_join_retries_unprocessed_keys_bounded() -> None:
    boto_client = _client()
    sleeps: list[float] = []
    adapter = DynamoVectorSearchClient(boto_client, TABLE, sleep=sleeps.append)
    entity = _anchored_entity()
    search_response = {
        "SearchResults": [{"Item": _projection_item(entity), "Score": 0.125}]
    }
    unprocessed = {
        "Responses": {TABLE: []},
        "UnprocessedKeys": {TABLE: {"Keys": [_line_key(1, 2)]}},
    }
    fulfilled = {"Responses": {TABLE: [_base_item_without_vector(entity)]}}

    with Stubber(boto_client) as stubber:
        stubber.add_response("search_vectors", search_response)
        stubber.add_response("batch_get_item", unprocessed)
        stubber.add_response("batch_get_item", fulfilled)
        results = adapter.search(_vector(), "line-embeddings", 10)

    assert results[0].metadata["normalized_phone_10"] == "5551234567"
    assert sleeps == [0.1]


@pytest.mark.unit
def test_word_search_fetch_joins_chroma_label_metadata_contract() -> None:
    """The index filter projects aggregate status, while the fetch join
    supplies the exact label arrays that semantic consumers vote on."""
    boto_client = _client()
    adapter = DynamoVectorSearchClient(boto_client, TABLE)
    entity = _word_entity()
    label_keys = [
        {
            "PK": {"S": f"IMAGE#{IMAGE_ID}"},
            "SK": {
                "S": ("RECEIPT#00001#LINE#00002#WORD#00003#LABEL#" f"{label}")
            },
        }
        for label in CORE_LABEL_NAMES
    ]
    labels = [
        {
            **label_keys[CORE_LABEL_NAMES.index(label)],
            "validation_status": {"S": status},
            "reasoning": {"S": reason},
        }
        for label, status, reason in (
            ("PRODUCT_NAME", ValidationStatus.VALID.value, "is a product"),
            ("TAX", ValidationStatus.INVALID.value, "not a tax amount"),
        )
    ]
    with Stubber(boto_client) as stubber:
        stubber.add_response(
            "search_vectors",
            {"SearchResults": [{"Item": entity.to_item(), "Score": 0.25}]},
        )
        stubber.add_response(
            "batch_get_item",
            {"Responses": {TABLE: labels}},
            _expected_word_join_request(label_keys),
        )
        results = adapter.search(
            _vector(),
            "word-embeddings",
            5,
            {"label_status": "validated"},
        )

    assert results[0].metadata["label_status"] == "validated"
    assert results[0].metadata["valid_labels_array"] == ["PRODUCT_NAME"]
    # Single-join provenance (E3 review P2-4): similar_labeled_words
    # reuses these rows instead of re-fetching the same keys.
    assert [
        (row["label"], row["validation_status"], row["reasoning"])
        for row in results[0].metadata["label_rows"]
    ] == [
        ("PRODUCT_NAME", "VALID", "is a product"),
        ("TAX", "INVALID", "not a tax amount"),
    ]
    chroma = ChromaVectorSearchClient(_ChromaWordClient()).search(
        _vector(),
        "word-embeddings",
        5,
        {"label_status": "validated"},
    )[0]
    contract_keys = {
        "image_id",
        "receipt_id",
        "line_id",
        "word_id",
        "label_status",
        "valid_labels_array",
    }
    assert {key: results[0].metadata[key] for key in contract_keys} == {
        key: chroma.metadata[key] for key in contract_keys
    }


def test_word_label_join_failure_omits_vote_metadata() -> None:
    boto_client = _client()
    adapter = DynamoVectorSearchClient(boto_client, TABLE)
    entity = _word_entity()
    with Stubber(boto_client) as stubber:
        stubber.add_response(
            "search_vectors",
            {"SearchResults": [{"Item": entity.to_item(), "Score": 0.25}]},
        )
        stubber.add_client_error(
            "batch_get_item",
            service_error_code="ProvisionedThroughputExceededException",
            service_message="throttled",
            http_status_code=400,
        )
        results = adapter.search(
            _vector(),
            "word-embeddings",
            5,
            {"label_status": "validated"},
        )

    assert "valid_labels_array" not in results[0].metadata
    assert "label_rows" not in results[0].metadata


@pytest.mark.unit
def test_word_search_without_validated_filter_skips_label_join() -> None:
    boto_client = _client()
    adapter = DynamoVectorSearchClient(boto_client, TABLE)
    entity = _word_entity(label_status="none")
    with Stubber(boto_client) as stubber:
        stubber.add_response(
            "search_vectors",
            {"SearchResults": [{"Item": entity.to_item(), "Score": 0.25}]},
        )
        results = adapter.search(_vector(), "word-embeddings", 5)

    assert results[0].metadata["label_status"] == "none"


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
