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
class PriceColumn:
    """A column of vertically-aligned prices."""
    column_id: int
    x_center: float  # Average X coordinate of the column
    x_min: float     # Leftmost X coordinate
    x_max: float     # Rightmost X coordinate
    prices: List[PatternMatch]  # Currency patterns in this column
    confidence: float  # Confidence this is a real price column
    y_span: Optional[float] = None  # Y-coordinate span of the column (for debugging)


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


class VerticalAlignmentDetector:
    """Detects price columns and matches products using vertical alignment."""
    
    def __init__(self, alignment_tolerance: float = 0.02):
        """
        Initialize detector.
        
        Args:
            alignment_tolerance: Maximum X-coordinate difference for alignment (as fraction of page width)
        """
        self.alignment_tolerance = alignment_tolerance
        self.logger = logger
        
    def detect_price_columns(self, currency_patterns: List[PatternMatch]) -> List[PriceColumn]:
        """
        Detect vertically-aligned price columns.
        
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
        
        # Step 1: Group by X-coordinate alignment (as before)
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
            y_clustered_groups = self._cluster_by_y_coordinate(group_data)
            
            # Create price columns from Y-clusters that have sufficient density
            for cluster in y_clustered_groups:
                if len(cluster) >= 2:  # Each Y-cluster needs at least 2 prices
                    column = self._create_price_column_from_cluster(cluster, len(price_columns))
                    price_columns.append(column)
        
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
                            word_x_right = word.bounding_box['left'] + word.bounding_box['width']
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
                
                line_items.append(AlignedLineItem(
                    product_text=product_text,
                    product_words=best_product,
                    price=price_pattern,
                    product_line=best_product[0].line_id,
                    price_line=price_line,
                    line_distance=best_distance,
                    alignment_confidence=best_score * price_column.confidence,
                    column_id=price_column.column_id
                ))
                used_price_ids.add(price_id)
                
                self.logger.info(f"Matched '{product_text}' → {price_pattern.word.text} "
                               f"(distance={best_distance}, confidence={best_score:.2f})")
        
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
        
        return {
            'line_items': unique_items,
            'total_items': len(unique_items),
            'price_columns': len(price_columns),
            'best_column_confidence': max(c.confidence for c in price_columns) if price_columns else 0,
            'currency_patterns': len(currency_patterns)
        }
    
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