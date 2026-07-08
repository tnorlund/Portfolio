"""Canonical receipt-section vocabulary and seed projections.

The font-intelligence epic reframes the font model from ``merchant -> font`` to
``(merchant, section) -> (family, face)``. Section is the strongest prior on
face (header -> display, body -> regular, totals -> bold), so we need a single
canonical section vocabulary and two ways to *seed* it from evidence we already
have:

1. **Word labels** (``CORE_LABELS``) -> section. A ``GRAND_TOTAL`` word sits in
   the total line; a ``PRODUCT_NAME`` word sits in the items block. This turns
   the existing hand-validated label corpus into free section seeds.
2. **stylescan rules** -> section. ``stylescan.py`` already classifies each
   visual line for all nine merchants, but with per-merchant ad-hoc names
   (``warehouse_header``, ``extracare``, ``qty_line`` ...). This module folds
   those into the canonical vocabulary so all nine speak one language.

Both are *seeds*: they land as ``SECTION_*`` ``ReceiptWordLabel`` rows with
``validation_status=PENDING`` and are promoted by QA (M1). Nothing here writes
to Dynamo — this is pure projection logic.

The canonical vocabulary is stylescan's ten sections (epic sec. 4.1).
"""

from __future__ import annotations

from typing import Optional

# --- canonical section vocabulary (epic sec. 4.1) -------------------------

CANONICAL_SECTIONS: tuple[str, ...] = (
    "storefront",  # merchant name / logo / hours / self-checkout lane header
    "address",  # street address + store phone
    "items",  # purchased line items (name / qty / unit price / line total)
    "section_header",  # department dividers ("PRODUCE", "GROCERY", ...)
    "summary",  # subtotal / tax / discounts / item counts
    "total_line",  # the grand-total line
    "payment",  # tender: card / auth / change / cash back
    "survey",  # post-purchase survey / sweepstakes invite
    "footer",  # thank-you / policy / rewards trailers / register metadata
    "barcode",  # numeric barcode captions
)
CANONICAL_SECTION_SET = frozenset(CANONICAL_SECTIONS)

# The label namespace persisted in DynamoDB: SECTION_<CANONICAL>.
SECTION_LABEL_PREFIX = "SECTION_"


def is_canonical_section(section: Optional[str]) -> bool:
    """True if ``section`` is one of the ten canonical section names."""
    return section in CANONICAL_SECTION_SET


def section_label(section: str) -> str:
    """Canonical section -> its ``SECTION_*`` label string (e.g. total_line ->
    ``SECTION_TOTAL_LINE``)."""
    if not is_canonical_section(section):
        raise ValueError(f"unknown canonical section {section!r}")
    return SECTION_LABEL_PREFIX + section.upper()


def parse_section_label(label: Optional[str]) -> Optional[str]:
    """``SECTION_*`` label string -> canonical section, or None if ``label`` is
    not a section label / not canonical."""
    if not label or not label.upper().startswith(SECTION_LABEL_PREFIX):
        return None
    section = label[len(SECTION_LABEL_PREFIX):].lower()
    return section if is_canonical_section(section) else None


# --- CORE_LABELS -> section projection ------------------------------------
#
# confidence:
#   "high"   -> the label has an unambiguous section home; safe to seed.
#   "medium" -> defensible but placement varies by merchant/receipt; seed only
#               when explicitly asked (the seed builder defaults to high-only).
# Labels absent from this map (DATE, TIME) are too positionally scattered to
# seed and are left to M1 propagation.

