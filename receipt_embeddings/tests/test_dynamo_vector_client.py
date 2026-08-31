"""SearchVectors wire-format and graceful-degradation tests."""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from receipt_embeddings.dynamo_client import DynamoVectorSearchClient
from receipt_embeddings.indexes import (
    DEV_TABLE_NAME,
    EMBEDDING_DIMENSION,
    LINE_INDEX,
    MAX_SEARCH_VECTORS_TOP_K,
    encode_search_vector,
    validate_search_args,
)
from receipt_embeddings.testing import FakeVectorIndex
from receipt_embeddings.vector_client import VectorItem


def _vector(seed: float = 1.0) -> list[float]:
    values = [0.0] * EMBEDDING_DIMENSION
    values[0] = seed
    return values


def _client_error(code: str) -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": code}}, "SearchVectors"
    )


def test_search_vector_is_attribute_value_list_not_floats() -> None:
    encoded = encode_search_vector([0.01, -0.5])
    assert encoded == [{"N": "0.01"}, {"N": "-0.5"}]
    assert all(set(entry) == {"N"} for entry in encoded)


def test_search_vectors_kwargs_match_judge_verified_wire_format() -> None:
    boto = MagicMock()
    boto.search_vectors.return_value = {
        "SearchResults": [
            {
                "Item": {
                    "PK": {"S": "IMAGE#abc"},
                    "SK": {"S": "RECEIPT#00001#LINE#00002#EMBEDDING"},
                    "merchant_name": {"S": "Sprouts"},
                    "section_type": {"S": "ITEMS"},
                },
                "Score": 0.12,
            }
        ],
        "ConsumedCapacity": {"VectorSearchRequestBytes": 40123},
    }
    client = DynamoVectorSearchClient(table_name=DEV_TABLE_NAME, client=boto)
    hits = client.search(_vector(), "lines-vectors", 10)
    kwargs = boto.search_vectors.call_args.kwargs
    assert kwargs["TableName"] == DEV_TABLE_NAME
    assert kwargs["IndexName"] == LINE_INDEX
    assert kwargs["TopK"] == 10
    assert isinstance(kwargs["SearchVector"], list)
    assert kwargs["SearchVector"][0] == {"N": "1"}
    assert "L" not in kwargs["SearchVector"][0]
    assert kwargs["ReturnConsumedCapacity"] == "TOTAL"
    boto.update_table.assert_not_called()
    boto.create_table.assert_not_called()
    assert hits[0].key == "IMAGE#abc#RECEIPT#00001#LINE#00002"
    assert hits[0].distance == pytest.approx(0.12)
    assert hits[0].metadata["merchant_name"] == "Sprouts"
    assert client.last_request_units == pytest.approx(40123)


def test_search_results_key_is_not_items() -> None:
    source = inspect.getsource(DynamoVectorSearchClient)
    assert "SearchResults" in source
    assert 'get("Items")' not in source


def test_never_mutates_indexes() -> None:
    source = inspect.getsource(DynamoVectorSearchClient)
    for banned in (
        "update_table(",
        "create_table(",
        "VectorIndexes",
        "VectorIndexUpdates",
    ):
        assert banned not in source


def test_prod_table_is_refused() -> None:
    with pytest.raises(RuntimeError, match="production"):
        DynamoVectorSearchClient(table_name="ReceiptsTable-d7ff76a")


def test_throttle_returns_empty_neighbors() -> None:
    boto = MagicMock()
    boto.search_vectors.side_effect = _client_error("ThrottlingException")
    client = DynamoVectorSearchClient(table_name=DEV_TABLE_NAME, client=boto)
    assert client.search(_vector(), LINE_INDEX, 5) == []


def test_missing_vector_raises_keyerror() -> None:
    boto = MagicMock()
    boto.get_item.return_value = {}
    client = DynamoVectorSearchClient(table_name=DEV_TABLE_NAME, client=boto)
    with pytest.raises(KeyError, match="unknown vector key"):
        client.get_vector(
            "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"
            "#RECEIPT#00001#LINE#00001"
        )


def test_fake_and_dynamo_share_topk_contract() -> None:
    fake = FakeVectorIndex(
        [
            VectorItem(
                key="a",
                index="lines-vectors",
                vector=[1.0, 0.0],
                metadata={},
            )
        ]
    )
    with pytest.raises(ValueError, match="between 1 and 100"):
        fake.search([1.0, 0.0], "lines-vectors", MAX_SEARCH_VECTORS_TOP_K + 1)
    with pytest.raises(ValueError, match="between 1 and 100"):
        validate_search_args(top_k=MAX_SEARCH_VECTORS_TOP_K + 1, filters=None)
