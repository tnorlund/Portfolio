"""
Enhanced line item detector using horizontal grouping to reduce LLM dependency.

This module implements advanced horizontal grouping techniques to detect line items
on receipts by analyzing spatial relationships between words, reducing the need
for expensive LLM calls.
"""

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Set

from receipt_dynamo.entities.receipt_word import ReceiptWord
from receipt_label.pattern_detection.base import PatternMatch, PatternType
from receipt_label.spatial.geometry_utils import (
    SpatialWord, 
    is_horizontally_aligned_group,
    group_words_into_line_items
)

logger = logging.getLogger(__name__)


@dataclass
class LineItem:
    """Represents a detected line item with associated metadata."""
    
    words: List[ReceiptWord]
    description: str
    quantity: Optional[float] = None
    unit_price: Optional[float] = None
    total_price: Optional[float] = None
    confidence: float = 0.0
    detection_method: str = "horizontal_grouping"
    

@dataclass
class HorizontalGroupingConfig:
    """Configuration for horizontal grouping detection."""
    
    # Y-coordinate tolerance for same line detection
    y_tolerance: float = 0.02
    
    # X-coordinate gap threshold for line item separation
    x_gap_threshold: float = 0.8
    
    # Minimum confidence to accept a line item
    min_confidence: float = 0.3
    
    # Minimum words required for a valid line item
    min_words_per_item: int = 2
    
    # Maximum horizontal distance between related words
    max_word_distance: float = 0.15
    

