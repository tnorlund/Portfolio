"""
Line item detector using Pinecone embeddings for validation.

This approach:
1. Finds all currency values
2. Uses math to find valid combinations that sum to total
3. Validates products using Pinecone embeddings
"""

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple
import asyncio
import os

from receipt_dynamo.entities.receipt_word import ReceiptWord
from receipt_label.pattern_detection.base import PatternMatch

from .vertical_alignment_detector import VerticalAlignmentDetector
from .math_solver_detector import MathSolverDetector
from .sequential_matcher import SequentialMatcher, SequentialLineItem

# Only import Pinecone types during type checking
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from pinecone import Pinecone

logger = logging.getLogger(__name__)


class PineconeLineItemDetector:
    """Detector that uses Pinecone embeddings to validate line items."""
    
    def __init__(self, pinecone_api_key: Optional[str] = None, tolerance: float = 0.01):
        """Initialize the detector."""
        self.tolerance = tolerance
        self.alignment_detector = VerticalAlignmentDetector()
        self.math_solver = MathSolverDetector(tolerance=tolerance)
        self.sequential_matcher = SequentialMatcher()
        self.logger = logger
        
        # Initialize Pinecone
        self.pinecone_client = None
        self.index = None
        if pinecone_api_key or os.getenv("PINECONE_API_KEY"):
            try:
                from pinecone import Pinecone
                pc = Pinecone(
                    api_key=pinecone_api_key or os.getenv("PINECONE_API_KEY")
                )
                self.pinecone_client = pc
                index_name = os.getenv("PINECONE_INDEX_NAME", "receipt-validation-dev")
                host = os.getenv("PINECONE_HOST", "https://receipt-validation-dev-sy23fax.svc.aped-4627-b74a.pinecone.io")
                self.index = pc.Index(index_name, host=host)
                self.logger.info(f"Pinecone initialized successfully with index: {index_name}")
            except Exception as e:
                self.logger.warning(f"Failed to initialize Pinecone: {e}")
        
    async def detect_line_items(self, 
                               words: List[ReceiptWord],
                               pattern_matches: List[PatternMatch]) -> Dict:
        """
        Detect line items using math validation + Pinecone semantic validation.
        
        Args:
            words: All receipt words
            pattern_matches: All pattern matches from detectors
            
        Returns:
            Dictionary with detected line items and metadata
        """
        # Step 1: Extract all currency values (excluding $0.00 and unreasonable values)
        currency_patterns = {
            'CURRENCY', 'GRAND_TOTAL', 'SUBTOTAL', 'TAX', 
            'DISCOUNT', 'UNIT_PRICE', 'LINE_TOTAL'
        }
        currency_values = []
        for match in pattern_matches:
            if match.pattern_type.name in currency_patterns and match.extracted_value:
                try:
                    value = float(match.extracted_value)
                    # Filter out unreasonable values (likely OCR errors)
                    # Reasonable grocery receipt: $0.01 to $999.99
                    if 0.001 <= abs(value) <= 999.99:
                        currency_values.append((value, match))
                    else:
                        self.logger.debug(f"Filtered out unreasonable currency value: ${value:.2f}")
                except (ValueError, TypeError):
                    continue
        
        if not currency_values:
            return self._empty_result()
            
        self.logger.info(f"Found {len(currency_values)} currency values")
        
        # Step 2: Detect price columns from ALL currency values first
        all_price_columns = self.alignment_detector.detect_price_columns(
            [v[1] for v in currency_values]
        )
        
        # Step 3: Filter currency values to only those in price columns
        if all_price_columns:
            # Get line IDs that are in price columns
            column_lines = set()
            for column in all_price_columns:
                column_lines.update(p.word.line_id for p in column.prices)
            
            # Filter to only currencies in price columns
            column_currencies = [
                (value, match) for value, match in currency_values
                if match.word.line_id in column_lines
            ]
            
            self.logger.info(f"Filtered to {len(column_currencies)} currencies in price columns")
            
            if column_currencies:
                currency_values = column_currencies
        
        # Step 4: Find mathematically valid solutions (only from column currencies)
        solutions = self.math_solver.solve_receipt_math(currency_values)
        
        if not solutions:
            self.logger.warning("No valid mathematical solutions found")
            return self._empty_result()
            
        self.logger.info(f"Found {len(solutions)} mathematical solutions")
        
        # Step 5: For each solution, validate using Pinecone
        # Optimize: stop early if we find a good solution to avoid timeout
        best_solution = None
        best_score = 0.0
        solutions_checked = 0
        max_solutions_to_check = 20  # Limit Pinecone queries to prevent timeout
        
        # Sort solutions by confidence to check best ones first
        sorted_solutions = sorted(solutions, key=lambda s: s.confidence, reverse=True)
        
        for solution in sorted_solutions:
            if solutions_checked >= max_solutions_to_check:
                self.logger.info(f"Reached max Pinecone checks limit ({max_solutions_to_check}), stopping")
                break
                
            score = await self._score_solution_with_pinecone(solution, words)
            self.logger.info(f"Solution score: {score:.2f}")
            solutions_checked += 1
            
            if score > best_score:
                best_score = score
                best_solution = solution
                
            # Early termination: if we find a high-confidence solution, use it
            if score > 0.7:  # 70% confidence is pretty good
                self.logger.info(f"Found high-confidence solution (score={score:.2f}), stopping early")
                break
        
        if not best_solution:
            return self._empty_result()
            
        # Step 6: Extract validated prices from best solution
        validated_prices = [(p[0], p[1]) for p in best_solution.item_prices]
        
        # Step 7: Get price columns for the final validated prices
        price_columns = self.alignment_detector.detect_price_columns(
            [p[1] for p in validated_prices]
        )
        
        # Step 8: Match products to prices
        line_items = self.sequential_matcher.match_sequential(
            words, validated_prices
        )
        
        # Step 9: Additional Pinecone validation for product names
        if self.pinecone_client and line_items:
            line_items = await self._validate_products_with_pinecone(line_items)
        
        # Compile results
        result = {
            'line_items': line_items,
            'total_items': len(line_items),
            'subtotal': best_solution.subtotal,
            'tax': best_solution.tax[0] if best_solution.tax else None,
            'grand_total': best_solution.grand_total[0],
            'price_columns': price_columns,
            'math_solutions': len(solutions),
            'pinecone_score': best_score
        }
        
        self.logger.info(f"Detection complete: {result['total_items']} items found")
        return result
        
    async def _score_solution_with_pinecone(self, 
                                          solution: 'MathSolution',
                                          words: List[ReceiptWord]) -> float:
        """Score a mathematical solution using Pinecone embeddings."""
        if not self.pinecone_client:
            return 1.0  # Default score if no Pinecone
            
        # Get words near the prices in this solution
        price_lines = [p[1].word.line_id for p in solution.item_prices]
        
        # Find words on lines above prices (potential products)
        product_lines = []
        for price_line in price_lines:
            # Look up to 5 lines above each price
            for offset in range(1, 6):
                product_line = price_line - offset
                if product_line > 0:
                    product_lines.append(product_line)
        
        product_lines = list(set(product_lines))
        
        # Get words from those lines
        product_words = [w for w in words if w.line_id in product_lines]
        
        if not product_words:
            return 0.5  # Neutral score
            
        # Query Pinecone for these words
        valid_product_count = 0
        total_queries = 0
        
        for word in product_words[:10]:  # Limit queries
            if len(word.text) < 3 or word.text.isdigit():
                continue
                
            vector_id = self._build_vector_id(word)
            try:
                # Query for similar words
                results = self.index.query(
                    namespace="words",
                    id=vector_id,
                    top_k=5,
                    include_metadata=True
                )
                
                # Check if similar words are labeled as products
                if results.matches:
                    product_labels = ['PRODUCT_NAME', 'ITEM_NAME', 'PRODUCT']
                    for match in results.matches:
                        if match.score > 0.8:  # High similarity
                            metadata = match.metadata or {}
                            valid_labels = metadata.get('valid_labels', [])
                            if any(label in valid_labels for label in product_labels):
                                valid_product_count += 1
                                break
                
                total_queries += 1
                
            except Exception as e:
                self.logger.debug(f"Pinecone query failed for {vector_id}: {e}")
                continue
        
        if total_queries == 0:
            return 0.5
            
        # Return ratio of valid products
        return valid_product_count / total_queries
        
    async def _validate_products_with_pinecone(self, 
                                              line_items: List[SequentialLineItem]) -> List[SequentialLineItem]:
        """Validate product names using Pinecone embeddings."""
        if not self.pinecone_client:
            return line_items
            
        validated_items = []
        
        for item in line_items:
            # Skip obvious non-products
            if self._is_obvious_metadata(item.product_text):
                continue
                
            # Query Pinecone for the product text
            is_valid_product = await self._check_if_valid_product(item.product_text)
            
            if is_valid_product:
                validated_items.append(item)
            else:
                self.logger.info(f"Filtered out non-product: {item.product_text}")
        
        return validated_items
        
    async def _check_if_valid_product(self, text: str) -> bool:
        """Check if text is a valid product using Pinecone."""
        if not self.pinecone_client:
            return True
            
        try:
            # For now, use a simple embedding approach
            # In production, this would use actual text embeddings
            results = self.index.query(
                namespace="words",
                vector=[0.0] * 1536,  # Placeholder
                top_k=10,
                filter={
                    "text": {"$eq": text.upper()}
                },
                include_metadata=True
            )
            
            if results.matches:
                for match in results.matches:
                    metadata = match.metadata or {}
                    valid_labels = metadata.get('valid_labels', [])
                    invalid_labels = metadata.get('invalid_labels', [])
                    
                    # Check if mostly labeled as product
                    if 'PRODUCT_NAME' in valid_labels and 'PRODUCT_NAME' not in invalid_labels:
                        return True
                    
                    # If mostly labeled as non-product
                    if any(label in invalid_labels for label in ['MERCHANT_NAME', 'ADDRESS', 'PHONE']):
                        return False
            
            # Default to including if uncertain
            return True
            
        except Exception as e:
            self.logger.debug(f"Pinecone validation failed for '{text}': {e}")
            return True
        
    def _is_obvious_metadata(self, text: str) -> bool:
        """Quick check for obvious metadata."""
        upper_text = text.upper()
        
        # Store names and locations
        if any(word in upper_text for word in ['SPROUTS', 'WALMART', 'TARGET', 'WESTLAKE']):
            return True
            
        # Transaction metadata
        if any(word in upper_text for word in ['AUTH#', 'REF#', 'STORE', 'TID', 'MID']):
            return True
            
        # All digits or single character
        if text.replace('.', '').replace(',', '').isdigit() or len(text) <= 1:
            return True
            
        return False
        
    def _build_vector_id(self, word: ReceiptWord) -> str:
        """Build vector ID for Pinecone query."""
        # Format: IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#WORD#{word_id:05d}
        return f"IMAGE#{word.image_id}#RECEIPT#{word.receipt_id:05d}#LINE#{word.line_id:05d}#WORD#{word.word_id:05d}"
        
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
            'pinecone_score': 0.0
        }