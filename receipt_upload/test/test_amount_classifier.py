from types import SimpleNamespace

from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.entities import ReceiptWordLabel
from receipt_upload.label_validation.amount_classifier import (
    classify_amount_labels,
)

IMAGE_ID = "00000000-0000-4000-8000-000000000001"


def _word(line_id: int, word_id: int, text: str) -> SimpleNamespace:
    return SimpleNamespace(line_id=line_id, word_id=word_id, text=text)


def _amount(line_id: int, word_id: int) -> ReceiptWordLabel:
    return ReceiptWordLabel(
        image_id=IMAGE_ID,
        receipt_id=1,
        line_id=line_id,
        word_id=word_id,
        label="AMOUNT",
        reasoning="test",
        timestamp_added="2026-01-01T00:00:00+00:00",
        validation_status=ValidationStatus.PENDING.value,
    )


def test_classifies_keyword_and_exact_math_amounts():
    words = [
        _word(1, 1, "Coffee"),
        _word(1, 2, "$5,00"),
        _word(2, 1, "Tea"),
        _word(2, 2, "$3,82"),
        _word(3, 1, "Subtotal"),
        _word(3, 2, "$8,82"),
        _word(4, 1, "Tax"),
        _word(4, 2, "$0.72"),
        _word(5, 1, "Total"),
        _word(5, 2, "$9.54"),
    ]
    labels = [_amount(line, 2) for line in range(1, 6)]

    result = classify_amount_labels(words, labels)

    assert result[(1, 2)].label == "LINE_TOTAL"
    assert result[(2, 2)].label == "LINE_TOTAL"
    assert result[(3, 2)].label == "SUBTOTAL"
    assert result[(4, 2)].label == "TAX"
    assert result[(5, 2)].label == "GRAND_TOTAL"


def test_leaves_ambiguous_amount_unclassified():
    words = [_word(1, 1, "Random"), _word(1, 2, "$8.82")]
    labels = [_amount(1, 2)]

    result = classify_amount_labels(words, labels)

    assert result == {}
