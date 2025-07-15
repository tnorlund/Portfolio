"""
Pattern-first line item detection using spatial relationships.

This module implements a NEW line item detector that follows our proven
pattern-first approach, using spatial analysis to detect line items without
relying on the previous implementation.
"""

import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

from receipt_dynamo.entities.receipt_word import ReceiptWord
from receipt_label.spatial.geometry_utils import LineItemSpatialDetector, SpatialWord
from receipt_label.pattern_detection.currency import CurrencyPatternDetector
from receipt_label.pattern_detection.quantity import QuantityPatternDetector

logger = logging.getLogger(__name__)


@dataclass
class LineItemMatch:
    """Represents a detected line item with spatial and pattern evidence."""
    line_number: int
    product_words: List[ReceiptWord]
    price_word: ReceiptWord
    quantity_word: Optional[ReceiptWord] = None
    confidence: float = 0.0
    
    @property
    def product_text(self) -> str:
        """Combined product description text."""
        return " ".join(word.text for word in self.product_words)
    
    @property
    def price_value(self) -> Optional[str]:
        """Price text value."""
        return self.price_word.text if self.price_word else None
    
    @property
    def quantity_value(self) -> Optional[str]:
        """Quantity text value."""
        return self.quantity_word.text if self.quantity_word else None


@dataclass
class LineItemSummary:
    """Summary of line item detection results."""
    items_found: bool
    item_count: int
    confidence: float
    items_data: List[LineItemMatch]
    
    @property
    def has_sufficient_line_items(self) -> bool:
        """Check if we have enough line items for complete receipt."""
        return self.items_found and self.item_count >= 1


