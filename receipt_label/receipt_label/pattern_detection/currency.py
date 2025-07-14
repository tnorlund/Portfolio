"""Currency pattern detection with smart classification."""

import re
from typing import Dict, List, Set

from receipt_dynamo.entities import ReceiptWord

from receipt_label.pattern_detection.base import (
    PatternDetector,
    PatternMatch,
    PatternType,
)
from receipt_label.pattern_detection.pattern_utils import (
    CURRENCY_KEYWORD_MATCHER,
    ContextAnalyzer,
    PatternOptimizer,
)
from receipt_label.pattern_detection.patterns_config import PatternConfig


class CurrencyPatternDetector(PatternDetector):
    """Detects and classifies currency patterns in receipt text."""

    def _initialize_patterns(self) -> None:
        """Compile regex patterns for currency detection using centralized config."""
        self._compiled_patterns = PatternConfig.get_currency_patterns()

        # Get centralized keyword sets
        self._keyword_sets = PatternConfig.CURRENCY_KEYWORDS

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

        # Check for specific classifications - prioritize same-line context first
        # This ensures exact context matches take precedence over nearby matches

        # Check same line first for more specific matches
        if CURRENCY_KEYWORD_MATCHER.has_keywords(same_line_text, "tax"):
            return PatternType.TAX

        if CURRENCY_KEYWORD_MATCHER.has_keywords(same_line_text, "subtotal"):
            return PatternType.SUBTOTAL

        if CURRENCY_KEYWORD_MATCHER.has_keywords(same_line_text, "discount"):
            return PatternType.DISCOUNT

        if CURRENCY_KEYWORD_MATCHER.has_keywords(same_line_text, "total"):
            return PatternType.GRAND_TOTAL

        # Then check same line + nearby context for broader matches
        combined_text = same_line_text + " " + nearby_text

        if CURRENCY_KEYWORD_MATCHER.has_keywords(combined_text, "tax"):
            return PatternType.TAX

        if CURRENCY_KEYWORD_MATCHER.has_keywords(combined_text, "subtotal"):
            return PatternType.SUBTOTAL

        if CURRENCY_KEYWORD_MATCHER.has_keywords(combined_text, "discount"):
            return PatternType.DISCOUNT

        if CURRENCY_KEYWORD_MATCHER.has_keywords(combined_text, "total"):
            return PatternType.GRAND_TOTAL

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

        # Boost confidence for clear keyword matches on same line
        same_line_text = context.get("same_line_text", "").lower()

        if pattern_type == PatternType.GRAND_TOTAL:
            if CURRENCY_KEYWORD_MATCHER.has_keywords(same_line_text, "total"):
                confidence += 0.3
        elif pattern_type == PatternType.TAX:
            if CURRENCY_KEYWORD_MATCHER.has_keywords(same_line_text, "tax"):
                confidence += 0.3
        elif pattern_type == PatternType.SUBTOTAL:
            if CURRENCY_KEYWORD_MATCHER.has_keywords(
                same_line_text, "subtotal"
            ):
                confidence += 0.3
        elif pattern_type == PatternType.DISCOUNT:
            if CURRENCY_KEYWORD_MATCHER.has_keywords(
                same_line_text, "discount"
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
