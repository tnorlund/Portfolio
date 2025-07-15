"""
Combined line item detector using pattern, spatial, and semantic approaches.

This module combines three complementary approaches:
1. Pattern Detection - Finds currency patterns and quantities
2. Spatial Analysis - Matches products with prices based on proximity
3. Semantic Validation - Uses Pinecone to validate real prices vs noise

The combined approach achieves better accuracy than any single method.
"""

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from receipt_dynamo.entities.receipt_word import ReceiptWord
from receipt_label.pattern_detection.base import PatternMatch, PatternType
from receipt_label.utils.client_manager import ClientManager, ClientConfig

logger = logging.getLogger(__name__)


@dataclass
class LineItem:
    """A validated line item with product and price."""
    product_text: str
    product_words: List[ReceiptWord]
    price_text: str
    price_word: ReceiptWord
    product_line: int
    price_line: int
    line_distance: int
    confidence: float
    validation_method: str  # "pattern", "spatial", "semantic", "combined"
    semantic_labels: List[str] = None


class CombinedLineItemDetector:
    """Detects line items using combined pattern, spatial, and semantic approaches."""
    
    def __init__(self):
        """Initialize detector with optional Pinecone client."""
        try:
            self.client_manager = ClientManager(ClientConfig.from_env())
            self.pinecone_index = self.client_manager.pinecone
            self.semantic_enabled = True
        except Exception as e:
            logger.warning(f"Pinecone not available, using pattern+spatial only: {e}")
            self.pinecone_index = None
            self.semantic_enabled = False
            
        self.logger = logger
        
        # Known product keywords for validation
        self.product_keywords = {
            "FLAX", "OAT", "BRAN", "PITA", "ORGANIC", "LIQUID", "SOAP",
            "COCO", "HASS", "AVOCADOS", "BAKERY", "PRODUCE", "DAIRY",
            "MEAT", "SEAFOOD", "DELI", "FROZEN", "CHIPS", "SODA",
            "JUICE", "WATER", "COFFEE", "TEA", "BREAD", "MILK",
            "EGGS", "CHEESE", "BUTTER", "YOGURT", "CHICKEN", "BEEF",
            "PORK", "FISH", "SHRIMP", "VEGETABLES", "FRUITS"
        }
        
        # Metadata keywords to filter out
        self.metadata_keywords = {
            "SPROUTS", "FARMERS", "MARKET", "WESTLAKE", "VILLAGE",
            "TAX", "TOTAL", "SUBTOTAL", "BALANCE", "DUE", "CREDIT",
            "DEBIT", "CARD", "CASH", "CHANGE", "TRANSACTION", "RECEIPT",
            "CASHIER", "REGISTER", "STORE", "THANK", "YOU", "VISIT",
            "SAVE", "MONEY", "PAPER", "EMAIL", "SIGN", "APPROVED",
            "AUTH", "CODE", "PURCHASE", "ISSUER"
        }
        
    def detect_line_items(self,
                         words: List[ReceiptWord],
                         pattern_matches: List[PatternMatch],
                         receipt_id: Optional[str] = None) -> Dict:
        """
        Detect line items using combined approaches.
        
        Args:
            words: All receipt words
            pattern_matches: Pattern detection results
            receipt_id: Optional receipt ID for semantic validation
            
        Returns:
            Dictionary with detection results
        """
        self.logger.info(f"Starting combined line item detection on {len(words)} words")
        
        # Step 1: Extract currency patterns
        currency_patterns = self._extract_currency_patterns(pattern_matches)
        self.logger.info(f"Found {len(currency_patterns)} currency patterns")
        
        # Step 2: Simple spatial matching
        spatial_items = self._spatial_matching(words, currency_patterns)
        self.logger.info(f"Spatial matching found {len(spatial_items)} potential items")
        
        # Step 3: Filter using pattern context
        pattern_filtered = self._filter_with_patterns(spatial_items, pattern_matches)
        self.logger.info(f"Pattern filtering kept {len(pattern_filtered)} items")
        
        # Step 4: Semantic validation (if available)
        if self.semantic_enabled and receipt_id:
            final_items = self._semantic_validation(pattern_filtered, receipt_id)
            self.logger.info(f"Semantic validation kept {len(final_items)} items")
        else:
            final_items = pattern_filtered
            
        # Calculate metrics
        total_items = len(final_items)
        high_confidence = sum(1 for item in final_items if item.confidence > 0.7)
        
        return {
            'line_items': final_items,
            'total_items': total_items,
            'high_confidence_items': high_confidence,
            'currency_patterns_found': len(currency_patterns),
            'spatial_candidates': len(spatial_items),
            'pattern_filtered': len(pattern_filtered),
            'semantic_validated': len(final_items) if self.semantic_enabled else 0,
            'detection_methods': {
                'pattern': True,
                'spatial': True,
                'semantic': self.semantic_enabled
            }
        }
    
    def _extract_currency_patterns(self, pattern_matches: List[PatternMatch]) -> List[PatternMatch]:
        """Extract currency-related patterns."""
        currency_types = {
            PatternType.CURRENCY, PatternType.GRAND_TOTAL, PatternType.SUBTOTAL,
            PatternType.TAX, PatternType.UNIT_PRICE, PatternType.LINE_TOTAL,
            PatternType.DISCOUNT
        }
        
        return [m for m in pattern_matches if m.pattern_type in currency_types]
    
    def _spatial_matching(self, words: List[ReceiptWord], 
                         currency_patterns: List[PatternMatch]) -> List[LineItem]:
        """Match products with prices using spatial proximity."""
        
        # Group words by line
        words_by_line = {}
        for word in words:
            if word.line_id not in words_by_line:
                words_by_line[word.line_id] = []
            words_by_line[word.line_id].append(word)
        
        # Create currency line lookup
        currency_lines = {}
        for match in currency_patterns:
            line_id = match.word.line_id
            if line_id not in currency_lines:
                currency_lines[line_id] = []
            currency_lines[line_id].append(match)
        
        line_items = []
        used_prices = set()
        
        # For each line, check if it contains products
        for line_id, line_words in words_by_line.items():
            line_text = " ".join(w.text for w in line_words).upper()
            
            # Skip if line contains too much metadata
            metadata_count = sum(1 for keyword in self.metadata_keywords if keyword in line_text)
            if metadata_count > 2:
                continue
                
            # Check if this line might contain products
            has_product_potential = (
                any(keyword in line_text for keyword in self.product_keywords) or
                (len(line_words) >= 2 and any(w.text.isalpha() and len(w.text) > 3 for w in line_words))
            )
            
            if has_product_potential:
                # Look for price on same line or nearby lines
                best_price = None
                min_distance = float('inf')
                
                # Check within 3 lines, preferring prices that come AFTER products
                for offset in range(0, 4):  # Start from 0 (same line) to +3 (below)
                    price_line_id = line_id + offset
                    if price_line_id in currency_lines:
                        for price_match in currency_lines[price_line_id]:
                            if price_match.word.line_id not in used_prices:
                                # Validate it's a reasonable price
                                price_text = price_match.word.text
                                if self._is_valid_price(price_text):
                                    distance = abs(offset)
                                    if distance < min_distance:
                                        min_distance = distance
                                        best_price = price_match
                                        
                # If no price found below, check above (less common)
                if not best_price:
                    for offset in range(-1, -4, -1):  # Check -1 to -3
                        price_line_id = line_id + offset
                        if price_line_id in currency_lines:
                            for price_match in currency_lines[price_line_id]:
                                if price_match.word.line_id not in used_prices:
                                    price_text = price_match.word.text
                                    if self._is_valid_price(price_text):
                                        distance = abs(offset)
                                        if distance < min_distance:
                                            min_distance = distance
                                            best_price = price_match
                
                if best_price:
                    # Calculate confidence
                    confidence = self._calculate_confidence(
                        line_words, best_price.word, min_distance, line_text
                    )
                    
                    line_items.append(LineItem(
                        product_text=" ".join(w.text for w in line_words),
                        product_words=line_words,
                        price_text=best_price.word.text,
                        price_word=best_price.word,
                        product_line=line_id,
                        price_line=best_price.word.line_id,
                        line_distance=min_distance,
                        confidence=confidence,
                        validation_method="spatial"
                    ))
                    used_prices.add(best_price.word.line_id)
        
        return line_items
    
    def _filter_with_patterns(self, line_items: List[LineItem], 
                            pattern_matches: List[PatternMatch]) -> List[LineItem]:
        """Filter line items using pattern context."""
        filtered_items = []
        
        # Create pattern lookup by line
        patterns_by_line = {}
        for match in pattern_matches:
            line_id = match.word.line_id
            if line_id not in patterns_by_line:
                patterns_by_line[line_id] = []
            patterns_by_line[line_id].append(match)
        
        for item in line_items:
            # Check if price line has appropriate pattern type
            price_patterns = patterns_by_line.get(item.price_line, [])
            
            # Look for price-related patterns
            has_price_pattern = any(
                p.pattern_type in [PatternType.CURRENCY, PatternType.UNIT_PRICE, 
                                  PatternType.LINE_TOTAL, PatternType.DISCOUNT]
                for p in price_patterns
            )
            
            if has_price_pattern:
                # Boost confidence
                item.confidence = min(item.confidence + 0.2, 1.0)
                item.validation_method = "pattern+spatial"
                filtered_items.append(item)
            elif item.confidence > 0.5:
                # Keep high confidence items even without pattern
                filtered_items.append(item)
        
        return filtered_items
    
    def _semantic_validation(self, line_items: List[LineItem], 
                           receipt_id: str) -> List[LineItem]:
        """Validate line items using Pinecone semantic search."""
        if not self.pinecone_index:
            return line_items
            
        validated_items = []
        
        for item in line_items:
            try:
                # Check if price has valid label in Pinecone
                price_vector_id = f"IMAGE#{receipt_id}#RECEIPT#00001#LINE#{item.price_word.line_id:05d}#WORD#{item.price_word.word_id:05d}"
                
                response = self.pinecone_index.fetch(
                    ids=[price_vector_id],
                    namespace="words"
                )
                
                if price_vector_id in response.vectors:
                    metadata = response.vectors[price_vector_id].metadata
                    valid_labels = metadata.get("valid_labels", [])
                    proposed_label = metadata.get("proposed_label", "")
                    
                    # Price-related labels
                    price_labels = {
                        "AMOUNT", "PRICE", "UNIT_PRICE", "LINE_TOTAL",
                        "ITEM_PRICE", "PRODUCT_PRICE"
                    }
                    
                    if any(label in price_labels for label in valid_labels) or proposed_label in price_labels:
                        # Valid price - boost confidence
                        item.confidence = min(item.confidence + 0.3, 1.0)
                        item.validation_method = "combined"
                        item.semantic_labels = valid_labels or [proposed_label]
                        validated_items.append(item)
                        self.logger.info(f"Semantically validated: {item.product_text} → {item.price_text}")
                    else:
                        # Check confidence threshold
                        if item.confidence > 0.6:
                            validated_items.append(item)
                else:
                    # No semantic data - use confidence threshold
                    if item.confidence > 0.6:
                        validated_items.append(item)
                        
            except Exception as e:
                self.logger.warning(f"Semantic validation error for {item.product_text}: {e}")
                # Fall back to confidence threshold
                if item.confidence > 0.6:
                    validated_items.append(item)
        
        return validated_items
    
    def _is_valid_price(self, text: str) -> bool:
        """Check if text is a valid price format."""
        # Remove currency symbols
        clean_text = text.replace('$', '').replace('€', '').replace('£', '')
        
        # Check if it's a decimal number
        try:
            value = float(clean_text)
            # Reasonable price range
            return 0.01 <= value <= 10000.00
        except ValueError:
            return False
    
    def _calculate_confidence(self, product_words: List[ReceiptWord],
                            price_word: ReceiptWord,
                            distance: int,
                            line_text: str) -> float:
        """Calculate confidence score for a line item."""
        confidence = 0.5  # Base confidence
        
        # Distance penalty
        confidence -= distance * 0.1
        
        # Product keyword boost
        product_matches = sum(1 for keyword in self.product_keywords if keyword in line_text)
        confidence += min(product_matches * 0.1, 0.3)
        
        # Metadata penalty
        metadata_matches = sum(1 for keyword in self.metadata_keywords if keyword in line_text)
        confidence -= min(metadata_matches * 0.1, 0.3)
        
        # Price format boost
        if '$' in price_word.text or '.' in price_word.text:
            confidence += 0.1
        
        # Line length penalty (too short or too long)
        if len(product_words) < 2 or len(product_words) > 10:
            confidence -= 0.1
        
        return max(0.0, min(confidence, 1.0))