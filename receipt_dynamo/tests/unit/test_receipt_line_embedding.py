"""Unit tests for ReceiptLineEmbedding (no GSI keys, dedicated vector attr)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from receipt_dynamo.data._receipt_line_embedding import _ReceiptLineEmbedding
from receipt_dynamo.entities.embedding_codec import (
    EMBEDDING_DIMENSIONS,
    LINE_VECTOR_ATTR,
)
from receipt_dynamo.entities.receipt_line_embedding import (
    ReceiptLineEmbedding,
    item_to_receipt_line_embedding,
)

IMAGE_ID = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
TABLE = "ReceiptsTable-dc5be22"


def _vector() -> list[float]:
    values = [0.0] * EMBEDDING_DIMENSIONS
    values[0] = 1.0
    return values


def _entity(**overrides: object) -> ReceiptLineEmbedding:
    fields: dict[str, object] = {
        "image_id": IMAGE_ID,
        "receipt_id": 1,
        "line_id": 3,
        "line_vector": _vector(),
        "text": "<EDGE>\nMILK 2%\n<EDGE>",
        "row_line_ids": [3, 4],
        "merchant_name": "Sprouts",
        "place_id": "ChIJ",
        "section_type": "ITEMS",
    }
    fields.update(overrides)
    return ReceiptLineEmbedding(**fields)  # type: ignore[arg-type]


@pytest.mark.unit
def test_sk_is_under_receipt_prefix_and_embedding_suffix() -> None:
    item = _entity().to_item()
    assert item["PK"]["S"] == f"IMAGE#{IMAGE_ID}"
    assert item["SK"]["S"] == "RECEIPT#00001#LINE#00003#EMBEDDING"
    assert item["TYPE"]["S"] == "RECEIPT_LINE_EMBEDDING"
    assert LINE_VECTOR_ATTR in item
    assert item[LINE_VECTOR_ATTR]["L"][0] == {"N": "1.0"}
    for gsi in (
        "GSI1PK",
        "GSI1SK",
        "GSI2PK",
        "GSI2SK",
        "GSI3PK",
        "GSI3SK",
        "GSI4PK",
        "GSI4SK",
    ):
        assert gsi not in item


@pytest.mark.unit
def test_round_trip_preserves_projection_attrs() -> None:
    original = _entity()
    restored = item_to_receipt_line_embedding(original.to_item())
    assert restored.image_id == original.image_id
    assert restored.line_id == 3
    assert restored.row_line_ids == [3, 4]
    assert restored.section_type == "ITEMS"
    assert restored.line_vector[0] == pytest.approx(1.0)
    assert restored.vector_search_key == (
        f"IMAGE#{IMAGE_ID}#RECEIPT#00001#LINE#00003"
    )


@pytest.mark.unit
def test_rejects_wrong_dimension() -> None:
    with pytest.raises(ValueError, match="1536"):
        _entity(line_vector=[1.0, 0.0])


@pytest.mark.unit
def test_line_id_must_belong_to_row() -> None:
    with pytest.raises(ValueError, match="row_line_ids"):
        _entity(row_line_ids=[9, 10])


@pytest.mark.unit
def test_put_scopes_existence_check_to_this_run_keys() -> None:
    """Never scan the table; skip only the keys this call attempted."""

    existing = _entity()
    fresh = _entity(line_id=8, row_line_ids=[8])
    client = MagicMock()
    client.batch_get_item.return_value = {
        "Responses": {TABLE: [existing.key]},
        "UnprocessedKeys": {},
    }
    client.batch_write_item.return_value = {"UnprocessedItems": {}}

    class _Writer(_ReceiptLineEmbedding):
        def __init__(self) -> None:
            self._client = client
            self.table_name = TABLE

    report = _Writer().put_embedding_items_idempotent([existing, fresh])
    requested = client.batch_get_item.call_args.kwargs["RequestItems"][TABLE][
        "Keys"
    ]
    assert requested == [existing.key, fresh.key]
    assert report.skipped_keys == [existing.vector_search_key]
    assert report.written_keys == [fresh.vector_search_key]
    written_item = client.batch_write_item.call_args.kwargs["RequestItems"][
        TABLE
    ][0]["PutRequest"]["Item"]
    assert written_item["SK"] == fresh.key["SK"]
