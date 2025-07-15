"""
Proper geometric spatial analysis using bounding boxes instead of arbitrary tolerances.

This module implements correct spatial grouping based on actual word geometry,
not arbitrary x/y tolerances that fail in real-world scenarios.
"""

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from receipt_dynamo.entities.receipt_word import ReceiptWord
from receipt_label.pattern_detection.base import PatternMatch, PatternType

logger = logging.getLogger(__name__)


@dataclass
class GeometricRow:
    """A row of words determined by bounding box overlap."""
    y_center: float
    y_min: float
    y_max: float
    words: List[ReceiptWord]
    
    def contains_y_point(self, y: float) -> bool:
        """Check if a y-coordinate falls within this row's bounds."""
        return self.y_min <= y <= self.y_max
    
    def overlaps_with_word(self, word: ReceiptWord) -> bool:
        """Check if a word's y-bounds overlap with this row using corner coordinates."""
        # Get y-bounds from corner coordinates, not bounding box
        word_y_min = min(word.top_left["y"], word.top_right["y"])
        word_y_max = max(word.bottom_left["y"], word.bottom_right["y"])
        
        # Check for any overlap
        return not (word_y_max < self.y_min or word_y_min > self.y_max)


@dataclass  
class GeometricColumn:
    """A column of words determined by bounding box overlap."""
    x_center: float
    x_min: float
    x_max: float
    words: List[ReceiptWord]
    column_type: str  # 'product', 'price', 'quantity', 'mixed'
    
    def contains_x_point(self, x: float) -> bool:
        """Check if an x-coordinate falls within this column's bounds."""
        return self.x_min <= x <= self.x_max
    
    def overlaps_with_word(self, word: ReceiptWord) -> bool:
        """Check if a word's x-bounds overlap with this column using corner coordinates."""
        # Get x-bounds from corner coordinates, not bounding box
        word_x_min = min(word.top_left["x"], word.bottom_left["x"])
        word_x_max = max(word.top_right["x"], word.bottom_right["x"])
        
        # Check for any overlap
        return not (word_x_max < self.x_min or word_x_min > self.x_max)


