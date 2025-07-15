"""
Vertical alignment detector for identifying price columns in receipts.

This module identifies vertically-aligned price columns by clustering currency values
with similar X-coordinates. This leverages the common receipt pattern where prices
form a right-aligned column.
"""

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple, Any
from collections import defaultdict

from receipt_dynamo.entities.receipt_word import ReceiptWord
from receipt_label.pattern_detection.base import PatternMatch, PatternType

logger = logging.getLogger(__name__)


@dataclass
class FontMetrics:
    """Font size and style metrics for receipt analysis."""
    avg_height: float
    height_variance: float
    is_larger_than_normal: bool
    is_smaller_than_normal: bool
    confidence: float


@dataclass
class PriceColumn:
    """A column of vertically-aligned prices."""
    column_id: int
    x_center: float  # Average X coordinate of the column
    x_min: float     # Leftmost X coordinate
    x_max: float     # Rightmost X coordinate
    prices: List[PatternMatch]  # Currency patterns in this column
    confidence: float  # Confidence this is a real price column
    y_span: Optional[float] = None  # Y-coordinate span of the column (for debugging)
    x_alignment_tightness: Optional[float] = None  # How tightly aligned are the X-coordinates
    font_consistency: Optional[FontMetrics] = None  # Font size analysis for the column


@dataclass
class AlignedLineItem:
    """A line item with product and price from aligned column."""
    product_text: str
    product_words: List[ReceiptWord]
    price: PatternMatch
    product_line: int
    price_line: int
    line_distance: int
    alignment_confidence: float
    column_id: int
    has_indented_description: bool = False
    description_lines: List[str] = None


