"""
Enhanced line item detector with semantic improvements.

Implements:
- Phase 1: Quick wins (exclude $0.00, use pattern labels, spatial contiguity)
- Phase 2: Layout segmentation (header/items/footer detection)
"""

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple
from collections import defaultdict

from receipt_dynamo.entities.receipt_word import ReceiptWord
from receipt_label.pattern_detection.base import PatternMatch

from .vertical_alignment_detector import VerticalAlignmentDetector, PriceColumn
from .math_solver_detector import MathSolverDetector, MathSolution
from .sequential_matcher import SequentialMatcher, SequentialLineItem

logger = logging.getLogger(__name__)


@dataclass
class ReceiptSection:
    """Represents a section of the receipt."""
    start_line: int
    end_line: int
    section_type: str  # 'header', 'items', 'footer'


class EnhancedLineItemDetector:
    """Enhanced detector with layout segmentation and pattern exclusion."""
    
    def __init__(self, tolerance: float = 0.01):
        """Initialize the enhanced detector."""
        self.tolerance = tolerance
        self.alignment_detector = VerticalAlignmentDetector()
        self.math_solver = MathSolverDetector(tolerance=tolerance)
        self.sequential_matcher = SequentialMatcher()
        self.logger = logger
        
        # Patterns that indicate non-item lines
        self.excluded_labels = {
            'MERCHANT_NAME', 'PHONE_NUMBER', 'DATE', 'TIME', 'DATETIME',
            'EMAIL', 'WEBSITE'
        }
        
    def detect_line_items(self, 
                         words: List[ReceiptWord],
                         pattern_matches: List[PatternMatch]) -> Dict:
        """
        Detect line items with enhanced filtering.
        
        Args:
            words: All receipt words
            pattern_matches: All pattern matches from detectors
            
        Returns:
            Dictionary with detected line items and metadata
        """
        # Step 1: Segment receipt into sections
        sections = self._segment_receipt(words, pattern_matches)
        self.logger.info(f"Receipt sections: {sections}")
        
        # Step 2: Filter pattern matches by exclusion and section
        filtered_matches = self._filter_matches(pattern_matches, sections)
        
        # Step 3: Extract currency values (excluding $0.00)
        # Include all currency-related patterns, not just CURRENCY
        currency_patterns = {
            'CURRENCY', 'GRAND_TOTAL', 'SUBTOTAL', 'TAX', 
            'DISCOUNT', 'UNIT_PRICE', 'LINE_TOTAL'
        }
        currency_values = []
        for match in filtered_matches:
            if match.pattern_type.name in currency_patterns and match.extracted_value:
                try:
                    # extracted_value is already a float for currency patterns
                    value = float(match.extracted_value)
                    if abs(value) > 0.001:  # Exclude $0.00
                        currency_values.append((value, match))
                except (ValueError, TypeError):
                    continue
        
        if not currency_values:
            return self._empty_result()
            
        # Step 4: Find mathematically valid solutions
        solutions = self.math_solver.solve_receipt_math(currency_values)
        
        if not solutions:
            self.logger.warning("No valid mathematical solutions found")
            return self._empty_result()
            
        # Step 5: Apply spatial contiguity check
        valid_solutions = self._check_spatial_contiguity(solutions, sections)
        
        if not valid_solutions:
            self.logger.warning("No spatially contiguous solutions found")
            return self._empty_result()
            
        # Step 6: Detect price columns for validated prices
        best_solution = valid_solutions[0]  # Take first valid solution
        validated_prices = [(p[0], p[1]) for p in best_solution.item_prices]
        
        price_columns = self.alignment_detector.detect_price_columns(
            [p[1] for p in validated_prices]
        )
        
        # Step 7: Match products to prices
        line_items = self.sequential_matcher.match_sequential(
            words, validated_prices
        )
        
        # Step 8: Filter line items to only those in item section
        if sections.get('items'):
            item_section = sections['items']
            line_items = [
                item for item in line_items
                if item_section.start_line <= item.product_line <= item_section.end_line
            ]
        
        # Compile results
        result = {
            'line_items': line_items,
            'total_items': len(line_items),
            'subtotal': best_solution.subtotal,
            'tax': best_solution.tax,
            'grand_total': best_solution.grand_total,
            'price_columns': price_columns,
            'math_solutions': len(valid_solutions),
            'sections': {
                name: {'start': s.start_line, 'end': s.end_line}
                for name, s in sections.items()
            }
        }
        
        self.logger.info(f"Enhanced detection complete: {result['total_items']} items found")
        return result
        
    def _segment_receipt(self,
                        words: List[ReceiptWord],
                        pattern_matches: List[PatternMatch]) -> Dict[str, ReceiptSection]:
        """
        Segment receipt into header, items, and footer sections.
        """
        sections = {}
        
        # Group words by line
        words_by_line = defaultdict(list)
        for word in words:
            words_by_line[word.line_id].append(word)
        
        line_ids = sorted(words_by_line.keys())
        if not line_ids:
            return sections
            
        # Find header end (first price or after date/time/phone)
        header_end = 0
        for match in pattern_matches:
            if match.pattern_type.name in ['DATE', 'TIME', 'DATETIME', 'PHONE_NUMBER']:
                header_end = max(header_end, match.word.line_id)
        
        # Find first price as potential start of items
        first_price_line = None
        currency_patterns = {
            'CURRENCY', 'GRAND_TOTAL', 'SUBTOTAL', 'TAX', 
            'DISCOUNT', 'UNIT_PRICE', 'LINE_TOTAL'
        }
        for match in pattern_matches:
            if match.pattern_type.name in currency_patterns and match.extracted_value:
                try:
                    # extracted_value is already a float for currency patterns
                    value = float(match.extracted_value)
                    if value > 0 and value < 1000:  # Reasonable item price
                        if first_price_line is None or match.word.line_id < first_price_line:
                            first_price_line = match.word.line_id
                except:
                    continue
        
        # Find footer start (look for TOTAL, TAX, SUBTOTAL)
        footer_start = line_ids[-1]
        for match in pattern_matches:
            line_text = ' '.join(w.text.upper() for w in words_by_line[match.word.line_id])
            if any(keyword in line_text for keyword in ['TOTAL', 'TAX', 'SUBTOTAL', 'BALANCE']):
                footer_start = min(footer_start, match.word.line_id)
                
        # Adjust sections
        if first_price_line and first_price_line > header_end:
            # Items likely start a bit before first price
            items_start = max(header_end + 1, first_price_line - 5)
        else:
            items_start = header_end + 1
            
        # Create sections
        if header_end > 0:
            sections['header'] = ReceiptSection(
                start_line=line_ids[0],
                end_line=header_end,
                section_type='header'
            )
            
        if items_start < footer_start:
            sections['items'] = ReceiptSection(
                start_line=items_start,
                end_line=footer_start - 1,
                section_type='items'
            )
            
        if footer_start < line_ids[-1]:
            sections['footer'] = ReceiptSection(
                start_line=footer_start,
                end_line=line_ids[-1],
                section_type='footer'
            )
            
        return sections
        
    def _filter_matches(self,
                       pattern_matches: List[PatternMatch],
                       sections: Dict[str, ReceiptSection]) -> List[PatternMatch]:
        """
        Filter pattern matches by exclusion rules and sections.
        """
        filtered = []
        
        # Get item section bounds
        item_section = sections.get('items')
        
        for match in pattern_matches:
            # Skip excluded patterns
            if match.pattern_type.name in self.excluded_labels:
                continue
                
            # Skip if outside item section (for currency-related patterns)
            currency_patterns = {
                'CURRENCY', 'GRAND_TOTAL', 'SUBTOTAL', 'TAX', 
                'DISCOUNT', 'UNIT_PRICE', 'LINE_TOTAL'
            }
            if match.pattern_type.name in currency_patterns and item_section:
                if not (item_section.start_line <= match.word.line_id <= item_section.end_line):
                    continue
                    
            filtered.append(match)
            
        return filtered
        
    def _check_spatial_contiguity(self,
                                 solutions: List[MathSolution],
                                 sections: Dict[str, ReceiptSection]) -> List[MathSolution]:
        """
        Check that prices in solution are spatially contiguous.
        """
        valid_solutions = []
        
        for solution in solutions:
            # Get line numbers of all prices
            price_lines = [p[1].word.line_id for p in solution.item_prices]
            if solution.tax:
                price_lines.append(solution.tax[1].word.line_id)
            price_lines.append(solution.grand_total[1].word.line_id)
            
            if not price_lines:
                continue
                
            # Check if they're clustered
            min_line = min(price_lines)
            max_line = max(price_lines)
            line_span = max_line - min_line + 1
            
            # Prices should be relatively close together
            if line_span <= len(price_lines) * 3:  # Allow some gaps
                # Also check if they're in the item section
                item_section = sections.get('items')
                if item_section:
                    # Most prices should be in item section
                    in_section = sum(
                        1 for line in price_lines 
                        if item_section.start_line <= line <= item_section.end_line
                    )
                    if in_section >= len(price_lines) * 0.7:  # 70% threshold
                        valid_solutions.append(solution)
                else:
                    # No section info, just use contiguity
                    valid_solutions.append(solution)
                    
        return valid_solutions
        
    def _empty_result(self) -> Dict:
        """Return empty result structure."""
        return {
            'line_items': [],
            'total_items': 0,
            'subtotal': None,
            'tax': None,
            'grand_total': None,
            'price_columns': [],
            'math_solutions': 0,
            'sections': {}
        }