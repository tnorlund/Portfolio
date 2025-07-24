"""
Enhanced multicolumn handler for receipt line item detection.

This module provides advanced column detection and line item assembly for receipts
with multiple price/quantity columns, reducing reliance on LLM calls by providing
structured data extraction.
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple, Any
from collections import defaultdict
from enum import Enum

from receipt_dynamo.entities.receipt_word import ReceiptWord
from receipt_label.pattern_detection.base import PatternMatch, PatternType
from receipt_label.spatial.geometry_utils import SpatialWord
from receipt_label.spatial.vertical_alignment_detector import PriceColumn, AlignedLineItem

logger = logging.getLogger(__name__)


class ColumnType(Enum):
    """Types of columns found in receipts."""
    DESCRIPTION = "description"
    QUANTITY = "quantity"
    UNIT_PRICE = "unit_price"
    LINE_TOTAL = "line_total"
    DISCOUNT = "discount"
    TAX_AMOUNT = "tax_amount"
    UNKNOWN = "unknown"


@dataclass
class ColumnClassification:
    """Classification result for a detected column."""
    column_id: int
    column_type: ColumnType
    confidence: float
    x_position: float  # Average X position (0-1)
    supporting_evidence: List[str] = field(default_factory=list)
    value_statistics: Dict[str, float] = field(default_factory=dict)


@dataclass
class MultiColumnLineItem:
    """Complete line item assembled from multiple columns."""
    description: str
    description_words: List[ReceiptWord]
    quantity: Optional[float] = None
    unit_price: Optional[float] = None
    line_total: Optional[float] = None
    discount: Optional[float] = None
    tax_amount: Optional[float] = None
    line_number: int = 0
    confidence: float = 0.0
    validation_status: Dict[str, bool] = field(default_factory=dict)
    source_columns: Dict[str, int] = field(default_factory=dict)  # Maps field to column_id


class MultiColumnHandler:
    """
    Enhanced handler for receipts with multiple price/quantity columns.
    
    This handler detects and classifies multiple columns, then assembles
    complete line items by matching data across columns on the same line.
    """
    
    def __init__(
        self,
        alignment_tolerance: float = 0.02,
        horizontal_tolerance: float = 0.05,
        min_column_items: int = 3,
        validation_threshold: float = 0.01
    ):
        """
        Initialize the multicolumn handler.
        
        Args:
            alignment_tolerance: Maximum X-coordinate difference for vertical alignment
            horizontal_tolerance: Maximum Y-coordinate difference for horizontal alignment
            min_column_items: Minimum items required to consider a valid column
            validation_threshold: Maximum difference for mathematical validation
        """
        self.alignment_tolerance = alignment_tolerance
        self.horizontal_tolerance = horizontal_tolerance
        self.min_column_items = min_column_items
        self.validation_threshold = validation_threshold
        self.logger = logger
    
    def detect_column_structure(
        self,
        receipt_words: List[ReceiptWord],
        currency_patterns: List[PatternMatch],
        quantity_patterns: List[PatternMatch] = None
    ) -> Dict[int, ColumnClassification]:
        """
        Detect and classify all columns in the receipt.
        
        Args:
            receipt_words: All words from the receipt
            currency_patterns: Detected currency patterns
            quantity_patterns: Detected quantity patterns (optional)
        
        Returns:
            Dictionary mapping column_id to ColumnClassification
        """
        # Step 1: Detect currency columns
        currency_columns = self._detect_currency_columns(currency_patterns)
        
        # Step 2: Detect quantity columns if patterns provided
        quantity_columns = []
        if quantity_patterns:
            quantity_columns = self._detect_quantity_columns(quantity_patterns)
        
        # Step 3: Classify each column based on content and position
        classified_columns = {}
        
        for col in currency_columns:
            classification = self._classify_currency_column(col, receipt_words)
            classified_columns[col.column_id] = classification
        
        for col in quantity_columns:
            classification = ColumnClassification(
                column_id=col['column_id'],
                column_type=ColumnType.QUANTITY,
                confidence=col['confidence'],
                x_position=col['x_center']
            )
            classified_columns[col['column_id']] = classification
        
        # Step 4: Detect description column (leftmost non-numeric content)
        description_col = self._detect_description_column(receipt_words, classified_columns)
        if description_col:
            classified_columns[description_col.column_id] = description_col
        
        self.logger.info(
            f"Detected {len(classified_columns)} columns: "
            f"{[f'{c.column_type.value}@{c.x_position:.2f}' for c in classified_columns.values()]}"
        )
        
        return classified_columns
    
    def _detect_currency_columns(self, currency_patterns: List[PatternMatch]) -> List[PriceColumn]:
        """Detect vertically-aligned currency columns."""
        from receipt_label.spatial.vertical_alignment_detector import VerticalAlignmentDetector
        
        detector = VerticalAlignmentDetector(
            alignment_tolerance=self.alignment_tolerance,
            use_enhanced_clustering=True
        )
        
        columns = detector.detect_price_columns(currency_patterns)
        
        # Filter out columns with too few items
        valid_columns = [
            col for col in columns 
            if len(col.prices) >= self.min_column_items
        ]
        
        return valid_columns
    
    def _detect_quantity_columns(self, quantity_patterns: List[PatternMatch]) -> List[Dict]:
        """Detect vertically-aligned quantity columns."""
        if not quantity_patterns:
            return []
        
        # Group quantities by X-coordinate
        x_groups = defaultdict(list)
        
        for pattern in quantity_patterns:
            word = pattern.word
            x_pos = self._get_x_center(word)
            
            if x_pos is not None:
                # Find existing group or create new one
                found_group = False
                for group_x, group_patterns in x_groups.items():
                    if abs(group_x - x_pos) < self.alignment_tolerance:
                        group_patterns.append(pattern)
                        found_group = True
                        break
                
                if not found_group:
                    x_groups[x_pos] = [pattern]
        
        # Convert groups to column structures
        columns = []
        for idx, (x_center, patterns) in enumerate(x_groups.items()):
            if len(patterns) >= self.min_column_items:
                columns.append({
                    'column_id': 1000 + idx,  # Use high IDs to avoid conflicts
                    'x_center': x_center,
                    'patterns': patterns,
                    'confidence': len(patterns) / len(quantity_patterns)
                })
        
        return columns
    
    def _classify_currency_column(
        self,
        column: PriceColumn,
        receipt_words: List[ReceiptWord]
    ) -> ColumnClassification:
        """
        Classify a currency column as unit_price, line_total, discount, etc.
        """
        # Analyze column position
        avg_x = column.x_center
        
        # Analyze values in the column
        values = [self._extract_currency_value(p) for p in column.prices]
        valid_values = [v for v in values if v is not None]
        
        if not valid_values:
            return ColumnClassification(
                column_id=column.column_id,
                column_type=ColumnType.UNKNOWN,
                confidence=0.0,
                x_position=avg_x
            )
        
        avg_value = sum(valid_values) / len(valid_values)
        max_value = max(valid_values)
        min_value = min(valid_values)
        value_range = max_value - min_value
        
        # Analyze surrounding context
        has_quantity_left = self._has_quantity_patterns_left(column, receipt_words)
        has_total_keywords = self._has_total_keywords_nearby(column, receipt_words)
        
        # Classification logic
        classification = ColumnClassification(
            column_id=column.column_id,
            column_type=ColumnType.UNKNOWN,
            confidence=0.0,
            x_position=avg_x,
            value_statistics={
                'avg_value': avg_value,
                'max_value': max_value,
                'min_value': min_value,
                'value_range': value_range
            }
        )
        
        # Rule-based classification
        if avg_x > 0.7:  # Right side of receipt
            if has_total_keywords or value_range > avg_value * 2:
                classification.column_type = ColumnType.LINE_TOTAL
                classification.confidence = 0.8
                classification.supporting_evidence.append("Right-aligned position")
                classification.supporting_evidence.append("High value variance suggests totals")
            else:
                classification.column_type = ColumnType.LINE_TOTAL
                classification.confidence = 0.6
                classification.supporting_evidence.append("Right-aligned position typical for totals")
        
        elif avg_x < 0.5 and has_quantity_left:  # Center-left with quantities
            classification.column_type = ColumnType.UNIT_PRICE
            classification.confidence = 0.8
            classification.supporting_evidence.append("Positioned after quantities")
            classification.supporting_evidence.append("Center-left alignment")
        
        elif all(v < 0 for v in valid_values):  # All negative values
            classification.column_type = ColumnType.DISCOUNT
            classification.confidence = 0.9
            classification.supporting_evidence.append("All negative values")
        
        elif avg_value < 1.0 and max_value < 10.0:  # Small values
            classification.column_type = ColumnType.TAX_AMOUNT
            classification.confidence = 0.5
            classification.supporting_evidence.append("Small values typical for tax")
        
        else:  # Default to unit price for middle columns
            classification.column_type = ColumnType.UNIT_PRICE
            classification.confidence = 0.5
            classification.supporting_evidence.append("Default classification")
        
        return classification
    
    def _detect_description_column(
        self,
        receipt_words: List[ReceiptWord],
        existing_columns: Dict[int, ColumnClassification]
    ) -> Optional[ColumnClassification]:
        """Detect the description column (usually leftmost text)."""
        # Get X positions of existing columns
        existing_x_positions = [col.x_position for col in existing_columns.values()]
        
        # Find leftmost significant text concentration
        x_groups = defaultdict(list)
        
        for word in receipt_words:
            # Skip if word is too short or numeric
            if len(word.text) < 3 or word.text.replace('.', '').replace(',', '').isdigit():
                continue
            
            x_pos = self._get_x_center(word)
            if x_pos is None:
                continue
            
            # Skip if too close to existing columns
            if any(abs(x_pos - ex_x) < self.alignment_tolerance * 2 for ex_x in existing_x_positions):
                continue
            
            # Group by X position
            found_group = False
            for group_x, group_words in x_groups.items():
                if abs(group_x - x_pos) < self.alignment_tolerance * 2:
                    group_words.append(word)
                    found_group = True
                    break
            
            if not found_group:
                x_groups[x_pos] = [word]
        
        # Find the leftmost group with sufficient words
        valid_groups = [
            (x, words) for x, words in x_groups.items()
            if len(words) >= self.min_column_items
        ]
        
        if not valid_groups:
            return None
        
        # Sort by X position and take leftmost
        valid_groups.sort(key=lambda g: g[0])
        leftmost_x, leftmost_words = valid_groups[0]
        
        # Only consider as description column if it's on the left side
        if leftmost_x > 0.5:
            return None
        
        return ColumnClassification(
            column_id=2000,  # Special ID for description column
            column_type=ColumnType.DESCRIPTION,
            confidence=0.7,
            x_position=leftmost_x,
            supporting_evidence=[
                f"Leftmost text column at x={leftmost_x:.2f}",
                f"Contains {len(leftmost_words)} text items"
            ]
        )
    
    def assemble_line_items(
        self,
        columns: Dict[int, ColumnClassification],
        receipt_words: List[ReceiptWord],
        pattern_matches: Dict[str, List[PatternMatch]]
    ) -> List[MultiColumnLineItem]:
        """
        Assemble complete line items from multiple columns.
        
        Args:
            columns: Classified columns
            receipt_words: All receipt words
            pattern_matches: All pattern matches by type
        
        Returns:
            List of assembled multi-column line items
        """
        line_items = []
        
        # Get all currency and quantity patterns
        currency_patterns = pattern_matches.get('currency', [])
        quantity_patterns = pattern_matches.get('quantity', [])
        
        # Group words by line number
        words_by_line = defaultdict(list)
        for word in receipt_words:
            if hasattr(word, 'line_id') and word.line_id is not None:
                words_by_line[word.line_id].append(word)
        
        # Get patterns by line
        patterns_by_line = self._group_patterns_by_line(
            currency_patterns + quantity_patterns
        )
        
        # Process each line that has patterns
        for line_id, line_patterns in patterns_by_line.items():
            line_words = words_by_line.get(line_id, [])
            
            if not line_words:
                continue
            
            # Try to assemble a line item from this line's data
            line_item = self._assemble_line_item_from_line(
                line_id,
                line_words,
                line_patterns,
                columns
            )
            
            if line_item:
                line_items.append(line_item)
        
        # Validate mathematical relationships
        validated_items = self._validate_line_items(line_items)
        
        self.logger.info(
            f"Assembled {len(validated_items)} line items from {len(patterns_by_line)} lines with patterns"
        )
        
        return validated_items
    
    def _group_patterns_by_line(self, patterns: List[PatternMatch]) -> Dict[int, List[PatternMatch]]:
        """Group patterns by their line number."""
        patterns_by_line = defaultdict(list)
        
        for pattern in patterns:
            if hasattr(pattern.word, 'line_id') and pattern.word.line_id is not None:
                patterns_by_line[pattern.word.line_id].append(pattern)
        
        return patterns_by_line
    
    def _assemble_line_item_from_line(
        self,
        line_id: int,
        line_words: List[ReceiptWord],
        line_patterns: List[PatternMatch],
        columns: Dict[int, ColumnClassification]
    ) -> Optional[MultiColumnLineItem]:
        """Assemble a line item from a single line's data."""
        line_item = MultiColumnLineItem(
            description="",
            description_words=[],
            line_number=line_id,
            confidence=0.0
        )
        
        # Extract values from patterns based on their column classification
        for pattern in line_patterns:
            column_id = self._find_pattern_column(pattern, columns)
            
            if column_id is None:
                continue
            
            column_class = columns.get(column_id)
            if not column_class:
                continue
            
            # Extract value based on pattern type
            if pattern.pattern_type == PatternType.QUANTITY:
                line_item.quantity = self._extract_quantity_value(pattern)
                line_item.source_columns['quantity'] = column_id
            
            elif pattern.pattern_type == PatternType.CURRENCY:
                value = self._extract_currency_value(pattern)
                
                if column_class.column_type == ColumnType.UNIT_PRICE:
                    line_item.unit_price = value
                    line_item.source_columns['unit_price'] = column_id
                elif column_class.column_type == ColumnType.LINE_TOTAL:
                    line_item.line_total = value
                    line_item.source_columns['line_total'] = column_id
                elif column_class.column_type == ColumnType.DISCOUNT:
                    line_item.discount = value
                    line_item.source_columns['discount'] = column_id
                elif column_class.column_type == ColumnType.TAX_AMOUNT:
                    line_item.tax_amount = value
                    line_item.source_columns['tax_amount'] = column_id
        
        # Extract description from non-pattern words
        description_words = self._extract_description_words(
            line_words, line_patterns, columns
        )
        
        if description_words:
            line_item.description = " ".join([w.text for w in description_words])
            line_item.description_words = description_words
        
        # Calculate confidence based on completeness
        completeness_score = self._calculate_completeness_score(line_item)
        line_item.confidence = completeness_score
        
        # Only return if we have meaningful data
        if completeness_score > 0.3 and (line_item.description or line_item.line_total):
            return line_item
        
        return None
    
    def _find_pattern_column(
        self,
        pattern: PatternMatch,
        columns: Dict[int, ColumnClassification]
    ) -> Optional[int]:
        """Find which column a pattern belongs to based on X position."""
        pattern_x = self._get_x_center(pattern.word)
        
        if pattern_x is None:
            return None
        
        # Find the closest column
        best_match = None
        min_distance = float('inf')
        
        for col_id, col_class in columns.items():
            distance = abs(pattern_x - col_class.x_position)
            
            if distance < self.alignment_tolerance * 2 and distance < min_distance:
                min_distance = distance
                best_match = col_id
        
        return best_match
    
    def _extract_description_words(
        self,
        line_words: List[ReceiptWord],
        line_patterns: List[PatternMatch],
        columns: Dict[int, ColumnClassification]
    ) -> List[ReceiptWord]:
        """Extract description words from a line."""
        # Get words that are part of patterns
        pattern_words = {p.word.word_id for p in line_patterns if hasattr(p.word, 'word_id')}
        
        # Find description column if it exists
        desc_column = None
        for col in columns.values():
            if col.column_type == ColumnType.DESCRIPTION:
                desc_column = col
                break
        
        description_words = []
        
        for word in line_words:
            # Skip if part of a pattern
            if hasattr(word, 'word_id') and word.word_id in pattern_words:
                continue
            
            # Skip pure numbers and punctuation
            if word.text.replace('.', '').replace(',', '').replace('-', '').isdigit():
                continue
            
            if len(word.text) < 2:
                continue
            
            # If we have a description column, check alignment
            if desc_column:
                word_x = self._get_x_center(word)
                if word_x and abs(word_x - desc_column.x_position) < self.alignment_tolerance * 3:
                    description_words.append(word)
            else:
                # No description column defined, take leftmost words
                word_x = self._get_x_center(word)
                if word_x and word_x < 0.6:  # Left 60% of receipt
                    description_words.append(word)
        
        # Sort by X position to maintain reading order
        description_words.sort(key=lambda w: self._get_x_center(w) or 0)
        
        return description_words
    
    def _validate_line_items(self, line_items: List[MultiColumnLineItem]) -> List[MultiColumnLineItem]:
        """Validate mathematical relationships in line items."""
        for item in line_items:
            validation_results = {}
            
            # Validate quantity × unit_price = line_total
            if item.quantity is not None and item.unit_price is not None and item.line_total is not None:
                expected_total = item.quantity * item.unit_price
                difference = abs(expected_total - item.line_total)
                
                # Handle edge cases: negative, zero, or very small totals
                if abs(item.line_total) < 0.01:  # Very small or zero total
                    validation_results['quantity_price_total'] = difference <= self.validation_threshold
                elif item.line_total < 0:  # Negative total (e.g., refunds, credits)
                    # For negative totals, use absolute value for percentage calculation
                    validation_results['quantity_price_total'] = (
                        difference <= self.validation_threshold or
                        difference / abs(item.line_total) <= 0.01  # 1% tolerance
                    )
                else:  # Normal positive total
                    validation_results['quantity_price_total'] = (
                        difference <= self.validation_threshold or
                        difference / item.line_total <= 0.01  # 1% tolerance
                    )
                
                if validation_results['quantity_price_total']:
                    # Boost confidence for validated items
                    item.confidence = max(0.8, item.confidence * 1.2)
            
            # Check if discount is applied correctly
            if item.discount is not None and item.line_total is not None and item.unit_price is not None:
                if item.quantity is not None:
                    expected_total = (item.quantity * item.unit_price) + item.discount
                    validation_results['discount_calculation'] = (
                        abs(expected_total - item.line_total) <= self.validation_threshold
                    )
            
            item.validation_status = validation_results
        
        return line_items
    
    def _calculate_completeness_score(self, line_item: MultiColumnLineItem) -> float:
        """Calculate how complete a line item is."""
        score = 0.0
        
        # Essential fields
        if line_item.description:
            score += 0.3
        if line_item.line_total is not None:
            score += 0.3
        
        # Additional fields that increase confidence
        if line_item.quantity is not None:
            score += 0.15
        if line_item.unit_price is not None:
            score += 0.15
        
        # Validation boosts
        if line_item.validation_status.get('quantity_price_total'):
            score += 0.1
        
        return min(score, 1.0)
    
    def _extract_currency_value(self, pattern: PatternMatch) -> Optional[float]:
        """Extract numeric value from currency pattern."""
        try:
            # Remove currency symbols and convert to float
            text = pattern.matched_text.replace('$', '').replace(',', '').strip()
            return float(text)
        except (ValueError, AttributeError):
            return None
    
    def _extract_quantity_value(self, pattern: PatternMatch) -> Optional[float]:
        """Extract numeric value from quantity pattern."""
        try:
            # Extract the numeric part from quantity patterns like "2x" or "3 @"
            import re
            match = re.search(r'(\d+(?:\.\d+)?)', pattern.matched_text)
            if match:
                return float(match.group(1))
        except (ValueError, AttributeError):
            pass
        
        return None
    
    def _get_x_center(self, word: ReceiptWord) -> Optional[float]:
        """Get the X center coordinate of a word."""
        if hasattr(word, 'x') and word.x is not None:
            if hasattr(word, 'width') and word.width:
                return word.x + word.width / 2
            return word.x
        
        # Try bounding box
        if hasattr(word, 'bounding_box') and word.bounding_box:
            if isinstance(word.bounding_box, dict):
                return word.bounding_box.get('x', 0) + word.bounding_box.get('width', 0) / 2
            elif isinstance(word.bounding_box, (list, tuple)) and len(word.bounding_box) >= 4:
                return word.bounding_box[0] + word.bounding_box[2] / 2
        
        return None
    
    def _has_quantity_patterns_left(self, column: PriceColumn, receipt_words: List[ReceiptWord]) -> bool:
        """Check if there are quantity patterns to the left of this column."""
        column_x = column.x_center
        
        for word in receipt_words:
            word_x = self._get_x_center(word)
            if word_x and word_x < column_x - 0.1:  # Significantly left
                # Check for quantity patterns like "2x", "3 @", etc.
                if any(char in word.text for char in ['x', 'X', '@']) and any(char.isdigit() for char in word.text):
                    return True
        
        return False
    
    def _has_total_keywords_nearby(self, column: PriceColumn, receipt_words: List[ReceiptWord]) -> bool:
        """Check if there are total-related keywords near this column."""
        total_keywords = {'total', 'subtotal', 'amount', 'due', 'balance'}
        
        # Get line IDs from column prices
        column_lines = {p.word.line_id for p in column.prices if hasattr(p.word, 'line_id')}
        
        for word in receipt_words:
            if hasattr(word, 'line_id') and word.line_id in column_lines:
                if any(keyword in word.text.lower() for keyword in total_keywords):
                    return True
        
        return False


def create_enhanced_line_item_detector() -> MultiColumnHandler:
    """Factory function to create a multicolumn handler with optimal settings."""
    return MultiColumnHandler(
        alignment_tolerance=0.02,
        horizontal_tolerance=0.05,
        min_column_items=3,
        validation_threshold=0.01
    )