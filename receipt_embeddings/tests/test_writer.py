"""Idempotent embed-and-put writer."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from receipt_embeddings.writer import (
    label_status_for_word,
    prepare_embedding_items,
    write_embedding_items,
)

from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.data._receipt_line_embedding import EmbeddingWriteReport
from receipt_dynamo.entities.embedding_codec import EMBEDDING_DIMENSIONS
from receipt_dynamo.entities.receipt_line_embedding import ReceiptLineEmbedding

IMAGE_ID = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"


def _vector() -> list[float]:
    values = [0.0] * EMBEDDING_DIMENSIONS
    values[0] = 1.0
    return values


@pytest.mark.unit
def test_write_is_idempotent_when_keys_exist() -> None:
    dynamo = MagicMock()
    dynamo.put_embedding_items_idempotent.return_value = EmbeddingWriteReport(
        written=0, skipped_existing=2
    )
    entity = ReceiptLineEmbedding(
        image_id=IMAGE_ID,
        receipt_id=1,
        line_id=1,
        line_vector=_vector(),
        text="row",
        row_line_ids=[1],
    )
    first = write_embedding_items(dynamo, [entity])
    second = write_embedding_items(dynamo, [entity])
    assert first.written == 0
    assert second.skipped_existing == 2
    assert dynamo.put_embedding_items_idempotent.call_count == 2


@pytest.mark.unit
def test_label_status_mapping() -> None:
    valid = SimpleNamespace(validation_status=ValidationStatus.VALID.value)
    pending = SimpleNamespace(validation_status=ValidationStatus.PENDING.value)
    assert label_status_for_word([valid]) == "validated"
    assert label_status_for_word([pending]) == "pending"
    assert label_status_for_word([]) == "none"


@pytest.mark.unit
def test_prepare_builds_line_and_word_items() -> None:
    class _Receipt:
        image_id = IMAGE_ID
        receipt_id = 1

    class _Geom:
        image_id = IMAGE_ID
        receipt_id = 1
        bounding_box = {"x": 0.1, "y": 0.8, "width": 0.4, "height": 0.05}

        def calculate_centroid(self) -> tuple[float, float]:
            box = self.bounding_box
            return (
                box["x"] + box["width"] / 2,
                box["y"] + box["height"] / 2,
            )

    class _Line(_Geom):
        line_id = 1
        text = "MILK"

    class _Word(_Geom):
        line_id = 1
        word_id = 1
        text = "MILK"

    class _Details:
        receipt = _Receipt()
        lines = [_Line()]
        words = [_Word()]
        labels = []
        place = SimpleNamespace(merchant_name="Sprouts", place_id="ChIJ")

    line_key = f"IMAGE#{IMAGE_ID}#RECEIPT#00001#LINE#00001"
    word_key = f"{line_key}#WORD#00001"
    prepared = prepare_embedding_items(
        _Details(),
        vectors_by_key={line_key: _vector(), word_key: _vector()},
    )
    assert len(prepared.items) == 2
    assert prepared.skipped == []
    assert {item.vector_search_key for item in prepared.items} == {
        line_key,
        word_key,
    }