class VerticalAlignmentDetector:
    """Detects price columns and matches products using vertical alignment."""
    
    def __init__(self, alignment_tolerance: float = 0.02, use_enhanced_clustering: bool = True):
        """
        Initialize detector.
        
        Args:
            alignment_tolerance: Maximum X-coordinate difference for alignment (as fraction of page width)
            use_enhanced_clustering: Use Phase 2 enhanced clustering with font analysis and tighter alignment
        """
        self.alignment_tolerance = alignment_tolerance
        self.use_enhanced_clustering = use_enhanced_clustering
        self.logger = logger
        
    def detect_price_columns(self, currency_patterns: List[PatternMatch]) -> List[PriceColumn]:
        """
        Detect vertically-aligned price columns.
        
        Phase 2 Enhancement: Multi-column layout detection
        
        Args:
            currency_patterns: Currency patterns found by pattern detection
            
        Returns:
            List of detected price columns
        """
        if not currency_patterns:
            return []
            
        # Extract position data for all patterns
        pattern_positions = []
        for pattern in currency_patterns:
            word = pattern.word
            
            # Get all coordinate data to determine the actual left and right edges
            # (The naming in the data seems inconsistent - "left" and "right" may be swapped)
            x_coords = []
            y_coords = []
            
            # Collect all X and Y coordinates from corners
            if hasattr(word, 'bottom_right') and word.bottom_right:
                br = word.bottom_right
                if isinstance(br, dict):
                    x_coords.append(br['x'])
                    y_coords.append(br['y'])
                else:
                    x_coords.append(br[0])
                    y_coords.append(br[1])
                    
            if hasattr(word, 'bottom_left') and word.bottom_left:
                bl = word.bottom_left
                if isinstance(bl, dict):
                    x_coords.append(bl['x'])
                    y_coords.append(bl['y'])
                else:
                    x_coords.append(bl[0])
                    y_coords.append(bl[1])
                    
            if hasattr(word, 'top_right') and word.top_right:
                tr = word.top_right
                if isinstance(tr, dict):
                    x_coords.append(tr['x'])
                    y_coords.append(tr['y'])
                else:
                    x_coords.append(tr[0])
                    y_coords.append(tr[1])
                    
            if hasattr(word, 'top_left') and word.top_left:
                tl = word.top_left
                if isinstance(tl, dict):
                    x_coords.append(tl['x'])
                    y_coords.append(tl['y'])
                else:
                    x_coords.append(tl[0])
                    y_coords.append(tl[1])
            
            # Fall back to bounding box if corners not available
            if not x_coords and hasattr(word, 'bounding_box') and word.bounding_box:
                if isinstance(word.bounding_box, dict):
                    bb = word.bounding_box
                    if 'x' in bb and 'width' in bb:
                        x_coords.extend([bb['x'], bb['x'] + bb['width']])
                    if 'y' in bb and 'height' in bb:
                        y_coords.extend([bb['y'], bb['y'] + bb['height']])
                else:
                    # List format: [left, top, width, height]
                    bb = word.bounding_box
                    x_coords.extend([bb[0], bb[0] + bb[2]])
                    y_coords.extend([bb[1], bb[1] + bb[3]])
            
            if not x_coords:
                self.logger.warning(f"Word {word.text} has no position data")
                continue
                
            # Use actual min/max coordinates regardless of naming
            x_left = min(x_coords)
            x_right = max(x_coords)
            y_pos = sum(y_coords) / len(y_coords)  # Average Y position
                
            pattern_positions.append({
                'pattern': pattern,
                'x_left': x_left,
                'x_right': x_right,
                'y_pos': y_pos,
                'line_id': word.line_id
            })
        
        # Step 1: Enhanced X-coordinate clustering with Phase 2 improvements
        if self.use_enhanced_clustering:
            x_groups = self._enhanced_x_coordinate_clustering(pattern_positions)
        else:
            # Legacy clustering for backward compatibility
            x_groups = defaultdict(list)
            for pos_data in pattern_positions:
                x_right = pos_data['x_right']
                
                # Find existing group with similar X coordinate
                found_group = False
                for group_x, group_data in x_groups.items():
                    if abs(group_x - x_right) < self.alignment_tolerance:
                        group_data.append(pos_data)
                        found_group = True
                        break
                        
                if not found_group:
                    x_groups[x_right] = [pos_data]
        
        # Step 2: For each X-group, perform Y-coordinate clustering
        price_columns = []
        for group_x, group_data in x_groups.items():
            if len(group_data) < 2:  # Need at least 2 prices
                continue
                
            # Apply Y-coordinate clustering to filter out distant values
            if self.use_enhanced_clustering:
                y_clustered_groups = self._enhanced_y_coordinate_clustering(group_data)
            else:
                y_clustered_groups = self._cluster_by_y_coordinate(group_data)
            
            # Create price columns from Y-clusters that have sufficient density
            for cluster in y_clustered_groups:
                if len(cluster) >= 2:  # Each Y-cluster needs at least 2 prices
                    # Enhanced column creation with Phase 2 features
                    if self.use_enhanced_clustering:
                        column = self._create_enhanced_price_column(cluster, len(price_columns), group_x)
                    else:
                        column = self._create_price_column_from_cluster(cluster, len(price_columns))
                    price_columns.append(column)
        
        # Phase 2: Detect multi-column layouts
        if self.use_enhanced_clustering and len(price_columns) >= 2:
            price_columns = self._detect_multi_column_layout(price_columns)
        
        # Sort columns by confidence
        return sorted(price_columns, key=lambda c: c.confidence, reverse=True)
    
    def _cluster_by_y_coordinate(self, position_data: List[Dict]) -> List[List[Dict]]:
        """
        Cluster position data by Y-coordinate proximity.
        
        Args:
            position_data: List of position dictionaries
            
        Returns:
            List of clusters, where each cluster is a list of nearby positions
        """
        if len(position_data) <= 1:
            return [position_data]
        
        # For price columns, we want to be more permissive with Y-spacing
        # The key insight: if X-coordinates are aligned, the items are likely related
        # even if they're somewhat far apart in Y (e.g., item prices and totals)
        
        # Simple approach: if we have ≤3 items in X-group, keep them all as one cluster
        # This handles the common case of 2-3 aligned prices
        if len(position_data) <= 3:
            self.logger.info(f"Y-clustering: Small X-group ({len(position_data)} items), keeping as single cluster")
            return [position_data]
        
        # For larger groups, use more sophisticated clustering
        y_values = [d['y_pos'] for d in position_data]
        y_range = max(y_values) - min(y_values)
        
        # Use line-based clustering: group items that are within reasonable line distance
        # Typical receipt has ~50-100 lines, so items within 15-20 line gap could be related
        max_line_gap = 20
        
        # Sort by line ID for line-based clustering
        sorted_data = sorted(position_data, key=lambda x: x['line_id'])
        
        clusters = []
        current_cluster = [sorted_data[0]]
        
        self.logger.info(f"Y-clustering: {len(sorted_data)} items in X-group, using line-based clustering (max gap: {max_line_gap})")
        
        for i in range(1, len(sorted_data)):
            current_line = sorted_data[i]['line_id']
            previous_line = sorted_data[i-1]['line_id']
            line_gap = current_line - previous_line
            
            self.logger.info(f"Y-clustering step {i}: line {current_line} vs prev line {previous_line}, gap={line_gap}")
            
            # If lines are reasonably close, keep in same cluster
            if line_gap <= max_line_gap:
                current_cluster.append(sorted_data[i])
                self.logger.info(f"  → Added to current cluster (size now {len(current_cluster)})")
            else:
                # Line gap too large, start new cluster
                if len(current_cluster) >= 1:
                    clusters.append(current_cluster)
                    self.logger.info(f"  → Finished cluster with {len(current_cluster)} items")
                current_cluster = [sorted_data[i]]
                self.logger.info(f"  → Started new cluster (large line gap)")
        
        # Don't forget the last cluster
        if current_cluster:
            clusters.append(current_cluster)
            self.logger.info(f"Final cluster: {len(current_cluster)} items")
        
        # Filter to keep only clusters with sufficient items
        filtered_clusters = [cluster for cluster in clusters if len(cluster) >= 2]
        
        self.logger.info(f"Y-clustering result: {len(filtered_clusters)} clusters after filtering")
        
        return filtered_clusters
    
    def _enhanced_y_coordinate_clustering(self, position_data: List[Dict]) -> List[List[Dict]]:
        """
        Enhanced Y-coordinate clustering with inter-line spacing analysis.
        
        Phase 2 Features:
        - Detects section breaks via large gaps
        - Analyzes line spacing consistency
        - Uses font size for grouping hints
        - Handles multi-line items with indentation
        
        Args:
            position_data: List of position dictionaries
            
        Returns:
            List of clusters with enhanced section awareness
        """
        if len(position_data) <= 1:
            return [position_data]
        
        self.logger.info(f"Enhanced Y-clustering: Processing {len(position_data)} items")
        
        # Step 1: Analyze line spacing patterns
        spacing_analysis = self._analyze_line_spacing(position_data)
        
        # Step 2: Detect section breaks using spacing gaps
        section_breaks = self._detect_section_breaks(position_data, spacing_analysis)
        
        # Step 3: Group items by sections
        sections = self._group_by_sections(position_data, section_breaks)
        
        # Step 4: Within each section, do fine-grained clustering
        final_clusters = []
        for section_id, section_items in enumerate(sections):
            if len(section_items) >= 2:
                # Use different clustering strategies based on section characteristics
                section_clusters = self._cluster_within_section(section_items, section_id)
                final_clusters.extend(section_clusters)
            elif len(section_items) == 1:
                # Single item sections can still be valid (like standalone totals)
                font_metrics = section_items[0].get('font_metrics')
                if font_metrics and (font_metrics.is_larger_than_normal or font_metrics.confidence > 0.7):
                    final_clusters.append(section_items)
                    self.logger.info(f"Enhanced Y-clustering: Kept single-item section due to distinctive font")
        
        filtered_clusters = [cluster for cluster in final_clusters if len(cluster) >= 2 or 
                           (len(cluster) == 1 and cluster[0].get('font_metrics', FontMetrics(0,0,False,False,0)).is_larger_than_normal)]
        
        self.logger.info(f"Enhanced Y-clustering: {len(sections)} sections → {len(filtered_clusters)} final clusters")
        return filtered_clusters
    
    def _analyze_line_spacing(self, position_data: List[Dict]) -> Dict:
        """
        Analyze line spacing patterns to understand receipt structure.
        
        Args:
            position_data: Position data sorted by line_id
            
        Returns:
            Dictionary with spacing analysis results
        """
        if len(position_data) < 2:
            return {'avg_spacing': 1.0, 'spacing_variance': 0.0, 'large_gaps': []}
        
        # Sort by line ID to analyze sequential spacing
        sorted_data = sorted(position_data, key=lambda x: x['line_id'])
        
        # Calculate line-to-line gaps
        line_gaps = []
        for i in range(1, len(sorted_data)):
            current_line = sorted_data[i]['line_id']
            previous_line = sorted_data[i-1]['line_id']
            gap = current_line - previous_line
            line_gaps.append(gap)
        
        # Statistical analysis of gaps
        avg_spacing = sum(line_gaps) / len(line_gaps)
        spacing_variance = sum((gap - avg_spacing) ** 2 for gap in line_gaps) / len(line_gaps)
        
        # Identify unusually large gaps (potential section breaks)
        # A "large gap" is significantly larger than the average
        large_gap_threshold = avg_spacing + 2 * (spacing_variance ** 0.5)  # 2 standard deviations
        large_gap_threshold = max(large_gap_threshold, avg_spacing * 2)  # At least 2x average
        
        large_gaps = []
        for i, gap in enumerate(line_gaps):
            if gap > large_gap_threshold:
                large_gaps.append({
                    'position': i + 1,  # Position in sorted list
                    'line_before': sorted_data[i]['line_id'],
                    'line_after': sorted_data[i + 1]['line_id'],
                    'gap_size': gap,
                    'gap_ratio': gap / avg_spacing
                })
        
        self.logger.info(f"Line spacing analysis: avg={avg_spacing:.1f}, variance={spacing_variance:.1f}, "
                       f"large_gaps={len(large_gaps)} (threshold={large_gap_threshold:.1f})")
        
        return {
            'avg_spacing': avg_spacing,
            'spacing_variance': spacing_variance,
            'large_gap_threshold': large_gap_threshold,
            'large_gaps': large_gaps,
            'line_gaps': line_gaps
        }
    
    def _detect_section_breaks(self, position_data: List[Dict], spacing_analysis: Dict) -> List[int]:
        """
        Detect section breaks based on line spacing analysis.
        
        Section breaks typically occur:
        - Before/after totals (large font)
        - At large spacing gaps
        - Between item lists and summary sections
        
        Args:
            position_data: Position data
            spacing_analysis: Results from line spacing analysis
            
        Returns:
            List of line IDs where section breaks occur
        """
        section_breaks = []
        
        # Method 1: Large spacing gaps
        for gap_info in spacing_analysis['large_gaps']:
            section_breaks.append(gap_info['line_after'])
            self.logger.info(f"Section break detected at line {gap_info['line_after']} "
                           f"(gap={gap_info['gap_size']}, ratio={gap_info['gap_ratio']:.1f}x)")
        
        # Method 2: Font size changes (large text often indicates section headers/totals)
        sorted_data = sorted(position_data, key=lambda x: x['line_id'])
        
        for i in range(1, len(sorted_data)):
            current_metrics = sorted_data[i].get('font_metrics')
            previous_metrics = sorted_data[i-1].get('font_metrics')
            
            if current_metrics and previous_metrics:
                # Section break if font size changes significantly
                height_ratio = current_metrics.avg_height / (previous_metrics.avg_height + 0.001)
                
                if height_ratio > 1.5 or height_ratio < 0.67:  # 50% size change
                    line_id = sorted_data[i]['line_id']
                    if line_id not in section_breaks:
                        section_breaks.append(line_id)
                        self.logger.info(f"Section break detected at line {line_id} "
                                       f"(font size change: {height_ratio:.2f}x)")
        
        # Method 3: Pattern type changes (e.g., currency items → totals)
        for i in range(1, len(sorted_data)):
            current_pattern = sorted_data[i]['pattern']
            previous_pattern = sorted_data[i-1]['pattern']
            
            # Break between regular prices and totals
            if (previous_pattern.pattern_type.name in ['CURRENCY', 'UNIT_PRICE', 'LINE_TOTAL'] and
                current_pattern.pattern_type.name in ['GRAND_TOTAL', 'SUBTOTAL', 'TAX']):
                line_id = sorted_data[i]['line_id']
                if line_id not in section_breaks:
                    section_breaks.append(line_id)
                    self.logger.info(f"Section break detected at line {line_id} "
                                   f"(pattern transition: {previous_pattern.pattern_type.name} → {current_pattern.pattern_type.name})")
        
        return sorted(section_breaks)
    
    def _group_by_sections(self, position_data: List[Dict], section_breaks: List[int]) -> List[List[Dict]]:
        """
        Group position data into sections based on detected breaks.
        
        Args:
            position_data: All position data
            section_breaks: Line IDs where sections break
            
        Returns:
            List of sections, each containing position data
        """
        if not section_breaks:
            return [position_data]
        
        sorted_data = sorted(position_data, key=lambda x: x['line_id'])
        sections = []
        current_section = []
        
        for pos_data in sorted_data:
            line_id = pos_data['line_id']
            
            # Check if this line starts a new section
            if line_id in section_breaks and current_section:
                sections.append(current_section)
                current_section = [pos_data]
            else:
                current_section.append(pos_data)
        
        # Don't forget the last section
        if current_section:
            sections.append(current_section)
        
        self.logger.info(f"Grouped into {len(sections)} sections with sizes: {[len(s) for s in sections]}")
        return sections
    
    def _cluster_within_section(self, section_items: List[Dict], section_id: int) -> List[List[Dict]]:
        """
        Perform fine-grained clustering within a section.
        
        Different sections use different clustering strategies:
        - Item sections: Group by proximity and font consistency
        - Total sections: Keep items separate or group by type
        - Header sections: Usually single items
        
        Args:
            section_items: Items within a section
            section_id: Section identifier for logging
            
        Returns:
            List of clusters within the section
        """
        if len(section_items) <= 2:
            return [section_items]  # Small sections stay together
        
        # Determine section characteristics
        has_totals = any(item['pattern'].pattern_type.name in ['GRAND_TOTAL', 'SUBTOTAL', 'TAX'] 
                        for item in section_items)
        has_large_fonts = any(item.get('font_metrics', FontMetrics(0,0,False,False,0)).is_larger_than_normal 
                            for item in section_items)
        
        if has_totals or has_large_fonts:
            # Total/header sections: More permissive clustering
            return self._cluster_totals_section(section_items, section_id)
        else:
            # Item sections: Stricter proximity clustering
            return self._cluster_items_section(section_items, section_id)
    
    def _cluster_totals_section(self, section_items: List[Dict], section_id: int) -> List[List[Dict]]:
        """
        Cluster items in a totals/summary section.
        
        Totals sections often have:
        - Larger spacing between items
        - Different font sizes
        - Items that should be grouped by type rather than proximity
        
        Args:
            section_items: Items in the totals section
            section_id: Section identifier
            
        Returns:
            List of clusters
        """
        self.logger.info(f"Clustering totals section {section_id} with {len(section_items)} items")
        
        # For totals sections, use more permissive line gap tolerance
        max_line_gap = 15  # Larger gaps allowed in totals
        
        sorted_items = sorted(section_items, key=lambda x: x['line_id'])
        clusters = []
        current_cluster = [sorted_items[0]]
        
        for i in range(1, len(sorted_items)):
            line_gap = sorted_items[i]['line_id'] - sorted_items[i-1]['line_id']
            
            if line_gap <= max_line_gap:
                current_cluster.append(sorted_items[i])
            else:
                if current_cluster:
                    clusters.append(current_cluster)
                current_cluster = [sorted_items[i]]
        
        if current_cluster:
            clusters.append(current_cluster)
        
        self.logger.info(f"Totals section {section_id}: {len(clusters)} clusters")
        return clusters
    
    def _cluster_items_section(self, section_items: List[Dict], section_id: int) -> List[List[Dict]]:
        """
        Cluster items in a regular items section.
        
        Item sections usually have:
        - Consistent line spacing
        - Similar font sizes
        - Dense packing of items
        
        Args:
            section_items: Items in the items section
            section_id: Section identifier
            
        Returns:
            List of clusters
        """
        self.logger.info(f"Clustering items section {section_id} with {len(section_items)} items")
        
        # For item sections, use stricter line gap tolerance
        max_line_gap = 10  # Tighter clustering for item lists
        
        sorted_items = sorted(section_items, key=lambda x: x['line_id'])
        clusters = []
        current_cluster = [sorted_items[0]]
        
        for i in range(1, len(sorted_items)):
            line_gap = sorted_items[i]['line_id'] - sorted_items[i-1]['line_id']
            
            if line_gap <= max_line_gap:
                current_cluster.append(sorted_items[i])
            else:
                if current_cluster:
                    clusters.append(current_cluster)
                current_cluster = [sorted_items[i]]
        
        if current_cluster:
            clusters.append(current_cluster)
        
        # Filter out very small clusters in item sections (likely noise)
        filtered_clusters = [cluster for cluster in clusters if len(cluster) >= 2]
        
        self.logger.info(f"Items section {section_id}: {len(filtered_clusters)} clusters (filtered from {len(clusters)})")
        return filtered_clusters
    
    def _enhanced_x_coordinate_clustering(self, pattern_positions: List[Dict]) -> Dict[float, List[Dict]]:
        """
        Enhanced X-coordinate clustering with Phase 2 improvements.
        
        Uses multi-pass clustering:
        1. Right-edge alignment (price columns)
        2. Left-edge alignment (product columns)
        3. Center alignment (headers/totals)
        4. Font size analysis for confidence boosting
        
        Args:
            pattern_positions: List of position dictionaries with pattern info
            
        Returns:
            Dictionary mapping representative X coordinates to position groups
        """
        self.logger.info(f"Enhanced X-clustering: Processing {len(pattern_positions)} patterns")
        
        # Add font metrics to each position
        for pos_data in pattern_positions:
            pos_data['font_metrics'] = self._analyze_font_metrics(pos_data['pattern'])
        
        x_groups = defaultdict(list)
        
        # Pass 1: Right-edge alignment (typical price columns)
        self._cluster_by_edge_alignment(pattern_positions, x_groups, 'x_right', 'right-edge')
        
        # Pass 2: Handle remaining patterns with left-edge alignment
        unclustered = [p for p in pattern_positions if not self._is_already_clustered(p, x_groups)]
        if unclustered:
            self._cluster_by_edge_alignment(unclustered, x_groups, 'x_left', 'left-edge')
        
        # Pass 3: Center alignment for remaining patterns (headers, standalone totals)
        unclustered = [p for p in pattern_positions if not self._is_already_clustered(p, x_groups)]
        if unclustered:
            center_groups = defaultdict(list)
            for pos_data in unclustered:
                x_center = (pos_data['x_left'] + pos_data['x_right']) / 2
                
                # Find existing center-aligned group
                found_group = False
                for group_x, group_data in center_groups.items():
                    if abs(group_x - x_center) < self.alignment_tolerance * 1.5:  # More permissive for centers
                        group_data.append(pos_data)
                        found_group = True
                        break
                        
                if not found_group:
                    center_groups[x_center] = [pos_data]
            
            # Merge center groups into main groups with unique keys
            for center_x, center_data in center_groups.items():
                if len(center_data) >= 1:  # Even single centered items can be important (totals)
                    unique_key = center_x + 1000  # Offset to avoid conflicts
                    x_groups[unique_key] = center_data
                    self.logger.info(f"Enhanced X-clustering: Created center-aligned group at {center_x:.3f} with {len(center_data)} items")
        
        self.logger.info(f"Enhanced X-clustering: Created {len(x_groups)} total groups")
        return dict(x_groups)
    
    def _cluster_by_edge_alignment(self, patterns: List[Dict], x_groups: Dict, edge_key: str, edge_type: str):
        """
        Cluster patterns by specific edge alignment (left or right).
        
        Args:
            patterns: Pattern positions to cluster
            x_groups: Dictionary to store groups in
            edge_key: 'x_left' or 'x_right' for alignment type
            edge_type: Description for logging
        """
        edge_tolerance = self.alignment_tolerance
        
        for pos_data in patterns:
            edge_coord = pos_data[edge_key]
            
            # Find existing group with similar edge coordinate
            found_group = False
            for group_x, group_data in x_groups.items():
                if group_data and abs(group_data[0][edge_key] - edge_coord) < edge_tolerance:
                    group_data.append(pos_data)
                    found_group = True
                    break
                    
            if not found_group:
                x_groups[edge_coord] = [pos_data]
        
        # Filter out groups with only one item (unless they have high confidence fonts)
        groups_created = 0
        for group_x, group_data in list(x_groups.items()):
            if len(group_data) == 1:
                # Keep single items if they have distinctive font characteristics
                font_metrics = group_data[0].get('font_metrics')
                if not (font_metrics and (font_metrics.is_larger_than_normal or font_metrics.confidence > 0.8)):
                    del x_groups[group_x]
                else:
                    groups_created += 1
                    self.logger.info(f"Enhanced X-clustering: Kept single {edge_type} item due to distinctive font")
            else:
                groups_created += 1
        
        self.logger.info(f"Enhanced X-clustering: Created {groups_created} {edge_type} groups")
    
    def _is_already_clustered(self, pos_data: Dict, x_groups: Dict) -> bool:
        """
        Check if a position is already in any existing cluster.
        
        Args:
            pos_data: Position data to check
            x_groups: Existing clusters
            
        Returns:
            True if already clustered, False otherwise
        """
        pattern_id = id(pos_data['pattern'])
        
        for group_data in x_groups.values():
            if any(id(p['pattern']) == pattern_id for p in group_data):
                return True
        return False
    
    def _compute_adaptive_font_thresholds(self, words: List[ReceiptWord]) -> Tuple[float, float, float]:
        """
        Dynamically compute font size thresholds based on actual height distribution.
        
        This method analyzes the distribution of text heights in the receipt to determine
        appropriate thresholds for small, normal, and large text classification.
        
        Args:
            words: List of receipt words to analyze
            
        Returns:
            Tuple of (small_threshold, normal_threshold, large_threshold)
        """
        # Collect all valid heights
        heights = []
        for word in words:
            height = 0.0
            if hasattr(word, 'bounding_box') and word.bounding_box:
                if isinstance(word.bounding_box, dict):
                    height = word.bounding_box.get('height', 0.0)
                else:
                    # List format: [left, top, width, height]
                    height = word.bounding_box[3] if len(word.bounding_box) > 3 else 0.0
            
            # If no bounding box, try corner coordinates
            if height == 0.0:
                y_coords = []
                for corner_attr in ['top_left', 'top_right', 'bottom_left', 'bottom_right']:
                    if hasattr(word, corner_attr):
                        corner = getattr(word, corner_attr)
                        if corner:
                            if isinstance(corner, dict):
                                y_coords.append(corner.get('y', 0))
                            else:
                                y_coords.append(corner[1] if len(corner) > 1 else 0)
                
                if len(y_coords) >= 2:
                    height = max(y_coords) - min(y_coords)
            
            if height > 0:
                heights.append(height)
        
        if not heights:
            # Fallback to safe defaults for normalized coordinates
            return 0.02, 0.03, 0.04
        
        # Sort heights to compute percentiles
        sorted_heights = sorted(heights)
        n = len(sorted_heights)
        
        # Compute percentiles for threshold determination
        # Small text: bottom 20th percentile
        # Normal text: 20th to 80th percentile  
        # Large text: top 80th percentile
        p20_idx = int(n * 0.2)
        p50_idx = int(n * 0.5)
        p80_idx = int(n * 0.8)
        
        p20 = sorted_heights[p20_idx] if p20_idx < n else sorted_heights[-1]
        p50 = sorted_heights[p50_idx] if p50_idx < n else sorted_heights[-1]
        p80 = sorted_heights[p80_idx] if p80_idx < n else sorted_heights[-1]
        
        # Set thresholds with some margin
        small_threshold = p20 * 1.1  # 10% above 20th percentile
        large_threshold = p80 * 0.9   # 10% below 80th percentile
        
        # Ensure thresholds are in valid normalized range (0-1)
        small_threshold = max(0.001, min(small_threshold, 0.1))
        large_threshold = max(small_threshold * 1.5, min(large_threshold, 0.2))
        
        # Normal threshold is between small and large
        normal_threshold = (small_threshold + large_threshold) / 2
        
        self.logger.debug(f"Adaptive font thresholds: small={small_threshold:.3f}, normal={normal_threshold:.3f}, large={large_threshold:.3f}")
        self.logger.debug(f"Based on {n} words with heights - p20={p20:.3f}, p50={p50:.3f}, p80={p80:.3f}")
        
        return small_threshold, normal_threshold, large_threshold
    
    def _analyze_font_metrics(self, pattern: PatternMatch) -> FontMetrics:
        """
        Analyze font characteristics from OCR bounding box data.
        
        Phase 2 Feature: Uses bounding box height to detect:
        - Larger text (headers, totals) → higher confidence
        - Smaller text (footnotes, details) → lower confidence
        - Consistent font sizes within columns
        
        Args:
            pattern: PatternMatch with word containing bounding box
            
        Returns:
            FontMetrics object with size analysis
        """
        word = pattern.word
        
        # Extract height from bounding box
        height = 0.0
        if hasattr(word, 'bounding_box') and word.bounding_box:
            if isinstance(word.bounding_box, dict):
                height = word.bounding_box.get('height', 0.0)
            else:
                # List format: [left, top, width, height]
                height = word.bounding_box[3] if len(word.bounding_box) > 3 else 0.0
        
        # If no bounding box, try to calculate from corner coordinates
        if height == 0.0:
            y_coords = []
            for corner_attr in ['top_left', 'top_right', 'bottom_left', 'bottom_right']:
                if hasattr(word, corner_attr):
                    corner = getattr(word, corner_attr)
                    if corner:
                        if isinstance(corner, dict):
                            y_coords.append(corner.get('y', 0))
                        else:
                            y_coords.append(corner[1] if len(corner) > 1 else 0)
            
            if len(y_coords) >= 2:
                height = max(y_coords) - min(y_coords)
        
        # Use adaptive thresholds if available, otherwise compute them
        if hasattr(self, '_font_thresholds'):
            small_threshold, normal_threshold, large_threshold = self._font_thresholds
        else:
            # Safe defaults for normalized coordinates (0-1 range)
            # These will be overridden when detect_line_items_with_alignment is called
            small_threshold = 0.02   # 2% of normalized height
            normal_threshold = 0.03  # 3% of normalized height
            large_threshold = 0.04   # 4% of normalized height
        
        is_larger = height > large_threshold
        is_smaller = height < small_threshold and height > 0
        
        # Calculate confidence based on distinctiveness
        confidence = 0.5  # Base confidence
        if is_larger:
            confidence = 0.9  # High confidence for large text (likely totals/headers)
        elif is_smaller:
            confidence = 0.3  # Lower confidence for small text (might be noise)
        elif small_threshold <= height <= large_threshold:
            confidence = 0.7  # Good confidence for normal text
        
        self.logger.debug(f"Font analysis for '{word.text}': height={height:.1f}, large={is_larger}, small={is_smaller}, confidence={confidence:.2f}")
        
        return FontMetrics(
            avg_height=height,
            height_variance=0.0,  # Will be calculated at column level
            is_larger_than_normal=is_larger,
            is_smaller_than_normal=is_smaller,
            confidence=confidence
        )
    
    def _create_enhanced_price_column(self, cluster: List[Dict], column_id: int, group_x: float) -> PriceColumn:
        """
        Create an enhanced PriceColumn with Phase 2 spatial features.
        
        Args:
            cluster: List of position data dictionaries
            column_id: Unique identifier for this column
            group_x: Representative X coordinate for the group
            
        Returns:
            Enhanced PriceColumn with font and alignment analysis
        """
        patterns = [d['pattern'] for d in cluster]
        x_lefts = [d['x_left'] for d in cluster]
        x_rights = [d['x_right'] for d in cluster]
        y_positions = [d['y_pos'] for d in cluster]
        font_metrics_list = [d.get('font_metrics') for d in cluster]
        
        # Enhanced X-coordinate analysis
        x_min = min(x_lefts)
        x_max = max(x_rights)
        x_center = (x_min + x_max) / 2
        
        # Calculate X-alignment tightness (Phase 2 feature)
        avg_x_right = sum(x_rights) / len(x_rights)
        x_variance = sum((x - avg_x_right) ** 2 for x in x_rights) / len(x_rights)
        x_alignment_tightness = 1.0 - min(x_variance / (self.alignment_tolerance ** 2), 1.0)
        
        # Calculate Y-proximity confidence
        y_span = max(y_positions) - min(y_positions)
        y_density = len(cluster) / (y_span + 0.001)  # Items per unit Y-distance
        y_proximity_score = min(y_density / 10.0, 1.0)  # Normalize to 0-1
        
        # Font consistency analysis (Phase 2 feature)
        font_consistency = self._analyze_font_consistency(font_metrics_list)
        
        # Enhanced confidence calculation
        base_confidence = (x_alignment_tightness + y_proximity_score) / 2
        
        # Font size boost for consistent large text (likely totals)
        if font_consistency and font_consistency.is_larger_than_normal:
            base_confidence *= 1.4
            self.logger.info(f"Column {column_id}: Font boost for large text (confidence +40%)")
        
        # Boost confidence if column has totals
        has_total = any(p.pattern_type in [PatternType.GRAND_TOTAL, PatternType.SUBTOTAL] 
                      for p in patterns)
        if has_total:
            base_confidence *= 1.3
            self.logger.info(f"Column {column_id}: Total boost (confidence +30%)")
        
        # Boost for tight X-alignment (Phase 2 feature)
        if x_alignment_tightness > 0.9:
            base_confidence *= 1.2
            self.logger.info(f"Column {column_id}: Tight alignment boost (confidence +20%)")
        
        confidence = min(base_confidence, 1.0)
        
        self.logger.info(f"Enhanced column {column_id} at X={x_center:.3f}, Y-span={y_span:.3f} "
                       f"with {len(patterns)} prices, X-tight={x_alignment_tightness:.2f}, "
                       f"Y-prox={y_proximity_score:.2f}, font={font_consistency.confidence if font_consistency else 0:.2f}, "
                       f"confidence={confidence:.2f}")
        
        return PriceColumn(
            column_id=column_id,
            x_center=x_center,
            x_min=x_min,
            x_max=x_max,
            prices=sorted(patterns, key=lambda p: p.word.line_id),
            confidence=confidence,
            y_span=y_span,
            x_alignment_tightness=x_alignment_tightness,
            font_consistency=font_consistency
        )
    
    def _analyze_font_consistency(self, font_metrics_list: List[Optional[FontMetrics]]) -> Optional[FontMetrics]:
        """
        Analyze font consistency across a column of text.
        
        Args:
            font_metrics_list: List of font metrics for each item in column
            
        Returns:
            Combined FontMetrics representing the column's font characteristics
        """
        valid_metrics = [fm for fm in font_metrics_list if fm is not None]
        if not valid_metrics:
            return None
        
        heights = [fm.avg_height for fm in valid_metrics]
        avg_height = sum(heights) / len(heights)
        height_variance = sum((h - avg_height) ** 2 for h in heights) / len(heights) if len(heights) > 1 else 0.0
        
        # Determine if this is consistently large or small text
        large_count = sum(1 for fm in valid_metrics if fm.is_larger_than_normal)
        small_count = sum(1 for fm in valid_metrics if fm.is_smaller_than_normal)
        
        # Column characteristics
        is_consistently_large = large_count / len(valid_metrics) > 0.6
        is_consistently_small = small_count / len(valid_metrics) > 0.6
        
        # Consistency confidence - higher for uniform sizes
        consistency_ratio = 1.0 - (height_variance / (avg_height + 0.001))
        base_confidence = sum(fm.confidence for fm in valid_metrics) / len(valid_metrics)
        final_confidence = (consistency_ratio + base_confidence) / 2
        
        return FontMetrics(
            avg_height=avg_height,
            height_variance=height_variance,
            is_larger_than_normal=is_consistently_large,
            is_smaller_than_normal=is_consistently_small,
            confidence=final_confidence
        )
    
    def _create_price_column_from_cluster(self, cluster: List[Dict], column_id: int) -> PriceColumn:
        """
        Create a PriceColumn from a Y-coordinate cluster.
        
        Args:
            cluster: List of position data dictionaries
            column_id: Unique identifier for this column
            
        Returns:
            PriceColumn object
        """
        patterns = [d['pattern'] for d in cluster]
        x_lefts = [d['x_left'] for d in cluster]
        x_rights = [d['x_right'] for d in cluster]
        y_positions = [d['y_pos'] for d in cluster]
        
        # Calculate column properties
        x_min = min(x_lefts)
        x_max = max(x_rights)
        x_center = (x_min + x_max) / 2
        
        # Calculate alignment confidence based on X-coordinate consistency
        avg_x_right = sum(x_rights) / len(x_rights)
        x_variance = sum((x - avg_x_right) ** 2 for x in x_rights) / len(x_rights)
        x_alignment_score = 1.0 - min(x_variance / (self.alignment_tolerance ** 2), 1.0)
        
        # Calculate Y-proximity confidence
        y_span = max(y_positions) - min(y_positions)
        y_density = len(cluster) / (y_span + 0.001)  # Items per unit Y-distance
        y_proximity_score = min(y_density / 10.0, 1.0)  # Normalize to 0-1
        
        # Combined confidence: both X-alignment and Y-proximity matter
        base_confidence = (x_alignment_score + y_proximity_score) / 2
        
        # Boost confidence if column has totals
        has_total = any(p.pattern_type in [PatternType.GRAND_TOTAL, PatternType.SUBTOTAL] 
                      for p in patterns)
        confidence = base_confidence * (1.3 if has_total else 1.0)
        
        self.logger.info(f"Created price column {column_id} at X={x_center:.3f}, Y-span={y_span:.3f} "
                       f"with {len(patterns)} prices, X-align={x_alignment_score:.2f}, "
                       f"Y-prox={y_proximity_score:.2f}, confidence={confidence:.2f}")
        
        return PriceColumn(
            column_id=column_id,
            x_center=x_center,
            x_min=x_min,
            x_max=x_max,
            prices=sorted(patterns, key=lambda p: p.word.line_id),
            confidence=min(confidence, 1.0),
            y_span=y_span
        )
    
    def match_products_to_column(self, 
                               words: List[ReceiptWord],
                               price_column: PriceColumn) -> List[AlignedLineItem]:
        """
        Match products to prices in a vertical column.
        
        Phase 2 Enhancement: Detects indented description lines
        
        Args:
            words: All receipt words
            price_column: Detected price column
            
        Returns:
            List of line items with aligned prices
        """
        # Group words by line
        words_by_line = defaultdict(list)
        for word in words:
            words_by_line[word.line_id].append(word)
        
        # Phase 2: Analyze indentation patterns
        line_indentations = self._analyze_line_indentations(words_by_line)
        
        line_items = []
        used_price_ids = set()  # Track by line_id instead of object
        
        # For each price in the column, find the best product match
        for price_pattern in price_column.prices:
            price_id = (price_pattern.word.line_id, price_pattern.word.word_id)
            if price_id in used_price_ids:
                continue
                
            price_line = price_pattern.word.line_id
            best_product = None
            best_score = 0
            best_distance = float('inf')
            
            # Look for products on the same line or nearby lines
            for offset in range(0, -4, -1):  # Check same line and up to 3 lines above
                product_line = price_line + offset
                if product_line not in words_by_line:
                    continue
                    
                line_words = words_by_line[product_line]
                
                # Filter words that are to the left of the price column
                product_words = []
                for word in line_words:
                    # Get word's rightmost X coordinate
                    if hasattr(word, 'bottom_right') and word.bottom_right:
                        word_x_right = word.bottom_right['x'] if isinstance(word.bottom_right, dict) else word.bottom_right[0]
                    elif hasattr(word, 'bounding_box') and word.bounding_box:
                        if isinstance(word.bounding_box, dict):
                            word_x_right = word.bounding_box['x'] + word.bounding_box['width']
                        else:
                            word_x_right = word.bounding_box[0] + word.bounding_box[2]
                    else:
                        continue
                    
                    # Word should be to the left of the price column
                    if word_x_right < price_column.x_min - 0.01:
                        product_words.append(word)
                
                if not product_words:
                    continue
                
                # Score this potential product
                score = self._score_product_match(product_words, price_pattern, abs(offset))
                
                if score > best_score:
                    best_score = score
                    best_product = product_words
                    best_distance = abs(offset)
            
            # If we found a good product match, create line item
            if best_product and best_score > 0.3:
                product_text = " ".join(w.text for w in sorted(best_product, key=lambda w: w.word_id))
                product_line_id = best_product[0].line_id
                
                # Phase 2: Check for indented description lines
                description_lines = []
                has_indented_description = False
                
                if self.use_enhanced_clustering:
                    # Look for indented lines following the product
                    for check_line in range(product_line_id + 1, min(product_line_id + 4, price_line + 1)):
                        if check_line in words_by_line:
                            line_words = words_by_line[check_line]
                            line_indent = line_indentations.get(check_line, 0)
                            base_indent = line_indentations.get(product_line_id, 0)
                            
                            # Check if this line is indented relative to product line
                            if line_indent > base_indent + 0.02:  # Indented by at least 2% of page width
                                # Check if line has no price
                                has_price = any(self._is_word_in_column(w, price_column) for w in line_words)
                                
                                if not has_price:
                                    description_text = " ".join(w.text for w in sorted(line_words, key=lambda w: w.word_id))
                                    description_lines.append(description_text)
                                    has_indented_description = True
                                    self.logger.info(f"   Found indented description: '{description_text}'")
                                else:
                                    break  # Stop if we hit another priced item
                
                line_items.append(AlignedLineItem(
                    product_text=product_text,
                    product_words=best_product,
                    price=price_pattern,
                    product_line=product_line_id,
                    price_line=price_line,
                    line_distance=best_distance,
                    alignment_confidence=best_score * price_column.confidence,
                    column_id=price_column.column_id,
                    has_indented_description=has_indented_description,
                    description_lines=description_lines if description_lines else None
                ))
                used_price_ids.add(price_id)
                
                log_msg = f"Matched '{product_text}' → {price_pattern.word.text} "
                log_msg += f"(distance={best_distance}, confidence={best_score:.2f})"
                if has_indented_description:
                    log_msg += f" + {len(description_lines)} description lines"
                self.logger.info(log_msg)
        
        return line_items
    
    def detect_line_items_with_alignment(self,
                                       words: List[ReceiptWord],
                                       pattern_matches: List[PatternMatch]) -> Dict:
        """
        Detect line items using vertical price column alignment.
        
        Args:
            words: All receipt words
            pattern_matches: All pattern matches from pattern detection
            
        Returns:
            Dictionary with detection results
        """
        # Compute adaptive font thresholds based on the actual receipt
        self._font_thresholds = self._compute_adaptive_font_thresholds(words)
        
        # Extract currency patterns
        currency_patterns = [m for m in pattern_matches if m.pattern_type in [
            PatternType.CURRENCY, PatternType.GRAND_TOTAL, PatternType.SUBTOTAL,
            PatternType.TAX, PatternType.UNIT_PRICE, PatternType.LINE_TOTAL,
            PatternType.DISCOUNT
        ]]
        
        self.logger.info(f"Processing {len(currency_patterns)} currency patterns for alignment")
        
        # Detect price columns
        price_columns = self.detect_price_columns(currency_patterns)
        self.logger.info(f"Detected {len(price_columns)} price columns")
        
        # Match products to the best price column
        all_line_items = []
        
        for column in price_columns:
            if column.confidence < 0.5:
                continue
                
            items = self.match_products_to_column(words, column)
            all_line_items.extend(items)
        
        # Remove duplicates and sort by line number
        unique_items = []
        seen_products = set()
        seen_prices = set()
        
        for item in sorted(all_line_items, key=lambda i: i.product_line):
            price_id = (item.price.word.line_id, item.price.word.word_id)
            if item.product_text not in seen_products and price_id not in seen_prices:
                unique_items.append(item)
                seen_products.add(item.product_text)
                seen_prices.add(price_id)
        
        # Enhanced metadata for Phase 2 analysis
        enhanced_metadata = {
            'line_items': unique_items,
            'total_items': len(unique_items),
            'price_columns': len(price_columns),
            'best_column_confidence': max(c.confidence for c in price_columns) if price_columns else 0,
            'currency_patterns': len(currency_patterns)
        }
        
        # Add Phase 2 spatial analysis results
        if self.use_enhanced_clustering and price_columns:
            best_column = max(price_columns, key=lambda c: c.confidence)
            enhanced_metadata.update({
                'x_alignment_tightness': best_column.x_alignment_tightness,
                'font_consistency_confidence': best_column.font_consistency.confidence if best_column.font_consistency else 0,
                'has_large_font_patterns': best_column.font_consistency.is_larger_than_normal if best_column.font_consistency else False,
                'spatial_analysis_version': 'enhanced_v2'
            })
        
        return enhanced_metadata
    
    def _score_product_match(self, 
                           product_words: List[ReceiptWord],
                           price_pattern: PatternMatch,
                           line_distance: int) -> float:
        """Calculate score for a product-price match."""
        score = 0.5  # Base score
        
        # Distance penalty
        score -= line_distance * 0.15
        
        # Boost for reasonable product length
        product_text = " ".join(w.text for w in product_words)
        if 3 <= len(product_text) <= 50:
            score += 0.2
        
        # Boost if product contains alphabetic words
        if any(w.text.isalpha() and len(w.text) > 2 for w in product_words):
            score += 0.2
        
        # Penalty for metadata keywords
        metadata_keywords = {"TAX", "TOTAL", "SUBTOTAL", "BALANCE", "CREDIT", "CARD"}
        if any(keyword in product_text.upper() for keyword in metadata_keywords):
            score -= 0.3
        
        return max(0.0, min(score, 1.0))
    
    def _analyze_line_indentations(self, words_by_line: Dict[int, List[ReceiptWord]]) -> Dict[int, float]:
        """
        Analyze the leftmost X coordinate of each line to detect indentation.
        
        Phase 2 Feature: Indentation detection for multi-line items
        
        Args:
            words_by_line: Dictionary mapping line IDs to words
            
        Returns:
            Dictionary mapping line IDs to leftmost X coordinate
        """
        line_indentations = {}
        
        for line_id, line_words in words_by_line.items():
            if not line_words:
                continue
                
            # Find leftmost X coordinate for this line
            min_x = float('inf')
            for word in line_words:
                x_left = self._get_word_left_x(word)
                if x_left < min_x:
                    min_x = x_left
            
            if min_x != float('inf'):
                line_indentations[line_id] = min_x
        
        return line_indentations
    
    def _get_word_left_x(self, word: ReceiptWord) -> float:
        """
        Get the leftmost X coordinate of a word.
        
        Args:
            word: ReceiptWord object
            
        Returns:
            Leftmost X coordinate
        """
        x_coords = []
        
        # Try corner coordinates first
        if hasattr(word, 'bottom_left') and word.bottom_left:
            if isinstance(word.bottom_left, dict):
                x_coords.append(word.bottom_left.get('x', float('inf')))
            else:
                x_coords.append(word.bottom_left[0] if len(word.bottom_left) > 0 else float('inf'))
                
        if hasattr(word, 'top_left') and word.top_left:
            if isinstance(word.top_left, dict):
                x_coords.append(word.top_left.get('x', float('inf')))
            else:
                x_coords.append(word.top_left[0] if len(word.top_left) > 0 else float('inf'))
        
        # Fall back to bounding box
        if not x_coords and hasattr(word, 'bounding_box') and word.bounding_box:
            if isinstance(word.bounding_box, dict):
                x_coords.append(word.bounding_box.get('x', float('inf')))
            else:
                x_coords.append(word.bounding_box[0] if len(word.bounding_box) > 0 else float('inf'))
        
        return min(x_coords) if x_coords else float('inf')
    
    def _is_word_in_column(self, word: ReceiptWord, column: PriceColumn) -> bool:
        """
        Check if a word is within the X-coordinate range of a price column.
        
        Args:
            word: Word to check
            column: Price column to check against
            
        Returns:
            True if word is in column range
        """
        # Get word's X coordinates
        x_coords = []
        
        if hasattr(word, 'bottom_right') and word.bottom_right:
            if isinstance(word.bottom_right, dict):
                x_coords.append(word.bottom_right.get('x', 0))
            else:
                x_coords.append(word.bottom_right[0] if len(word.bottom_right) > 0 else 0)
                
        if hasattr(word, 'bottom_left') and word.bottom_left:
            if isinstance(word.bottom_left, dict):
                x_coords.append(word.bottom_left.get('x', 0))
            else:
                x_coords.append(word.bottom_left[0] if len(word.bottom_left) > 0 else 0)
        
        if not x_coords:
            return False
            
        word_center_x = sum(x_coords) / len(x_coords)
        
        # Check if word center is within column bounds (with tolerance)
        tolerance = 0.05  # 5% of page width
        return column.x_min - tolerance <= word_center_x <= column.x_max + tolerance
    
    def _detect_multi_column_layout(self, price_columns: List[PriceColumn]) -> List[PriceColumn]:
        """
        Detect and handle multi-column receipt layouts.
        
        Phase 2 Feature: Some receipts have two side-by-side item lists.
        This method identifies such layouts and adjusts confidence accordingly.
        
        Args:
            price_columns: Initial detected price columns
            
        Returns:
            Price columns with adjusted confidence for multi-column layouts
        """
        if len(price_columns) < 2:
            return price_columns
        
        # Analyze X-coordinate distribution
        x_positions = [col.x_center for col in price_columns]
        page_width = max(col.x_max for col in price_columns) - min(col.x_min for col in price_columns)
        
        # Check for clear left/right separation
        left_columns = [col for col in price_columns if col.x_center < 0.5]
        right_columns = [col for col in price_columns if col.x_center >= 0.5]
        
        # Multi-column layout indicators
        has_left_right_split = len(left_columns) > 0 and len(right_columns) > 0
        
        # Check Y-coordinate overlap between potential left/right columns
        if has_left_right_split:
            for left_col in left_columns:
                for right_col in right_columns:
                    y_overlap = self._calculate_y_overlap(left_col, right_col)
                    
                    if y_overlap > 0.5:  # Significant Y-overlap
                        # This suggests side-by-side columns
                        self.logger.info(f"Multi-column layout detected: Column {left_col.column_id} (left) "
                                       f"and Column {right_col.column_id} (right) with {y_overlap:.1%} Y-overlap")
                        
                        # Boost confidence for multi-column awareness
                        left_col.confidence *= 1.1
                        right_col.confidence *= 1.1
                        
                        # Mark as multi-column (could add a flag to PriceColumn dataclass)
                        # For now, just log and boost confidence
        
        # Check for column pairs with similar item counts (another multi-column indicator)
        for i, col1 in enumerate(price_columns):
            for col2 in price_columns[i+1:]:
                if abs(len(col1.prices) - len(col2.prices)) <= 2:  # Similar item counts
                    x_separation = abs(col1.x_center - col2.x_center)
                    
                    if 0.3 < x_separation < 0.7:  # Columns are well-separated horizontally
                        self.logger.info(f"Potential multi-column pair: Columns {col1.column_id} and {col2.column_id} "
                                       f"with similar item counts ({len(col1.prices)} vs {len(col2.prices)})")
        
        return price_columns
    
    def _calculate_y_overlap(self, col1: PriceColumn, col2: PriceColumn) -> float:
        """
        Calculate the Y-coordinate overlap between two columns.
        
        Args:
            col1, col2: Price columns to compare
            
        Returns:
            Overlap ratio (0.0 = no overlap, 1.0 = complete overlap)
        """
        # Get Y ranges for each column
        col1_y_min = min(p.word.line_id for p in col1.prices)
        col1_y_max = max(p.word.line_id for p in col1.prices)
        col2_y_min = min(p.word.line_id for p in col2.prices)
        col2_y_max = max(p.word.line_id for p in col2.prices)
        
        # Calculate overlap
        overlap_start = max(col1_y_min, col2_y_min)
        overlap_end = min(col1_y_max, col2_y_max)
        
        if overlap_start > overlap_end:
            return 0.0  # No overlap
        
        overlap_lines = overlap_end - overlap_start + 1
        total_lines = max(col1_y_max - col1_y_min + 1, col2_y_max - col2_y_min + 1)
        
        return overlap_lines / total_lines if total_lines > 0 else 0.0