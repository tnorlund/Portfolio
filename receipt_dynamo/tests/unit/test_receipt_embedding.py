"""Contract tests for dedicated receipt embedding items."""

from __future__ import annotations

import pytest
from receipt_dynamo.entities import (
    EMBEDDING_DIMENSIONS,
    ReceiptLineEmbedding,
    ReceiptWordEmbedding,
    item_to_receipt_embedding,
)

IMAGE_ID = "2f1e7204-84f1-4ab3-9b05-7dc6edebc1b7"


def _vector() -> list[float]:
    return [0.01] * EMBEDDING_DIMENSIONS


def test_line_embedding_round_trip_has_sparse_index_shape() -> None:
    embedding = ReceiptLineEmbedding(
        image_id=IMAGE_ID,
        receipt_id=1,
        line_id=2,
        text="ORGANIC COFFEE 12.99",
        merchant_name="Fixture Mart",
        place_id="place-1",
        row_line_ids=[2, 3],
        section_type="ITEMS",
        line_vector=_vector(),
    )

    item = embedding.to_item()

    assert item["SK"] == {"S": "RECEIPT#00001#LINE#00002#EMBEDDING"}
    assert item["TYPE"] == {"S": "RECEIPT_LINE_EMBEDDING"}
    assert "line_vector" in item and "word_vector" not in item
    assert not any(key.startswith("GSI") for key in item)
    assert item_to_receipt_embedding(item) == embedding


def test_word_embedding_round_trip_has_sparse_index_shape() -> None:
    embedding = ReceiptWordEmbedding(
        image_id=IMAGE_ID,
        receipt_id=1,
        line_id=2,
        word_id=4,
        text="COFFEE",
        merchant_name="Fixture Mart",
        label_status="validated",
        word_vector=_vector(),
    )

    item = embedding.to_item()

    assert item["SK"] == {"S": "RECEIPT#00001#LINE#00002#WORD#00004#EMBEDDING"}
    assert item["TYPE"] == {"S": "RECEIPT_WORD_EMBEDDING"}
    assert "word_vector" in item and "line_vector" not in item
    assert not any(key.startswith("GSI") for key in item)
    assert item_to_receipt_embedding(item) == embedding


def test_line_embedding_anchor_fields_round_trip() -> None:
    embedding = ReceiptLineEmbedding(
        image_id=IMAGE_ID,
        receipt_id=1,
        line_id=2,
        text="CALL 555-123-4567",
        merchant_name="Fixture Mart",
        place_id="place-1",
        row_line_ids=[2],
        section_type="HEADER",
        line_vector=_vector(),
        normalized_phone_10="5551234567",
        normalized_full_address="123 MAIN ST HENDERSON NV 89014",
    )

    item = embedding.to_item()

    assert item["normalized_phone_10"] == {"S": "5551234567"}
    assert item["normalized_full_address"] == {
        "S": "123 MAIN ST HENDERSON NV 89014"
    }
    assert item_to_receipt_embedding(item) == embedding


def test_line_embedding_anchor_fields_are_sparse_when_absent() -> None:
    """Empty anchors omit the attributes entirely, mirroring the Chroma
    metadata writer's presence-only anchor keys."""
    embedding = ReceiptLineEmbedding(
        image_id=IMAGE_ID,
        receipt_id=1,
        line_id=2,
        text="ORGANIC COFFEE 12.99",
        merchant_name="Fixture Mart",
        place_id="place-1",
        row_line_ids=[2],
        section_type="ITEMS",
        line_vector=_vector(),
    )

    item = embedding.to_item()

    assert "normalized_phone_10" not in item
    assert "normalized_full_address" not in item
    assert item_to_receipt_embedding(item) == embedding


@pytest.mark.parametrize("dimensions", [0, EMBEDDING_DIMENSIONS - 1, 1537])
def test_embedding_rejects_wrong_vector_dimensions(dimensions: int) -> None:
    with pytest.raises(ValueError, match="1536"):
        ReceiptWordEmbedding(
            image_id=IMAGE_ID,
            receipt_id=1,
            line_id=2,
            word_id=4,
            text="COFFEE",
            merchant_name="",
            label_status="none",
            word_vector=[0.0] * dimensions,
        )


def test_line_embedding_requires_primary_line_in_row() -> None:
    with pytest.raises(ValueError, match="include line_id"):
        ReceiptLineEmbedding(
            image_id=IMAGE_ID,
            receipt_id=1,
            line_id=2,
            text="COFFEE",
            merchant_name="",
            place_id="",
            row_line_ids=[3],
            section_type="",
            line_vector=_vector(),
        )
