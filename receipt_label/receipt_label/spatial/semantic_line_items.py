"""
Semantic-aware line item detection using Pinecone embeddings to identify real prices.

This module uses Pinecone vector search to distinguish actual prices from other numeric patterns,
then applies spatial reasoning to match products with their semantically-validated prices.
"""

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from receipt_dynamo.entities.receipt_word import ReceiptWord
from receipt_label.pattern_detection.base import PatternMatch, PatternType
from receipt_label.utils.client_manager import ClientManager, ClientConfig

logger = logging.getLogger(__name__)


@dataclass
class SemanticPrice:
    """A semantically-validated price from Pinecone."""
    word: ReceiptWord
    confidence: float
    pinecone_score: float
    label: str
    metadata: Dict


class SemanticPriceValidator:
    """Validates currency patterns using Pinecone semantic search."""
    
    def __init__(self):
        """Initialize with client manager."""
        self.client_manager = ClientManager(ClientConfig.from_env())
        self.pinecone_index = self.client_manager.pinecone
        self.logger = logger
        
    def validate_currency_patterns(self, 
                                 currency_words: Set[ReceiptWord],
                                 receipt_id: str) -> List[SemanticPrice]:
        """
        Validate currency patterns using Pinecone to find real prices.
        
        Args:
            currency_words: Words detected as currency by pattern matching
            receipt_id: Receipt ID for namespace filtering
            
        Returns:
            List of semantically validated prices
        """
        if not currency_words:
            return []
            
        validated_prices = []
        
        # Query each currency word in Pinecone
        for word in currency_words:
            try:
                # Build the vector ID matching the format from word embedding
                # Format: IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#WORD#{word_id:05d}
                # In Pinecone, image_id is actually the receipt UUID
                vector_id = f"IMAGE#{receipt_id}#RECEIPT#{word.receipt_id:05d}#LINE#{word.line_id:05d}#WORD#{word.word_id:05d}"
                
                # Fetch the vector to get its embedding and metadata
                fetch_response = self.pinecone_index.fetch(
                    ids=[vector_id],
                    namespace="words"
                )
                
                if vector_id not in fetch_response.vectors:
                    self.logger.info(f"Vector not found for word '{word.text}' (ID: {vector_id})")
                    continue
                
                vector_data = fetch_response.vectors[vector_id]
                
                # Check if this word has a price-related label
                metadata = vector_data.metadata
                valid_labels = metadata.get("valid_labels", [])
                proposed_label = metadata.get("proposed_label", "")
                
                # Price-related labels we're looking for
                price_labels = {
                    "AMOUNT", "PRICE", "UNIT_PRICE", "LINE_TOTAL", 
                    "SUBTOTAL", "GRAND_TOTAL", "TAX_AMOUNT", "DISCOUNT_AMOUNT",
                    "TOTAL_AMOUNT", "ITEM_PRICE", "PRODUCT_PRICE"
                }
                
                # Check if any valid label is a price label
                matching_labels = [label for label in valid_labels if label in price_labels]
                if not matching_labels and proposed_label in price_labels:
                    matching_labels = [proposed_label]
                
                if matching_labels:
                    # This is a real price!
                    validated_prices.append(SemanticPrice(
                        word=word,
                        confidence=metadata.get("confidence", 0.0),
                        pinecone_score=1.0,  # Direct label match
                        label=matching_labels[0],
                        metadata=metadata
                    ))
                    self.logger.info(f"Validated price: '{word.text}' as {matching_labels[0]} (conf: {metadata.get('confidence', 0):.2f})")
                else:
                    self.logger.info(f"Found word '{word.text}' with labels {valid_labels} (proposed: {proposed_label}) - not a price label")
                    # Query for similar price vectors
                    embedding = vector_data.values
                    
                    # Search for similar vectors that ARE prices
                    query_response = self.pinecone_index.query(
                        namespace="words",
                        vector=embedding,
                        top_k=5,
                        include_metadata=True,
                        filter={
                            "label": {"$in": list(price_labels)}
                        }
                    )
                    
                    if query_response.matches and query_response.matches[0].score > 0.85:
                        # High similarity to known prices
                        best_match = query_response.matches[0]
                        validated_prices.append(SemanticPrice(
                            word=word,
                            confidence=best_match.score,
                            pinecone_score=best_match.score,
                            label=best_match.metadata.get("label", "AMOUNT"),
                            metadata=best_match.metadata
                        ))
                        self.logger.info(f"Validated price by similarity: '{word.text}' (score: {best_match.score:.2f})")
                    else:
                        self.logger.debug(f"Rejected non-price: '{word.text}' (label: {label})")
                        
            except Exception as e:
                self.logger.error(f"Error validating word '{word.text}': {e}")
                continue
                
        return validated_prices
    
    def find_product_candidates(self, 
                              words: List[ReceiptWord],
                              receipt_id: str) -> List[ReceiptWord]:
        """
        Find words that are likely product names using Pinecone.
        
        Args:
            words: All words from receipt
            receipt_id: Receipt ID for namespace filtering
            
        Returns:
            List of words likely to be product names
        """
        product_candidates = []
        
        # Product-related labels
        product_labels = {
            "ITEM_DESCRIPTION", "PRODUCT_NAME", "ITEM_NAME", 
            "DESCRIPTION", "PRODUCT_DESCRIPTION"
        }
        
        for word in words:
            # Skip obvious non-products
            if len(word.text) < 3 or word.text.isdigit():
                continue
                
            try:
                # Same format as above
                vector_id = f"IMAGE#{receipt_id}#RECEIPT#{word.receipt_id:05d}#LINE#{word.line_id:05d}#WORD#{word.word_id:05d}"
                
                fetch_response = self.pinecone_index.fetch(
                    ids=[vector_id],
                    namespace="words"
                )
                
                if vector_id in fetch_response.vectors:
                    metadata = fetch_response.vectors[vector_id].metadata
                    valid_labels = metadata.get("valid_labels", [])
                    proposed_label = metadata.get("proposed_label", "")
                    
                    # Check if any valid label is a product label
                    matching_labels = [label for label in valid_labels if label in product_labels]
                    if not matching_labels and proposed_label in product_labels:
                        matching_labels = [proposed_label]
                    
                    if matching_labels:
                        product_candidates.append(word)
                        self.logger.info(f"Found product: '{word.text}' labeled as {matching_labels[0]}")
                        
            except Exception as e:
                self.logger.error(f"Error checking product word '{word.text}': {e}")
                
        return product_candidates


