"""``item_count`` prefers real ReceiptLineItem rows over LINE_TOTAL labels.

Receipts ingested through the current pipeline never receive
``LINE_TOTAL`` word labels -- their line items are ``ReceiptLineItem``
rows written by the line-item updater's band-block decoder. The legacy
"count VALID LINE_TOTAL labels" rule therefore reported ``item_count ==
0`` for freshly-ingested receipts that hold real line items (observed on
IMG_3404 / IMG_3411 / IMG_3420, holding 5 / 1 / 7 items).

The label count stays as the fallback: 25 prod summaries carry
LINE_TOTAL labels while the extractor produced no rows, and those must
not regress to zero.
"""

from types import SimpleNamespace

import pytest

from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.entities.receipt_summary import (
    ReceiptSummary,
    resolve_item_count,
)
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel

pytestmark = pytest.mark.unit

IMAGE_ID = "2b630bec-ecd6-4c22-b9de-554dca66c146"
TIMESTAMP = "2026-01-01T00:00:00.000+00:00"


def _word(line_id: int, word_id: int, text: str) -> SimpleNamespace:
    return SimpleNamespace(
        line_id=line_id,
        word_id=word_id,
        text=text,
        bounding_box={
            "x": 0.1,
            "y": 0.5 - line_id * 0.02,
            "width": 0.2,
            "height": 0.012,
        },
    )


def _line_total_label(line_id: int) -> ReceiptWordLabel:
    return ReceiptWordLabel(
        image_id=IMAGE_ID,
        receipt_id=1,
        line_id=line_id,
        word_id=1,
        label="LINE_TOTAL",
        reasoning=None,
        timestamp_added=TIMESTAMP,
        validation_status=ValidationStatus.VALID.value,
    )


class TestResolveItemCount:
    """The resolution rule itself."""

    def test_line_items_win_when_present(self) -> None:
        assert resolve_item_count(label_item_count=0, line_item_count=5) == 5

    def test_line_items_win_even_when_labels_disagree(self) -> None:
        assert resolve_item_count(label_item_count=3, line_item_count=7) == 7

    def test_falls_back_to_labels_when_no_rows(self) -> None:
        assert resolve_item_count(label_item_count=4, line_item_count=0) == 4

    def test_falls_back_to_labels_when_not_looked_up(self) -> None:
        assert (
            resolve_item_count(label_item_count=4, line_item_count=None) == 4
        )

    def test_zero_when_neither_source_has_anything(self) -> None:
        assert resolve_item_count(label_item_count=0, line_item_count=0) == 0


class TestFromWordLabelsAndWords:
    """The constructor the summary updater Lambda calls."""

    def test_fresh_ingest_receipt_counts_line_item_rows(self) -> None:
        """IMG_3404: no LINE_TOTAL labels, 5 ReceiptLineItem rows."""
        summary = ReceiptSummary.from_word_labels_and_words(
            image_id=IMAGE_ID,
            receipt_id=1,
            merchant_name="Trader Joe's",
            word_labels=[],
            words=[_word(1, 1, "TRADER")],
            line_item_count=5,
        )
        assert summary.item_count == 5

    def test_no_rows_keeps_legacy_label_count(self) -> None:
        summary = ReceiptSummary.from_word_labels_and_words(
            image_id=IMAGE_ID,
            receipt_id=1,
            merchant_name="Sprouts Farmers Market",
            word_labels=[_line_total_label(3), _line_total_label(4)],
            words=[_word(3, 1, "2.99"), _word(4, 1, "3.49")],
            line_item_count=0,
        )
        assert summary.item_count == 2

    def test_omitting_the_argument_is_backwards_compatible(self) -> None:
        summary = ReceiptSummary.from_word_labels_and_words(
            image_id=IMAGE_ID,
            receipt_id=1,
            merchant_name="Sprouts Farmers Market",
            word_labels=[_line_total_label(3)],
            words=[_word(3, 1, "2.99")],
        )
        assert summary.item_count == 1


class TestFromReceiptData:
    """The other constructor keeps the same rule."""

    def test_line_item_rows_win(self) -> None:
        receipt = SimpleNamespace(image_id=IMAGE_ID, receipt_id=1)
        summary = ReceiptSummary.from_receipt_data(
            receipt=receipt,
            place=SimpleNamespace(merchant_name="Trader Joe's"),
            word_labels=[],
            words=[_word(1, 1, "TRADER")],
            line_item_count=7,
        )
        assert summary.item_count == 7

    def test_defaults_to_labels(self) -> None:
        receipt = SimpleNamespace(image_id=IMAGE_ID, receipt_id=1)
        summary = ReceiptSummary.from_receipt_data(
            receipt=receipt,
            place=None,
            word_labels=[_line_total_label(3)],
            words=[_word(3, 1, "2.99")],
        )
        assert summary.item_count == 1
