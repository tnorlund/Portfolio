"""Branded tender rows must never decode as line items.

``SETTLEMENT_RE`` only ever matched a row that is EXACTLY the tender word,
so every branded form slipped through and became a phantom item. A corpus
scan of prod found 12 receipts carrying one; Trader Joe's Henderson
("Visa Debit  $37.51") is the receipt that surfaced it.

The guard must not reach real food: dev stores two genuine products whose
names contain a tender word, and they are pinned here as keeps.

The vocabulary is CLOSED and every entry below is a measured observation,
not a guess -- so it goes stale silently unless someone re-measures. The
corpus drift check is that re-measurement::

    python3.13 scripts/backfill_receipt_line_items.py --check \\
        --table ReceiptsTable-d7ff76a

It is read-only (prod-safe) and prints every receipt whose STORED
reconciliation_status disagrees with a live recompute, with sums and
baselines. That is how "Paid with card (8644): 32.30" surfaced -- as a
drift row on 916d7955, not by anyone reading receipts. A new branded form
appears there the same way; PROD_TENDER_PHANTOMS is where to add it.
"""

from __future__ import annotations

import re

import pytest
from receipt_upload.line_items.geometry import (
    extract_items,
    is_settlement_row,
    is_tender_row,
)

# Every distinct branded-tender string the prod corpus scan turned up
# (12 receipts; ReceiptsTable-d7ff76a, 2026-08-04).
PROD_TENDER_PHANTOMS = [
    "Visa",  # Omaha Bar, The Chicken Shack, Yogurtland
    "VISA",  # TRADER JOE'S d224118a
    "MASTERCARD",  # Stanley of New Orleans
    "MasterCard 1394 (Swipe)",  # 614 Gravier St
    "Visa ...3931",  # Moody Market & Provisions
    "xXXX5061 MASTERCARD",  # Sprouts cee0fbe1
    "Visa Tendered: Trans *: 9 Batch#:",  # Sushi Planet
    "Local Cash",  # TRADER JOE'S d224118a / 70e013f2
    "Payment (Cash):",  # Green NV Henderson, RISE Recreational
    "Visa Debit",  # TRADER JOE'S IMG_3404 (the new golden receipt)
    "MASTERCARD ...8644 for 32.30",  # Stanley of New Orleans
    # 2026-08-05: the anchorless form. Every word here is payment
    # vocabulary, but "paid"/"with"/"card" were all AFFIXES, and affixes
    # alone are (correctly) never enough -- so this row decoded as a
    # 32.30 item and left Stanley of New Orleans (prod 916d7955) at
    # mismatch, 61.55 against the 29.25 its two real dishes sum to
    # exactly. Fixed by promoting "paid" to an anchor, which is the
    # maintenance path the note below prescribes.
    "Paid with card (8644): 32.30",  # Stanley of New Orleans
]

# Real items that carry payment vocabulary and MUST survive.
REAL_PRODUCTS = [
    "33965 PORK TENDER",  # Costco e8b658b6 / c1672313
    "E 33965 PORK TENDER",  # Costco c1672313 (with the dept prefix)
    "CHICKEN TENDER",  # Millennium Maxwell House fa48c537
    "TENDER GREENS SALAD",
    # Promoting "paid" to an anchor must not reach food. A real row needs
    # only ONE word outside the vocabulary to survive, and these have it.
    "PAID LEAVE GIFT BOX",
    "PREPAID PHONE CARD",  # substring "paid" inside "PREPAID"
    "CHICKEN WITH RICE",  # "with" is an affix, never an anchor
    "GIFT CARD",
    "VISA GIFT CARD",
    "LOCAL HONEY",
    "CASHEWS RAW",
    "STORE CREDIT VOUCHER",
    "MASTER LOCK",
    "094030409 UP VITAMIN C",
]


# Card-tail phrasings that are DELIBERATELY not in the vocabulary.
#
# The closed vocabulary is evidence-driven: every token in it was put
# there by a string that actually reached an ITEMS zone. These did not.
# A full scan of every ITEMS-section line in dev (656 receipts) and prod
# (699 receipts) on 2026-08-04 found ZERO occurrences of "ENDING IN" or
# any "<brand> ending in <digits>" form -- "AMEX ENDING IN 6081" exists
# only in test_tender.py, as a vector for card-NETWORK classification,
# which is a different function on a different code path. The nearest
# real hits are three loyalty/credit rows that are not tender at all:
# "ExtraCare Card #: ********2953" (x2) and "Applied to Account:".
#
# So these stay items rather than widening the vocabulary on speculation.
# If one ever shows up in a live items zone, add "ending"/"in" to
# PAYMENT_AFFIX_TOKENS and move the string into PROD_TENDER_PHANTOMS --
# this test failing is the signal to re-measure, not to delete it.
LATENT_NOT_IN_VOCABULARY = [
    "AMEX ENDING IN 6081",
    "ExtraCare Card #: ********2953",
]


def _bare(text: str) -> str:
    """Amount-stripped row text, exactly as both call sites build it."""
    return re.sub(r"\$?\d[\d.,]*", " ", text).strip()


