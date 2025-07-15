"""
Final integrated line item detector combining math and spatial alignment.

This detector:
1. Uses pure math to find valid price combinations
2. Uses vertical alignment to identify price columns
3. Matches products to mathematically validated prices
"""

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from receipt_dynamo.entities.receipt_word import ReceiptWord
from receipt_label.pattern_detection.base import PatternMatch, PatternType
from .math_solver_detector import MathSolverDetector, MathSolution
from .vertical_alignment_detector import VerticalAlignmentDetector
from .sequential_matcher import SequentialMatcher

logger = logging.getLogger(__name__)


@dataclass
class FinalLineItem:
    """A fully validated line item."""
    product_text: str
    product_words: List[ReceiptWord]
    price: float
    price_match: PatternMatch
    product_line: int
    price_line: int
    confidence: float
    validation_method: str  # "math", "math+spatial", "math+spatial+semantic"


class FinalLineItemDetector:
    """Integrated line item detector using math and spatial alignment."""
    
    def __init__(self, tolerance: float = 0.02):
        """Initialize detector."""
        self.tolerance = tolerance
        self.math_solver = MathSolverDetector(tolerance)
        self.alignment_detector = VerticalAlignmentDetector()
        self.sequential_matcher = SequentialMatcher()
        self.logger = logger
        
    def detect_line_items(self,
                         words: List[ReceiptWord],
                         pattern_matches: List[PatternMatch]) -> Dict:
        """
        Detect line items using integrated approach.
        
        Steps:
        1. Extract all currency values
        2. Find mathematically valid solutions
        3. Identify price columns
        4. Choose best solution based on alignment
        5. Match products to validated prices
        
        Args:
            words: All receipt words
            pattern_matches: All pattern matches
            
        Returns:
            Dictionary with detection results
        """
        self.logger.info("Starting integrated line item detection")
        
        # Step 1: Extract currency values
        currency_values = self.math_solver.extract_currency_values(pattern_matches)
        self.logger.info(f"Found {len(currency_values)} currency values")
        
        # Step 2: Find mathematically valid solutions
        math_solutions = self.math_solver.solve_receipt_math(currency_values)
        self.logger.info(f"Found {len(math_solutions)} mathematically valid solutions")
        
        if not math_solutions:
            return {
                'line_items': [],
                'total_items': 0,
                'math_solutions': 0,
                'method': 'none'
            }
        
        # Step 3: Identify price columns
        currency_patterns = [m for m in pattern_matches if m.pattern_type in [
            PatternType.CURRENCY, PatternType.GRAND_TOTAL, PatternType.SUBTOTAL,
            PatternType.TAX, PatternType.UNIT_PRICE, PatternType.LINE_TOTAL,
            PatternType.DISCOUNT
        ]]
        
        price_columns = self.alignment_detector.detect_price_columns(currency_patterns)
        self.logger.info(f"Found {len(price_columns)} price columns")
        
        # Find the main price column (one with most prices)
        main_column_x = None
        if price_columns:
            # Look for column containing prices from our solutions
            solution_prices = set()
            for solution in math_solutions[:3]:  # Check top 3 solutions
                for value, match in solution.item_prices:
                    solution_prices.add(match.word.text)
            
            for column in price_columns:
                column_texts = {p.word.text for p in column.prices}
                overlap = len(solution_prices & column_texts)
                if overlap >= 2:  # Found column with solution prices
                    main_column_x = column.x_center
                    self.logger.info(f"Found main price column at X={main_column_x:.3f}")
                    break
        
        # Step 4: Choose best solution
        best_solution = self.math_solver.find_best_solution(math_solutions, main_column_x)
        
        if not best_solution:
            best_solution = math_solutions[0]  # Fallback to first solution
        
        self.logger.info(f"Selected solution with {len(best_solution.item_prices)} items, "
                        f"tax={'yes' if best_solution.tax else 'no'}, "
                        f"total=${best_solution.grand_total[0]:.2f}")
        
        # Step 5: Match products to prices using sequential matching
        # Convert solution to format expected by sequential matcher
        price_tuples = [(v, m) for v, m in best_solution.item_prices]
        line_items = self.sequential_matcher.match_sequential(words, price_tuples)
        
        # Convert to FinalLineItem format
        final_items = []
        for item in line_items:
            final_items.append(FinalLineItem(
                product_text=item.product_text,
                product_words=item.product_words,
                price=item.price,
                price_match=item.price_match,
                product_line=item.product_line,
                price_line=item.price_line,
                confidence=0.9,  # High confidence from math validation
                validation_method="math+sequential"
            ))
        
        return {
            'line_items': final_items,
            'total_items': len(final_items),
            'math_solutions': len(math_solutions),
            'price_columns': len(price_columns),
            'subtotal': best_solution.subtotal,
            'tax': best_solution.tax[0] if best_solution.tax else None,
            'grand_total': best_solution.grand_total[0],
            'method': 'math+spatial'
        }
    
    def _match_products_to_prices(self,
                                 words: List[ReceiptWord],
                                 solution: MathSolution) -> List[FinalLineItem]:
        """Match product descriptions to mathematically validated prices."""
        # Group words by line
        words_by_line = {}
        for word in words:
            if word.line_id not in words_by_line:
                words_by_line[word.line_id] = []
            words_by_line[word.line_id].append(word)
        
        # Sort prices by line number for sequential matching
        sorted_prices = sorted(solution.item_prices, key=lambda x: x[1].word.line_id)
        
        line_items = []
        used_lines = set()
        
        for value, price_match in sorted_prices:
            price_line = price_match.word.line_id
            
            # Look for product above this price
            best_product = None
            best_score = 0
            best_line = None
            
            # Search up to 5 lines above (receipts can have spacing)
            for offset in range(1, 6):
                check_line = price_line - offset
                
                # Skip if already used or doesn't exist
                if check_line in used_lines or check_line not in words_by_line:
                    continue
                
                line_words = words_by_line[check_line]
                score = self._score_product_line(line_words, price_line - check_line)
                
                if score > best_score:
                    best_score = score
                    best_product = line_words
                    best_line = check_line
            
            if best_product and best_score > 0.3:
                product_text = " ".join(w.text for w in sorted(best_product, key=lambda w: w.word_id))
                
                line_items.append(FinalLineItem(
                    product_text=product_text,
                    product_words=best_product,
                    price=value,
                    price_match=price_match,
                    product_line=best_line,
                    price_line=price_line,
                    confidence=best_score,
                    validation_method="math+spatial"
                ))
                
                used_lines.add(best_line)
                self.logger.info(f"Matched '{product_text}' → ${value:.2f}")
            else:
                # No product found, use generic description
                self.logger.warning(f"No product found for ${value:.2f} on line {price_line}")
        
        return line_items
    
    def _score_product_line(self, line_words: List[ReceiptWord], distance: int) -> float:
        """Score how likely a line is to be a product description."""
        if not line_words:
            return 0.0
            
        score = 0.5  # Base score
        
        # Distance penalty (prefer closer lines)
        score -= distance * 0.1
        
        # Length preference (2-6 words is typical)
        word_count = len(line_words)
        if 2 <= word_count <= 6:
            score += 0.2
        elif word_count == 1:
            score -= 0.1
        elif word_count > 8:
            score -= 0.2
        
        # Alphabetic content (products have letters)
        alpha_words = sum(1 for w in line_words if any(c.isalpha() for c in w.text))
        if alpha_words >= 1:
            score += 0.2
        
        # All uppercase penalty (often headers)
        line_text = " ".join(w.text for w in line_words)
        if line_text.isupper() and len(line_words) == 1:
            score -= 0.2
        
        # Numeric only penalty
        if all(w.text.replace('.', '').replace(',', '').isdigit() for w in line_words):
            score -= 0.3
        
        return max(0.0, min(score, 1.0))