CORE_LABEL_SECTION: dict[str, tuple[str, str]] = {
    # storefront
    "MERCHANT_NAME": ("storefront", "high"),
    "STORE_HOURS": ("storefront", "high"),
    # address block
    "ADDRESS_LINE": ("address", "high"),
    "PHONE_NUMBER": ("address", "high"),
    # items
    "PRODUCT_NAME": ("items", "high"),
    "QUANTITY": ("items", "high"),
    "UNIT_PRICE": ("items", "high"),
    "LINE_TOTAL": ("items", "high"),
    # summary block (subtotal/tax are anchors; the rest sit here but can also
    # appear inline with items, hence medium)
    "SUBTOTAL": ("summary", "high"),
    "TAX": ("summary", "high"),
    "DISCOUNT": ("summary", "medium"),
    "COUPON": ("summary", "medium"),
    "TIP": ("summary", "medium"),
    "REFUND": ("summary", "medium"),
    # the grand total
    "GRAND_TOTAL": ("total_line", "high"),
    # payment / tender exchange
    "PAYMENT_METHOD": ("payment", "high"),
    "CHANGE": ("payment", "medium"),
    "CASH_BACK": ("payment", "medium"),
    # loyalty id: near tender on most receipts, but Costco prints the member #
    # in the header -> genuinely ambiguous, medium.
    "LOYALTY_ID": ("payment", "medium"),
    # website: usually the footer URL, occasionally the storefront header.
    "WEBSITE": ("footer", "medium"),
}


def section_for_core_label(label: Optional[str]) -> Optional[str]:
    """CORE label -> canonical section, or None if it has no confident seed."""
    if not label:
        return None
    entry = CORE_LABEL_SECTION.get(label.upper())
    return entry[0] if entry else None


def core_label_confidence(label: Optional[str]) -> Optional[str]:
    """CORE label -> seed confidence ("high"/"medium"), or None."""
    if not label:
        return None
    entry = CORE_LABEL_SECTION.get(label.upper())
    return entry[1] if entry else None


# --- stylescan raw names -> canonical section -----------------------------
#
# stylescan.py emits per-merchant rule names plus a few generic ones. This
# folds every raw name any merchant's rules can produce into the canonical
# vocabulary. Non-obvious folds are commented; "->None" means "not a section"
# (layout artifact / unclassified) and should not seed.

STYLESCAN_TO_SECTION: dict[str, Optional[str]] = {
    # --- generic _classify outputs ---
    "section_header": "section_header",
    "barcode_caption": "barcode",
    "item": "items",
    "separator": None,  # rule of dashes/asterisks, not a section
    "other": None,  # unclassified
    # --- storefront / header ---
    "store_hours": "storefront",
    "store_header": "storefront",
    "warehouse_header": "storefront",  # Costco "WHOLESALE #123"
    "self_checkout": "storefront",  # Costco SELF-CHECKOUT lane header
    "member": "storefront",  # Costco member # printed in the header block
    # --- address ---
    "address": "address",
    # --- items ---
    "qty_line": "items",  # TJ "N @ $price" weigh/qty line
    "item_header": "items",  # Wild Fork Item/Qty/Price column header
    "pharmacy": "items",  # CVS RX#/vaccine -> the purchased service lines
    # --- summary ---
    "summary": "summary",
    "savings": "summary",  # Costco/Vons money-off lines
    "discount": "summary",  # Home Depot discount lines
    "fsa": "summary",  # CVS "FSA Eligible Total" -> a financial subtotal
    "items_sold": "summary",  # Costco "ITEMS SOLD" count line near totals
    # --- total ---
    "total_line": "total_line",
    # --- payment ---
    "payment": "payment",
    # --- survey ---
    "survey": "survey",
    # --- footer / trailers / register metadata ---
    "footer": "footer",
    "points": "footer",  # Vons rewards-earned trailer
    "extracare": "footer",  # CVS ExtraBucks reward coupons print at bottom
    "policy": "footer",  # Wild Fork FEFO/returns policy blurb
    "transaction": "footer",  # ticket#/station/cashier metadata trailer
    "reg_line": "footer",  # CVS REG#/TRN#/CSHR# register metadata
    "note": "footer",  # In-N-Out order note
}


def normalize_stylescan_section(raw: Optional[str]) -> Optional[str]:
    """stylescan raw section name -> canonical section, or None if it maps to
    no section (layout artifact / unclassified / unknown)."""
    if not raw:
        return None
    return STYLESCAN_TO_SECTION.get(raw.lower())