class LineItemPatternDetector:
    """
    NEW line item detector using pattern-first spatial analysis.
    
    This detector follows our proven approach:
    1. Use spatial relationships to identify potential line items
    2. Apply existing pattern detectors for validation
    3. Build confidence scores based on spatial + pattern evidence
    4. Only use GPT when patterns are insufficient
    """
    
    def __init__(self, 
                 y_tolerance: float = 5.0,
                 x_tolerance: float = 20.0,
                 min_confidence: float = 0.3):
        self.spatial_detector = LineItemSpatialDetector(y_tolerance, x_tolerance, min_confidence)
        self.currency_detector = CurrencyPatternDetector()
        self.quantity_detector = QuantityPatternDetector()
        self.min_confidence = min_confidence
        
        # Compile regex patterns for performance
        self._currency_pattern = re.compile(r'\$?\d{1,3}(?:,\d{3})*(?:\.\d{2})?')
        self._quantity_pattern = re.compile(r'^\d+([.,]\d+)?$|^\d+[xX]$|^\d+\s*(lb|kg|oz|g|ea|each)$', re.IGNORECASE)
        self._subtotal_pattern = re.compile(r'\b(subtotal|tax|total|discount|coupon)\b', re.IGNORECASE)
    
    def detect_line_items(
        self, 
        words: List[ReceiptWord], 
        lines: Optional[List] = None
    ) -> LineItemSummary:
        """
        Main entry point for line item detection using spatial analysis.
        
        Args:
            words: Receipt words to analyze
            lines: Optional receipt lines (not used, for backward compatibility)
            
        Returns:
            LineItemSummary with detected line items
        """
        logger.info(f"Starting pattern-first line item detection on {len(words)} words")
        
        if not words:
            return LineItemSummary(False, 0, 0.0, [])
        
        # Step 1: Analyze spatial structure
        spatial_structure = self.spatial_detector.detect_spatial_structure(words)
        rows = spatial_structure["rows"]
        columns = spatial_structure["columns"]
        
        logger.info(f"Spatial analysis: {len(rows)} rows, {len(columns)} columns")
        
        # Step 2: Identify potential line item rows
        line_item_candidates = self._identify_line_item_rows(rows, columns)
        
        # Step 3: Extract line item components using patterns
        line_items = []
        for row_index, row in enumerate(line_item_candidates):
            matches = self._extract_line_items_from_row(row_index, row, columns)
            line_items.extend(matches)
        
        # Step 4: Filter and rank by confidence
        filtered_items = self._filter_and_rank_line_items(line_items)
        
        # Step 5: Create summary
        summary = self._create_line_item_summary(filtered_items)
        
        logger.info(f"Detected {summary.item_count} line items with avg confidence {summary.confidence:.2f}")
        
        return summary
    
    def _identify_line_item_rows(self, rows, columns) -> List:
        """Identify rows that likely contain line items using spatial heuristics."""
        line_item_rows = []
        
        for row in rows:
            # Apply heuristics from the other model's recommendations:
            
            # 1. Must have at least one currency pattern (right side)
            if not self._row_has_currency_on_right(row):
                continue
            
            # 2. Filter out subtotal/tax/total rows
            if self._is_subtotal_row(row):
                continue
            
            # 3. Must have description words (left side)
            if not self._row_has_description_words(row):
                continue
            
            # 4. Apply horizontal whitespace gate
            if not self._passes_whitespace_gate(row):
                continue
            
            line_item_rows.append(row)
        
        logger.debug(f"Identified {len(line_item_rows)} potential line item rows")
        return line_item_rows
    
    def _row_has_currency_on_right(self, row) -> bool:
        """Check if row has currency patterns on the right side."""
        if not row.words:
            return False
        
        # Get rightmost words
        sorted_words = sorted(row.words, key=lambda w: SpatialWord(w).x)
        rightmost_words = sorted_words[-3:]  # Check last 3 words
        
        return any(self._currency_pattern.search(word.text) for word in rightmost_words)
    
    def _is_subtotal_row(self, row) -> bool:
        """Check if row contains subtotal/tax/total keywords."""
        row_text = " ".join(word.text for word in row.words).lower()
        return bool(self._subtotal_pattern.search(row_text))
    
    def _row_has_description_words(self, row) -> bool:
        """Check if row has non-currency words that could be descriptions."""
        description_words = 0
        for word in row.words:
            if not self._currency_pattern.search(word.text) and len(word.text) > 1:
                description_words += 1
        
        return description_words >= 1
    
    def _passes_whitespace_gate(self, row) -> bool:
        """Apply horizontal whitespace gate heuristic."""
        if len(row.words) < 2:
            return True  # Single word rows pass by default
        
        # Calculate gaps between consecutive words
        sorted_words = sorted(row.words, key=lambda w: SpatialWord(w).x)
        gaps = []
        
        for i in range(len(sorted_words) - 1):
            word1 = sorted_words[i]
            word2 = sorted_words[i + 1]
            
            word1_right = word1.bounding_box["x"] + word1.bounding_box["width"]
            word2_left = word2.bounding_box["x"]
            gap = word2_left - word1_right
            gaps.append(gap)
        
        if not gaps:
            return True
        
        # Check if there's a significant gap (potential column separator)
        median_gap = sorted(gaps)[len(gaps) // 2]
        std_dev = (sum((g - median_gap) ** 2 for g in gaps) / len(gaps)) ** 0.5
        threshold = median_gap + std_dev
        
        # Row passes if it has at least one significant gap
        return any(gap > threshold for gap in gaps)
    
    def _extract_line_items_from_row(self, row_index: int, row, columns) -> List[LineItemMatch]:
        """Extract line item components from a spatial row."""
        matches = []
        
        # Get price words (right side, currency patterns)
        price_words = self._get_price_words_from_row(row)
        
        for price_word in price_words:
            # Get description words (left side, non-currency)
            description_words = self._get_description_words_for_price(row, price_word)
            
            # Look for quantity patterns
            quantity_word = self._find_quantity_word_in_row(row)
            
            # Calculate confidence using spatial + pattern evidence
            confidence = self._calculate_line_item_confidence(
                row, description_words, price_word, quantity_word
            )
            
            if confidence >= self.min_confidence:
                matches.append(LineItemMatch(
                    line_number=row_index,
                    product_words=description_words,
                    price_word=price_word,
                    quantity_word=quantity_word,
                    confidence=confidence
                ))
        
        return matches
    
    def _get_price_words_from_row(self, row) -> List[ReceiptWord]:
        """Get currency words from row, preferring right-aligned ones."""
        price_words = []
        
        for word in row.words:
            if self._currency_pattern.search(word.text):
                price_words.append(word)
        
        # Sort by x-position to prefer rightmost prices
        price_words.sort(key=lambda w: SpatialWord(w).x, reverse=True)
        
        return price_words
    
    def _get_description_words_for_price(self, row, price_word: ReceiptWord) -> List[ReceiptWord]:
        """Get description words (left of price, non-currency)."""
        description_words = []
        price_x = SpatialWord(price_word).x
        
        for word in row.words:
            # Skip the price word itself
            if word.word_id == price_word.word_id:
                continue
            
            # Skip other currency words
            if self._currency_pattern.search(word.text):
                continue
            
            # Skip quantity patterns (handle separately)
            if self._quantity_pattern.match(word.text):
                continue
            
            # Include words that are to the left of the price
            if SpatialWord(word).x < price_x:
                description_words.append(word)
        
        # Sort by x-coordinate to maintain reading order
        description_words.sort(key=lambda w: SpatialWord(w).x)
        
        return description_words
    
    def _find_quantity_word_in_row(self, row) -> Optional[ReceiptWord]:
        """Find quantity patterns in the row."""
        for word in row.words:
            if self._quantity_pattern.match(word.text):
                return word
        return None
    
    def _calculate_line_item_confidence(
        self,
        row,
        description_words: List[ReceiptWord],
        price_word: ReceiptWord,
        quantity_word: Optional[ReceiptWord]
    ) -> float:
        """Calculate confidence score using spatial + pattern evidence."""
        confidence = 0.0
        
        # Base confidence: has price and description
        if price_word and description_words:
            confidence += 0.4
        
        # Spatial evidence: price on right side
        if price_word:
            all_words = [SpatialWord(w) for w in row.words]
            price_spatial = SpatialWord(price_word)
            relative_position = price_spatial.get_relative_position_on_line(all_words)
            
            if relative_position > 0.7:  # Right side
                confidence += 0.2
        
        # Pattern evidence: clear currency format
        if price_word and self._has_clear_currency_format(price_word):
            confidence += 0.2
        
        # Description evidence: reasonable product text
        if self._looks_like_product_description(description_words):
            confidence += 0.1
        
        # Quantity evidence: has quantity pattern
        if quantity_word:
            confidence += 0.1
        
        return min(confidence, 1.0)
    
    def _has_clear_currency_format(self, word: ReceiptWord) -> bool:
        """Check if word has clear currency formatting."""
        text = word.text
        # Strong currency indicators
        return bool(re.match(r'^\$\d+\.\d{2}$', text) or  # $12.99
                   re.match(r'^\d+\.\d{2}$', text))       # 12.99
    
    def _looks_like_product_description(self, words: List[ReceiptWord]) -> bool:
        """Check if words look like a reasonable product description."""
        if not words:
            return False
        
        total_text = " ".join(word.text for word in words)
        
        # Simple heuristics for product descriptions
        # - Has some alphabetic characters
        # - Not just numbers/symbols
        # - Reasonable length
        has_letters = bool(re.search(r'[a-zA-Z]', total_text))
        reasonable_length = 2 <= len(total_text) <= 50
        not_just_numbers = not re.match(r'^[\d\s\.\,\-]+$', total_text)
        
        return has_letters and reasonable_length and not_just_numbers
    
    def _filter_and_rank_line_items(self, line_items: List[LineItemMatch]) -> List[LineItemMatch]:
        """Filter and rank line items by confidence."""
        # Filter by minimum confidence
        filtered = [item for item in line_items if item.confidence >= self.min_confidence]
        
        # Sort by confidence (descending)
        filtered.sort(key=lambda x: x.confidence, reverse=True)
        
        # Remove duplicates (same price on same line)
        deduplicated = []
        seen_combinations = set()
        
        for item in filtered:
            key = (item.line_number, item.price_value)
            if key not in seen_combinations:
                deduplicated.append(item)
                seen_combinations.add(key)
        
        return deduplicated
    
    def _create_line_item_summary(self, line_items: List[LineItemMatch]) -> LineItemSummary:
        """Create summary of line item detection results."""
        if not line_items:
            return LineItemSummary(False, 0, 0.0, [])
        
        avg_confidence = sum(item.confidence for item in line_items) / len(line_items)
        
        return LineItemSummary(
            items_found=True,
            item_count=len(line_items),
            confidence=avg_confidence,
            items_data=line_items
        )