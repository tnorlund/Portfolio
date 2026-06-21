"""Regression tests for GRAND_TOTAL dedup.

Built from the Trader Joe's June21 receipt, where the final total $43.94 was
printed three times ("Balance to pay", a bare total, "TOTAL PURCHASE") and the
first-pass model tagged every copy GRAND_TOTAL. A receipt has exactly one grand
total; the dedup keeps the canonical (lowest-on-receipt) copy and reports the
equal-valued restatements for invalidation.
"""

from types import SimpleNamespace

from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.entities import ReceiptWordLabel

from receipt_upload.line_items import dedupe_grand_total

IMAGE_ID = "00000000-0000-4000-8000-000000000a01"


def _w(line_id, word_id, text, x, y):
    return SimpleNamespace(
        image_id=IMAGE_ID,
        receipt_id=1,
        line_id=line_id,
        word_id=word_id,
        text=text,
        bounding_box={"x": x, "y": y, "width": 0.08, "height": 0.02},
    )


def _label(line_id, word_id, label, status=ValidationStatus.VALID.value):
    return ReceiptWordLabel(
        image_id=IMAGE_ID,
        receipt_id=1,
        line_id=line_id,
        word_id=word_id,
        label=label,
        reasoning="test",
        timestamp_added="2026-01-01T00:00:00.000+00:00",
        validation_status=status,
    )


def _keys(labels):
    return {(l.line_id, l.word_id) for l in labels}


_PENDING = ValidationStatus.PENDING.value


def test_all_pending_keeps_lowest_invalidates_rest():
    # y is bottom-origin: header high-y, the final total prints lowest (smallest y).
    words = [
        _w(40, 1, "$43.94", 0.72, 0.30),  # "Balance to pay"
        _w(41, 1, "$43.94", 0.72, 0.22),  # bare total
        _w(54, 1, "$43.94", 0.72, 0.10),  # "TOTAL PURCHASE" — lowest, canonical
    ]
    labels = [
        _label(40, 1, "GRAND_TOTAL", _PENDING),
        _label(41, 1, "GRAND_TOTAL", _PENDING),
        _label(54, 1, "GRAND_TOTAL", _PENDING),
    ]
    redundant = dedupe_grand_total(words, labels)
    # canonical (lowest cy => smallest y) is L54; the two above are redundant
    assert _keys(redundant) == {(40, 1), (41, 1)}
    assert all(l.validation_status == _PENDING for l in redundant)


def test_confirmed_copy_is_canonical_only_pending_dropped():
    """A VALID (human/validator) copy is canonical and never invalidated; only
    its PENDING duplicates are reported."""
    words = [
        _w(40, 1, "$43.94", 0.72, 0.30),
        _w(41, 1, "$43.94", 0.72, 0.22),
        _w(54, 1, "$43.94", 0.72, 0.10),
    ]
    labels = [
        _label(40, 1, "GRAND_TOTAL", _PENDING),
        _label(41, 1, "GRAND_TOTAL", _PENDING),
        _label(54, 1, "GRAND_TOTAL", ValidationStatus.VALID.value),  # confirmed
    ]
    redundant = dedupe_grand_total(words, labels)
    # The VALID copy is NEVER returned; both PENDING dupes are.
    assert _keys(redundant) == {(40, 1), (41, 1)}
    assert (54, 1) not in _keys(redundant)
    assert all(l.validation_status == _PENDING for l in redundant)


def test_multiple_confirmed_copies_abstain():
    """With >=2 deliberate (VALID) copies we don't reconcile — never override."""
    words = [
        _w(40, 1, "$43.94", 0.72, 0.30),
        _w(41, 1, "$43.94", 0.72, 0.22),
        _w(54, 1, "$43.94", 0.72, 0.10),
    ]
    labels = [
        _label(40, 1, "GRAND_TOTAL", _PENDING),
        _label(41, 1, "GRAND_TOTAL", ValidationStatus.VALID.value),
        _label(54, 1, "GRAND_TOTAL", ValidationStatus.VALID.value),
    ]
    # Two VALID copies -> abstain entirely, even the PENDING one is left alone.
    assert dedupe_grand_total(words, labels) == []


def test_single_grand_total_is_untouched():
    words = [_w(54, 1, "$43.94", 0.72, 0.10)]
    labels = [_label(54, 1, "GRAND_TOTAL")]
    assert dedupe_grand_total(words, labels) == []


def test_different_values_are_not_deduped():
    """Distinct grand-total values are conservatively left alone."""
    words = [
        _w(40, 1, "$43.94", 0.72, 0.30),
        _w(54, 1, "$41.00", 0.72, 0.10),
    ]
    labels = [
        _label(40, 1, "GRAND_TOTAL"),
        _label(54, 1, "GRAND_TOTAL"),
    ]
    assert dedupe_grand_total(words, labels) == []


def test_invalid_duplicate_is_ignored():
    """An already-INVALID copy doesn't count toward the duplicate set."""
    words = [
        _w(40, 1, "$43.94", 0.72, 0.30),
        _w(54, 1, "$43.94", 0.72, 0.10),
    ]
    labels = [
        _label(40, 1, "GRAND_TOTAL", ValidationStatus.INVALID.value),
        _label(54, 1, "GRAND_TOTAL"),
    ]
    # only one ACTIVE GRAND_TOTAL remains -> nothing to dedupe
    assert dedupe_grand_total(words, labels) == []
