"""Discount detection must match WORDS, not substrings.

``DISCOUNT_WORDS`` was tested with ``w in upper``, so "OFF" matched inside
COFFEE / TOFFEE / Office and flagged 15 real prod items as discounts.
Discounts are excluded from reconciliation, so every one of those receipts
was arithmetically unbalanceable.

Bare "OFF" is also product vocabulary ("SALMON FILLET SKIN OFF", "EASY
OFF"), so a real markdown has to carry its percent/amount.
"""

from __future__ import annotations

import pytest

from receipt_upload.line_items.geometry import DISCOUNT_WORD_RE

# Real prod item names wrongly flagged as discounts by the substring scan.
NOT_DISCOUNTS = [
    "ORG BIRCHWOOD COFFEE",
    "Drip Coffee",
    "Coffee Sm",
    "VT ICD COFFEE",
    "FTO FRENCH ROAST COFFEE",
    "House Regular Coffee Cup",
    "COFFEE MATE CRMR S",
    "TOFFEE ICE CREAM BAR",
    "Sticky Butter Toffee Cake",
    "Office Supply",
    "003050188 EASY OFF",
    "SALMON FILLET SKIN OFF",
    "SALMON FILLET SKIN OFF C",
]

# Genuine markdown rows that must keep being discounts.
DISCOUNTS = [
    "30% OFF SELECT ION H",
    "20% OFF ORG PRODU",
    "BOGO 50% OFF GROC",
    "SC 20% Off Wine/Sprt",
    "25% off up to",
    "- Visit 1 - 40% OFF Entire",
    "Saved off",
    "Saved Saved off",
    "MFR COUPON",
    "TOTAL DISCOUNT",
    "PROMO APPLIED",
    "MEMBER SAVINGS",
]


@pytest.mark.parametrize("name", NOT_DISCOUNTS)
def test_product_names_are_not_discounts(name: str) -> None:
    assert not DISCOUNT_WORD_RE.search(name.upper()), name


@pytest.mark.parametrize("name", DISCOUNTS)
def test_markdown_rows_are_discounts(name: str) -> None:
    assert DISCOUNT_WORD_RE.search(name.upper()), name
