CORE_LABELS: dict[str, str] = {
    # ── Merchant & store info ───────────────────────────────────
    "MERCHANT_NAME": "Trading name or brand of the store issuing the receipt.",
    "STORE_HOURS": "Printed business hours or opening times for the merchant.",
    "PHONE_NUMBER": "Telephone number printed on the receipt "
    "(store's main line).",
    "WEBSITE": "Web or email address printed on the receipt "
    "(e.g., sprouts.com).",
    "LOYALTY_ID": "Customer loyalty / rewards / membership identifier.",
    # ── Location / address ──────────────────────────────────────
    "ADDRESS_LINE": "Full address line (street + city etc.) printed on "
    "the receipt.",
    # If you later break it down, add:
    # "ADDRESS_NUMBER": "Street/building number.",
    # "STREET_NAME":    "Street name.",
    # "CITY":           "City name.",
    # "STATE":          "State / province abbreviation.",
    # "POSTAL_CODE":    "ZIP or postal code.",
    # ── Transaction info ───────────────────────────────────────
    "DATE": "Calendar date of the transaction.",
    "TIME": "Time of the transaction.",
    "PAYMENT_METHOD": "Payment instrument summary "
    "(e.g., VISA ••••1234, CASH).",
    "COUPON": "Coupon code or description that reduces price.",
    "DISCOUNT": "Any non-coupon discount line item "
    "(e.g., 10% member discount).",
    # ── Line-item fields ───────────────────────────────────────
    "PRODUCT_NAME": "Descriptive text of a purchased product (item name).",
    "QUANTITY": "Numeric count or weight of the item (e.g., 2, 1.31 lb).",
    "UNIT_PRICE": "Price per single unit / weight before tax.",
    "LINE_TOTAL": "Extended price for that line (quantity x unit price).",
    # ── Totals & taxes ─────────────────────────────────────────
    "SUBTOTAL": "Sum of all line totals before tax and discounts.",
    "TAX": "Any tax line (sales tax, VAT, bottle deposit).",
    "GRAND_TOTAL": "Final amount due after all discounts, taxes and fees.",
}

# Noise Detection Configuration
from .utils.noise_detection import NoiseDetectionConfig

# Default noise detection configuration
NOISE_DETECTION_CONFIG = NoiseDetectionConfig(
    # Single punctuation marks to filter
    punctuation_patterns=[r"^[.,;:!?\"\'\-\(\)\[\]\{\}]$"],
    # Separator characters
    separator_patterns=[r"^[|/\\~_=+*&%]$"],
    # Non-alphanumeric artifacts
    artifact_patterns=[r"^[^\w\s]+$"],
    # Minimum word length (not used for direct filtering)
    min_word_length=2,
    # Preserve currency symbols and amounts
    preserve_currency=True,
)
