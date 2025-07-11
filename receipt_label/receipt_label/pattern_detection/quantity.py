"""Quantity pattern detection for receipt line items."""

import re
from typing import Dict, List, Optional, Tuple

from receipt_dynamo.entities import ReceiptWord

from receipt_label.pattern_detection.base import (
    PatternDetector,
    PatternMatch,
    PatternType,
)


class QuantityPatternDetector(PatternDetector):
    """Detects quantity patterns in receipt text."""

    # Common quantity units
    UNITS = {
        "ea",
        "each",
        "pc",
        "pcs",
        "piece",
        "pieces",
        "lb",
        "lbs",
        "pound",
        "pounds",
        "oz",
        "ounce",
        "ounces",
        "kg",
        "kilogram",
        "kilograms",
        "g",
        "gram",
        "grams",
        "l",
        "liter",
        "liters",
        "litre",
        "litres",
        "ml",
        "milliliter",
        "milliliters",
        "gal",
        "gallon",
        "gallons",
        "qt",
        "quart",
        "quarts",
        "pt",
        "pint",
        "pints",
        "pk",
        "pack",
        "packs",
        "pkg",
        "package",
        "box",
        "boxes",
        "bag",
        "bags",
        "bottle",
        "bottles",
        "can",
        "cans",
        "item",
        "items",
        "unit",
        "units",
    }

    def _initialize_patterns(self) -> None:
        """Compile regex patterns for quantity detection."""
        # Build units pattern
        units_pattern = "|".join(re.escape(unit) for unit in self.UNITS)

        self._compiled_patterns = {
            # "2 @ $5.99" or "2 @ 5.99"
            "quantity_at": re.compile(
                r"(\d+(?:\.\d+)?)\s*@\s*(?:\$)?(\d+(?:\.\d+)?)", re.IGNORECASE
            ),
            # "3 x $4.50" or "Qty: 3 x $4.50" or "3 X 4.50"
            "quantity_times": re.compile(
                r"(?:qty:?\s*)?(\d+(?:\.\d+)?)\s*[xX]\s*(?:\$)?(\d+(?:\.\d+)?)",
                re.IGNORECASE,
            ),
            # "2/$10.00" or "2 / $10"
            "quantity_slash": re.compile(
                r"(\d+)\s*/\s*(?:\$)?(\d+(?:\.\d+)?)", re.IGNORECASE
            ),
            # "3 for $15.00" or "3 FOR 15"
            "quantity_for": re.compile(
                r"(\d+)\s+for\s+(?:\$)?(\d+(?:\.\d+)?)", re.IGNORECASE
            ),
            # "Qty: 5" or "QTY 5" or "Quantity: 5"
            "quantity_label": re.compile(
                r"(?:qty|quantity):?\s*(\d+(?:\.\d+)?)", re.IGNORECASE
            ),
            # "2 items" or "3.5 lbs" or "1 each"
            "quantity_unit": re.compile(
                rf"(\d+(?:\.\d+)?)\s*({units_pattern})\b", re.IGNORECASE
            ),
            # Plain number that might be quantity (context-dependent)
            "quantity_plain": re.compile(r"^(\d+)$"),
        }

    async def detect(self, words: List[ReceiptWord]) -> List[PatternMatch]:
        """Detect quantity patterns in receipt words."""
        matches = []

        # Process words with context
        for i, word in enumerate(words):
            if word.is_noise:
                continue

            # Check for explicit quantity patterns
            if match_info := self._match_quantity_at(word.text):
                match = self._create_match(
                    word, PatternType.QUANTITY_AT, match_info, words
                )
                matches.append(match)

            elif match_info := self._match_quantity_times(word.text):
                match = self._create_match(
                    word, PatternType.QUANTITY_TIMES, match_info, words
                )
                matches.append(match)

            elif match_info := self._match_quantity_slash(word.text):
                match = self._create_match(
                    word, PatternType.QUANTITY, match_info, words
                )
                matches.append(match)

            elif match_info := self._match_quantity_for(word.text):
                match = self._create_match(
                    word, PatternType.QUANTITY_FOR, match_info, words
                )
                matches.append(match)

            elif match_info := self._match_quantity_label(word.text):
                match = self._create_match(
                    word, PatternType.QUANTITY, match_info, words
                )
                matches.append(match)

            elif match_info := self._match_quantity_unit(word.text):
                match = self._create_match(
                    word, PatternType.QUANTITY, match_info, words
                )
                matches.append(match)

            # Check for plain numbers that might be quantities
            elif match_info := self._match_plain_quantity(word, words, i):
                match = self._create_match(
                    word, PatternType.QUANTITY, match_info, words
                )
                matches.append(match)

        return matches

    def get_supported_patterns(self) -> List[PatternType]:
        """Get supported pattern types."""
        return [
            PatternType.QUANTITY,
            PatternType.QUANTITY_AT,
            PatternType.QUANTITY_TIMES,
            PatternType.QUANTITY_FOR,
        ]

    def _match_quantity_at(self, text: str) -> Optional[Dict]:
        """Match '2 @ $5.99' pattern."""
        if match := self._compiled_patterns["quantity_at"].search(text):
            quantity, price = match.groups()
            return {
                "matched_text": match.group(0),
                "quantity": float(quantity),
                "unit_price": float(price),
                "total": float(quantity) * float(price),
                "format": "at_symbol",
            }
        return None

    def _match_quantity_times(self, text: str) -> Optional[Dict]:
        """Match '3 x $4.50' pattern."""
        if match := self._compiled_patterns["quantity_times"].search(text):
            quantity, price = match.groups()
            return {
                "matched_text": match.group(0),
                "quantity": float(quantity),
                "unit_price": float(price),
                "total": float(quantity) * float(price),
                "format": "times_symbol",
            }
        return None

    def _match_quantity_slash(self, text: str) -> Optional[Dict]:
        """Match '2/$10.00' pattern."""
        if match := self._compiled_patterns["quantity_slash"].search(text):
            quantity, total = match.groups()
            qty_float = float(quantity)
            total_float = float(total)
            return {
                "matched_text": match.group(0),
                "quantity": qty_float,
                "unit_price": total_float / qty_float if qty_float > 0 else 0,
                "total": total_float,
                "format": "slash_notation",
            }
        return None

    def _match_quantity_for(self, text: str) -> Optional[Dict]:
        """Match '3 for $15.00' pattern."""
        if match := self._compiled_patterns["quantity_for"].search(text):
            quantity, total = match.groups()
            qty_float = float(quantity)
            total_float = float(total)
            return {
                "matched_text": match.group(0),
                "quantity": qty_float,
                "unit_price": total_float / qty_float if qty_float > 0 else 0,
                "total": total_float,
                "format": "for_notation",
            }
        return None

    def _match_quantity_label(self, text: str) -> Optional[Dict]:
        """Match 'Qty: 5' pattern."""
        if match := self._compiled_patterns["quantity_label"].search(text):
            quantity = match.group(1)
            return {
                "matched_text": match.group(0),
                "quantity": float(quantity),
                "format": "labeled",
            }
        return None

    def _match_quantity_unit(self, text: str) -> Optional[Dict]:
        """Match '2 items' or '3.5 lbs' pattern."""
        if match := self._compiled_patterns["quantity_unit"].search(text):
            quantity, unit = match.groups()
            return {
                "matched_text": match.group(0),
                "quantity": float(quantity),
                "unit": unit.lower(),
                "format": "with_unit",
            }
        return None

    def _match_plain_quantity(
        self, word: ReceiptWord, all_words: List[ReceiptWord], word_index: int
    ) -> Optional[Dict]:
        """Match plain numbers that might be quantities based on context."""
        if match := self._compiled_patterns["quantity_plain"].search(
            word.text
        ):
            quantity = int(match.group(1))

            # Check if it's likely a quantity based on context
            if self._is_likely_quantity(word, all_words, word_index, quantity):
                return {
                    "matched_text": match.group(0),
                    "quantity": float(quantity),
                    "format": "plain_number",
                }
        return None

    def _is_likely_quantity(
        self,
        word: ReceiptWord,
        all_words: List[ReceiptWord],
        word_index: int,
        value: int,
    ) -> bool:
        """Determine if a plain number is likely a quantity based on context."""
        # Quantities are typically small numbers
        if value > 100:
            return False

        # Check nearby words for product names or prices
        nearby_words = self._find_nearby_words(
            word, all_words, max_distance=100
        )

        # Look for price indicators nearby
        has_price_nearby = False
        has_product_nearby = False

        for nearby_word, distance in nearby_words[:5]:
            text_lower = nearby_word.text.lower()

            # Check for currency symbols
            if any(symbol in nearby_word.text for symbol in ["$", "€", "£"]):
                has_price_nearby = True

            # Check for product-like words (capitalized, longer than 3 chars)
            if (
                len(text_lower) > 3
                and nearby_word.text[0].isupper()
                and text_lower not in ["total", "tax", "subtotal"]
            ):
                has_product_nearby = True

        # Check position - quantities usually appear at start of line
        context = self._calculate_position_context(word, all_words)
        is_line_start = (
            context.get("line_word_count", 1) > 2
        )  # Line has multiple words

        # High confidence if we have both price and product context
        return (has_price_nearby or has_product_nearby) and value <= 20

    def _create_match(
        self,
        word: ReceiptWord,
        pattern_type: PatternType,
        match_info: Dict,
        all_words: List[ReceiptWord],
    ) -> PatternMatch:
        """Create a PatternMatch object."""
        # Calculate confidence based on pattern type and context
        confidence = self._calculate_confidence(
            pattern_type, match_info, word, all_words
        )

        # Add position context
        match_info.update(self._calculate_position_context(word, all_words))

        return PatternMatch(
            word=word,
            pattern_type=pattern_type,
            confidence=confidence,
            matched_text=match_info["matched_text"],
            extracted_value=match_info.get("quantity", 0),
            metadata=match_info,
        )

    def _calculate_confidence(
        self,
        pattern_type: PatternType,
        match_info: Dict,
        word: ReceiptWord,
        all_words: List[ReceiptWord],
    ) -> float:
        """Calculate confidence score for quantity detection."""
        # Base confidence by pattern type
        base_confidence = {
            PatternType.QUANTITY_AT: 0.95,  # Very explicit
            PatternType.QUANTITY_TIMES: 0.95,  # Very explicit
            PatternType.QUANTITY_FOR: 0.9,  # Clear pattern
            PatternType.QUANTITY: 0.8,  # General quantity
        }.get(pattern_type, 0.7)

        # Adjust based on format
        format_adjustments = {
            "at_symbol": 0.0,  # Already high
            "times_symbol": 0.0,  # Already high
            "for_notation": 0.0,  # Already good
            "slash_notation": 0.05,  # Slightly boost
            "labeled": 0.1,  # Explicit label
            "with_unit": 0.1,  # Has unit
            "plain_number": -0.2,  # Less certain
        }

        adjustment = format_adjustments.get(match_info.get("format", ""), 0)

        return min(base_confidence + adjustment, 1.0)