@pytest.mark.parametrize("text", PROD_TENDER_PHANTOMS)
def test_prod_tender_phantoms_are_settlement_rows(text: str) -> None:
    assert is_settlement_row(_bare(text)), text


@pytest.mark.parametrize("text", REAL_PRODUCTS)
def test_real_products_are_not_settlement_rows(text: str) -> None:
    assert not is_settlement_row(_bare(text)), text


@pytest.mark.parametrize("text", LATENT_NOT_IN_VOCABULARY)
def test_card_tail_phrasings_absent_from_the_corpus_are_not_dropped(
    text: str,
) -> None:
    assert not is_settlement_row(_bare(text)), text


def test_bare_settlement_vocabulary_still_matches() -> None:
    """The pre-existing SETTLEMENT_RE forms keep working."""
    for text in (
        "BALANCE DUE",
        "Balance to pay",
        "17.98 DUE BALANCE",
        "CHANGE DUE",
        "SUB-TTL",
        "Amount Due",
        "[1 item] Sub Total",
        "AUTH DEBIT",
    ):
        assert is_settlement_row(_bare(text)), text


def _row(line_id: int, y: float, tokens: list[str]) -> list[dict]:
    return [
        {
            "line_id": line_id,
            "word_id": i + 1,
            "text": t,
            "x": 0.1 + 0.2 * i,
            "y_mid": y,
            "h": 0.02,
        }
        for i, t in enumerate(tokens)
    ]


def test_branded_tender_row_does_not_become_an_item() -> None:
    """End to end through the decoder, not just the predicate."""
    words = _row(1, 0.30, ["REAL", "WIDGET", "3.00"]) + _row(
        2, 0.25, ["Visa", "Debit", "37.51"]
    )
    items, _ = extract_items(words, {1, 2})
    assert [i["name"] for i in items] == ["REAL WIDGET"]


def test_pork_tender_still_becomes_an_item() -> None:
    words = _row(1, 0.30, ["REAL", "WIDGET", "3.00"]) + _row(
        2, 0.25, ["33965", "PORK", "TENDER", "19.51"]
    )
    items, _ = extract_items(words, {1, 2})
    prices = sorted(i["price"] for i in items)
    assert prices == [3.00, 19.51]


# --- is_tender_row: the SECTION assigner's narrower vocabulary ---------
#
# `is_settlement_row` answers "is this row an item?" and so includes the
# summary words. The section assigner needs the opposite bias on exactly
# those words: In-N-Out and Trader Joe's print a TOTAL inside the items
# block, so a guard that evicted every settlement row from ITEMS would
# fight a real merchant format. `is_tender_row` is the tender-only
# subset, and the two tests below are the fence.


@pytest.mark.parametrize("text", PROD_TENDER_PHANTOMS)
def test_prod_tender_phantoms_are_tender_rows(text: str) -> None:
    """Everything the decoder rejects as a tender, the assigner sees."""
    assert is_tender_row(_bare(text)), text


@pytest.mark.parametrize("text", REAL_PRODUCTS + LATENT_NOT_IN_VOCABULARY)
def test_real_products_are_not_tender_rows(text: str) -> None:
    assert not is_tender_row(_bare(text)), text


@pytest.mark.parametrize(
    "text",
    [
        "Sub Total",
        "SUB-TTL",
        "[1 item] Sub Total",
        "TOTAL",
        "Sales Tax",
        "TAX",
    ],
)
def test_summary_rows_are_settlement_but_not_tender(text: str) -> None:
    """The one deliberate gap between the two predicates.

    These rows must keep scoring as settlement (the decoder must never
    name an item after them) while NOT being tender (the section
    assigner must leave a mid-items total where the model put it --
    In-N-Out and Trader Joe's print one).
    """
    assert is_settlement_row(_bare(text)), text
    assert not is_tender_row(_bare(text)), text


@pytest.mark.parametrize(
    "text",
    [
        # Not settlement vocabulary either (SETTLEMENT_RE wants the row
        # to be EXACTLY the total word), but it is the row the Tropical
        # Smoothie receipt prints, so pin the direction that matters.
        "Order Total",
        "TOTAL PURCHASE",
        "Rounding",
    ],
)
def test_total_phrasings_are_not_tender_rows(text: str) -> None:
    assert not is_tender_row(_bare(text)), text


def test_is_tender_row_is_row_level_by_contract() -> None:
    """The whole row is the unit; a single OCR line is not.

    Dev Costco splits its pork tenderloin across two OCR lines. The
    line alone de-amounts to "TENDER" and matches; the ROW it belongs
    to de-amounts to "PORK TENDER" and correctly does not. A caller
    that reached for the convenient line-level call would forbid that
    row from ITEMS and kill a real item's decode.
    """
    assert is_tender_row(_bare("33965 19.51 TENDER"))
    assert not is_tender_row(_bare("PORK 33965 19.51 TENDER 41.99"))


@pytest.mark.parametrize(
    "text",
    [
        "Cash",
        "CASH",
        "Change Due",
        "CHANGE",
        "Amount Due",
        "BALANCE DUE",
        "Balance to pay",
        "AUTH DEBIT",
        "CREDIT",
        "TENDERED",
    ],
)
def test_bare_tender_vocabulary_matches(text: str) -> None:
    assert is_tender_row(_bare(text)), text
