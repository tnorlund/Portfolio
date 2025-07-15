"""
Geometric utilities for spatial line item detection.

This module implements the pattern-first spatial analysis approach for detecting
line items using geometric relationships between words and receipt layout structure.
"""

import logging
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from receipt_dynamo.entities.receipt_word import ReceiptWord

logger = logging.getLogger(__name__)


@dataclass
class SpatialColumn:
    """Represents a detected column in receipt layout."""
    x_position: float
    width: float
    column_type: str  # 'description', 'quantity', 'unit_price', 'line_total'
    word_count: int
    confidence: float


@dataclass
class SpatialRow:
    """Represents a row of spatially aligned words."""
    y_position: float
    height: float
    words: List[ReceiptWord]
    columns: Dict[str, ReceiptWord]  # column_type -> word


class SpatialWord:
    """Enhanced ReceiptWord with spatial analysis capabilities."""
    
    def __init__(self, word: ReceiptWord):
        self.word = word
        self._cached_centroid = None
    
    @property
    def centroid(self) -> Tuple[float, float]:
        """Get cached centroid coordinates."""
        if self._cached_centroid is None:
            self._cached_centroid = self.word.calculate_centroid()
        return self._cached_centroid
    
    @property
    def x(self) -> float:
        """X-coordinate of word center."""
        return self.centroid[0]
    
    @property
    def y(self) -> float:
        """Y-coordinate of word center."""
        return self.centroid[1]
    
    def is_on_same_line_as(self, other: 'SpatialWord', tolerance: float = 5.0) -> bool:
        """Check if two words are on the same horizontal line using y-coordinate tolerance."""
        return abs(self.y - other.y) <= tolerance
    
    def get_horizontal_distance_to(self, other: 'SpatialWord') -> float:
        """Get horizontal distance between word centers."""
        return abs(self.x - other.x)
    
    def is_left_aligned_with(self, other_words: List['SpatialWord'], tolerance: float = 10.0) -> bool:
        """Check if word is left-aligned with a group of words."""
        if not other_words:
            return False
        
        # Get leftmost x-coordinate in the group
        min_x = min(w.word.bounding_box["x"] for w in other_words)
        word_x = self.word.bounding_box["x"]
        
        return abs(word_x - min_x) <= tolerance
    
    def is_right_aligned_with(self, other_words: List['SpatialWord'], tolerance: float = 10.0) -> bool:
        """Check if word is right-aligned with a group of words."""
        if not other_words:
            return False
        
        # Get rightmost x-coordinate in the group (x + width)
        max_x = max(w.word.bounding_box["x"] + w.word.bounding_box["width"] for w in other_words)
        word_max_x = self.word.bounding_box["x"] + self.word.bounding_box["width"]
        
        return abs(word_max_x - max_x) <= tolerance
    
    def get_relative_position_on_line(self, all_line_words: List['SpatialWord']) -> float:
        """Return position as percentage (0.0 = leftmost, 1.0 = rightmost)."""
        if not all_line_words:
            return 0.5
        
        x_positions = [w.x for w in all_line_words]
        min_x, max_x = min(x_positions), max(x_positions)
        
        if max_x == min_x:
            return 0.5
        
        return (self.x - min_x) / (max_x - min_x)
    
    def is_currency_word(self) -> bool:
        """Check if word contains currency patterns."""
        # Reuse existing currency patterns from pattern detection
        currency_pattern = re.compile(r'\$?\d{1,3}(?:,\d{3})*(?:\.\d{2})?')
        return bool(currency_pattern.search(self.word.text))
    
    def is_quantity_word(self) -> bool:
        """Check if word contains quantity patterns."""
        # Common quantity patterns
        quantity_patterns = [
            r'^\d+([.,]\d+)?$',  # Simple numbers
            r'^\d+[xX]$',        # "2x" format
            r'^\d+\s*(lb|kg|oz|g|ea|each)$',  # With units
        ]
        
        for pattern in quantity_patterns:
            if re.match(pattern, self.word.text, re.IGNORECASE):
                return True
        return False


