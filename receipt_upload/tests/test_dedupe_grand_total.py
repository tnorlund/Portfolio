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
    return {(lab.line_id, lab.word_id) for lab in labels}


_PENDING = ValidationStatus.PENDING.value


def test_all_pending_keeps_lowest_invalidates_rest():
    # y is bottom-origin: header high-y, the final total prints lowest (smallest y).
    words = [
        _w(40, 1, "$43.94", 0.72, 0.30),  # "Balance to pay"
        _w(41, 1, "$43.94", 0.72, 0.22),  # bare total
        _w(
            54, 1, "$43.94", 0.72, 0.10
        ),  # "TOTAL PURCHASE" — lowest, canonical
    ]
    labels = [
        _label(40, 1, "GRAND_TOTAL", _PENDING),
        _label(41, 1, "GRAND_TOTAL", _PENDING),
        _label(54, 1, "GRAND_TOTAL", _PENDING),
    ]
    redundant = dedupe_grand_total(words, labels)
    # canonical (lowest cy => smallest y) is L54; the two above are redundant
    assert _keys(redundant) == {(40, 1), (41, 1)}
    assert all(lab.validation_status == _PENDING for lab in redundant)


def test_keyword_anchored_copy_wins_over_lower_stray():
    """Regression for the Smith's June22 receipt: the grand total 9.08 appeared as
    "TOTAL: 9.08" and as a stray 9.08 that landed lowest (in the "TOTAL NUMBER OF
    ITEMS SOLD" footer). Lowest-y alone kept the stray, a validator then
    invalidated it, and the receipt was left with no grand total. The explicit
    "TOTAL:" row must be elected canonical even though it is not the lowest.
    """
    words = [
        _w(19, 1, "TOTAL:", 0.10, 0.40),  # keyword anchors line 19
        _w(19, 2, "9.08", 0.72, 0.40),  # the real grand total
        _w(14, 1, "9.08", 0.72, 0.55),  # "BALANCE" restatement (higher)
        # Stray 9.08 sits lowest, on a "TOTAL NUMBER OF ITEMS SOLD" count row:
        # the row carries "TOTAL" but the count disqualifiers must stop it from
        # anchoring (else lowest-y would re-elect this stray).
        _w(28, 1, "TOTAL", 0.05, 0.12),
        _w(28, 2, "NUMBER", 0.15, 0.12),
        _w(28, 3, "OF", 0.25, 0.12),
        _w(28, 4, "ITEMS", 0.35, 0.12),
        _w(28, 5, "SOLD", 0.45, 0.12),
        _w(28, 6, "9.08", 0.72, 0.12),
    ]
    labels = [
        _label(19, 2, "GRAND_TOTAL", _PENDING),
        _label(14, 1, "GRAND_TOTAL", _PENDING),
        _label(28, 6, "GRAND_TOTAL", _PENDING),
    ]
    redundant = dedupe_grand_total(words, labels)
    # The keyword-anchored "TOTAL: 9.08" (L19) is kept; the two restatements go —
    # including the stray, even though its row contains the word "TOTAL".
    assert _keys(redundant) == {(14, 1), (28, 6)}
    assert (19, 2) not in _keys(redundant)


def test_lowest_y_among_keyword_anchored_copies():
    """When several copies are keyword-anchored, keep the lowest-on-receipt one."""
    words = [
        _w(10, 1, "BALANCE", 0.10, 0.40),
        _w(10, 2, "9.08", 0.72, 0.40),  # anchored, higher
        _w(20, 1, "TOTAL", 0.10, 0.15),
        _w(20, 2, "9.08", 0.72, 0.15),  # anchored, lowest -> canonical
    ]
    labels = [
        _label(10, 2, "GRAND_TOTAL", _PENDING),
        _label(20, 2, "GRAND_TOTAL", _PENDING),
    ]
    redundant = dedupe_grand_total(words, labels)
    assert _keys(redundant) == {(10, 2)}


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
        _label(
            54, 1, "GRAND_TOTAL", ValidationStatus.VALID.value
        ),  # confirmed
    ]
    redundant = dedupe_grand_total(words, labels)
    # The VALID copy is NEVER returned; both PENDING dupes are.
    assert _keys(redundant) == {(40, 1), (41, 1)}
    assert (54, 1) not in _keys(redundant)
    assert all(lab.validation_status == _PENDING for lab in redundant)


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


