"""Quantity pattern detection for receipt line items."""

from typing import Dict, List, Optional

from receipt_label.pattern_detection.base import (
    PatternDetector,
    PatternMatch,
    PatternType,
)
from receipt_label.pattern_detection.patterns_config import PatternConfig
from receipt_dynamo.entities import ReceiptWord


class QuantityPatternDetector(PatternDetector):
    """Detects quantity patterns in receipt text."""

    def _initialize_patterns(self) -> None:
        """Compile regex patterns for quantity detection using centralized config."""
        self._compiled_patterns = PatternConfig.get_quantity_patterns()

    async def detect(self, words: List[ReceiptWord]) -> List[PatternMatch]:
        """Detect quantity patterns in receipt words."""
        matches = []

        # First pass: try to detect multi-word quantity patterns
        self._detect_multi_word_quantities(words, matches)

        # Process words with context
        for i, word in enumerate(words):
            if word.is_noise:
                continue

            # Skip if already matched as part of multi-word quantity
            if any(match.word.word_id == word.word_id for match in matches):
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

            elif match_info := self._match_weight(word.text):
                match = self._create_match(
                    word, PatternType.QUANTITY, match_info, words
                )
                matches.append(match)

            elif match_info := self._match_volume(word.text):
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
        if match := self._compiled_patterns["quantity_at_price"].search(text):
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
        if match := self._compiled_patterns["quantity_x_price"].search(text):
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

            # Exclude date patterns (MM/DD, DD/MM format)
            qty_int = int(quantity)
            total_float = float(total)

            # Only exclude very obvious date patterns like "01/15" or "12/25"
            # Be conservative - only exclude if it has leading zero (indicating date formatting)
            # or if it's a very common date pattern
            original_qty = match.group(
                1
            )  # Get original text with leading zeros
            is_obvious_date = (
                qty_int <= 12
                and total_float <= 31
                and "$" not in text
                and "." not in total
                and len(total) <= 2
                and (
                    original_qty.startswith("0")  # Leading zero suggests date
                    or (qty_int == 1 and total_float == 1)
                )  # 1/1 is definitely a date
            )

            if is_obvious_date:
                return None

            return {
                "matched_text": match.group(0),
                "quantity": float(quantity),
                "unit_price": (
                    total_float / float(quantity) if float(quantity) > 0 else 0
                ),
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

    def _match_weight(self, text: str) -> Optional[Dict]:
        """Match weight patterns like '3.5 lbs' or '2.3 kg'."""
        if match := self._compiled_patterns["weight"].search(text):
            quantity, unit = match.groups()
            return {
                "matched_text": match.group(0),
                "quantity": float(quantity),
                "unit": unit.lower(),
                "format": "with_unit",
            }
        return None

    def _match_volume(self, text: str) -> Optional[Dict]:
        """Match volume patterns like '12 oz' or '1 L'."""
        if match := self._compiled_patterns["volume"].search(text):
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
        word_index: int,  # pylint: disable=unused-argument
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

        for nearby_word, _ in nearby_words[:5]:
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
            context.get("line_position", 1) == 0
            and context.get("line_word_count", 1) > 1
        )  # First word in a multi-word line

        # High confidence if we have context indicators and appropriate position
        return (
            (has_price_nearby or has_product_nearby)
            and value <= 20
            and (is_line_start or has_price_nearby)
        )

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

    def _detect_multi_word_quantities(
        self, words: List[ReceiptWord], matches: List[PatternMatch]
    ) -> None:
        """Detect quantity patterns that span multiple words on the same line."""
        # Track word IDs that are already part of multi-word matches to prevent overlaps
        used_word_ids = set()

        # First pass: Check for 3-word patterns (highest priority)
        for i in range(len(words) - 2):
            word1 = words[i]
            word2 = words[i + 1]
            word3 = words[i + 2]

            # Skip if any word is already used or is noise
            if (
                word1.word_id in used_word_ids
                or word2.word_id in used_word_ids
                or word3.word_id in used_word_ids
                or word1.is_noise
                or word2.is_noise
                or word3.is_noise
            ):
                continue

            # Check if words are on the same line (similar y coordinates)
            y_tolerance = 5
            if (
                abs(word1.bounding_box["y"] - word2.bounding_box["y"])
                > y_tolerance
                or abs(word2.bounding_box["y"] - word3.bounding_box["y"])
                > y_tolerance
            ):
                continue

            # Check if words are reasonably close horizontally
            word1_right = word1.bounding_box["x"] + word1.bounding_box["width"]
            word2_left = word2.bounding_box["x"]
            word2_right = word2.bounding_box["x"] + word2.bounding_box["width"]
            word3_left = word3.bounding_box["x"]

            max_gap = 30  # Max 30px gap between words
            if (
                word2_left - word1_right > max_gap
                or word3_left - word2_right > max_gap
            ):
                continue

            # Try combining the words for "quantity @ price" pattern
            combined_text = f"{word1.text} {word2.text} {word3.text}"
            match_info = self._match_quantity_at(combined_text)

            if match_info:
                # Create match using the first word as primary
                match = self._create_match(
                    word1, PatternType.QUANTITY_AT, match_info, words
                )
                # Update metadata to indicate multi-word span
                match.metadata.update(
                    {
                        "spans_multiple_words": True,
                        "word_ids": [
                            word1.word_id,
                            word2.word_id,
                            word3.word_id,
                        ],
                    }
                )
                matches.append(match)

                # Mark all words as used to prevent overlapping matches
                used_word_ids.update(
                    [word1.word_id, word2.word_id, word3.word_id]
                )
                continue

        # Second pass: Check for 2-word patterns (only for unused words)
        for i in range(len(words) - 1):
            word1 = words[i]
            word2 = words[i + 1]

            # Skip if any word is already used or is noise
            if (
                word1.word_id in used_word_ids
                or word2.word_id in used_word_ids
                or word1.is_noise
                or word2.is_noise
            ):
                continue

            # Check same line and proximity
            y_tolerance = 5
            if (
                abs(word1.bounding_box["y"] - word2.bounding_box["y"])
                > y_tolerance
            ):
                continue

            word1_right = word1.bounding_box["x"] + word1.bounding_box["width"]
            word2_left = word2.bounding_box["x"]
            max_gap = 30
            if word2_left - word1_right > max_gap:
                continue

            combined_text = f"{word1.text} {word2.text}"

            # Try various 2-word patterns
            for pattern_method, pattern_type in [
                (self._match_quantity_times, PatternType.QUANTITY_TIMES),
                (self._match_quantity_for, PatternType.QUANTITY_FOR),
            ]:
                match_info = pattern_method(combined_text)
                if match_info:
                    match = self._create_match(
                        word1, pattern_type, match_info, words
                    )
                    match.metadata.update(
                        {
                            "spans_multiple_words": True,
                            "word_ids": [word1.word_id, word2.word_id],
                        }
                    )
                    matches.append(match)

                    # Mark both words as used to prevent overlapping matches
                    used_word_ids.update([word1.word_id, word2.word_id])
                    break  # Found a match, don't try other patterns for this word pair