class ProperSpatialGrouper:
    """Spatial grouping using actual bounding box geometry."""
    
    def __init__(self):
        self.logger = logger
    
    def group_words_into_rows(self, words: List[ReceiptWord]) -> List[GeometricRow]:
        """Group words into rows using receipt line_id as primary grouping."""
        if not words:
            return []
        
        # First, group by line_id (receipt lines from OCR)
        words_by_line_id = {}
        for word in words:
            if word.line_id not in words_by_line_id:
                words_by_line_id[word.line_id] = []
            words_by_line_id[word.line_id].append(word)
        
        rows = []
        
        # Create geometric rows from receipt lines
        for line_id, line_words in words_by_line_id.items():
            if not line_words:
                continue
                
            # Calculate bounds from all words in this line
            all_y_mins = [min(w.top_left["y"], w.top_right["y"]) for w in line_words]
            all_y_maxs = [max(w.bottom_left["y"], w.bottom_right["y"]) for w in line_words]
            
            y_min = min(all_y_mins)
            y_max = max(all_y_maxs)
            y_center = (y_min + y_max) / 2
            
            row = GeometricRow(
                y_center=y_center,
                y_min=y_min,
                y_max=y_max,
                words=line_words
            )
            rows.append(row)
        
        # Sort rows by y-position (top to bottom)
        rows.sort(key=lambda r: r.y_center)
        
        self.logger.info(f"Grouped {len(words)} words into {len(rows)} geometric rows (based on line_id)")
        return rows
    
    def group_words_into_columns(self, words: List[ReceiptWord], 
                                currency_words: Set[ReceiptWord],
                                quantity_words: Set[ReceiptWord]) -> List[GeometricColumn]:
        """Group words into columns using centroid proximity instead of overlap."""
        if not words:
            return []
        
        columns = []
        column_tolerance = 0.05  # 5% of receipt width for column grouping
        
        for word in words:
            centroid = word.calculate_centroid()
            word_x = centroid[0]
            
            # Find existing column whose center is close to this word's center
            placed = False
            for column in columns:
                # Use centroid proximity instead of bounding box overlap
                if abs(column.x_center - word_x) <= column_tolerance:
                    column.words.append(word)
                    # Update column bounds to include this word using corner coordinates
                    word_x_min = min(word.top_left["x"], word.bottom_left["x"])
                    word_x_max = max(word.top_right["x"], word.bottom_right["x"])
                    column.x_min = min(column.x_min, word_x_min)
                    column.x_max = max(column.x_max, word_x_max)
                    
                    # Recalculate center as average of all word centroids in column
                    all_x_centers = [w.calculate_centroid()[0] for w in column.words]
                    column.x_center = sum(all_x_centers) / len(all_x_centers)
                    placed = True
                    break
            
            # If no close column found, create new column
            if not placed:
                # Use corner coordinates to determine actual word bounds
                word_x_min = min(word.top_left["x"], word.bottom_left["x"])
                word_x_max = max(word.top_right["x"], word.bottom_right["x"])
                new_column = GeometricColumn(
                    x_center=word_x,
                    x_min=word_x_min,
                    x_max=word_x_max,
                    words=[word],
                    column_type="mixed"  # Will be classified later
                )
                columns.append(new_column)
        
        # Classify columns based on pattern content
        for column in columns:
            column.column_type = self._classify_column_type(
                column.words, currency_words, quantity_words
            )
        
        # Sort columns by x-position (left to right)
        columns.sort(key=lambda c: c.x_center)
        
        self.logger.info(f"Grouped {len(words)} words into {len(columns)} geometric columns")
        for i, column in enumerate(columns):
            self.logger.info(f"  Column {i+1}: {column.column_type} ({len(column.words)} words)")
        
        return columns
    
    def _classify_column_type(self, column_words: List[ReceiptWord], 
                             currency_words: Set[ReceiptWord],
                             quantity_words: Set[ReceiptWord]) -> str:
        """Classify column type based on pattern detection results."""
        if not column_words:
            return "empty"
        
        currency_count = sum(1 for word in column_words if word in currency_words)
        quantity_count = sum(1 for word in column_words if word in quantity_words)
        total_count = len(column_words)
        
        currency_ratio = currency_count / total_count
        quantity_ratio = quantity_count / total_count
        
        # High concentration of currency patterns = price column
        if currency_ratio > 0.3:
            return "price"
        
        # High concentration of quantity patterns = quantity column  
        if quantity_ratio > 0.3:
            return "quantity"
        
        # Mostly text = product description column
        text_words = sum(1 for word in column_words 
                        if word.text.isalpha() and len(word.text) > 2)
        text_ratio = text_words / total_count
        
        if text_ratio > 0.6:
            return "product"
        
        return "mixed"


