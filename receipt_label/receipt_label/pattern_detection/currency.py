"""Currency pattern detection with smart classification."""

import re
from typing import Dict, List, Set

from receipt_dynamo.entities import ReceiptWord

from receipt_label.pattern_detection.base import (
    PatternDetector,
    PatternMatch,
    PatternType,
)


class CurrencyPatternDetector(PatternDetector):
    """Detects and classifies currency patterns in receipt text."""

    # Keywords that indicate specific currency types
    TOTAL_KEYWORDS = {
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
    }
    SUBTOTAL_KEYWORDS = {
        "subtotal",
        "sub total",
        "sub-total",
        "merchandise",
        "net total",
        "items total",
        "goods total",
        "product total",
    }
    TAX_KEYWORDS = {
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
    }
    DISCOUNT_KEYWORDS = {
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
    }

    def _initialize_patterns(self) -> None:
        """Compile regex patterns for currency detection."""
        # Common currency symbols
        currency_symbols = r"[$€£¥₹¢]"

        # Number pattern with optional thousand separators and decimal
        number_pattern = r"\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?"

        # Main currency patterns
        self._compiled_patterns = {
            # Symbol before number: $5.99, €10,00
            "symbol_prefix": re.compile(
                rf"({currency_symbols})\s*({number_pattern})", re.IGNORECASE
            ),
            # Symbol after number: 5.99$, 10€
            "symbol_suffix": re.compile(
                rf"({number_pattern})\s*({currency_symbols})", re.IGNORECASE
            ),
            # Plain number that could be currency: 5.99, 10.00
            "plain_number": re.compile(
                rf"^({number_pattern})$", re.IGNORECASE
            ),
            # Negative amounts: -$5.99, ($5.99), $5.99-
            "negative": re.compile(
                rf"(?:-\s*{currency_symbols}\s*{number_pattern}|"
                rf"\(\s*{currency_symbols}\s*{number_pattern}\s*\)|"
                rf"{currency_symbols}\s*{number_pattern}\s*-)",
                re.IGNORECASE,
            ),
        }

    async def detect(self, words: List[ReceiptWord]) -> List[PatternMatch]:
        """Detect currency patterns in receipt words."""
        matches = []

        # First pass: detect all currency-like patterns
        currency_words = []
        for word in words:
            if word.is_noise:
                continue

            match_info = self._match_currency_pattern(word.text)
            if match_info:
                currency_words.append((word, match_info))

        # Second pass: classify based on context
        for word, match_info in currency_words:
            pattern_type = self._classify_currency(word, words)
            confidence = self._calculate_confidence(word, words, pattern_type)

            match = PatternMatch(
                word=word,
                pattern_type=pattern_type,
                confidence=confidence,
                matched_text=match_info["matched_text"],
                extracted_value=match_info["value"],
                metadata={
                    "currency_symbol": match_info.get("symbol", "$"),
                    "is_negative": match_info.get("is_negative", False),
                    "format_type": match_info.get("format_type"),
                    **self._calculate_position_context(word, words),
                },
            )
            matches.append(match)

        return matches

    def get_supported_patterns(self) -> List[PatternType]:
        """Get supported pattern types."""
        return [
            PatternType.CURRENCY,
            PatternType.GRAND_TOTAL,
            PatternType.SUBTOTAL,
            PatternType.TAX,
            PatternType.DISCOUNT,
            PatternType.UNIT_PRICE,
            PatternType.LINE_TOTAL,
        ]

    def _match_currency_pattern(self, text: str) -> Dict:
        """Check if text matches a currency pattern."""
        text = text.strip()

        # Check negative patterns first
        if match := self._compiled_patterns["negative"].search(text):
            return {
                "matched_text": match.group(0),
                "value": self._extract_numeric_value(match.group(0)),
                "is_negative": True,
                "format_type": "negative",
            }

        # Check symbol prefix: $5.99
        if match := self._compiled_patterns["symbol_prefix"].search(text):
            return {
                "matched_text": match.group(0),
                "symbol": match.group(1),
                "value": float(match.group(2).replace(",", "")),
                "format_type": "symbol_prefix",
            }

        # Check symbol suffix: 5.99€
        if match := self._compiled_patterns["symbol_suffix"].search(text):
            return {
                "matched_text": match.group(0),
                "symbol": match.group(2),
                "value": float(match.group(1).replace(",", "")),
                "format_type": "symbol_suffix",
            }

        # Check plain number (only if it looks like a price)
        if match := self._compiled_patterns["plain_number"].search(text):
            value_str = match.group(1).replace(",", "")
            if "." in value_str or len(value_str) >= 3:  # Likely a price
                return {
                    "matched_text": match.group(0),
                    "value": float(value_str),
                    "format_type": "plain_number",
                }

        return {}

    def _classify_currency(  # pylint: disable=too-many-return-statements
        self, word: ReceiptWord, all_words: List[ReceiptWord]
    ) -> PatternType:
        """Classify currency based on context and position."""
        context = self._calculate_position_context(word, all_words)
        same_line_text = context.get("same_line_text", "").lower()

        # Check for nearby keywords
        nearby_words = self._find_nearby_words(
            word, all_words, max_distance=100
        )
        nearby_text = " ".join([w[0].text.lower() for w in nearby_words[:5]])

        # Check for specific classifications
        if self._has_keywords(
            same_line_text + " " + nearby_text, self.TOTAL_KEYWORDS
        ):
            return PatternType.GRAND_TOTAL

        if self._has_keywords(
            same_line_text + " " + nearby_text, self.TAX_KEYWORDS
        ):
            return PatternType.TAX

        if self._has_keywords(
            same_line_text + " " + nearby_text, self.SUBTOTAL_KEYWORDS
        ):
            return PatternType.SUBTOTAL

        if self._has_keywords(
            same_line_text + " " + nearby_text, self.DISCOUNT_KEYWORDS
        ):
            return PatternType.DISCOUNT

        # Position-based classification
        if context.get("is_bottom_20_percent", False):
            # Bottom section likely contains totals
            return PatternType.GRAND_TOTAL

        # Check if there's a quantity pattern nearby (indicates line item)
        if self._has_quantity_pattern_nearby(word, all_words):
            # Determine if it's unit price or line total based on position
            # If quantity is before the price, it's likely unit price
            # If quantity is after or far away, it's likely line total
            return (
                PatternType.UNIT_PRICE
            )  # Will refine this with QuantityDetector

        # Default to generic currency
        return PatternType.CURRENCY

    def _calculate_confidence(
        self,
        word: ReceiptWord,
        all_words: List[ReceiptWord],
        pattern_type: PatternType,
    ) -> float:
        """Calculate confidence score for the classification."""
        confidence = 0.5  # Base confidence

        context = self._calculate_position_context(word, all_words)

        # Boost confidence for clear keyword matches
        if pattern_type in (
            PatternType.GRAND_TOTAL,
            PatternType.TAX,
            PatternType.SUBTOTAL,
        ):
            same_line_text = context.get("same_line_text", "").lower()
            if any(
                keyword in same_line_text for keyword in self.TOTAL_KEYWORDS
            ):
                confidence += 0.3

        # Boost confidence for expected positions
        if pattern_type == PatternType.GRAND_TOTAL and context.get(
            "is_bottom_20_percent"
        ):
            confidence += 0.2

        # Boost confidence for typical currency formats
        if word.text.startswith("$") or word.text.startswith("€"):
            confidence += 0.1

        return min(confidence, 1.0)

    def _has_keywords(self, text: str, keywords: Set[str]) -> bool:
        """Check if text contains any of the keywords."""
        text_lower = text.lower()
        return any(keyword in text_lower for keyword in keywords)

    def _has_quantity_pattern_nearby(
        self, word: ReceiptWord, all_words: List[ReceiptWord]
    ) -> bool:
        """Check if there's a quantity pattern near this currency amount."""
        # Simple check for now - enhanced when QuantityDetector is implemented
        nearby_words = self._find_nearby_words(
            word, all_words, max_distance=50
        )
        quantity_patterns = ["@", "x", "qty", "quantity", "ea", "each"]

        for nearby_word, _ in nearby_words[:3]:  # Check closest 3 words
            if any(
                pattern in nearby_word.text.lower()
                for pattern in quantity_patterns
            ):
                return True

        return False

    def _extract_numeric_value(self, text: str) -> float:
        """Extract numeric value from text, handling negatives."""
        # Remove currency symbols and whitespace
        cleaned = re.sub(r"[$€£¥₹¢\s()]", "", text)
        # Remove trailing minus
        cleaned = cleaned.rstrip("-")
        # Handle leading minus
        is_negative = cleaned.startswith("-") or "(" in text
        cleaned = cleaned.lstrip("-")

        try:
            value = float(cleaned.replace(",", ""))
            return -value if is_negative else value
        except ValueError:
            return 0.0
