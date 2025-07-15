"""
Smart vertical alignment detector with improved product/metadata distinction.

This module enhances vertical alignment detection by:
1. Better filtering of metadata vs product lines
2. Respecting receipt structure (products before prices)
3. Using context clues to identify real line items
"""

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple
from collections import defaultdict

from receipt_dynamo.entities.receipt_word import ReceiptWord
from receipt_label.pattern_detection.base import PatternMatch, PatternType

logger = logging.getLogger(__name__)


@dataclass
class SmartLineItem:
    """A line item with enhanced validation."""
    product_text: str
    product_words: List[ReceiptWord]
    price: PatternMatch
    product_line: int
    price_line: int
    line_distance: int
    confidence: float
    column_id: int
    is_likely_product: bool


class SmartVerticalDetector:
    """Enhanced vertical alignment detector with smarter product detection."""
    
    def __init__(self, alignment_tolerance: float = 0.02):
        """Initialize detector."""
        self.alignment_tolerance = alignment_tolerance
        self.logger = logger
        
        # Enhanced product indicators
        self.product_indicators = {
            # Food items
            "ORGANIC", "FRESH", "FROZEN", "NATURAL", "WHOLE",
            # Descriptors
            "LARGE", "SMALL", "MEDIUM", "PACK", "BAG", "BOX",
            # Specific products
            "PITA", "BREAD", "SOAP", "MILK", "EGGS", "MEAT",
            "CHICKEN", "BEEF", "PORK", "FISH", "FRUIT", "VEGETABLE",
            # Brands/varieties
            "FLAX", "OAT", "BRAN", "LIQUID", "POWDER", "CREAM",
            # Units
            "LB", "OZ", "GAL", "QT", "PT", "EA", "PKG"
        }
        
        # Strong metadata indicators
        self.metadata_indicators = {
            # Store info
            "SPROUTS", "MARKET", "STORE", "LOCATION", "BRANCH",
            # Transaction metadata
            "TAX", "TOTAL", "SUBTOTAL", "BALANCE", "DUE", "CREDIT",
            "DEBIT", "CASH", "CHANGE", "PAYMENT", "TENDER",
            # Receipt metadata
            "CASHIER", "REGISTER", "TRANSACTION", "RECEIPT", "DATE",
            "TIME", "THANK", "VISIT", "SAVE", "SURVEY", "FEEDBACK",
            # System info
            "AUTH", "CODE", "REF", "SEQ", "TID", "POS", "APPROVED",
            "CARD", "XXXX", "****"
        }
        
        # Department/category names (could be products or headers)
        self.category_names = {
            "BAKERY", "PRODUCE", "DAIRY", "MEAT", "SEAFOOD",
            "DELI", "FROZEN", "GROCERY", "BULK", "VITAMINS"
        }
        
    def find_product_price_column(self, 
                                 currency_patterns: List[PatternMatch],
                                 words: List[ReceiptWord]) -> Optional[int]:
        """
        Find the most likely product price column.
        
        Returns column index or None.
        """
        # First detect all price columns
        columns = self._detect_price_columns(currency_patterns)
        
        if not columns:
            return None
            
        # Score each column
        best_column = None
        best_score = 0
        
        for i, column in enumerate(columns):
            score = 0
            
            # Check if column contains reasonable prices (not IDs or years)
            reasonable_prices = 0
            for price in column['prices']:
                try:
                    value = float(price.word.text.replace('$', '').replace(',', ''))
                    if 0.01 <= value <= 1000:
                        reasonable_prices += 1
                except:
                    pass
            
            score += reasonable_prices * 10
            
            # Check if prices are in the product area (lines 10-40 typically)
            product_area_prices = sum(1 for p in column['prices'] 
                                    if 10 <= p.word.line_id <= 40)
            score += product_area_prices * 5
            
            # Check for nearby product words
            for price in column['prices']:
                nearby_words = self._get_words_on_nearby_lines(
                    words, price.word.line_id, distance=3
                )
                product_words = sum(1 for w in nearby_words 
                                  if any(ind in w.text.upper() for ind in self.product_indicators))
                score += product_words
            
            # Penalty for metadata words
            for price in column['prices']:
                nearby_words = self._get_words_on_nearby_lines(
                    words, price.word.line_id, distance=1
                )
                metadata_words = sum(1 for w in nearby_words 
                                   if any(ind in w.text.upper() for ind in self.metadata_indicators))
                score -= metadata_words * 2
            
            self.logger.info(f"Column {i} score: {score}")
            
            if score > best_score:
                best_score = score
                best_column = i
                
        return best_column
    
    def detect_smart_line_items(self,
                               words: List[ReceiptWord],
                               pattern_matches: List[PatternMatch]) -> Dict:
        """
        Detect line items with smart product/metadata distinction.
        """
        # Extract currency patterns
        currency_patterns = [m for m in pattern_matches if m.pattern_type in [
            PatternType.CURRENCY, PatternType.GRAND_TOTAL, PatternType.SUBTOTAL,
            PatternType.TAX, PatternType.UNIT_PRICE, PatternType.LINE_TOTAL,
            PatternType.DISCOUNT
        ]]
        
        self.logger.info(f"Processing {len(currency_patterns)} currency patterns")
        
        # Find the product price column
        column_idx = self.find_product_price_column(currency_patterns, words)
        
        if column_idx is None:
            self.logger.warning("No suitable product price column found")
            return {
                'line_items': [],
                'total_items': 0,
                'column_found': False
            }
        
        # Get the column
        columns = self._detect_price_columns(currency_patterns)
        product_column = columns[column_idx]
        
        self.logger.info(f"Using column {column_idx} at X={product_column['x_center']:.3f}")
        
        # Match products to prices
        line_items = self._match_smart_products(words, product_column)
        
        # Filter to keep only likely products
        filtered_items = [item for item in line_items if item.is_likely_product]
        
        return {
            'line_items': filtered_items,
            'total_items': len(filtered_items),
            'column_found': True,
            'column_x': product_column['x_center'],
            'all_candidates': len(line_items),
            'filtered_count': len(filtered_items)
        }
    
    def _detect_price_columns(self, currency_patterns: List[PatternMatch]) -> List[Dict]:
        """Detect vertically aligned price columns."""
        if not currency_patterns:
            return []
            
        # Group by X coordinate
        x_groups = defaultdict(list)
        
        for pattern in currency_patterns:
            word = pattern.word
            
            # Get X coordinate
            if hasattr(word, 'bottom_right') and word.bottom_right:
                x_right = word.bottom_right['x'] if isinstance(word.bottom_right, dict) else word.bottom_right[0]
            elif hasattr(word, 'bounding_box') and word.bounding_box:
                if isinstance(word.bounding_box, dict):
                    x_right = word.bounding_box['left'] + word.bounding_box['width']
                else:
                    x_right = word.bounding_box[0] + word.bounding_box[2]
            else:
                continue
            
            # Find or create group
            found = False
            for group_x in list(x_groups.keys()):
                if abs(group_x - x_right) < self.alignment_tolerance:
                    x_groups[group_x].append(pattern)
                    found = True
                    break
            
            if not found:
                x_groups[x_right] = [pattern]
        
        # Convert to column objects
        columns = []
        for x_coord, patterns in x_groups.items():
            if len(patterns) >= 2:  # Need at least 2 for a column
                columns.append({
                    'x_center': x_coord,
                    'prices': sorted(patterns, key=lambda p: p.word.line_id),
                    'count': len(patterns)
                })
        
        return sorted(columns, key=lambda c: c['count'], reverse=True)
    
    def _match_smart_products(self, 
                            words: List[ReceiptWord],
                            price_column: Dict) -> List[SmartLineItem]:
        """Match products to prices with smart filtering."""
        # Group words by line
        words_by_line = defaultdict(list)
        for word in words:
            words_by_line[word.line_id].append(word)
        
        line_items = []
        used_prices = set()
        
        for price_pattern in price_column['prices']:
            price_id = (price_pattern.word.line_id, price_pattern.word.word_id)
            if price_id in used_prices:
                continue
            
            price_line = price_pattern.word.line_id
            
            # Look for products ABOVE the price (typical receipt layout)
            best_product = None
            best_score = 0
            best_line = None
            
            for line_offset in range(-1, -4, -1):  # Check 1-3 lines above
                check_line = price_line + line_offset
                if check_line not in words_by_line:
                    continue
                
                line_words = words_by_line[check_line]
                line_text = " ".join(w.text for w in line_words)
                
                # Score this line
                score = self._score_product_line(line_text, line_words)
                
                if score > best_score:
                    best_score = score
                    best_product = line_words
                    best_line = check_line
            
            # Create line item if we found a good match
            if best_product and best_score > 0:
                product_text = " ".join(w.text for w in best_product)
                
                line_items.append(SmartLineItem(
                    product_text=product_text,
                    product_words=best_product,
                    price=price_pattern,
                    product_line=best_line,
                    price_line=price_line,
                    line_distance=abs(price_line - best_line),
                    confidence=best_score,
                    column_id=0,
                    is_likely_product=best_score > 0.5
                ))
                
                used_prices.add(price_id)
                
                self.logger.info(f"Matched '{product_text}' → {price_pattern.word.text} "
                               f"(score={best_score:.2f})")
        
        return line_items
    
    def _score_product_line(self, line_text: str, line_words: List[ReceiptWord]) -> float:
        """Score how likely a line is to be a product."""
        score = 0.5  # Base score
        upper_text = line_text.upper()
        
        # Strong positive indicators
        product_matches = sum(1 for ind in self.product_indicators 
                            if ind in upper_text)
        score += product_matches * 0.2
        
        # Strong negative indicators
        metadata_matches = sum(1 for ind in self.metadata_indicators 
                             if ind in upper_text)
        score -= metadata_matches * 0.3
        
        # Category names are neutral (could be section headers)
        category_matches = sum(1 for cat in self.category_names 
                             if cat in upper_text)
        if category_matches > 0 and len(line_words) == 1:
            # Single word category is likely a header
            score -= 0.2
        
        # Length heuristics
        if len(line_words) >= 2 and len(line_words) <= 6:
            score += 0.1  # Good product length
        elif len(line_words) == 1:
            score -= 0.1  # Single words less likely
        elif len(line_words) > 8:
            score -= 0.2  # Too long, might be address or message
        
        # Alphabetic content
        alpha_words = sum(1 for w in line_words 
                         if w.text.isalpha() and len(w.text) > 2)
        if alpha_words >= 2:
            score += 0.1
        
        # All caps penalty (often headers/metadata)
        if line_text.isupper() and len(line_words) > 1:
            score -= 0.1
        
        return max(0.0, min(score, 1.0))
    
    def _get_words_on_nearby_lines(self,
                                  words: List[ReceiptWord],
                                  line_id: int,
                                  distance: int) -> List[ReceiptWord]:
        """Get words on nearby lines."""
        nearby_words = []
        for word in words:
            if abs(word.line_id - line_id) <= distance:
                nearby_words.append(word)
        return nearby_words