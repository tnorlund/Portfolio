"""Unit tests for RECEIPT_LINE_EMBEDDING items."""

from receipt_dynamo.entities.receipt_line_embedding import (
    GSI_KEY_NAMES,
    LINE_EMBEDDING_TYPE,
    LINE_VECTOR_ATTR,
    ReceiptLineEmbedding,
    item_to_receipt_line_embedding,
)

_IMAGE = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"


def _item(**overrides):
    entity = ReceiptLineEmbedding(
        image_id=_IMAGE,
        receipt_id=1,
        line_id=4,
        line_vector=[0.1, -0.2, 0.3],
        text="TOTAL 4.00",
        merchant_name="Sprouts",
        place_id="place-1",
        row_line_ids=[4, 5],
        section_type="TOTALS",
        **overrides,
    )
    return entity


def test_round_trip_and_sk_shape() -> None:
    entity = _item()
    item = entity.to_item()
    assert item["TYPE"]["S"] == LINE_EMBEDDING_TYPE
    assert item["SK"]["S"] == "RECEIPT#00001#LINE#00004#EMBEDDING"
    assert item["PK"]["S"] == f"IMAGE#{_IMAGE}"
    assert LINE_VECTOR_ATTR in item
    assert item[LINE_VECTOR_ATTR]["L"][0]["N"]
    for banned in GSI_KEY_NAMES:
        assert banned not in item
    restored = item_to_receipt_line_embedding(item)
    assert restored.line_id == 4
    assert restored.row_line_ids == [4, 5]
    assert restored.section_type == "TOTALS"
    assert restored.harness_key().endswith("#LINE#00004")


def test_default_row_line_ids_is_primary() -> None:
    entity = ReceiptLineEmbedding(
        image_id=_IMAGE,
        receipt_id=2,
        line_id=0,
        line_vector=[1.0],
    )
    assert entity.row_line_ids == [0]