class GeometricLineItemDetector:
    """Line item detection using proper geometric spatial analysis."""
    
    def __init__(self):
        self.spatial_grouper = ProperSpatialGrouper()
        self.logger = logger
    
    def detect_line_items_with_patterns(self, 
                                      words: List[ReceiptWord],
                                      pattern_matches: List[PatternMatch]) -> Dict:
        """
        Detect line items using geometric spatial analysis + existing pattern detection.
        
        Args:
            words: All words from the receipt
            pattern_matches: Already detected patterns (currency, quantity, etc.)
            
        Returns:
            Dict with line item analysis results
        """
        self.logger.info(f"Starting geometric line item detection on {len(words)} words with {len(pattern_matches)} patterns")
        
        # Step 1: Extract pattern-detected words
        currency_words = set()
        quantity_words = set()
        
        for match in pattern_matches:
            if match.pattern_type in [PatternType.CURRENCY, PatternType.GRAND_TOTAL, 
                                    PatternType.SUBTOTAL, PatternType.TAX, 
                                    PatternType.UNIT_PRICE, PatternType.LINE_TOTAL,
                                    PatternType.DISCOUNT]:  # Include DISCOUNT pattern
                currency_words.add(match.word)
            elif match.pattern_type in [PatternType.QUANTITY, PatternType.QUANTITY_AT, 
                                      PatternType.QUANTITY_TIMES, PatternType.QUANTITY_FOR]:
                quantity_words.add(match.word)
        
        self.logger.info(f"Pattern analysis: {len(currency_words)} currency words, {len(quantity_words)} quantity words")
        
        # Debug: Show which currency words were found
        currency_by_line = {}
        for word in currency_words:
            if word.line_id not in currency_by_line:
                currency_by_line[word.line_id] = []
            currency_by_line[word.line_id].append(word.text)
        
        self.logger.info(f"Currency words by line: {dict(sorted(currency_by_line.items()))}")
        
        # Step 2: Create proper geometric structure
        rows = self.spatial_grouper.group_words_into_rows(words)
        columns = self.spatial_grouper.group_words_into_columns(words, currency_words, quantity_words)
        
        # Step 3: Identify line items using spatial proximity matching
        line_item_rows = []
        
        # Separate product rows and price rows
        product_rows = []
        price_rows = []
        
        for row in rows:
            row_currency_words = [w for w in row.words if w in currency_words]
            row_quantity_words = [w for w in row.words if w in quantity_words]
            
            product_words = [w for w in row.words 
                           if w not in currency_words 
                           and w not in quantity_words
                           and w.text.isalpha() 
                           and len(w.text) > 2]
            
            # Filter out metadata rows
            row_text = " ".join(word.text for word in row.words).upper()
            is_metadata = any(keyword in row_text for keyword in [
                'STORE', 'HOURS', 'PHONE', 'ADDRESS', 'TAX REPORT', 'BALANCE', 
                'CREDIT', 'AUTH#', 'REF#', 'CHANGE', 'TOTAL', 'SUBTOTAL',
                'FEEDBACK', 'SURVEY', 'GIFT', 'THANK YOU', 'RECEIPT', 'BAKERY'
            ])
            
            if is_metadata:
                continue
                
            # Classify rows
            if product_words and not row_currency_words:
                # Pure product row (product text, no prices)
                product_rows.append({
                    'row': row,
                    'product_words': product_words,
                    'row_text': row_text
                })
            elif row_currency_words and not product_words:
                # Pure price row (prices, no product text)
                price_rows.append({
                    'row': row,
                    'currency_words': row_currency_words,
                    'quantity_words': row_quantity_words,
                    'row_text': row_text
                })
            elif row_currency_words and product_words:
                # Combined row (both product and price) - traditional approach
                line_item_rows.append({
                    'row': row,
                    'currency_words': row_currency_words,
                    'quantity_words': row_quantity_words, 
                    'product_words': product_words,
                    'confidence': self._calculate_row_confidence(row, row_currency_words, product_words),
                    'match_type': 'same_line'
                })
        
        self.logger.info(f"Found {len(product_rows)} product-only rows, {len(price_rows)} price-only rows")
        
        # Step 3b: Match product rows with nearby price rows using spatial proximity
        proximity_threshold = 0.05  # 5% of receipt height
        
        for product_data in product_rows:
            product_row = product_data['row']
            product_y = product_row.y_center
            
            # Find the closest price row within threshold
            closest_price = None
            min_distance = float('inf')
            
            for price_data in price_rows:
                price_row = price_data['row']
                price_y = price_row.y_center
                distance = abs(price_y - product_y)
                
                if distance <= proximity_threshold and distance < min_distance:
                    min_distance = distance
                    closest_price = price_data
            
            if closest_price:
                # Create a matched line item
                confidence = self._calculate_proximity_confidence(
                    product_data['product_words'], 
                    closest_price['currency_words'], 
                    min_distance, 
                    proximity_threshold
                )
                
                line_item_rows.append({
                    'row': product_row,  # Use product row as primary
                    'price_row': closest_price['row'],
                    'currency_words': closest_price['currency_words'],
                    'quantity_words': closest_price['quantity_words'],
                    'product_words': product_data['product_words'],
                    'confidence': confidence,
                    'match_type': 'proximity',
                    'proximity_distance': min_distance
                })
                
                self.logger.info(f"Matched product '{product_data['row_text'].strip()}' with price '{closest_price['row_text'].strip()}' (distance: {min_distance:.3f})")
        
        self.logger.info(f"Identified {len(line_item_rows)} total line items ({sum(1 for item in line_item_rows if item.get('match_type') == 'same_line')} same-line, {sum(1 for item in line_item_rows if item.get('match_type') == 'proximity')} proximity)")
        
        # Step 4: Create detailed analysis
        result = {
            'total_words': len(words),
            'total_rows': len(rows),
            'total_columns': len(columns),
            'currency_words_found': len(currency_words),
            'quantity_words_found': len(quantity_words),
            'line_item_rows': len(line_item_rows),
            'column_types': [col.column_type for col in columns],
            'line_items': []
        }
        
        # Step 5: Extract actual line item details
        for item_data in line_item_rows:
            row = item_data['row']
            
            # Get product text (left-aligned words typically)
            product_text = " ".join(word.text for word in item_data['product_words'])
            
            # Get price (rightmost currency word typically)
            prices = [word.text for word in item_data['currency_words']]
            main_price = prices[-1] if prices else None  # Rightmost price
            
            # Get quantity if available
            quantities = [word.text for word in item_data['quantity_words']]
            quantity = quantities[0] if quantities else None
            
            # Create line item entry
            line_item = {
                'product_text': product_text,
                'price': main_price,
                'quantity': quantity,
                'confidence': item_data['confidence'],
                'row_y_position': row.y_center,
                'all_prices': prices,
                'match_type': item_data.get('match_type', 'unknown')
            }
            
            # Add different row text depending on match type
            if item_data.get('match_type') == 'proximity':
                # For proximity matches, show both product and price lines
                price_row_text = " ".join(word.text for word in item_data['price_row'].words)
                line_item['row_text'] = f"Product: {product_text} | Price: {price_row_text}"
                line_item['proximity_distance'] = item_data.get('proximity_distance', 0)
            else:
                # For same-line matches, show the combined row
                line_item['row_text'] = " ".join(word.text for word in row.words)
            
            result['line_items'].append(line_item)
        
        return result
    
    def _calculate_row_confidence(self, row: GeometricRow, 
                                 currency_words: List[ReceiptWord], 
                                 product_words: List[ReceiptWord]) -> float:
        """Calculate confidence score for a potential line item row."""
        confidence = 0.0
        
        # Base confidence for having both price and product
        if currency_words and product_words:
            confidence += 0.5
        
        # Boost for having reasonable product text length
        product_text = " ".join(word.text for word in product_words)
        if 3 <= len(product_text) <= 50:
            confidence += 0.2
        
        # Boost for having properly formatted currency
        for currency_word in currency_words:
            if '$' in currency_word.text or '.' in currency_word.text:
                confidence += 0.2
                break
        
        # Penalize if too many words (likely metadata)
        if len(row.words) > 15:
            confidence -= 0.2
        
        return min(confidence, 1.0)
    
    def _calculate_proximity_confidence(self, product_words: List[ReceiptWord], 
                                      currency_words: List[ReceiptWord],
                                      distance: float, threshold: float) -> float:
        """Calculate confidence score for proximity-matched line items."""
        confidence = 0.0
        
        # Base confidence for having both product and price
        confidence += 0.4
        
        # Boost for closer proximity (closer = higher confidence)
        proximity_score = 1.0 - (distance / threshold)  # 1.0 when distance=0, 0.0 when distance=threshold
        confidence += 0.3 * proximity_score
        
        # Boost for reasonable product text length
        product_text = " ".join(word.text for word in product_words)
        if 3 <= len(product_text) <= 50:
            confidence += 0.2
            
        # Boost for properly formatted currency
        for currency_word in currency_words:
            if '$' in currency_word.text or '.' in currency_word.text:
                confidence += 0.1
                break
        
        return min(confidence, 1.0)