"""
Pure mathematical approach to line item detection.

No keywords, no heuristics - just math. Find which numbers sum to other numbers.
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
class MathSolution:
    """A mathematical solution for receipt totals."""
    item_prices: List[Tuple[float, PatternMatch]]  # The line items
    subtotal: float  # Sum of item prices
    tax: Optional[Tuple[float, PatternMatch]]  # Tax amount if found
    grand_total: Tuple[float, PatternMatch]  # The total everything sums to
    confidence: float  # How confident we are in this solution


class MathSolverDetector:
    """Solves line item detection as a pure math problem."""
    
    def __init__(self, tolerance: float = 0.02, max_solutions: int = 50):
        """
        Initialize detector.
        
        Args:
            tolerance: Maximum difference allowed (e.g., 0.02 = 2 cents)
            max_solutions: Maximum number of solutions to find (prevent combinatorial explosion)
        """
        self.tolerance = tolerance
        self.max_solutions = max_solutions
        self.logger = logger
        
    def solve_receipt_math(self,
                          currency_values: List[Tuple[float, PatternMatch]]) -> List[MathSolution]:
        """
        Find all valid mathematical solutions for the receipt.
        
        A valid solution is where:
        - Some numbers (items) sum to a subtotal
        - Subtotal + another number (tax) = a larger number (total)
        - OR: Some numbers directly sum to a total
        
        Args:
            currency_values: List of (value, pattern_match) tuples
            
        Returns:
            List of possible solutions, sorted by confidence
        """
        self.logger.info(f"Solving receipt math with {len(currency_values)} values")
        
        # Sort values by amount
        values = sorted(currency_values, key=lambda x: x[0])
        
        solutions = []
        
        # For each potential grand total (usually larger values)
        for i in range(len(values) - 1, -1, -1):
            potential_total_value, potential_total_match = values[i]
            
            # Skip very small or very large values
            if potential_total_value < 5.0 or potential_total_value > 10000.0:
                continue
                
            self.logger.info(f"Testing ${potential_total_value:.2f} as potential total")
            
            # Find solutions for this total
            total_solutions = self._find_solutions_for_total(
                values[:i],  # Don't include the total itself
                potential_total_value,
                potential_total_match
            )
            
            solutions.extend(total_solutions)
        
        # Sort by confidence
        solutions.sort(key=lambda s: s.confidence, reverse=True)
        
        return solutions
    
    def _find_solutions_for_total(self,
                                 values: List[Tuple[float, PatternMatch]],
                                 total_amount: float,
                                 total_match: PatternMatch) -> List[MathSolution]:
        """Find all ways values can sum to the total."""
        solutions = []
        
        # Method 1: Direct sum (no tax)
        # Find combinations that directly sum to total
        for size in range(1, min(len(values), 10)):
            if len(solutions) >= self.max_solutions:
                self.logger.info(f"Reached max solutions limit ({self.max_solutions}), stopping search")
                break
                
            for combo in combinations(values, size):
                combo_sum = sum(v[0] for v in combo)
                if abs(combo_sum - total_amount) <= self.tolerance:
                    solutions.append(MathSolution(
                        item_prices=list(combo),
                        subtotal=combo_sum,
                        tax=None,
                        grand_total=(total_amount, total_match),
                        confidence=0.8  # Good confidence for direct match
                    ))
                    self.logger.info(f"Found direct sum: {[v[0] for v in combo]} = ${combo_sum:.2f}")
                    
                if len(solutions) >= self.max_solutions:
                    self.logger.info(f"Reached max solutions limit ({self.max_solutions}), stopping search")
                    break
        
        # Method 2: Items + Tax = Total
        # For each potential tax amount
        for j, (tax_value, tax_match) in enumerate(values):
            if len(solutions) >= self.max_solutions:
                break
                
            # Tax is usually small (< 20% of total)
            if tax_value > total_amount * 0.2:
                continue
                
            target_subtotal = total_amount - tax_value
            
            # Find combinations that sum to subtotal
            remaining_values = values[:j] + values[j+1:]  # Exclude the tax
            
            for size in range(1, min(len(remaining_values), 10)):
                if len(solutions) >= self.max_solutions:
                    break
                    
                for combo in combinations(remaining_values, size):
                    combo_sum = sum(v[0] for v in combo)
                    if abs(combo_sum - target_subtotal) <= self.tolerance:
                        # Verify: items + tax = total
                        if abs(combo_sum + tax_value - total_amount) <= self.tolerance:
                            solutions.append(MathSolution(
                                item_prices=list(combo),
                                subtotal=combo_sum,
                                tax=(tax_value, tax_match),
                                grand_total=(total_amount, total_match),
                                confidence=0.9  # High confidence for tax structure
                            ))
                            self.logger.info(f"Found with tax: {[v[0] for v in combo]} + ${tax_value:.2f} = ${total_amount:.2f}")
                            
                    if len(solutions) >= self.max_solutions:
                        break
        
        return solutions
    
    def find_best_solution(self,
                          solutions: List[MathSolution],
                          price_column_x: Optional[float] = None) -> Optional[MathSolution]:
        """
        Choose the best solution based on additional constraints.
        
        Args:
            solutions: List of mathematically valid solutions
            price_column_x: X-coordinate of price column (if known)
            
        Returns:
            Best solution or None
        """
        if not solutions:
            return None
            
        # If only one solution, return it
        if len(solutions) == 1:
            return solutions[0]
        
        # Score each solution
        best_solution = None
        best_score = -1
        
        for solution in solutions:
            score = solution.confidence
            
            # Prefer solutions with 2-10 items (typical receipt)
            num_items = len(solution.item_prices)
            if 2 <= num_items <= 10:
                score += 0.1
            
            # Prefer solutions with tax (more common)
            if solution.tax:
                score += 0.1
                # Tax should be reasonable (5-15% of subtotal)
                tax_rate = solution.tax[0] / solution.subtotal
                if 0.05 <= tax_rate <= 0.15:
                    score += 0.1
            
            # If we know the price column, prefer prices from that column
            if price_column_x is not None:
                aligned_count = 0
                for _, match in solution.item_prices:
                    if hasattr(match.word, 'bottom_right'):
                        x = match.word.bottom_right.get('x', 0) if isinstance(match.word.bottom_right, dict) else match.word.bottom_right[0]
                        if abs(x - price_column_x) < 0.02:
                            aligned_count += 1
                
                alignment_ratio = aligned_count / len(solution.item_prices)
                score += alignment_ratio * 0.2
            
            if score > best_score:
                best_score = score
                best_solution = solution
        
        return best_solution
    
    def extract_currency_values(self, pattern_matches: List[PatternMatch]) -> List[Tuple[float, PatternMatch]]:
        """Extract numeric values from currency patterns."""
        values = []
        
        for match in pattern_matches:
            # Only consider currency-related patterns
            if match.pattern_type not in [
                PatternType.CURRENCY, PatternType.GRAND_TOTAL, PatternType.SUBTOTAL,
                PatternType.TAX, PatternType.UNIT_PRICE, PatternType.LINE_TOTAL,
                PatternType.DISCOUNT
            ]:
                continue
                
            try:
                # Extract numeric value
                text = match.word.text.replace('$', '').replace(',', '')
                value = float(text)
                
                # Skip invalid values
                if value <= 0 or value > 100000:
                    continue
                    
                values.append((value, match))
            except:
                continue
        
        return values