class SemanticLineItemDetector:
    """Line item detection using semantic validation of prices."""
    
    def __init__(self):
        self.price_validator = SemanticPriceValidator()
        self.logger = logger
        
    def detect_line_items(self,
                         words: List[ReceiptWord],
                         pattern_matches: List[PatternMatch],
                         receipt_id: str) -> Dict:
        """
        Detect line items using semantic price validation.
        
        Args:
            words: All receipt words
            pattern_matches: Pattern detection results
            receipt_id: Receipt ID for Pinecone queries
            
        Returns:
            Dictionary with line item detection results
        """
        self.logger.info(f"Starting semantic line item detection for receipt {receipt_id}")
        
        # Step 1: Extract currency candidates from pattern matches
        currency_candidates = set()
        for match in pattern_matches:
            if match.pattern_type in [PatternType.CURRENCY, PatternType.GRAND_TOTAL,
                                    PatternType.SUBTOTAL, PatternType.TAX,
                                    PatternType.UNIT_PRICE, PatternType.LINE_TOTAL,
                                    PatternType.DISCOUNT]:
                currency_candidates.add(match.word)
                
        self.logger.info(f"Found {len(currency_candidates)} currency candidates from patterns")
        
        # Step 2: Validate prices using Pinecone
        validated_prices = self.price_validator.validate_currency_patterns(
            currency_candidates, receipt_id
        )
        
        self.logger.info(f"Validated {len(validated_prices)} real prices using Pinecone")
        
        # Step 3: Find product candidates
        product_candidates = self.price_validator.find_product_candidates(
            words, receipt_id
        )
        
        self.logger.info(f"Found {len(product_candidates)} product candidates using Pinecone")
        
        # Step 4: Match products with prices using spatial proximity
        line_items = self._match_products_with_prices(
            product_candidates, validated_prices, words
        )
        
        # Step 5: Create result summary
        result = {
            'receipt_id': receipt_id,
            'total_words': len(words),
            'currency_candidates': len(currency_candidates),
            'validated_prices': len(validated_prices),
            'product_candidates': len(product_candidates),
            'line_items_found': len(line_items),
            'line_items': line_items,
            'price_validation_rate': len(validated_prices) / len(currency_candidates) if currency_candidates else 0
        }
        
        return result
        
    def _match_products_with_prices(self,
                                   product_words: List[ReceiptWord],
                                   validated_prices: List[SemanticPrice],
                                   all_words: List[ReceiptWord]) -> List[Dict]:
        """
        Match product words with validated prices using spatial proximity.
        
        This is a simplified version that matches based on line proximity.
        """
        line_items = []
        used_prices = set()
        
        # Group words by line
        words_by_line = {}
        for word in all_words:
            if word.line_id not in words_by_line:
                words_by_line[word.line_id] = []
            words_by_line[word.line_id].append(word)
        
        # For each product, find the closest unused price
        for product_word in product_words:
            product_line = product_word.line_id
            
            # Find closest price (prefer same line, then adjacent lines)
            best_price = None
            min_distance = float('inf')
            
            for price in validated_prices:
                if price.word in used_prices:
                    continue
                    
                price_line = price.word.line_id
                line_distance = abs(price_line - product_line)
                
                # Prefer prices on same or adjacent lines
                if line_distance < min_distance and line_distance <= 3:
                    min_distance = line_distance
                    best_price = price
                    
            if best_price:
                # Get full product text from line
                product_line_words = [w for w in words_by_line.get(product_line, [])
                                    if w.text.isalpha() and len(w.text) > 2]
                product_text = " ".join(w.text for w in product_line_words)
                
                line_items.append({
                    'product_text': product_text,
                    'product_word': product_word.text,
                    'price': best_price.word.text,
                    'price_label': best_price.label,
                    'price_confidence': best_price.confidence,
                    'line_distance': min_distance,
                    'product_line': product_line,
                    'price_line': best_price.word.line_id
                })
                
                used_prices.add(best_price.word)
                
                self.logger.info(f"Matched: '{product_text}' -> {best_price.word.text} ({best_price.label})")
                
        return line_items