class HorizontalLineItemDetector:
    """
    Advanced line item detector using horizontal grouping techniques.
    
    This detector reduces LLM dependency by using spatial analysis to group
    words into line items based on horizontal alignment and pattern matching.
    """
    
    def __init__(self, config: Optional[HorizontalGroupingConfig] = None):
        self.config = config or HorizontalGroupingConfig()
        self._currency_pattern_types = {
            PatternType.CURRENCY, 
            PatternType.UNIT_PRICE,
            PatternType.LINE_TOTAL,
            PatternType.GRAND_TOTAL,
            PatternType.SUBTOTAL
        }
        self._quantity_pattern_types = {PatternType.QUANTITY}
        
    def detect_line_items(
        self,
        words: List[ReceiptWord],
        pattern_matches: Optional[List[PatternMatch]] = None
    ) -> List[LineItem]:
        """
        Detect line items using horizontal grouping techniques.
        
        Args:
            words: List of all words on the receipt
            pattern_matches: Optional pattern matches from pattern detection
            
        Returns:
            List of detected line items with metadata
        """
        if not words:
            return []
            
        logger.info(f"Detecting line items from {len(words)} words")
        
        # Step 1: Group words into potential line items
        word_groups = group_words_into_line_items(
            words, 
            pattern_matches,
            self.config.y_tolerance,
            self.config.x_gap_threshold
        )
        
        # Step 2: Analyze each group to extract line item information
        line_items = []
        pattern_map = self._create_pattern_map(pattern_matches)
        
        for group in word_groups:
            line_item = self._analyze_word_group(group, pattern_map)
            if line_item and line_item.confidence >= self.config.min_confidence:
                line_items.append(line_item)
                
        # Step 3: Post-process to handle multi-line items
        line_items = self._merge_multi_line_items(line_items)
        
        logger.info(f"Detected {len(line_items)} line items with horizontal grouping")
        return line_items
        
    def _create_pattern_map(
        self, 
        pattern_matches: Optional[List[PatternMatch]]
    ) -> Dict[int, PatternMatch]:
        """Create a mapping from word index to pattern match."""
        pattern_map = {}
        if pattern_matches:
            for i, match in enumerate(pattern_matches):
                # Map by word ID if available
                if hasattr(match.word, 'id'):
                    pattern_map[match.word.id] = match
                # Also store by index for fallback
                pattern_map[i] = match
        return pattern_map
        
    def _analyze_word_group(
        self,
        words: List[ReceiptWord],
        pattern_map: Dict[int, PatternMatch]
    ) -> Optional[LineItem]:
        """
        Analyze a group of horizontally aligned words to extract line item info.
        
        This method identifies:
        1. Product description (leftmost text)
        2. Quantity (if present)
        3. Unit price (if present)
        4. Total price (rightmost currency)
        """
        if len(words) < self.config.min_words_per_item:
            return None
            
        # Sort words by X position
        sorted_words = sorted(words, key=lambda w: w.bounding_box["x"])
        
        # Find currency and quantity patterns
        currency_words = []
        quantity_words = []
        description_words = []
        
        for i, word in enumerate(words):
            word_idx = self._get_word_index(word, pattern_map)
            pattern = pattern_map.get(word_idx)
            
            if pattern:
                if pattern.pattern_type in self._currency_pattern_types:
                    currency_words.append((i, word, pattern))
                elif pattern.pattern_type in self._quantity_pattern_types:
                    quantity_words.append((i, word, pattern))
                else:
                    description_words.append(word)
            else:
                # No pattern - likely description text
                description_words.append(word)
                
        # Build line item
        line_item = self._build_line_item(
            sorted_words,
            description_words,
            quantity_words,
            currency_words
        )
        
        return line_item
        
    def _get_word_index(
        self, 
        word: ReceiptWord, 
        pattern_map: Dict[int, PatternMatch]
    ) -> Optional[int]:
        """Find the index of a word in the pattern map."""
        # Check if word has an ID and if it's in the pattern map
        if hasattr(word, 'id') and word.id in pattern_map:
            return word.id
        
        # Otherwise check if word matches any pattern's word
        for idx, pattern in pattern_map.items():
            if pattern.word == word:
                return idx
        return None
        
    def _build_line_item(
        self,
        all_words: List[ReceiptWord],
        description_words: List[ReceiptWord],
        quantity_words: List[Tuple[int, ReceiptWord, PatternMatch]],
        currency_words: List[Tuple[int, ReceiptWord, PatternMatch]]
    ) -> Optional[LineItem]:
        """Build a LineItem from analyzed word groups."""
        
        # Must have at least description and price
        if not description_words and not currency_words:
            return None
            
        # Extract description
        description = self._extract_description(description_words, all_words)
        
        # Extract quantity
        quantity = None
        if quantity_words:
            quantity = self._extract_quantity(quantity_words)
            
        # Extract prices
        unit_price, total_price = self._extract_prices(currency_words, quantity)
        
        # Calculate confidence
        confidence = self._calculate_confidence(
            len(description_words),
            len(quantity_words),
            len(currency_words),
            len(all_words)
        )
        
        return LineItem(
            words=all_words,
            description=description,
            quantity=quantity,
            unit_price=unit_price,
            total_price=total_price,
            confidence=confidence,
            detection_method="horizontal_grouping"
        )
        
    def _extract_description(
        self,
        description_words: List[ReceiptWord],
        all_words: List[ReceiptWord]
    ) -> str:
        """Extract product description from words."""
        if description_words:
            # Use explicit description words
            sorted_desc = sorted(description_words, key=lambda w: w.bounding_box["x"])
            return " ".join(w.text for w in sorted_desc)
        else:
            # Use leftmost words as description
            sorted_all = sorted(all_words, key=lambda w: w.bounding_box["x"])
            # Take first 60% of words as description
            desc_count = max(1, int(len(sorted_all) * 0.6))
            return " ".join(w.text for w in sorted_all[:desc_count])
            
    def _extract_quantity(
        self, 
        quantity_words: List[Tuple[int, ReceiptWord, PatternMatch]]
    ) -> Optional[float]:
        """Extract quantity value from quantity patterns."""
        if not quantity_words:
            return None
            
        # Use the first quantity found
        _, word, pattern = quantity_words[0]
        
        # Extract numeric value from pattern
        try:
            # This is simplified - would need proper quantity parsing
            import re
            numbers = re.findall(r'\d+(?:\.\d+)?', word.text)
            if numbers:
                return float(numbers[0])
        except:
            pass
            
        return None
        
    def _extract_prices(
        self,
        currency_words: List[Tuple[int, ReceiptWord, PatternMatch]],
        quantity: Optional[float]
    ) -> Tuple[Optional[float], Optional[float]]:
        """Extract unit price and total price from currency patterns."""
        if not currency_words:
            return None, None
            
        # Sort by X position
        sorted_currency = sorted(currency_words, key=lambda x: x[1].bounding_box["x"])
        
        if len(sorted_currency) == 1:
            # Only one price - determine if unit or total based on quantity
            price_value = self._parse_currency_value(sorted_currency[0][1].text)
            if quantity and quantity > 1:
                # Likely total price
                return None, price_value
            else:
                # Likely unit price
                return price_value, price_value
                
        elif len(sorted_currency) >= 2:
            # Multiple prices - leftmost is unit, rightmost is total
            unit_price = self._parse_currency_value(sorted_currency[0][1].text)
            total_price = self._parse_currency_value(sorted_currency[-1][1].text)
            return unit_price, total_price
            
        return None, None
        
    def _parse_currency_value(self, text: str) -> Optional[float]:
        """Parse currency value from text."""
        try:
            import re
            # Remove currency symbols and extract number
            number_match = re.search(r'[\d,]+\.?\d*', text)
            if number_match:
                return float(number_match.group().replace(',', ''))
        except:
            pass
        return None
        
    def _calculate_confidence(
        self,
        desc_count: int,
        qty_count: int,
        price_count: int,
        total_count: int
    ) -> float:
        """Calculate confidence score for line item detection."""
        # Base confidence on presence of key components
        confidence = 0.0
        
        # Description present
        if desc_count > 0:
            confidence += 0.3
            
        # Price present (most important)
        if price_count > 0:
            confidence += 0.4
            
        # Quantity present
        if qty_count > 0:
            confidence += 0.2
            
        # Reasonable word count
        if 2 <= total_count <= 10:
            confidence += 0.1
            
        return min(1.0, confidence)
        
    def _merge_multi_line_items(self, line_items: List[LineItem]) -> List[LineItem]:
        """
        Merge line items that span multiple lines.
        
        This handles cases where product descriptions continue on the next line.
        """
        if len(line_items) <= 1:
            return line_items
            
        merged = []
        i = 0
        
        while i < len(line_items):
            current = line_items[i]
            
            # Check if next line might be continuation
            if i + 1 < len(line_items):
                next_item = line_items[i + 1]
                
                # Continuation criteria:
                # 1. Next line has no price
                # 2. Next line is indented or left-aligned
                # 3. Y-distance is small
                if (next_item.total_price is None and 
                    next_item.unit_price is None and
                    self._is_continuation(current, next_item)):
                    
                    # Merge items
                    merged_item = LineItem(
                        words=current.words + next_item.words,
                        description=f"{current.description} {next_item.description}",
                        quantity=current.quantity,
                        unit_price=current.unit_price,
                        total_price=current.total_price,
                        confidence=current.confidence,
                        detection_method="horizontal_grouping_merged"
                    )
                    merged.append(merged_item)
                    i += 2  # Skip next item
                    continue
                    
            merged.append(current)
            i += 1
            
        return merged
        
    def _is_continuation(self, item1: LineItem, item2: LineItem) -> bool:
        """Check if item2 is a continuation of item1."""
        if not item1.words or not item2.words:
            return False
            
        # Get Y positions
        y1 = max(w.bounding_box["y"] + w.bounding_box["height"] for w in item1.words)
        y2 = min(w.bounding_box["y"] for w in item2.words)
        
        # Check vertical distance
        y_distance = y2 - y1
        if y_distance > 0.05:  # Too far apart
            return False
            
        # Check horizontal alignment (indentation)
        x1_min = min(w.bounding_box["x"] for w in item1.words)
        x2_min = min(w.bounding_box["x"] for w in item2.words)
        
        # Continuation is usually indented or at same position
        if x2_min < x1_min - 0.02:  # Moved significantly left
            return False
            
        return True