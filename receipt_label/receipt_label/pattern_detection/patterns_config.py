"""
Centralized pattern definitions for all pattern detectors.

This module provides a unified configuration system for regex patterns,
keywords, and related constants used across all pattern detection classes.
Consolidating patterns here makes them easier to maintain, review, and extend.
"""

import re
from typing import Dict, List, Set


class PatternConfig:
    """Centralized configuration for all pattern detection rules."""

    # ========================================
    # COMMON REGEX COMPONENTS
    # ========================================

    # Base number patterns used across detectors
    BASIC_NUMBER = r"\d+"
    DECIMAL_NUMBER = r"\d+(?:\.\d{1,2})?"
    FORMATTED_NUMBER = r"\d{1,3}(?:,\d{3})+"  # With comma separators
    CURRENCY_NUMBER = (
        rf"(?:{FORMATTED_NUMBER}(?:\.\d{{1,2}})?|{DECIMAL_NUMBER})"
    )

    # Common currency symbols (added Ruble ₽ and Won ₩)
    CURRENCY_SYMBOLS = r"[$€£¥₹¢₽₩]"

    # Date component patterns
    DATE_SEPARATORS = r"[/\-\.]"
    MONTH_DAY = r"\d{1,2}"
    YEAR_2_DIGIT = r"\d{2}"
    YEAR_4_DIGIT = r"\d{4}"

    # ========================================
    # CURRENCY PATTERNS
    # ========================================

    CURRENCY_PATTERNS = {
        # Symbol before number: $5.99, €10,00
        "symbol_prefix": rf"({CURRENCY_SYMBOLS})\s*({CURRENCY_NUMBER})",
        # Symbol after number: 5.99$, 10€
        "symbol_suffix": rf"({CURRENCY_NUMBER})\s*({CURRENCY_SYMBOLS})",
        # Plain number that could be currency: 5.99, 10.00
        "plain_number": rf"^({CURRENCY_NUMBER})$",
        # Negative amounts: -$5.99, ($5.99), $5.99-
        "negative": rf"(?:-\s*{CURRENCY_SYMBOLS}\s*{CURRENCY_NUMBER}|"
        rf"\(\s*{CURRENCY_SYMBOLS}\s*{CURRENCY_NUMBER}\s*\)|"
        rf"{CURRENCY_SYMBOLS}\s*{CURRENCY_NUMBER}\s*-)",
    }

    # Currency classification keywords
    CURRENCY_KEYWORDS = {
        "total": {
            "total",
            "grand total",
            "amount due",
            "balance due",
            "due",
            "pay",
            "total amount",
            "total due",
            "total price",
            "final total",
        },
        "subtotal": {
            "subtotal",
            "sub total",
            "sub-total",
            "merchandise",
            "net total",
            "items total",
            "goods total",
            "product total",
        },
        "tax": {
            "tax",
            "sales tax",
            "vat",
            "gst",
            "hst",
            "pst",
            "taxes",
            "tax amount",
            "total tax",
            "city tax",
            "state tax",
        },
        "discount": {
            "discount",
            "coupon",
            "savings",
            "save",
            "off",
            "reduction",
            "promo",
            "promotion",
            "special",
            "deal",
            "markdown",
        },
    }

    # ========================================
    # DATETIME PATTERNS
    # ========================================

    DATETIME_PATTERNS = {
        # Date formats (matching what datetime detector expects)
        "date_iso": rf"({YEAR_4_DIGIT})-({MONTH_DAY})-({MONTH_DAY})",
        "date_mdy": rf"({MONTH_DAY})/({MONTH_DAY})/((?:{YEAR_4_DIGIT}|{YEAR_2_DIGIT}))",
        "date_month_name": r"\b(?:((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?)[\s-]+(\d{1,2}),?[\s-]+(\d{2,4})|(\d{1,2})[\s-]+((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?)[\s-]+(\d{2,4}))\b",
        "mdy_dash": rf"({MONTH_DAY})-({MONTH_DAY})-((?:{YEAR_4_DIGIT}|{YEAR_2_DIGIT}))",
        "mdy_dot": rf"({MONTH_DAY})\.({MONTH_DAY})\.((?:{YEAR_4_DIGIT}|{YEAR_2_DIGIT}))",
        # Time formats with capture groups
        "time_12h": r"(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(AM|PM|am|pm)",
        "time_24h": r"(\d{1,2}):(\d{2})(?::(\d{2}))?",
        # Combined date-time
        "datetime_combined": rf"({YEAR_4_DIGIT}-{MONTH_DAY}-{MONTH_DAY})\s+(\d{{2}}:\d{{2}}:\d{{2}})",
    }

    # ========================================
    # CONTACT PATTERNS
    # ========================================

    # Phone number patterns
    PHONE_PATTERNS = {
        "us_standard": r"\(?([0-9]{3})\)?[-.\s]?([0-9]{3})[-.\s]?([0-9]{4})",
        "us_long": r"\+?1[-.\s]?\(?([0-9]{3})\)?[-.\s]?([0-9]{3})[-.\s]?([0-9]{4})",
        "us_alpha": r"1-800-[A-Z]{3,7}",  # 1-800-FLOWERS format
        "international": r"\+[1-9][\d\s\-\.\(\)]{1,20}",
        "short": r"\d{3}-\d{4}",  # Local numbers
    }

    # Email pattern
    EMAIL_PATTERN = r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"

    # Common TLDs for website detection
    COMMON_TLDS = {
        "com",
        "org",
        "net",
        "edu",
        "gov",
        "mil",
        "int",
        "co",
        "io",
        "ai",
        "app",
        "dev",
        "tech",
        "store",
        "shop",
        "biz",
        "info",
        "name",
        "mobi",
        "tv",
        "uk",
        "us",
        "ca",
        "au",
        "de",
        "fr",
        "jp",
        "cn",
        "in",
        "br",
        "ru",
        "eu",
        "asia",
        "africa",
        "ly",  # Add ly for bit.ly
    }

    # Website patterns - handle multi-level domains like shop.example.co.uk
    WEBSITE_PATTERNS = {
        "website": rf"\b(?:https?://)?(?:www\.)?(?:[a-zA-Z0-9-]+\.)+(?:{'|'.join(COMMON_TLDS)})(?:/[^\s]*)?\b",
        "website_short": rf"\b(?:[a-zA-Z0-9-]+\.)+(?:{'|'.join(COMMON_TLDS)})(?:/[^\s]*)?\b",
        "website_no_protocol": rf"\b(?:www\.)?(?:[a-zA-Z0-9-]+\.)+(?:{'|'.join(COMMON_TLDS)})(?:/[^\s]*)?\b",
    }

    # ========================================
    # QUANTITY PATTERNS
    # ========================================

    QUANTITY_PATTERNS = {
        # Quantity with @ symbol: "2 @ $5.99"
        "quantity_at_price": rf"({DECIMAL_NUMBER})\s*@\s*\$?({CURRENCY_NUMBER})",
        # Quantity with x: "2 x $5.99" or "2x$5.99"
        "quantity_x_price": rf"(?:qty:?\s*)?({DECIMAL_NUMBER})\s*[xX]\s*\$?({CURRENCY_NUMBER})",
        # Quantity slash format: "2/$10.00" or "2 / $10"
        "quantity_slash": rf"({BASIC_NUMBER})\s*/\s*\$?\s*({CURRENCY_NUMBER})",
        # Quantity for format: "3 for $15.00"
        "quantity_for": rf"({BASIC_NUMBER})\s+for\s+\$?({CURRENCY_NUMBER})",
        # Quantity labels: "Qty: 5" or "Quantity: 5"
        "quantity_label": r"(?:qty|quantity):?\s*(\d+(?:\.\d+)?)",
        # Weight patterns: "1.5 lbs", "2.3 kg", "2½ lbs"
        "weight": rf"({DECIMAL_NUMBER}|[0-9½¼¾⅓⅔⅛⅜⅝⅞]+)\s*(lbs?|pounds?|kg|kgs?|oz|ounces?|g|grams?|cups?)",
        # Volume patterns: "12 oz", "1 L"
        "volume": rf"({DECIMAL_NUMBER})\s*(oz|fl\.?\s*oz|ml|l|liters?|gallons?|gal)",
        # Unit patterns: "2 items", "3 pieces", "1 each"
        "quantity_unit": rf"({DECIMAL_NUMBER})\s*(each|pieces?|items?|units?|packages?|bottles?|cans?|packs|boxes|bags|pack|pkg|box|bag|pcs|pc|ea)",
        # Plain quantity (context-dependent): just a number
        "quantity_plain": rf"^({BASIC_NUMBER})$",
    }

    # ========================================
    # MERCHANT-SPECIFIC PATTERNS
    # ========================================

    # Common merchant identifiers
    MERCHANT_PATTERNS = {
        "walmart": {
            "transaction_code": r"TC#\s*\d+",
            "store_number": r"ST#?\s*\d+",
            "operator": r"OP#?\s*\d+",
        },
        "mcdonalds": {
            "products": {
                "Big Mac",
                "McFlurry",
                "Quarter Pounder",
                "McNuggets",
                "McChicken",
            },
        },
        "gas_station": {
            "gallons": r"(\d+\.\d+)\s*GAL",
            "price_per_gal": r"Price/Gal:?\s*\$?(\d+\.\d+)",
            "pump": r"Pump:?\s*#?(\d+)",
        },
    }

    # ========================================
    # NOISE WORD PATTERNS
    # ========================================

    # Patterns for noise/artifact detection (used to skip processing)
    NOISE_PATTERNS = {
        "punctuation_only": r"^[^\w\s]+$",  # Only punctuation
        "single_char": r"^.$",  # Single character
        "repeating_chars": r"^(.)\1{3,}$",  # Same char repeated 4+ times
        "line_artifacts": r"^[-=_*]{2,}$",  # Line separators
        "ocr_artifacts": r"^[|\\\/\[\]{}()]+$",  # Common OCR mistakes
    }

    # ========================================
    # PATTERN COMPILATION
    # ========================================

    @classmethod
    def compile_patterns(
        cls, pattern_dict: Dict[str, str], flags: int = re.IGNORECASE
    ) -> Dict[str, re.Pattern]:
        """Compile a dictionary of regex patterns."""
        return {
            name: re.compile(pattern, flags)
            for name, pattern in pattern_dict.items()
        }

    @classmethod
    def get_currency_patterns(cls) -> Dict[str, re.Pattern]:
        """Get compiled currency patterns."""
        return cls.compile_patterns(cls.CURRENCY_PATTERNS)

    @classmethod
    def get_datetime_patterns(cls) -> Dict[str, re.Pattern]:
        """Get compiled datetime patterns."""
        return cls.compile_patterns(cls.DATETIME_PATTERNS)

    @classmethod
    def get_contact_patterns(cls) -> Dict[str, re.Pattern]:
        """Get compiled contact patterns."""
        patterns = {**cls.PHONE_PATTERNS}
        patterns["email"] = cls.EMAIL_PATTERN
        patterns.update(cls.WEBSITE_PATTERNS)
        return cls.compile_patterns(patterns)

    @classmethod
    def get_quantity_patterns(cls) -> Dict[str, re.Pattern]:
        """Get compiled quantity patterns."""
        return cls.compile_patterns(cls.QUANTITY_PATTERNS)

    @classmethod
    def get_noise_patterns(cls) -> Dict[str, re.Pattern]:
        """Get compiled noise detection patterns."""
        return cls.compile_patterns(cls.NOISE_PATTERNS)


