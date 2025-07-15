"""
Pure mathematical approach to line item detection.

No keywords, no heuristics - just math. Find which numbers sum to other numbers.
"""

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple
from itertools import combinations
import math
import numpy as np

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
    
    def __init__(self, tolerance: float = 0.02, max_solutions: int = 50, use_numpy_optimization: bool = True):
        """
        Initialize detector.
        
        Args:
            tolerance: Maximum difference allowed (e.g., 0.02 = 2 cents)
            max_solutions: Maximum number of solutions to find (prevent combinatorial explosion)
            use_numpy_optimization: Use NumPy-based dynamic programming for large value sets
        """
        self.tolerance = tolerance
        self.max_solutions = max_solutions
        self.use_numpy_optimization = use_numpy_optimization
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
            
            # Choose optimization strategy based on problem size and settings
            remaining_values = values[:i]  # Don't include the total itself
            
            if self.use_numpy_optimization and len(remaining_values) > 10:
                # Use NumPy DP optimization for large problems
                total_solutions = self._find_solutions_numpy_optimized(
                    remaining_values,
                    potential_total_value,
                    potential_total_match
                )
            else:
                # Use traditional brute force for small problems
                total_solutions = self._find_solutions_for_total(
                    remaining_values,
                    potential_total_value,
                    potential_total_match
                )
            
            solutions.extend(total_solutions)
        
        # Sort by confidence
        solutions.sort(key=lambda s: s.confidence, reverse=True)
        
        return solutions
    
    def _find_solutions_numpy_optimized(self,
                                      values: List[Tuple[float, PatternMatch]], 
                                      total_amount: float,
                                      total_match: PatternMatch) -> List[MathSolution]:
        """
        NumPy-optimized subset sum finder using dynamic programming.
        
        Converts the subset-sum problem to integer cents and uses vectorized
        operations to find all valid combinations efficiently.
        
        Time complexity: O(n * target_cents) vs O(2^n) brute force
        """
        if len(values) <= 10:
            # Use brute force for small cases (still fast)
            return self._find_solutions_for_total(values, total_amount, total_match)
        
        solutions = []
        
        # Convert to integer cents to avoid floating point precision issues
        target_cents = int(round(total_amount * 100))
        tolerance_cents = int(round(self.tolerance * 100))
        price_cents = np.array([int(round(v[0] * 100)) for v in values])
        
        # Method 1: Direct sum using dynamic programming
        direct_solutions = self._numpy_subset_sum_dp(
            price_cents, values, target_cents, tolerance_cents, total_match, tax_info=None
        )
        solutions.extend(direct_solutions)
        
        if len(solutions) >= self.max_solutions:
            return solutions[:self.max_solutions]
        
        # Method 2: Items + Tax = Total
        for tax_idx, (tax_value, tax_match) in enumerate(values):
            if len(solutions) >= self.max_solutions:
                break
                
            # Skip if tax is too large (> 20% of total)
            if tax_value > total_amount * 0.2:
                continue
            
            # Target subtotal = total - tax
            tax_cents = int(round(tax_value * 100))
            subtotal_target_cents = target_cents - tax_cents
            
            # Create array excluding the tax value
            remaining_indices = np.arange(len(values))
            remaining_indices = remaining_indices[remaining_indices != tax_idx]
            remaining_price_cents = price_cents[remaining_indices]
            remaining_values = [values[i] for i in remaining_indices]
            
            # Find combinations that sum to subtotal
            tax_solutions = self._numpy_subset_sum_dp(
                remaining_price_cents, remaining_values, subtotal_target_cents, 
                tolerance_cents, total_match, tax_info=(tax_value, tax_match)
            )
            solutions.extend(tax_solutions)
        
        return solutions[:self.max_solutions]
    
    def _numpy_subset_sum_dp(self,
                           price_cents: np.ndarray,
                           values: List[Tuple[float, PatternMatch]],
                           target_cents: int,
                           tolerance_cents: int,
                           total_match: PatternMatch,
                           tax_info: Optional[Tuple[float, PatternMatch]] = None) -> List[MathSolution]:
        """
        Dynamic programming subset-sum using NumPy for vectorized operations.
        
        Creates a boolean DP table where dp[i][s] = True if we can achieve sum s 
        using the first i items. Uses NumPy for efficient array operations.
        """
        n = len(price_cents)
        max_sum = target_cents + tolerance_cents + 1
        
        # Limit DP table size to prevent memory issues
        if max_sum > 100000:  # ~$1000 limit
            self.logger.warning(f"Target too large for DP optimization: ${target_cents/100:.2f}, falling back to brute force")
            return self._find_solutions_for_total(values, target_cents/100, total_match)
        
        # DP table: dp[i][s] = True if sum s is achievable using first i items
        # Use uint8 for memory efficiency (0 or 1)
        dp = np.zeros((n + 1, max_sum), dtype=np.uint8)
        dp[0, 0] = 1  # Base case: sum 0 with 0 items
        
        # Fill DP table using vectorized operations
        for i in range(1, n + 1):
            price = price_cents[i - 1]
            
            # Copy previous row (don't include current item)
            dp[i] = dp[i - 1].copy()
            
            # Add current item where possible (vectorized)
            if price < max_sum:
                # Use numpy's advanced indexing for efficiency
                possible_sums = np.where(dp[i - 1, :-price])[0]
                if len(possible_sums) > 0:
                    new_sums = possible_sums + price
                    valid_new_sums = new_sums[new_sums < max_sum]
                    dp[i, valid_new_sums] = 1
        
        # Find all valid target sums within tolerance
        valid_targets = range(
            max(0, target_cents - tolerance_cents),
            min(max_sum, target_cents + tolerance_cents + 1)
        )
        
        solutions = []
        for target in valid_targets:
            if dp[n, target]:
                # Backtrack to find which items were used
                combinations_found = self._backtrack_numpy_solutions(
                    dp, price_cents, values, target, n
                )
                
                for combination in combinations_found:
                    if len(solutions) >= self.max_solutions:
                        break
                    
                    # Create solution object
                    combo_sum = sum(v[0] for v in combination)
                    
                    if tax_info is None:
                        # Direct sum solution
                        solutions.append(MathSolution(
                            item_prices=combination,
                            subtotal=combo_sum,
                            tax=None,
                            grand_total=(target_cents / 100, total_match),
                            confidence=0.85  # Good confidence for DP match
                        ))
                        self.logger.info(f"Found direct sum (NumPy): {[v[0] for v in combination]} = ${combo_sum:.2f}")
                    else:
                        # Tax structure solution
                        tax_value, tax_match = tax_info
                        grand_total_value = combo_sum + tax_value
                        solutions.append(MathSolution(
                            item_prices=combination,
                            subtotal=combo_sum,
                            tax=tax_info,
                            grand_total=(grand_total_value, total_match),
                            confidence=0.9  # High confidence for tax structure
                        ))
                        self.logger.info(f"Found with tax (NumPy): {[v[0] for v in combination]} + ${tax_value:.2f} = ${grand_total_value:.2f}")
                
                if len(solutions) >= self.max_solutions:
                    break
        
        return solutions
    
    def _backtrack_numpy_solutions(self,
                                 dp: np.ndarray,
                                 price_cents: np.ndarray, 
                                 values: List[Tuple[float, PatternMatch]],
                                 target: int,
                                 n: int,
                                 max_combinations: int = 5) -> List[List[Tuple[float, PatternMatch]]]:
        """
        Backtrack through DP table to find actual item combinations.
        
        Returns up to max_combinations different ways to achieve the target sum.
        Uses iterative approach to avoid stack overflow on large problems.
        """
        combinations = []
        
        # Use iterative approach to avoid recursion depth issues
        # Stack contains tuples of (index, remaining_sum, current_combination)
        stack = [(n, target, [])]
        
        while stack and len(combinations) < max_combinations:
            i, remaining_sum, current_combo = stack.pop()
            
            if remaining_sum == 0:
                combinations.append(current_combo.copy())
                continue
            
            if i <= 0 or remaining_sum < 0 or remaining_sum >= dp.shape[1]:
                continue
            
            # Option 1: Don't include item i-1
            if remaining_sum < dp.shape[1] and dp[i - 1, remaining_sum]:
                stack.append((i - 1, remaining_sum, current_combo.copy()))
            
            # Option 2: Include item i-1 (if possible and within bounds)
            price = price_cents[i - 1]
            new_remaining = remaining_sum - price
            if (price <= remaining_sum and 
                new_remaining >= 0 and 
                new_remaining < dp.shape[1] and 
                dp[i - 1, new_remaining]):
                new_combo = current_combo.copy()
                new_combo.append(values[i - 1])
                stack.append((i - 1, new_remaining, new_combo))
        
        return combinations
    
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