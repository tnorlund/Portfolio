"""
Sequential matcher for line items.

Handles the common pattern where products are listed sequentially,
followed by their prices in the same order.
"""

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from receipt_dynamo.entities.receipt_word import ReceiptWord
from receipt_label.pattern_detection.base import PatternMatch

logger = logging.getLogger(__name__)


@dataclass
class SequentialLineItem:
    """A line item matched by sequential order."""
    product_text: str
    product_words: List[ReceiptWord]
    price: float
    price_match: PatternMatch
    product_line: int
    price_line: int
    sequence_index: int  # Order in the sequence


class SequentialMatcher:
    """Matches products to prices using sequential order."""
    
    def __init__(self):
        """Initialize matcher."""
        self.logger = logger
        
    def match_sequential(self,
                        words: List[ReceiptWord],
                        validated_prices: List[Tuple[float, PatternMatch]]) -> List[SequentialLineItem]:
        """
        Match products to prices using sequential order.
        
        This handles patterns like:
        - PRODUCT A
        - PRODUCT B  
        - PRODUCT C
        - PRICE A
        - PRICE B
        - PRICE C
        
        Args:
            words: All receipt words
            validated_prices: Mathematically validated prices in order
            
        Returns:
            List of matched line items
        """
        if not validated_prices:
            return []
            
        # Sort prices by line number
        sorted_prices = sorted(validated_prices, key=lambda p: p[1].word.line_id)
        
        # Find the line range of prices
        price_lines = [p[1].word.line_id for p in sorted_prices]
        min_price_line = min(price_lines)
        max_price_line = max(price_lines)
        
        self.logger.info(f"Prices span lines {min_price_line} to {max_price_line}")
        
        # Look for product lines above the first price
        product_candidates = self._find_product_lines(words, min_price_line)
        
        if not product_candidates:
            self.logger.warning("No product lines found above prices")
            return []
            
        # Match sequentially
        line_items = []
        num_matches = min(len(product_candidates), len(sorted_prices))
        
        for i in range(num_matches):
            product_line, product_words = product_candidates[i]
            price_value, price_match = sorted_prices[i]
            
            product_text = " ".join(w.text for w in sorted(product_words, key=lambda w: w.word_id))
            
            line_items.append(SequentialLineItem(
                product_text=product_text,
                product_words=product_words,
                price=price_value,
                price_match=price_match,
                product_line=product_line,
                price_line=price_match.word.line_id,
                sequence_index=i
            ))
            
            self.logger.info(f"Sequential match {i+1}: '{product_text}' → ${price_value:.2f}")
        
        return line_items
    
    def _find_product_lines(self, 
                           words: List[ReceiptWord],
                           first_price_line: int) -> List[Tuple[int, List[ReceiptWord]]]:
        """
        Find product lines above the first price.
        
        Returns:
            List of (line_id, words) tuples in order
        """
        # Group words by line
        words_by_line = {}
        for word in words:
            if word.line_id not in words_by_line:
                words_by_line[word.line_id] = []
            words_by_line[word.line_id].append(word)
        
        product_lines = []
        
        # Search backwards from first price
        for line_id in range(first_price_line - 1, max(0, first_price_line - 20), -1):
            if line_id not in words_by_line:
                continue
                
            line_words = words_by_line[line_id]
            
            # Check if this looks like a product line
            if self._is_product_line(line_words):
                product_lines.append((line_id, line_words))
                
            # Stop if we hit a clear section break
            line_text = " ".join(w.text for w in line_words).upper()
            if self._is_section_header(line_text):
                break
        
        # Reverse to get sequential order
        product_lines.reverse()
        
        # Filter to get only the last N products before prices
        # (where N = number of prices)
        if len(product_lines) > len(words_by_line):
            # Too many product candidates, keep only recent ones
            return product_lines[-len(words_by_line):]
            
        return product_lines
    
    def _is_product_line(self, line_words: List[ReceiptWord]) -> bool:
        """Check if a line is likely a product description."""
        if not line_words:
            return False
            
        # Must have some text
        line_text = " ".join(w.text for w in line_words)
        if not line_text.strip():
            return False
            
        # Skip the section header check to avoid heuristics
        # if self._is_section_header(line_text):
        #     return False
            
        # Should have alphabetic content
        if not any(c.isalpha() for c in line_text):
            return False
            
        # Avoid pure numbers
        if line_text.replace('.', '').replace(',', '').replace('$', '').isdigit():
            return False
            
        # Avoid obvious metadata
        upper_text = line_text.upper()
        metadata_keywords = {
            'TOTAL', 'TAX', 'SUBTOTAL', 'BALANCE', 'CASH', 'CREDIT',
            'CARD', 'CHANGE', 'THANK', 'VISIT', 'PHONE', 'ADDRESS',
            'STORE', 'RECEIPT', 'TRANSACTION', 'AUTH', 'REF'
        }
        
        if any(keyword in upper_text for keyword in metadata_keywords):
            return False
            
        # Reasonable length (1-10 words)
        if len(line_words) > 10:
            return False
            
        return True
    
    def _is_section_header(self, line_text: str) -> bool:
        """Check if this is a section header that separates products from other content."""
        headers = {
            'GROCERY', 'PRODUCE', 'DAIRY', 'MEAT', 'BAKERY',
            'DELI', 'FROZEN', 'BEVERAGES', 'SNACKS', 'HOUSEHOLD'
        }
        
        # Single word in all caps that matches a header
        words = line_text.strip().split()
        if len(words) == 1 and words[0] in headers:
            return True
            
        return False