# ========================================
# PATTERN UTILITIES
# ========================================


def is_noise_text(text: str) -> bool:
    """
    Check if a word is likely OCR noise/artifact.

    Args:
        text: The text to check

    Returns:
        True if the text appears to be noise
    """
    if not text or len(text.strip()) == 0:
        return True

    noise_patterns = PatternConfig.get_noise_patterns()
    return any(
        pattern.match(text.strip()) for pattern in noise_patterns.values()
    )


def classify_word_type(text: str) -> Set[str]:
    """
    Classify a word by its basic character composition.

    This allows selective detector invocation - only run relevant
    detectors based on word characteristics.

    Args:
        text: The text to classify

    Returns:
        Set of classifications: {"numeric", "alphabetic", "alphanumeric", "symbols", "currency_like"}
    """
    classifications = set()

    if not text:
        return classifications

    text = text.strip()

    # Basic character composition
    if text.isdigit():
        classifications.add("numeric")
    elif text.isalpha():
        classifications.add("alphabetic")
    elif text.isalnum():
        classifications.add("alphanumeric")

    # Check for symbols
    if any(c in text for c in "!@#$%^&*()_+-=[]{}|;':\",./<>?`~"):
        classifications.add("symbols")

    # Currency-like (contains currency symbols or decimal patterns)
    if any(c in text for c in "$€£¥₹¢") or re.search(r"\d+\.\d{2}", text):
        classifications.add("currency_like")

    # Contact-like (contains @ or common TLD patterns)
    if "@" in text or any(
        f".{tld}" in text.lower() for tld in PatternConfig.COMMON_TLDS
    ):
        classifications.add("contact_like")

    return classifications