def _section(section_type, line_ids):
    return SimpleNamespace(section_type=section_type, line_ids=line_ids)


def _payment_vs_total_words():
    """The 2026-08-10 audit shape: the printed total row and the tender row
    carry the identical amount, BOTH rows are grand-total-keyword anchored
    ("TOTAL" / "AMOUNT"), and the tender row prints lower on the paper."""
    return [
        _w(19, 1, "TOTAL", 0.10, 0.30),
        _w(19, 2, "$20.56", 0.72, 0.30),  # the printed grand total
        _w(25, 1, "DEBIT", 0.05, 0.18),
        _w(25, 2, "PAYMENT", 0.20, 0.18),
        _w(25, 3, "AMOUNT", 0.40, 0.18),  # "amount" keyword-anchors the row
        _w(25, 4, "$20.56", 0.72, 0.18),  # tender restatement, lower
    ]


def _payment_vs_total_labels():
    return [
        _label(19, 2, "GRAND_TOTAL", _PENDING),
        _label(25, 4, "GRAND_TOTAL", _PENDING),
    ]


def test_without_sections_lower_anchored_payment_row_wins():
    """Documents the fallback (and the audited bug shape): with no section
    info, both rows are keyword-anchored so lowest-y elects the tender row and
    the printed TOTAL row is reported redundant."""
    redundant = dedupe_grand_total(
        _payment_vs_total_words(), _payment_vs_total_labels()
    )
    assert _keys(redundant) == {(19, 2)}


def test_empty_sections_matches_no_sections():
    """An empty section list must reproduce the pre-section election exactly."""
    redundant = dedupe_grand_total(
        _payment_vs_total_words(), _payment_vs_total_labels(), sections=[]
    )
    assert _keys(redundant) == {(19, 2)}


def test_total_line_section_beats_lower_anchored_payment_row():
    """Regression for the 2026-08-10 audit (dev images 6e00af0f / f98c0c1a /
    8f31f88a): swift-worker-v1 had already marked the printed "TOTAL $20.56"
    row a TOTAL_LINE section (0.95) with the duplicate inside a PAYMENT
    section, yet dedupe invalidated the printed total and kept the tender-row
    copy. With sections supplied, the TOTAL_LINE copy must be canonical."""
    sections = [
        _section("TOTAL_LINE", [19]),
        _section("PAYMENT", [24, 25, 26]),
    ]
    redundant = dedupe_grand_total(
        _payment_vs_total_words(), _payment_vs_total_labels(), sections
    )
    assert _keys(redundant) == {(25, 4)}
    assert (19, 2) not in _keys(redundant)


def test_unsectioned_copy_beats_payment_section_copy():
    """Even without a TOTAL_LINE section, a copy inside the PAYMENT (tender)
    block must lose to a copy in no section at all."""
    sections = [_section("PAYMENT", [25])]
    redundant = dedupe_grand_total(
        _payment_vs_total_words(), _payment_vs_total_labels(), sections
    )
    assert _keys(redundant) == {(25, 4)}


def test_section_type_enum_is_unwrapped():
    """ReceiptSection entities may carry SectionType enum members, not bare
    strings; the tiebreak must read them the same way."""
    from receipt_dynamo.constants import SectionType

    sections = [
        _section(SectionType.TOTAL_LINE, [19]),
        _section(SectionType.PAYMENT, [25]),
    ]
    redundant = dedupe_grand_total(
        _payment_vs_total_words(), _payment_vs_total_labels(), sections
    )
    assert _keys(redundant) == {(25, 4)}


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
