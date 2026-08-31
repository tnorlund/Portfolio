"""Unit tests for ReceiptWordEmbedding."""

from __future__ import annotations

import pytest

from receipt_dynamo.entities.embedding_codec import (
    EMBEDDING_DIMENSIONS,
    LABEL_STATUS_VALIDATED,
    WORD_VECTOR_ATTR,
)
from receipt_dynamo.entities.receipt_word_embedding import (
    ReceiptWordEmbedding,
    item_to_receipt_word_embedding,
)

IMAGE_ID = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"


def _vector() -> list[float]:
    values = [0.0] * EMBEDDING_DIMENSIONS
    values[1] = 1.0
    return values


def _entity(**overrides: object) -> ReceiptWordEmbedding:
    fields: dict[str, object] = {
        "image_id": IMAGE_ID,
        "receipt_id": 2,
        "line_id": 5,
        "word_id": 7,
        "word_vector": _vector(),
        "text": "<EDGE> Total Tax",
        "label_status": LABEL_STATUS_VALIDATED,
        "merchant_name": "Sprouts",
    }
    fields.update(overrides)
    return ReceiptWordEmbedding(**fields)  # type: ignore[arg-type]


@pytest.mark.unit
def test_sk_and_filter_attr() -> None:
    item = _entity().to_item()
    assert item["SK"]["S"] == ("RECEIPT#00002#LINE#00005#WORD#00007#EMBEDDING")
    assert item["TYPE"]["S"] == "RECEIPT_WORD_EMBEDDING"
    assert item["label_status"]["S"] == "validated"
    assert WORD_VECTOR_ATTR in item
    assert "GSI1PK" not in item
    assert "GSI4SK" not in item


@pytest.mark.unit
def test_round_trip() -> None:
    restored = item_to_receipt_word_embedding(_entity().to_item())
    assert restored.word_id == 7
    assert restored.label_status == "validated"
    assert restored.vector_search_key.endswith("#WORD#00007")


@pytest.mark.unit
def test_rejects_unknown_label_status() -> None:
    with pytest.raises(ValueError, match="label_status"):
        _entity(label_status="valid")
