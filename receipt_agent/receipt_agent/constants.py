"""Shared constants for receipt_agent.

CORE_LABELS is imported from receipt_dynamo.constants (single source of truth).
This module extends with receipt_agent-specific groupings and relationships.
"""

from receipt_dynamo.constants import CORE_LABELS

# Set of valid core label names for quick lookup
CORE_LABELS_SET = set(CORE_LABELS.keys())

# =============================================================================
# Label Semantic Groupings (for pattern analysis)
# =============================================================================

# Semantic groupings of related labels for pattern understanding
LABEL_GROUPS = {
    "header": {
        "MERCHANT_NAME",
        "STORE_HOURS",
        "PHONE_NUMBER",
        "WEBSITE",
        "LOYALTY_ID",
        "ADDRESS_LINE",
    },
    "metadata": {"DATE", "TIME", "PAYMENT_METHOD"},
    "line_items": {"PRODUCT_NAME", "QUANTITY", "UNIT_PRICE", "LINE_TOTAL"},
    "discounts": {"COUPON", "DISCOUNT"},
    "totals": {"SUBTOTAL", "TAX", "GRAND_TOTAL"},
    "payment": {"CHANGE", "CASH_BACK", "REFUND"},
}

# Map each label to its group
LABEL_TO_GROUP = {
    label: group_name
    for group_name, labels in LABEL_GROUPS.items()
    for label in labels
}

# Priority within-group pairs (important relationships within semantic groups)
# NOTE: These define important spatial relationships between labels that commonly
# appear on the SAME LINE (e.g., PRODUCT_NAME next to QUANTITY). This is distinct
# from CONFLICTING_LABEL_PAIRS which defines labels that should never be on the
# SAME WORD. A pair can be in both sets: important when on same line, but invalid
# if both labels are applied to a single word.
WITHIN_GROUP_PRIORITY_PAIRS = {
    # Line item internal structure
    ("PRODUCT_NAME", "UNIT_PRICE"),
    ("UNIT_PRICE", "PRODUCT_NAME"),
    ("QUANTITY", "LINE_TOTAL"),
    ("LINE_TOTAL", "QUANTITY"),
    ("PRODUCT_NAME", "QUANTITY"),
    ("QUANTITY", "PRODUCT_NAME"),
    # Totals section structure
    ("SUBTOTAL", "TAX"),
    ("TAX", "SUBTOTAL"),
    ("TAX", "GRAND_TOTAL"),
    ("GRAND_TOTAL", "TAX"),
}

# Priority cross-group pairs (important relationships between groups)
CROSS_GROUP_PRIORITY_PAIRS = {
    # Line items to totals
    ("LINE_TOTAL", "SUBTOTAL"),
    ("SUBTOTAL", "LINE_TOTAL"),
    ("LINE_TOTAL", "GRAND_TOTAL"),
    ("GRAND_TOTAL", "LINE_TOTAL"),
    # Discounts to totals
    ("DISCOUNT", "SUBTOTAL"),
    ("SUBTOTAL", "DISCOUNT"),
    ("COUPON", "LINE_TOTAL"),
    ("LINE_TOTAL", "COUPON"),
}

# Conflicting label pairs that should NEVER appear on same word
# These indicate data quality issues in training labels
CONFLICTING_LABEL_PAIRS = {
    # A price can't be both unit and line total for same item
    ("QUANTITY", "UNIT_PRICE"),
    ("QUANTITY", "LINE_TOTAL"),
    ("UNIT_PRICE", "LINE_TOTAL"),
    # Semantic impossibilities
    ("PRODUCT_NAME", "QUANTITY"),
    ("PRODUCT_NAME", "UNIT_PRICE"),
    ("PRODUCT_NAME", "LINE_TOTAL"),
}

# Line item evaluation labels - labels that should be validated on line item rows
# Includes both currency labels and other line item components
# Only includes labels defined in CORE_LABELS
LINE_ITEM_EVALUATION_LABELS = {
    # Currency labels (money amounts)
    "CASH_BACK",
    "CHANGE",
    "DISCOUNT",
    "GRAND_TOTAL",
    "LINE_TOTAL",
    "REFUND",
    "SUBTOTAL",
    "TAX",
    "UNIT_PRICE",
    # Non-currency line item components
    "PRODUCT_NAME",
    "QUANTITY",
}

# Financial math labels - labels involved in receipt math validation
# Used by financial_subagent to verify: GRAND_TOTAL = SUBTOTAL + TAX, etc.
FINANCIAL_MATH_LABELS = {
    "GRAND_TOTAL",
    "SUBTOTAL",
    "TAX",
    "TIP",
    "LINE_TOTAL",
    "UNIT_PRICE",
    "QUANTITY",
    "DISCOUNT",
}

# Metadata evaluation labels - labels validated against ReceiptPlace or format patterns
# These are non-line-item labels that appear on receipts
METADATA_EVALUATION_LABELS = {
    # Validated against ReceiptPlace data
    "MERCHANT_NAME",
    "ADDRESS_LINE",
    "PHONE_NUMBER",
    "WEBSITE",
    "STORE_HOURS",
    # Validated by format patterns
    "DATE",
    "TIME",
    "PAYMENT_METHOD",
    "COUPON",
    "LOYALTY_ID",
}
