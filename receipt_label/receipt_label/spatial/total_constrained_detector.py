"""
Total-constrained line item detector.

This module uses the mathematical constraint that line item prices
must sum to the grand total (accounting for tax and discounts).
This provides a powerful validation mechanism for identifying real prices.
"""

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple
from itertools import combinations
import math

from receipt_dynamo.entities.receipt_word import ReceiptWord
from receipt_label.pattern_detection.base import PatternMatch, PatternType

logger = logging.getLogger(__name__)


@dataclass
class ConstrainedLineItem:
    """A line item validated by total constraint."""
    product_text: str
    product_words: List[ReceiptWord]
    price: float
    price_match: PatternMatch
    product_line: int
    price_line: int
    confidence: float


@dataclass
class ReceiptTotals:
    """Identified totals on a receipt."""
    subtotal: Optional[float] = None
    subtotal_match: Optional[PatternMatch] = None
    tax: Optional[float] = None
    tax_match: Optional[PatternMatch] = None
    grand_total: Optional[float] = None
    grand_total_match: Optional[PatternMatch] = None
    discount: Optional[float] = None
    discount_match: Optional[PatternMatch] = None


class TotalConstrainedDetector:
    """Detects line items using total sum constraint."""
    
    def __init__(self, tolerance: float = 0.02):
        """
        Initialize detector.
        
        Args:
            tolerance: Maximum difference allowed between sum and total (e.g., 0.02 = 2 cents)
        """
        self.tolerance = tolerance
        self.logger = logger
        
    def detect_with_total_constraint(self,
                                   words: List[ReceiptWord],
                                   pattern_matches: List[PatternMatch]) -> Dict:
        """
        Detect line items that sum to the grand total.
        
        Args:
            words: All receipt words
            pattern_matches: All pattern matches
            
        Returns:
            Dictionary with detection results
        """
        self.logger.info("Starting total-constrained line item detection")
        
        # Step 1: Find receipt totals
        totals = self._find_receipt_totals(pattern_matches, words)
        
        if not totals.grand_total:
            self.logger.warning("No grand total found - cannot use total constraint")
            return {
                'line_items': [],
                'total_items': 0,
                'grand_total': None,
                'constraint_used': False
            }
        
        self.logger.info(f"Found grand total: ${totals.grand_total:.2f}")
        if totals.subtotal:
            self.logger.info(f"Found subtotal: ${totals.subtotal:.2f}")
        if totals.tax:
            self.logger.info(f"Found tax: ${totals.tax:.2f}")
        
        # Step 2: Find candidate prices
        candidate_prices = self._find_candidate_prices(pattern_matches, totals)
        self.logger.info(f"Found {len(candidate_prices)} candidate prices")
        
        # Step 3: Find combinations that sum correctly
        valid_combinations = self._find_valid_combinations(candidate_prices, totals)
        self.logger.info(f"Found {len(valid_combinations)} valid price combinations")
        
        if not valid_combinations:
            return {
                'line_items': [],
                'total_items': 0,
                'grand_total': totals.grand_total,
                'constraint_used': True,
                'combinations_tested': len(candidate_prices)
            }
        
        # Step 4: Choose best combination and match with products
        best_combination = self._choose_best_combination(valid_combinations, words)
        line_items = self._match_products_to_prices(best_combination, words)
        
        return {
            'line_items': line_items,
            'total_items': len(line_items),
            'grand_total': totals.grand_total,
            'subtotal': totals.subtotal,
            'tax': totals.tax,
            'constraint_used': True,
            'valid_combinations': len(valid_combinations),
            'prices_in_solution': len(best_combination)
        }
    
    def _find_receipt_totals(self, 
                           pattern_matches: List[PatternMatch],
                           words: List[ReceiptWord]) -> ReceiptTotals:
        """Find grand total, subtotal, tax, etc."""
        totals = ReceiptTotals()
        
        # Group words by line for context
        words_by_line = {}
        for word in words:
            if word.line_id not in words_by_line:
                words_by_line[word.line_id] = []
            words_by_line[word.line_id].append(word)
        
        # First pass: find lines with total indicators
        total_indicator_lines = set()
        for line_id, line_words in words_by_line.items():
            line_text = " ".join(w.text for w in line_words).upper()
            if any(indicator in line_text for indicator in ["BALANCE DUE", "AMOUNT DUE", "GRAND TOTAL", "TOTAL DUE"]):
                total_indicator_lines.add(line_id)
                self.logger.info(f"Found total indicator on line {line_id}: {line_text}")
        
        # Second pass: find amounts
        for match in pattern_matches:
            try:
                value = float(match.word.text.replace('$', '').replace(',', ''))
            except:
                continue
                
            line_words = words_by_line.get(match.word.line_id, [])
            line_text = " ".join(w.text for w in line_words).upper()
            
            # Check if this amount is near a total indicator
            is_near_total = False
            for indicator_line in total_indicator_lines:
                if abs(match.word.line_id - indicator_line) <= 3:  # Within 3 lines
                    is_near_total = True
                    break
            
            # Check for grand total
            if is_near_total and value > 10.0 and value < 1000.0:  # Reasonable total range
                if not totals.grand_total or value > totals.grand_total:
                    totals.grand_total = value
                    totals.grand_total_match = match
                    self.logger.info(f"Found grand total: ${value:.2f} on line {match.word.line_id}")
                    
            # Check for subtotal
            elif match.pattern_type == PatternType.SUBTOTAL or "SUBTOTAL" in line_text:
                if not totals.subtotal or value > totals.subtotal:
                    totals.subtotal = value
                    totals.subtotal_match = match
                    
            # Check for tax
            elif any(indicator in line_text for indicator in ["TAX", "HST", "GST", "PST", "TT"]):
                # Tax is usually small
                if value < 5.0:  # Most taxes are under $5
                    if not totals.tax or value > totals.tax:
                        totals.tax = value
                        totals.tax_match = match
                        self.logger.info(f"Found tax: ${value:.2f} on line {match.word.line_id}")
                    
            # Check for discount
            elif match.pattern_type == PatternType.DISCOUNT or \
                 any(indicator in line_text for indicator in ["DISCOUNT", "SAVINGS", "COUPON"]):
                if not totals.discount:
                    totals.discount = value
                    totals.discount_match = match
        
        return totals
    
    def _find_candidate_prices(self,
                             pattern_matches: List[PatternMatch],
                             totals: ReceiptTotals) -> List[Tuple[float, PatternMatch]]:
        """Find prices that could be line items."""
        candidates = []
        
        # Get all currency patterns
        currency_patterns = [m for m in pattern_matches if m.pattern_type in [
            PatternType.CURRENCY, PatternType.UNIT_PRICE, PatternType.LINE_TOTAL,
            PatternType.DISCOUNT
        ]]
        
        for match in currency_patterns:
            try:
                value = float(match.word.text.replace('$', '').replace(',', ''))
            except:
                continue
            
            # Skip if it's a known total
            if totals.grand_total_match and match == totals.grand_total_match:
                continue
            if totals.subtotal_match and match == totals.subtotal_match:
                continue
            if totals.tax_match and match == totals.tax_match:
                continue
                
            # Skip unreasonable prices
            if value <= 0 or value > totals.grand_total:
                continue
                
            # Skip if it's in the bottom 20% of receipt (likely totals area)
            # This is a heuristic - adjust based on receipt format
            if match.word.line_id > 60:  # Rough estimate
                continue
                
            candidates.append((value, match))
            
        return candidates
    
    def _find_valid_combinations(self,
                               candidates: List[Tuple[float, PatternMatch]],
                               totals: ReceiptTotals) -> List[List[Tuple[float, PatternMatch]]]:
        """Find combinations of prices that sum to the total."""
        valid_combinations = []
        
        # Determine target sum
        if totals.subtotal and totals.tax:
            # We have subtotal and tax, so item prices should sum to subtotal
            target_sum = totals.subtotal
            self.logger.info(f"Looking for combinations that sum to subtotal: ${target_sum:.2f}")
        else:
            # Only have grand total, items + tax should sum to it
            # This is trickier - we might need to identify tax from the candidates
            target_sum = totals.grand_total
            if totals.tax:
                target_sum -= totals.tax
            self.logger.info(f"Looking for combinations that sum to: ${target_sum:.2f}")
        
        # Try different combination sizes (typically 2-10 items on a receipt)
        for size in range(1, min(len(candidates), 11)):
            for combo in combinations(candidates, size):
                combo_sum = sum(item[0] for item in combo)
                
                # Check if sum matches target (within tolerance)
                if abs(combo_sum - target_sum) <= self.tolerance:
                    valid_combinations.append(list(combo))
                    self.logger.info(f"Found valid combination of {size} items: "
                                   f"${combo_sum:.2f} ≈ ${target_sum:.2f}")
        
        return valid_combinations
    
    def _choose_best_combination(self,
                               valid_combinations: List[List[Tuple[float, PatternMatch]]],
                               words: List[ReceiptWord]) -> List[Tuple[float, PatternMatch]]:
        """Choose the most likely combination."""
        if len(valid_combinations) == 1:
            return valid_combinations[0]
        
        # Score each combination
        best_combo = None
        best_score = -1
        
        for combo in valid_combinations:
            score = 0
            
            # Prefer combinations with 2-5 items (most common)
            if 2 <= len(combo) <= 5:
                score += 10
            
            # Check if prices are in reasonable range
            for value, match in combo:
                if 0.50 <= value <= 50.00:  # Typical item price range
                    score += 1
            
            # Check if prices are from product area (middle of receipt)
            for value, match in combo:
                if 10 <= match.word.line_id <= 40:
                    score += 1
            
            # Check vertical alignment
            x_positions = []
            for value, match in combo:
                if hasattr(match.word, 'bottom_right') and match.word.bottom_right:
                    x_right = match.word.bottom_right.get('x', 0) if isinstance(match.word.bottom_right, dict) else match.word.bottom_right[0]
                    x_positions.append(x_right)
            
            # Calculate alignment score
            if x_positions and len(x_positions) > 1:
                x_variance = sum((x - sum(x_positions)/len(x_positions))**2 for x in x_positions) / len(x_positions)
                if x_variance < 0.01:  # Well aligned
                    score += 5
            
            if score > best_score:
                best_score = score
                best_combo = combo
        
        return best_combo
    
    def _match_products_to_prices(self,
                                price_combination: List[Tuple[float, PatternMatch]],
                                words: List[ReceiptWord]) -> List[ConstrainedLineItem]:
        """Match product descriptions to validated prices."""
        # Group words by line
        words_by_line = {}
        for word in words:
            if word.line_id not in words_by_line:
                words_by_line[word.line_id] = []
            words_by_line[word.line_id].append(word)
        
        line_items = []
        
        for value, price_match in price_combination:
            price_line = price_match.word.line_id
            
            # Look for product description above the price
            best_product = None
            best_score = 0
            
            for offset in range(-1, -4, -1):  # Check 1-3 lines above
                check_line = price_line + offset
                if check_line in words_by_line:
                    line_words = words_by_line[check_line]
                    line_text = " ".join(w.text for w in line_words)
                    
                    # Simple scoring
                    score = 0.5
                    
                    # Boost for product-like words
                    if any(w.text.isalpha() and len(w.text) > 3 for w in line_words):
                        score += 0.3
                    
                    # Penalty for metadata
                    metadata_keywords = {"TAX", "TOTAL", "CARD", "CASH", "THANK", "VISIT"}
                    if any(kw in line_text.upper() for kw in metadata_keywords):
                        score -= 0.5
                    
                    if score > best_score:
                        best_score = score
                        best_product = line_words
            
            if best_product:
                product_text = " ".join(w.text for w in best_product)
                line_items.append(ConstrainedLineItem(
                    product_text=product_text,
                    product_words=best_product,
                    price=value,
                    price_match=price_match,
                    product_line=best_product[0].line_id,
                    price_line=price_line,
                    confidence=best_score
                ))
                self.logger.info(f"Matched '{product_text}' → ${value:.2f}")
        
        return line_items