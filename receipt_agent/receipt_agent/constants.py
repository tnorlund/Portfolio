"""Shared constants for receipt_agent."""

CORE_LABELS = {
    "MERCHANT_NAME": "Trading name or brand of the store issuing the receipt.",
    "STORE_HOURS": "Printed business hours or opening times for the merchant.",
    "PHONE_NUMBER": "Telephone number printed on the receipt (store's main line).",
    "WEBSITE": "Web or email address printed on the receipt (e.g., sprouts.com).",
    "LOYALTY_ID": "Customer loyalty / rewards / membership identifier.",
    "ADDRESS_LINE": (
        "Full address line (street + city etc.) printed on the receipt."
    ),
    "DATE": "Calendar date of the transaction.",
    "TIME": "Time of the transaction.",
    "PAYMENT_METHOD": (
        "Payment instrument summary (e.g., VISA ••••1234, CASH)."
    ),
    "COUPON": "Coupon code or description that reduces price.",
    "DISCOUNT": "Any non-coupon discount line item (e.g., '10% OFF').",
    "PRODUCT_NAME": "Name of a product or item being purchased.",
    "QUANTITY": "Number of units purchased (e.g., '2', '1.5 lbs').",
    "UNIT_PRICE": "Price per unit of the product.",
    "LINE_TOTAL": "Total price for a line item (quantity × unit_price).",
    "SUBTOTAL": "Subtotal before tax and discounts.",
    "TAX": "Tax amount (sales tax, VAT, etc.).",
    "GRAND_TOTAL": (
        "Final total amount paid (after all discounts and taxes)."
    ),
    # Payment-related labels (added 2025-12-18)
    # These were missing from original schema and caused mislabeling in
    # training data. When these values appeared on receipts, they were
    # incorrectly labeled as LINE_TOTAL.
    "CHANGE": "Change amount returned to the customer after transaction.",
    "CASH_BACK": "Cash back amount dispensed from purchase.",
    "REFUND": "Refund amount (full or partial return).",
}
