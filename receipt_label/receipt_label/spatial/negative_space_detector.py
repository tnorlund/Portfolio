"""
Negative Space Detection for Enhanced Line Item Recognition

This module implements advanced whitespace and spatial gap analysis to improve
line item detection without relying on LLMs. It focuses on analyzing the
"negative space" (empty regions) between text elements to understand document
structure.

Key concepts:
- Whitespace patterns reveal document structure
- Consistent gaps indicate item boundaries
- Column channels show table-like layouts
- Section breaks separate logical groups
"""

from dataclasses import dataclass
from typing import List, Tuple, Optional, Dict, Set
import numpy as np
from collections import defaultdict

from receipt_label.models.receipt import ReceiptWord
from receipt_label.pattern_detection.base import PatternMatch, PatternType


@dataclass
class WhitespaceRegion:
    """Represents an empty region in the document"""
    x_start: float
    x_end: float
    y_start: float
    y_end: float
    width: float
    height: float
    area: float
    region_type: str  # 'vertical_gap', 'horizontal_gap', 'column_channel', 'section_break'
    
    @classmethod
    def from_coordinates(cls, x_start: float, x_end: float, y_start: float, y_end: float, region_type: str) -> 'WhitespaceRegion':
        width = x_end - x_start
        height = y_end - y_start
        area = width * height
        return cls(x_start, x_end, y_start, y_end, width, height, area, region_type)


@dataclass
class LineItemBoundary:
    """Represents the boundary of a detected line item"""
    words: List[ReceiptWord]
    y_start: float
    y_end: float
    confidence: float
    has_price: bool
    is_multi_line: bool
    indentation_level: int  # 0 = main item, 1+ = sub-items/modifiers


@dataclass
class ColumnStructure:
    """Represents detected column structure"""
    columns: List[Tuple[float, float]]  # (x_start, x_end) for each column
    column_types: List[str]  # 'description', 'quantity', 'unit_price', 'line_total'
    confidence: float


