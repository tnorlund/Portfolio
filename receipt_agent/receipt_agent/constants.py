"""Shared constants for receipt_agent."""

CORE_LABELS = {
    "MERCHANT_NAME": "Trading name or brand of the store issuing the receipt.",
    "STORE_HOURS": "Printed business hours or opening times for the merchant.",
    "PHONE_NUMBER": "Telephone number printed on the receipt (store's main line).",
    "WEBSITE": "Web or email address printed on the receipt (e.g., sprouts.com).",
    "LOYALTY_ID": "Customer loyalty / rewards / membership identifier.",
    "ADDRESS_LINE": ("Full address line (street + city etc.) printed on the receipt."),
    "DATE": "Calendar date of the transaction.",
    "TIME": "Time of the transaction.",
    "PAYMENT_METHOD": ("Payment instrument summary (e.g., VISA ••••1234, CASH)."),
    "COUPON": "Coupon code or description that reduces price.",
    "DISCOUNT": "Any non-coupon discount line item (e.g., '10% OFF').",
    "PRODUCT_NAME": "Name of a product or item being purchased.",
    "QUANTITY": "Number of units purchased (e.g., '2', '1.5 lbs').",
    "UNIT_PRICE": "Price per unit of the product.",
    "LINE_TOTAL": "Total price for a line item (quantity × unit_price).",
    "SUBTOTAL": "Subtotal before tax and discounts.",
    "TAX": "Tax amount (sales tax, VAT, etc.).",
    "GRAND_TOTAL": ("Final total amount paid (after all discounts and taxes)."),
    # Payment-related labels (added 2025-12-18)
    # These were missing from original schema and caused mislabeling in
    # training data. When these values appeared on receipts, they were
    # incorrectly labeled as LINE_TOTAL.
    "CHANGE": "Change amount returned to the customer after transaction.",
    "CASH_BACK": "Cash back amount dispensed from purchase.",
    "REFUND": "Refund amount (full or partial return).",
}

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
    label: group_name for group_name, labels in LABEL_GROUPS.items() for label in labels
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

# Backward compatibility alias
CURRENCY_LABELS = LINE_ITEM_EVALUATION_LABELS

# Financial math labels - labels involved in receipt math validation
# Used by financial_subagent to verify: GRAND_TOTAL = SUBTOTAL + TAX, etc.
FINANCIAL_MATH_LABELS = {
    "GRAND_TOTAL",
    "SUBTOTAL",
    "TAX",
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