class SpatialLine:
    """Spatial line created from grouped words with analysis capabilities."""
    
    def __init__(self, words: List[ReceiptWord]):
        self.spatial_words = [SpatialWord(word) for word in words]
        self._words_by_x = sorted(self.spatial_words, key=lambda w: w.x)
    
    def get_leftmost_words(self, count: int = 3) -> List[SpatialWord]:
        """Get the leftmost N words on this line."""
        return self._words_by_x[:count]
    
    def get_rightmost_words(self, count: int = 3) -> List[SpatialWord]:
        """Get the rightmost N words on this line."""
        return self._words_by_x[-count:]
    
    def has_currency_pattern(self) -> bool:
        """Check if line contains currency amounts."""
        return any(word.is_currency_word() for word in self.spatial_words)
    
    def get_line_width(self) -> float:
        """Calculate the total width of content on this line."""
        if not self.spatial_words:
            return 0.0
        
        leftmost = min(word.word.bounding_box["x"] for word in self.spatial_words)
        rightmost = max(
            word.word.bounding_box["x"] + word.word.bounding_box["width"] 
            for word in self.spatial_words
        )
        
        return rightmost - leftmost
    
    def split_by_alignment(self) -> Dict[str, List[SpatialWord]]:
        """Split line words into left, center, right alignment groups."""
        if not self.spatial_words:
            return {"left": [], "center": [], "right": []}
        
        # Calculate position thresholds
        line_width = self.get_line_width()
        left_threshold = line_width * 0.33
        right_threshold = line_width * 0.67
        
        leftmost_x = min(word.word.bounding_box["x"] for word in self.spatial_words)
        
        groups = {"left": [], "center": [], "right": []}
        
        for word in self.spatial_words:
            relative_x = word.word.bounding_box["x"] - leftmost_x
            
            if relative_x <= left_threshold:
                groups["left"].append(word)
            elif relative_x >= right_threshold:
                groups["right"].append(word)
            else:
                groups["center"].append(word)
        
        return groups
    
    def get_price_words(self) -> List[SpatialWord]:
        """Get words that match currency patterns on this line."""
        return [word for word in self.spatial_words if word.is_currency_word()]
    
    def get_description_words(self, exclude_prices: bool = True) -> List[SpatialWord]:
        """Get non-price words, typically product descriptions."""
        if not exclude_prices:
            return self.spatial_words
        
        return [word for word in self.spatial_words if not word.is_currency_word()]


class RowGrouper:
    """Groups words into rows using spatial relationships."""
    
    def __init__(self, y_tolerance: float = 5.0):
        self.y_tolerance = y_tolerance
    
    def group_words_into_rows(self, words: List[ReceiptWord]) -> List[SpatialRow]:
        """Group words into rows based on y-coordinate proximity."""
        if not words:
            return []
        
        spatial_words = [SpatialWord(word) for word in words]
        
        # Sort by y-coordinate
        spatial_words.sort(key=lambda w: w.y)
        
        rows = []
        current_row_words = [spatial_words[0]]
        current_y = spatial_words[0].y
        
        for word in spatial_words[1:]:
            if abs(word.y - current_y) <= self.y_tolerance:
                # Same row
                current_row_words.append(word)
            else:
                # New row
                rows.append(self._create_spatial_row(current_row_words))
                current_row_words = [word]
                current_y = word.y
        
        # Add final row
        if current_row_words:
            rows.append(self._create_spatial_row(current_row_words))
        
        return rows
    
    def _create_spatial_row(self, spatial_words: List[SpatialWord]) -> SpatialRow:
        """Create a SpatialRow from a list of words."""
        if not spatial_words:
            return SpatialRow(0, 0, [], {})
        
        # Sort words by x-coordinate for proper left-to-right order
        sorted_words = sorted(spatial_words, key=lambda w: w.x)
        
        # Calculate row bounds
        y_positions = [w.y for w in spatial_words]
        heights = [w.word.bounding_box["height"] for w in spatial_words]
        
        avg_y = sum(y_positions) / len(y_positions)
        max_height = max(heights)
        
        return SpatialRow(
            y_position=avg_y,
            height=max_height,
            words=[w.word for w in sorted_words],
            columns={}  # Will be populated by ColumnDetector
        )


