"""Unit tests for RECEIPT_WORD_EMBEDDING items."""

from receipt_dynamo.entities.receipt_line_embedding import GSI_KEY_NAMES
from receipt_dynamo.entities.receipt_word_embedding import (
    WORD_EMBEDDING_TYPE,
    WORD_VECTOR_ATTR,
    ReceiptWordEmbedding,
    item_to_receipt_word_embedding,
)

_IMAGE = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"


def test_round_trip_and_no_gsi_keys() -> None:
    entity = ReceiptWordEmbedding(
        image_id=_IMAGE,
        receipt_id=1,
        line_id=3,
        word_id=2,
        word_vector=[0.5, 0.25],
        text="Total",
        merchant_name="Costco",
        label_status="validated",
        primary_label="GRAND_TOTAL",
    )
    item = entity.to_item()
    assert item["TYPE"]["S"] == WORD_EMBEDDING_TYPE
    assert item["SK"]["S"] == "RECEIPT#00001#LINE#00003#WORD#00002#EMBEDDING"
    assert WORD_VECTOR_ATTR in item
    for banned in GSI_KEY_NAMES:
        assert banned not in item
    restored = item_to_receipt_word_embedding(item)
    assert restored.word_id == 2
    assert restored.label_status == "validated"
    assert restored.harness_key().endswith("#WORD#00002")
