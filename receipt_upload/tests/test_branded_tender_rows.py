"""Branded tender rows must never decode as line items.

``SETTLEMENT_RE`` only ever matched a row that is EXACTLY the tender word,
so every branded form slipped through and became a phantom item. A corpus
scan of prod found 12 receipts carrying one; Trader Joe's Henderson
("Visa Debit  $37.51") is the receipt that surfaced it.

The guard must not reach real food: dev stores two genuine products whose
names contain a tender word, and they are pinned here as keeps.
"""

from __future__ import annotations

import re

import pytest

from receipt_upload.line_items.geometry import extract_items, is_settlement_row

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
]

# Real items that carry payment vocabulary and MUST survive.
REAL_PRODUCTS = [
    "33965 PORK TENDER",  # Costco e8b658b6 / c1672313
    "E 33965 PORK TENDER",  # Costco c1672313 (with the dept prefix)
    "CHICKEN TENDER",  # Millennium Maxwell House fa48c537
    "TENDER GREENS SALAD",
    "GIFT CARD",
    "VISA GIFT CARD",
    "LOCAL HONEY",
    "CASHEWS RAW",
    "STORE CREDIT VOUCHER",
    "MASTER LOCK",
    "094030409 UP VITAMIN C",
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
