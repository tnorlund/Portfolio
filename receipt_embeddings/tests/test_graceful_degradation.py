"""Graceful degradation for missing vectors, throttles, and absent receipts."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError
from receipt_embeddings.dynamo_client import (
    DynamoVectorSearchClient,
    VectorSearchThrottled,
)
from receipt_embeddings.quotas import (
    DEV_TABLE_NAME,
    EMBEDDING_DIMENSIONS,
    PROTOCOL_LINE_INDEX,
)
from receipt_embeddings.writer import prepare_embedding_items

IMAGE_ID = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"


def _vector() -> list[float]:
    values = [0.0] * EMBEDDING_DIMENSIONS
    values[0] = 1.0
    return values


def _throttle() -> ClientError:
    return ClientError(
        {
            "Error": {
                "Code": "ThrottlingException",
                "Message": "Rate exceeded",
            }
        },
        "SearchVectors",
    )


@pytest.mark.unit
def test_get_vector_missing_is_keyerror() -> None:
    stub = MagicMock()
    stub.get_item.return_value = {}
    client = DynamoVectorSearchClient(table_name=DEV_TABLE_NAME, client=stub)
    with pytest.raises(KeyError, match="unknown vector key"):
        client.get_vector(f"IMAGE#{IMAGE_ID}#RECEIPT#00001#LINE#00001")


@pytest.mark.unit
def test_search_throttle_retries_then_raises() -> None:
    stub = MagicMock()
    stub.search_vectors.side_effect = _throttle()
    client = DynamoVectorSearchClient(table_name=DEV_TABLE_NAME, client=stub)
    with patch("receipt_embeddings.dynamo_client.time.sleep"):
        with pytest.raises(VectorSearchThrottled):
            client.search(_vector(), PROTOCOL_LINE_INDEX, 10)
    assert stub.search_vectors.call_count == 4


@pytest.mark.unit
def test_prepare_skips_missing_vectors_without_aborting() -> None:
    class _Receipt:
        image_id = IMAGE_ID
        receipt_id = 1

    class _Line:
        image_id = IMAGE_ID
        receipt_id = 1
        line_id = 1
        text = "MILK"
        bounding_box = {"x": 0.1, "y": 0.8, "width": 0.4, "height": 0.05}

        def calculate_centroid(self) -> tuple[float, float]:
            return (0.3, 0.825)

    class _Word(_Line):
        word_id = 1

    class _Details:
        receipt = _Receipt()
        lines = [_Line()]
        words = [_Word()]
        labels = []
        place = None

    prepared = prepare_embedding_items(_Details(), vectors_by_key={})
    assert prepared.items == []
    reasons = {row["reason"] for row in prepared.skipped}
    assert reasons == {"missing_vector"}