class ColumnDetector:
    """Detects column structure in receipt layout."""
    
    def __init__(self, x_tolerance: float = 20.0):
        self.x_tolerance = x_tolerance
    
    def detect_columns(self, rows: List[SpatialRow]) -> List[SpatialColumn]:
        """Detect column structure from spatial rows."""
        if not rows:
            return []
        
        # Collect all x-positions
        x_positions = []
        for row in rows:
            for word in row.words:
                spatial_word = SpatialWord(word)
                x_positions.append(spatial_word.x)
        
        if not x_positions:
            return []
        
        # Cluster x-positions to find columns
        column_centers = self._cluster_x_positions(x_positions)
        
        # Analyze each column
        columns = []
        for i, center_x in enumerate(column_centers):
            column = self._analyze_column(center_x, rows)
            if column:
                columns.append(column)
        
        return columns
    
    def _cluster_x_positions(self, x_positions: List[float]) -> List[float]:
        """Simple clustering of x-positions to find column centers."""
        if not x_positions:
            return []
        
        x_positions = sorted(x_positions)
        clusters = []
        current_cluster = [x_positions[0]]
        
        for x in x_positions[1:]:
            if x - current_cluster[-1] <= self.x_tolerance:
                current_cluster.append(x)
            else:
                # Finalize current cluster
                clusters.append(sum(current_cluster) / len(current_cluster))
                current_cluster = [x]
        
        # Add final cluster
        if current_cluster:
            clusters.append(sum(current_cluster) / len(current_cluster))
        
        return clusters
    
    def _analyze_column(self, center_x: float, rows: List[SpatialRow]) -> Optional[SpatialColumn]:
        """Analyze a column to determine its type and characteristics."""
        column_words = []
        
        # Collect words in this column
        for row in rows:
            for word in row.words:
                spatial_word = SpatialWord(word)
                if abs(spatial_word.x - center_x) <= self.x_tolerance:
                    column_words.append(spatial_word)
        
        if not column_words:
            return None
        
        # Determine column type
        column_type = self._determine_column_type(column_words)
        
        # Calculate column properties
        x_positions = [w.x for w in column_words]
        widths = [w.word.bounding_box["width"] for w in column_words]
        
        return SpatialColumn(
            x_position=center_x,
            width=max(widths) if widths else 0,
            column_type=column_type,
            word_count=len(column_words),
            confidence=self._calculate_column_confidence(column_words, column_type)
        )
    
    def _determine_column_type(self, words: List[SpatialWord]) -> str:
        """Determine the type of a column based on its contents."""
        currency_count = sum(1 for w in words if w.is_currency_word())
        quantity_count = sum(1 for w in words if w.is_quantity_word())
        total_count = len(words)
        
        if total_count == 0:
            return "unknown"
        
        # High percentage of currency = price column
        if currency_count / total_count > 0.7:
            # Determine if it's unit price or line total based on position
            avg_position = sum(w.get_relative_position_on_line([]) for w in words) / len(words)
            return "line_total" if avg_position > 0.7 else "unit_price"
        
        # High percentage of quantities = quantity column
        if quantity_count / total_count > 0.6:
            return "quantity"
        
        # Default to description for text-heavy columns
        return "description"
    
    def _calculate_column_confidence(self, words: List[SpatialWord], column_type: str) -> float:
        """Calculate confidence score for column type classification."""
        if not words:
            return 0.0
        
        if column_type == "description":
            # High confidence for text columns
            return 0.8
        elif column_type in ["line_total", "unit_price"]:
            # Confidence based on currency pattern consistency
            currency_ratio = sum(1 for w in words if w.is_currency_word()) / len(words)
            return min(0.9, currency_ratio + 0.3)
        elif column_type == "quantity":
            # Confidence based on quantity pattern consistency
            quantity_ratio = sum(1 for w in words if w.is_quantity_word()) / len(words)
            return min(0.8, quantity_ratio + 0.2)
        
        return 0.5


class LineItemSpatialDetector:
    """Main spatial detector for line items using pattern-first approach."""
    
    def __init__(self, 
                 y_tolerance: float = 5.0,
                 x_tolerance: float = 20.0,
                 min_confidence: float = 0.3):
        self.row_grouper = RowGrouper(y_tolerance)
        self.column_detector = ColumnDetector(x_tolerance)
        self.min_confidence = min_confidence
    
    def detect_spatial_structure(self, words: List[ReceiptWord]) -> Dict:
        """Detect the spatial structure of the receipt for line item analysis."""
        logger.info(f"Analyzing spatial structure of {len(words)} words")
        
        # Step 1: Group words into rows
        rows = self.row_grouper.group_words_into_rows(words)
        logger.info(f"Detected {len(rows)} spatial rows")
        
        # Step 2: Detect column structure
        columns = self.column_detector.detect_columns(rows)
        logger.info(f"Detected {len(columns)} columns: {[c.column_type for c in columns]}")
        
        # Step 3: Apply spatial heuristics from the other model's recommendations
        enhanced_rows = self._apply_spatial_heuristics(rows, columns)
        
        return {
            "rows": enhanced_rows,
            "columns": columns,
            "metadata": {
                "total_words": len(words),
                "row_count": len(rows),
                "column_count": len(columns),
                "column_types": [c.column_type for c in columns],
                "avg_confidence": sum(c.confidence for c in columns) / len(columns) if columns else 0
            }
        }
    
    def _apply_spatial_heuristics(self, rows: List[SpatialRow], columns: List[SpatialColumn]) -> List[SpatialRow]:
        """Apply spatial heuristics recommended by the other model."""
        enhanced_rows = []
        
        for row in rows:
            # Apply heuristics:
            # 1. Row alignment (already done by RowGrouper)
            # 2. Left-vs-right field typing
            # 3. Horizontal whitespace gate
            # 4. Regular-expression + lexical dictionary
            # 5. Duplicate detection suppression
            # 6. Row subtotals / "ADD-ON" pattern filter
            
            enhanced_row = self._enhance_row_with_heuristics(row, columns)
            enhanced_rows.append(enhanced_row)
        
        return enhanced_rows
    
    def _enhance_row_with_heuristics(self, row: SpatialRow, columns: List[SpatialColumn]) -> SpatialRow:
        """Apply spatial heuristics to enhance a single row."""
        # Implementation of the specific heuristics from the other model
        # This is where we'll implement the confidence boosting logic
        
        # For now, return the row as-is
        # TODO: Implement specific heuristics
        return row