"""Column-header rows must never be minted as a product name.

The band-block decoder emits an item whose NAME is the receipt's column
header ("Unit Price", "Item Qty Price Total", "Description Qty Amount"),
its markdown annotation ("ORIGINAL PRICE", "Reguler Price") or its footer
legalese. A sweep of every decoded item in dev (691 receipts) and prod
(730 receipts) on 2026-08-05 found 22 such items, 18 of them on receipts
that reconcile as a full ``match`` -- so the arithmetic gate cannot see
them and they flow straight into the training corpus as PRODUCT_NAME.

The guard is a CLOSED vocabulary, tested here in both directions: every
boilerplate name the corpus actually produced must be recognized, and
every real product that happens to carry a header word must survive.

The keeps are the load-bearing half. A receipt genuinely sells items whose
names contain "PRICE", "ORIGINAL", "REGULAR", "ITEM" and "EACH", and all
of them are here, taken from the corpus rather than invented.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from receipt_upload.line_items.geometry import is_column_header_row

# Every distinct boilerplate item NAME the dev + prod sweep produced
# (2026-08-05; ReceiptsTable-dc5be22 and ReceiptsTable-d7ff76a).
CORPUS_HEADER_NAMES = [
    "# • ITEM PRICE",
    "4 ITEMS",
    "9.75% TAX",
    "AMOUNT:",
    "DESCRIPTION QTY",
    "DESCRIPTION QTY AMOUNT",
    "DESCRIPTION QTY PRICE TOTAL",
    "Description Qty Amount",
    "ITEM QTY PRICE",
    "ITEM QTY PRICE AMOUNT",
    "ITEM QTY PRICE TOTAL",
    "ITEM UTY PRICE TOTAL",  # OCR reads "QTY" as "UTY"
    "Item Qty Price",
    "Item Uty Price Total",
    "ORIGINAL PRICE",
    "ORIGINAL PRICE:",
    "PRICING",
    "REGUL R PRICE",  # OCR dropped the "A" out of REGULAR
    "REGULER PRICE",  # OCR misspelling
    "Reguler Price",
    "TOTAL TAX",
    "TOTAL: SALE",
    "UNIT PRICE",
    "UNIT PRICE:",
    "Unit Price",
    "of trusards or alcotial. Restrict",  # "...or alcohol. Restrictions"
]

# Real items from the same sweep that carry header vocabulary and MUST
# survive. Every one of these is a genuine purchased line.
REAL_PRODUCTS = [
    "BAG SALE PAPER EA",
    "BANANA EACH",
    "BEAUTY SECRETS NOVELTY TWEEZERS GARDEN",
    "CUSTOM ITEM",
    "EA",
    "EACH",
    "HARD DELUXE 19.9 EA",
    "HOUSE REGULAR COFFEE CUP",
    "ITEM SUBTRACTED EA",
    "LEMON EACH",
    "ORIGINAL OAK SMOKED SALMO",
    "PEPPER BELL GREEN EACH",
    "POTATO SWEET EACH",
    "PRICE MATCH",
    "THE ORIGINAL MINI KABOB WRAP",
    "TOTAL SAVINGS (7 PERCENT)",
    "VERIFIED TOTAL SAVINGS",
    "16oz TRI TIP",
    "GRACO 50 MESH GUN FILTER 015-029 TIP",
    "MAX REFUND VALUE",
    "BOTTLE RETURN",
]

# The OFF-inside-COFFEE class of bug, pinned. A substring scan for the
# OCR misread "UTY" matches inside "BEAUTY"; one for "EA" matches inside
# almost everything. Whole-token matching is what makes the vocabulary
# safe, so these are asserted explicitly rather than left implicit.
SUBSTRING_TRAPS = [
    "BEAUTY SECRETS NOVELTY TWEEZERS GARDEN",  # UTY inside BEAUTY
    "PRICELESS COLLECTION",  # PRICE inside PRICELESS
    "UNITED FARMS SALSA",  # UNIT inside UNITED
    "TOTALLY CHOCOLATE",  # TOTAL inside TOTALLY
    "SUBTOTALLY IRRELEVANT BRAND",
    "ITEMIZED TOOL SET",  # ITEM inside ITEMIZED
]


@pytest.mark.parametrize("name", CORPUS_HEADER_NAMES)
def test_corpus_header_names_are_recognized(name: str) -> None:
    assert is_column_header_row(name), name


@pytest.mark.parametrize("name", REAL_PRODUCTS)
def test_real_products_survive(name: str) -> None:
    assert not is_column_header_row(name), name


@pytest.mark.parametrize("name", SUBSTRING_TRAPS)
def test_header_words_inside_longer_words_do_not_match(name: str) -> None:
    assert not is_column_header_row(name), name


def test_affixes_alone_are_never_a_header() -> None:
    """A qualifier with no anchor is unit vocabulary, not a header.

    Real items end in "EACH" / "EA" all the time, and "SALE" and
    "ORIGINAL" open real product names. Only an ANCHOR (a column noun)
    can start the match, exactly as a tender word must for
    ``is_settlement_row``.
    """
    for text in ("EACH", "EA", "SALE", "ORIGINAL", "REGULAR", "RETAIL"):
        assert not is_column_header_row(text), text


def test_empty_and_numeric_names_are_not_headers() -> None:
    for text in ("", "   ", "764666103221", "1900 1", "$3.19"):
        assert not is_column_header_row(text), text


def test_restriction_footer_is_recognized_through_ocr_damage() -> None:
    """Regal's ticket footer, as Vision actually read it.

    Deliberately the ONLY legalese family in the vocabulary. The corpus's
    other footer-shaped names are real charged rows or real annotations --
    "BOTTLE RETURN" is a CRV deposit and "MAX REFUND VALUE" appears 40
    times -- so REFUND / RETURN / VALUE are not vocabulary and those rows
    keep their names.
    """
    assert is_column_header_row("of trusards or alcotial. Restrict")
    assert is_column_header_row("Restrictions apply")
    assert not is_column_header_row("MAX REFUND VALUE")
    assert not is_column_header_row("BOTTLE RETURN")


def test_no_golden_truth_item_is_read_as_a_header() -> None:
    """The hand-labeled golden set is the strongest keep-list there is."""
    fixture = Path(__file__).parent / "fixtures" / "line_items_golden.json"
    golden = json.load(open(fixture))
    eaten = [
        (receipt["merchant"], item.get("name"))
        for receipt in golden["receipts"]
        for item in receipt["true_items"]
        if is_column_header_row(item.get("name") or "")
    ]
    assert not eaten, f"golden true_items read as headers: {eaten}"


def test_vocabulary_is_disjoint_from_the_settlement_vocabulary() -> None:
    """Two guards, two jobs.

    A row is either a tender row or a header row; nothing should be both,
    and an overlap would mean one vocabulary had drifted into the other's
    territory (where a change to one silently changes the other).
    """
    from receipt_upload.line_items.geometry import (
        COLUMN_HEADER_ANCHOR_TOKENS,
        TENDER_ANCHOR_TOKENS,
    )

    assert not (COLUMN_HEADER_ANCHOR_TOKENS & TENDER_ANCHOR_TOKENS)


def test_amounts_are_already_stripped_by_the_caller() -> None:
    """The predicate reads a decoded NAME, not raw row text.

    ``parse_band`` removes the price before the name reaches here, so
    "Unit Price 25.00" arrives as "Unit Price". Passing raw text is not
    the contract, but it must not blow up, and a trailing amount must not
    rescue a header from the match.
    """
    assert is_column_header_row("Unit Price 25.00")
    assert re.search(r"\d", "Unit Price 25.00")
