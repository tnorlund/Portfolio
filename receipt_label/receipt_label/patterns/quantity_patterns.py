"""
Quantity pattern detector for receipt labeling.

Detects quantity patterns like "2 @ $5.99", "Qty: 3", "x2", etc.
"""

import re
from typing import Dict, List, Optional, Tuple

# Import will be provided by the parent module to avoid circular imports


class QuantityPatternDetector:
    """Detects quantity patterns in receipt text."""

    # Labels to use (will be set by parent module)
    QUANTITY_LABEL = "QUANTITY"
    UNIT_PRICE_LABEL = "UNIT_PRICE"

    # Quantity patterns commonly found on receipts
    QUANTITY_PATTERNS = [
        # "2 @ $5.99" or "2@$5.99"
        (r"\b(\d+)\s*@\s*\$?([\d,]+\.?\d*)", "at_price"),
        # "Qty: 3" or "QTY 3" or "Quantity: 3"
        (r"\b(?:qty|quantity)[:.]?\s*(\d+)", "qty_prefix"),
        # "x3" or "X 3" or "x 3"
        (r"\b[xX]\s*(\d+)", "x_suffix"),
        # "3x" or "3 x"
        (r"\b(\d+)\s*[xX]\b", "x_prefix"),
        # "Qty: 3 x $5.99"
        (
            r"\b(?:qty|quantity)[:.]?\s*(\d+)\s*[x×]\s*\$?([\d,]+\.?\d*)",
            "qty_x_price",
        ),
        # "3 x $5.99"
        (r"\b(\d+)\s*[x×]\s*\$?([\d,]+\.?\d*)", "simple_x_price"),
        # "2/$5.00"
        (r"\b(\d+)\s*/\s*\$?([\d,]+\.?\d*)", "slash_price"),
        # "2 for $5.00"
        (r"\b(\d+)\s+for\s+\$?([\d,]+\.?\d*)", "for_price"),
        # "2 items" or "3 pcs"
        (r"\b(\d+)\s+(?:items?|pcs?|pieces?|units?|ea|each)", "count_unit"),
        # Just a number at start of line (common for quantities)
        (r"^(\d+)\s+[A-Za-z]", "leading_number"),
        # "EA 2" or "EACH 3" (quantity after unit)
        (r"\b(?:ea|each|pc|pcs|piece)\s+(\d+)", "unit_qty"),
    ]

    # Words that indicate a quantity context
    QUANTITY_INDICATORS = [
        "qty",
        "quantity",
        "count",
        "amount",
        "number",
        "ea",
        "each",
        "pc",
        "pcs",
        "piece",
        "pieces",
        "item",
        "items",
        "unit",
        "units",
        "pkg",
        "package",
        "box",
        "boxes",
        "case",
        "cases",
        "pack",
        "packs",
    ]

    def detect(self, words: List[Dict]) -> List[Dict]:
        """
        Detect quantity patterns in receipt words.

        Args:
            words: List of word dictionaries with 'text', 'word_id', etc.

        Returns:
            List of detected patterns with word_id, label, confidence
        """
        detected_patterns = []

        # Process each word
        for i, word in enumerate(words):
            text = word.get("text", "")
            word_id = word.get("word_id")

            # Check for quantity patterns
            qty_info = self._check_quantity_patterns(text, i, words)
            if qty_info:
                detected_patterns.append(
                    {
                        "word_id": word_id,
                        "text": text,
                        "label": self.QUANTITY_LABEL,
                        "confidence": qty_info["confidence"],
                        "pattern_type": qty_info["pattern_type"],
                        "quantity_value": qty_info.get("quantity"),
                        "unit_price": qty_info.get("unit_price"),
                    }
                )

                # Also mark related price if found
                if qty_info.get("price_word_offset"):
                    price_idx = i + qty_info["price_word_offset"]
                    if 0 <= price_idx < len(words):
                        price_word = words[price_idx]
                        # Check if it's actually a price
                        if self._is_price(price_word.get("text", "")):
                            detected_patterns.append(
                                {
                                    "word_id": price_word.get("word_id"),
                                    "text": price_word.get("text", ""),
                                    "label": self.UNIT_PRICE_LABEL,
                                    "confidence": qty_info["confidence"] * 0.9,
                                    "pattern_type": "related_to_quantity",
                                }
                            )

            # Check for standalone quantity indicators
            elif self._is_quantity_indicator(text):
                # Look at next word for number
                if i < len(words) - 1:
                    next_text = words[i + 1].get("text", "")
                    if re.match(r"^\d+$", next_text):
                        detected_patterns.append(
                            {
                                "word_id": word_id,
                                "text": text,
                                "label": self.QUANTITY_LABEL,
                                "confidence": 0.85,
                                "pattern_type": "indicator_word",
                            }
                        )
                        detected_patterns.append(
                            {
                                "word_id": words[i + 1].get("word_id"),
                                "text": next_text,
                                "label": self.QUANTITY_LABEL,
                                "confidence": 0.85,
                                "pattern_type": "quantity_value",
                                "quantity_value": int(next_text),
                            }
                        )

        return detected_patterns

    def _check_quantity_patterns(
        self, text: str, word_idx: int, words: List[Dict]
    ) -> Optional[Dict]:
        """Check if text matches any quantity pattern."""
        for pattern, pattern_type in self.QUANTITY_PATTERNS:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return self._extract_quantity_info(
                    match, pattern_type, text, word_idx, words
                )

        return None

    def _extract_quantity_info(
        self,
        match: re.Match,
        pattern_type: str,
        text: str,
        word_idx: int,
        words: List[Dict],
    ) -> Dict:
        """Extract quantity information from matched pattern."""
        info = {
            "pattern_type": pattern_type,
            "confidence": 0.85,  # Base confidence
        }

        try:
            if pattern_type in ["at_price", "qty_x_price", "simple_x_price"]:
                # Patterns with quantity and price
                info["quantity"] = int(match.group(1))
                info["unit_price"] = float(match.group(2).replace(",", ""))
                info["confidence"] = 0.95  # High confidence when we have both

            elif pattern_type in ["slash_price", "for_price"]:
                # "2/$5.00" or "2 for $5.00" - price is for all items
                quantity = int(match.group(1))
                total_price = float(match.group(2).replace(",", ""))
                info["quantity"] = quantity
                info["unit_price"] = total_price / quantity
                info["confidence"] = 0.93

            elif pattern_type in [
                "qty_prefix",
                "x_suffix",
                "x_prefix",
                "count_unit",
                "leading_number",
                "unit_qty",
            ]:
                # Just quantity, no price
                info["quantity"] = int(match.group(1))
                info["confidence"] = 0.88

                # Look for nearby price
                price_offset = self._find_nearby_price(word_idx, words)
                if price_offset:
                    info["price_word_offset"] = price_offset
                    info["confidence"] = 0.90

        except (ValueError, IndexError):
            # Failed to parse numbers
            info["confidence"] = 0.70

        # Adjust confidence based on context
        info["confidence"] = self._adjust_confidence(
            info["confidence"], pattern_type, text
        )

        return info

    def _is_quantity_indicator(self, text: str) -> bool:
        """Check if word is a quantity indicator."""
        return text.lower() in self.QUANTITY_INDICATORS

    def _is_price(self, text: str) -> bool:
        """Check if text looks like a price."""
        # Simple price patterns
        price_patterns = [
            r"^\$?\d+\.?\d*$",  # $5 or 5.99
            r"^\$?\d{1,3}(,\d{3})*\.?\d*$",  # $1,234.56
        ]
        return any(re.match(pattern, text) for pattern in price_patterns)

    def _find_nearby_price(
        self, word_idx: int, words: List[Dict], max_distance: int = 3
    ) -> Optional[int]:
        """Find a price within max_distance words."""
        # Look forward first
        for offset in range(1, min(max_distance + 1, len(words) - word_idx)):
            if self._is_price(words[word_idx + offset].get("text", "")):
                return offset

        # Look backward
        for offset in range(1, min(max_distance + 1, word_idx + 1)):
            if self._is_price(words[word_idx - offset].get("text", "")):
                return -offset

        return None

    def _adjust_confidence(
        self, base_confidence: float, pattern_type: str, text: str
    ) -> float:
        """Adjust confidence based on pattern type and context."""
        confidence = base_confidence

        # Patterns with explicit price are more reliable
        if pattern_type in ["at_price", "qty_x_price", "simple_x_price"]:
            confidence += 0.05

        # Very small or very large quantities might be less reliable
        numbers = re.findall(r"\d+", text)
        if numbers:
            qty = int(numbers[0])
            if qty > 1000 or qty == 0:
                confidence -= 0.10
            elif 1 <= qty <= 20:  # Common quantity range
                confidence += 0.05

        return max(0.0, min(1.0, confidence))