class NegativeSpaceDetector:
    """
    Analyzes whitespace patterns and spatial gaps to improve line item detection.
    
    This detector focuses on the "negative space" - the empty regions between
    text elements - to understand document structure without relying on text content.
    """
    
    def __init__(self, 
                 min_vertical_gap_ratio: float = 0.015,
                 min_horizontal_gap_ratio: float = 0.02,
                 column_channel_min_height_ratio: float = 0.3,
                 section_break_ratio: float = 0.03):
        """
        Initialize the negative space detector.
        
        Args:
            min_vertical_gap_ratio: Minimum vertical gap (as ratio of page height) to consider significant
            min_horizontal_gap_ratio: Minimum horizontal gap (as ratio of page width) to consider significant
            column_channel_min_height_ratio: Minimum height ratio for vertical column channels
            section_break_ratio: Minimum gap ratio to consider as section break
        """
        self.min_vertical_gap_ratio = min_vertical_gap_ratio
        self.min_horizontal_gap_ratio = min_horizontal_gap_ratio
        self.column_channel_min_height_ratio = column_channel_min_height_ratio
        self.section_break_ratio = section_break_ratio
    
    def detect_whitespace_regions(self, words: List[ReceiptWord]) -> List[WhitespaceRegion]:
        """
        Detect significant whitespace regions in the document.
        
        Args:
            words: List of receipt words with bounding boxes
            
        Returns:
            List of detected whitespace regions
        """
        if not words:
            return []
        
        regions = []
        
        # Get document bounds
        min_x = min(self._get_word_left(w) for w in words)
        max_x = max(self._get_word_right(w) for w in words)
        min_y = min(self._get_word_top(w) for w in words)
        max_y = max(self._get_word_bottom(w) for w in words)
        
        doc_width = max_x - min_x
        doc_height = max_y - min_y
        
        # Sort words by Y coordinate for vertical gap detection
        words_by_y = sorted(words, key=lambda w: self._get_word_top(w))
        
        # Detect vertical gaps between consecutive lines
        for i in range(len(words_by_y) - 1):
            current_bottom = self._get_word_bottom(words_by_y[i])
            next_top = self._get_word_top(words_by_y[i + 1])
            
            gap_height = next_top - current_bottom
            if gap_height > self.min_vertical_gap_ratio * doc_height:
                # Check if this is a section break (larger gap)
                region_type = 'section_break' if gap_height > self.section_break_ratio * doc_height else 'vertical_gap'
                region = WhitespaceRegion.from_coordinates(
                    min_x, max_x, current_bottom, next_top, region_type
                )
                regions.append(region)
        
        # Detect vertical column channels
        columns = self._detect_column_channels(words, doc_width, doc_height)
        regions.extend(columns)
        
        # Detect horizontal gaps within lines
        horizontal_gaps = self._detect_horizontal_gaps(words, doc_width)
        regions.extend(horizontal_gaps)
        
        return regions
    
    def detect_line_item_boundaries(self, 
                                  words: List[ReceiptWord], 
                                  whitespace_regions: List[WhitespaceRegion],
                                  pattern_matches: Optional[List[PatternMatch]] = None) -> List[LineItemBoundary]:
        """
        Detect line item boundaries using whitespace patterns.
        
        Args:
            words: List of receipt words
            whitespace_regions: Detected whitespace regions
            pattern_matches: Optional pattern matches for price detection
            
        Returns:
            List of detected line item boundaries
        """
        boundaries = []
        
        # Group words into lines based on Y-coordinate overlap
        lines = self._group_words_into_lines(words)
        
        # Sort lines by Y-coordinate
        sorted_lines = sorted(lines, key=lambda line: min(self._get_word_top(w) for w in line))
        
        # Find vertical gaps that separate items
        vertical_gaps = [r for r in whitespace_regions if r.region_type in ['vertical_gap', 'section_break']]
        vertical_gaps.sort(key=lambda g: g.y_start)
        
        # Process lines to find item boundaries
        current_item_lines = []
        
        for i, line in enumerate(sorted_lines):
            line_top = min(self._get_word_top(w) for w in line)
            line_bottom = max(self._get_word_bottom(w) for w in line)
            
            # Check if there's a significant gap before this line
            gap_before = any(gap.y_start <= line_top <= gap.y_end for gap in vertical_gaps)
            
            if gap_before and current_item_lines:
                # Create boundary for accumulated lines
                boundary = self._create_line_item_boundary(current_item_lines, pattern_matches)
                if boundary:
                    # Calculate indentation using all receipt words
                    boundary.indentation_level = self._calculate_indentation_level(current_item_lines[0], words)
                    boundaries.append(boundary)
                current_item_lines = [line]
            else:
                current_item_lines.append(line)
        
        # Don't forget the last item
        if current_item_lines:
            boundary = self._create_line_item_boundary(current_item_lines, pattern_matches)
            if boundary:
                # Calculate indentation using all receipt words
                boundary.indentation_level = self._calculate_indentation_level(current_item_lines[0], words)
                boundaries.append(boundary)
        
        return boundaries
    
    def detect_column_structure(self, words: List[ReceiptWord]) -> Optional[ColumnStructure]:
        """
        Detect table-like column structure using whitespace channels.
        
        Args:
            words: List of receipt words
            
        Returns:
            Detected column structure or None
        """
        if not words:
            return None
        
        # Get document bounds
        min_x = min(self._get_word_left(w) for w in words)
        max_x = max(self._get_word_right(w) for w in words)
        doc_width = max_x - min_x
        
        # Group words by X-coordinate ranges (more tolerant for column detection)
        x_clusters = self._cluster_by_x_coordinate(words, tolerance=0.05 * doc_width)
        
        if len(x_clusters) < 2:
            return None
        
        # Sort clusters by average X position
        sorted_clusters = sorted(x_clusters, key=lambda c: sum(self._get_word_left(w) for w in c) / len(c))
        
        # Determine column boundaries
        columns = []
        column_types = []
        
        for i, cluster in enumerate(sorted_clusters):
            cluster_left = min(self._get_word_left(w) for w in cluster)
            cluster_right = max(self._get_word_right(w) for w in cluster)
            columns.append((cluster_left, cluster_right))
            
            # Infer column type based on position and content
            if i == 0:
                column_types.append('description')
            elif i == len(sorted_clusters) - 1:
                # Last column often contains totals
                column_types.append('line_total')
            else:
                # Middle columns might be quantity or unit price
                column_types.append('unit_price' if i == len(sorted_clusters) - 2 else 'quantity')
        
        confidence = self._calculate_column_confidence(sorted_clusters)
        
        return ColumnStructure(columns=columns, column_types=column_types, confidence=confidence)
    
    def enhance_line_items_with_negative_space(self,
                                             words: List[ReceiptWord],
                                             existing_matches: List[PatternMatch]) -> List[PatternMatch]:
        """
        Enhance existing line item detection using negative space analysis.
        
        Args:
            words: List of receipt words
            existing_matches: Existing pattern matches
            
        Returns:
            Enhanced list of pattern matches
        """
        # Detect whitespace regions
        whitespace_regions = self.detect_whitespace_regions(words)
        
        # Detect line item boundaries
        boundaries = self.detect_line_item_boundaries(words, whitespace_regions, existing_matches)
        
        # Detect column structure
        column_structure = self.detect_column_structure(words)
        
        # Enhance matches with boundary information
        enhanced_matches = existing_matches.copy()
        
        for boundary in boundaries:
            # Check if this boundary represents a complete line item
            if boundary.has_price and boundary.confidence > 0.7:
                # Find description words in the boundary
                desc_words = [w for w in boundary.words if not self._is_price_word(w, existing_matches)]
                
                if desc_words:
                    # Create a new pattern match for the line item
                    # Use the first word as the primary word for the match
                    primary_word = desc_words[0]
                    matched_text = " ".join(w.text for w in desc_words)
                    
                    match = PatternMatch(
                        word=primary_word,
                        pattern_type=PatternType.PRODUCT_NAME,  # Line items are product names
                        matched_text=matched_text,
                        confidence=boundary.confidence * 0.9,  # Slightly lower confidence for inferred items
                        extracted_value=matched_text,
                        metadata={
                            "detection_method": "negative_space",
                            "is_multi_line": boundary.is_multi_line,
                            "indentation_level": boundary.indentation_level,
                            "boundary_confidence": boundary.confidence,
                            "all_words": [w.text for w in desc_words]
                        }
                    )
                    enhanced_matches.append(match)
        
        return enhanced_matches
    
    # Helper methods
    
    def _get_word_left(self, word: ReceiptWord) -> float:
        """Get left edge of word bounding box"""
        return word.bounding_box.get('x', word.bounding_box.get('left', 0))
    
    def _get_word_right(self, word: ReceiptWord) -> float:
        """Get right edge of word bounding box"""
        left = self._get_word_left(word)
        width = word.bounding_box.get('width', 0)
        if width:
            return left + width
        return word.bounding_box.get('right', left)
    
    def _get_word_top(self, word: ReceiptWord) -> float:
        """Get top edge of word bounding box"""
        return word.bounding_box.get('y', word.bounding_box.get('top', 0))
    
    def _get_word_bottom(self, word: ReceiptWord) -> float:
        """Get bottom edge of word bounding box"""
        top = self._get_word_top(word)
        height = word.bounding_box.get('height', 0)
        if height:
            return top + height
        return word.bounding_box.get('bottom', top)
    
    def _detect_column_channels(self, words: List[ReceiptWord], doc_width: float, doc_height: float) -> List[WhitespaceRegion]:
        """Detect vertical whitespace channels that might indicate columns"""
        channels = []
        
        # Create a grid to track word coverage
        grid_resolution = 50  # Number of horizontal bins
        x_coverage = [0] * grid_resolution
        
        min_x = min(self._get_word_left(w) for w in words)
        max_x = max(self._get_word_right(w) for w in words)
        
        # Mark grid cells covered by words
        for word in words:
            left_idx = int((self._get_word_left(word) - min_x) / doc_width * grid_resolution)
            right_idx = int((self._get_word_right(word) - min_x) / doc_width * grid_resolution)
            
            for idx in range(max(0, left_idx), min(grid_resolution, right_idx + 1)):
                x_coverage[idx] += 1
        
        # Find vertical channels (columns with no coverage)
        channel_start = None
        for i in range(grid_resolution):
            if x_coverage[i] == 0:
                if channel_start is None:
                    channel_start = i
            else:
                if channel_start is not None:
                    # Found end of channel
                    channel_width = (i - channel_start) / grid_resolution
                    if channel_width > self.min_horizontal_gap_ratio:
                        x_start = min_x + (channel_start / grid_resolution) * doc_width
                        x_end = min_x + (i / grid_resolution) * doc_width
                        
                        channel = WhitespaceRegion.from_coordinates(
                            x_start, x_end,
                            min(self._get_word_top(w) for w in words),
                            max(self._get_word_bottom(w) for w in words),
                            'column_channel'
                        )
                        channels.append(channel)
                    channel_start = None
        
        return channels
    
    def _detect_horizontal_gaps(self, words: List[ReceiptWord], doc_width: float) -> List[WhitespaceRegion]:
        """Detect horizontal gaps within lines"""
        gaps = []
        
        # Group words by line
        lines = self._group_words_into_lines(words)
        
        for line in lines:
            # Sort words in line by X coordinate
            sorted_words = sorted(line, key=lambda w: self._get_word_left(w))
            
            # Find gaps between consecutive words
            for i in range(len(sorted_words) - 1):
                current_right = self._get_word_right(sorted_words[i])
                next_left = self._get_word_left(sorted_words[i + 1])
                
                gap_width = next_left - current_right
                if gap_width > self.min_horizontal_gap_ratio * doc_width:
                    gap = WhitespaceRegion.from_coordinates(
                        current_right, next_left,
                        min(self._get_word_top(w) for w in [sorted_words[i], sorted_words[i + 1]]),
                        max(self._get_word_bottom(w) for w in [sorted_words[i], sorted_words[i + 1]]),
                        'horizontal_gap'
                    )
                    gaps.append(gap)
        
        return gaps
    
    def _group_words_into_lines(self, words: List[ReceiptWord]) -> List[List[ReceiptWord]]:
        """Group words into lines based on Y-coordinate overlap"""
        if not words:
            return []
        
        # Sort words by Y coordinate
        sorted_words = sorted(words, key=lambda w: self._get_word_top(w))
        
        lines = []
        current_line = [sorted_words[0]]
        
        for word in sorted_words[1:]:
            # Check if word overlaps vertically with current line
            line_top = min(self._get_word_top(w) for w in current_line)
            line_bottom = max(self._get_word_bottom(w) for w in current_line)
            
            word_top = self._get_word_top(word)
            word_bottom = self._get_word_bottom(word)
            
            # Check for vertical overlap
            if word_top < line_bottom and word_bottom > line_top:
                current_line.append(word)
            else:
                lines.append(current_line)
                current_line = [word]
        
        if current_line:
            lines.append(current_line)
        
        return lines
    
    def _cluster_by_x_coordinate(self, words: List[ReceiptWord], tolerance: float) -> List[List[ReceiptWord]]:
        """Cluster words by similar X coordinates"""
        clusters = []
        used_indices = set()
        
        for i, word in enumerate(words):
            if i in used_indices:
                continue
            
            cluster = [word]
            used_indices.add(i)
            word_x = self._get_word_left(word)
            
            # Find all words with similar X coordinate
            for j, other in enumerate(words):
                if j in used_indices:
                    continue
                
                other_x = self._get_word_left(other)
                if abs(word_x - other_x) <= tolerance:
                    cluster.append(other)
                    used_indices.add(j)
            
            clusters.append(cluster)
        
        return clusters
    
    def _create_line_item_boundary(self, lines: List[List[ReceiptWord]], pattern_matches: Optional[List[PatternMatch]]) -> Optional[LineItemBoundary]:
        """Create a line item boundary from grouped lines"""
        if not lines:
            return None
        
        all_words = [word for line in lines for word in line]
        
        # Calculate boundary coordinates
        y_start = min(self._get_word_top(w) for w in all_words)
        y_end = max(self._get_word_bottom(w) for w in all_words)
        
        # Check if any words are prices
        has_price = False
        if pattern_matches:
            has_price = any(self._is_price_word(w, pattern_matches) for w in all_words)
        
        # Determine if multi-line
        is_multi_line = len(lines) > 1
        
        # Calculate indentation level (need all words from receipt for proper min_x calculation)
        # This will be passed from the calling method
        indentation_level = 0
        
        # Calculate confidence based on structure
        confidence = 0.8
        if has_price:
            confidence += 0.1
        if len(lines) == 1:
            confidence += 0.05
        
        return LineItemBoundary(
            words=all_words,
            y_start=y_start,
            y_end=y_end,
            confidence=min(1.0, confidence),
            has_price=has_price,
            is_multi_line=is_multi_line,
            indentation_level=indentation_level
        )
    
    def _calculate_indentation_level(self, first_line: List[ReceiptWord], all_words: List[ReceiptWord]) -> int:
        """Calculate indentation level based on X position"""
        if not first_line or not all_words:
            return 0
        
        min_x = min(self._get_word_left(w) for w in all_words)
        first_line_x = min(self._get_word_left(w) for w in first_line)
        
        doc_width = max(self._get_word_right(w) for w in all_words) - min_x
        indent_ratio = (first_line_x - min_x) / doc_width if doc_width > 0 else 0
        
        # Convert ratio to discrete levels
        if indent_ratio < 0.05:
            return 0
        elif indent_ratio < 0.1:
            return 1
        else:
            return 2
    
    def _is_price_word(self, word: ReceiptWord, pattern_matches: List[PatternMatch]) -> bool:
        """Check if a word is identified as a price"""
        if not pattern_matches:
            return False
        
        # Check if this word is part of any currency pattern match
        for match in pattern_matches:
            if match.pattern_type == PatternType.CURRENCY and match.word.text == word.text:
                return True
        return False
    
    def _calculate_column_confidence(self, clusters: List[List[ReceiptWord]]) -> float:
        """Calculate confidence in column structure detection"""
        if len(clusters) < 2:
            return 0.0
        
        # Check vertical alignment consistency within clusters
        alignment_scores = []
        
        for cluster in clusters:
            if len(cluster) < 2:
                continue
            
            x_positions = [self._get_word_left(w) for w in cluster]
            x_variance = np.var(x_positions) if len(x_positions) > 1 else 0
            
            # Lower variance = better alignment
            alignment_score = 1.0 / (1.0 + x_variance)
            alignment_scores.append(alignment_score)
        
        if not alignment_scores:
            return 0.5
        
        return min(1.0, np.mean(alignment_scores))
    
    def _calculate_bounding_box(self, words: List[ReceiptWord]) -> Dict[str, float]:
        """Calculate bounding box for a group of words"""
        if not words:
            return {}
        
        left = min(self._get_word_left(w) for w in words)
        right = max(self._get_word_right(w) for w in words)
        top = min(self._get_word_top(w) for w in words)
        bottom = max(self._get_word_bottom(w) for w in words)
        
        return {
            'x': left,
            'y': top,
            'width': right - left,
            'height': bottom - top
        }