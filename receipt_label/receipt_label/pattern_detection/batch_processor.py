"""
Advanced batch processing for pattern detection optimization.

This module implements Phase 2's batch regex evaluation using alternation
patterns to minimize the number of regex operations per receipt.
"""

import re
import time
from typing import Dict, List, Tuple, Set, Optional, Any
from dataclasses import dataclass
from receipt_label.pattern_detection.patterns_config import PatternConfig
from receipt_label.pattern_detection.pattern_utils import BatchPatternMatcher
from receipt_dynamo.entities import ReceiptWord


@dataclass
class BatchMatchResult:
    """Result from batch pattern matching."""
    word: ReceiptWord
    pattern_name: str
    pattern_category: str  # "currency", "datetime", etc.
    match_object: re.Match
    confidence: float = 0.8  # Default confidence for pattern matches


class AdvancedBatchProcessor:
    """
    Advanced batch processor that combines patterns across detectors.
    
    This implements the core optimization from Phase 2: instead of running
    each detector separately, combine compatible patterns and run them
    together in a single pass.
    """
    
    def __init__(self):
        """Initialize with pre-compiled batch patterns."""
        self._batch_patterns = {}
        self._pattern_metadata = {}
        self._initialize_batch_patterns()
    
    def _initialize_batch_patterns(self) -> None:
        """Pre-compile batch patterns for maximum efficiency."""
        
        # Batch 1: All currency patterns across detectors
        currency_patterns = PatternConfig.CURRENCY_PATTERNS.copy()
        
        # Add quantity patterns that involve currency (for cross-detector matching)
        quantity_patterns = PatternConfig.QUANTITY_PATTERNS
        currency_patterns.update({
            f"qty_{name}": pattern for name, pattern in quantity_patterns.items()
            if "$" in pattern or "currency" in pattern.lower()
        })
        
        self._batch_patterns["currency_and_quantity"], self._pattern_metadata["currency_and_quantity"] = \
            BatchPatternMatcher.combine_patterns(currency_patterns, group_names=True)
        
        # Batch 2: All datetime patterns  
        datetime_patterns = PatternConfig.DATETIME_PATTERNS
        self._batch_patterns["datetime"], self._pattern_metadata["datetime"] = \
            BatchPatternMatcher.combine_patterns(datetime_patterns, group_names=True)
        
        # Batch 3: Contact patterns
        contact_patterns = PatternConfig.get_contact_patterns()
        contact_pattern_strings = {name: pattern.pattern for name, pattern in contact_patterns.items()}
        self._batch_patterns["contact"], self._pattern_metadata["contact"] = \
            BatchPatternMatcher.combine_patterns(contact_pattern_strings, group_names=True)
        
        # Batch 4: Pure quantity patterns (non-currency)
        pure_quantity_patterns = {
            name: pattern for name, pattern in quantity_patterns.items()
            if "$" not in pattern and "currency" not in pattern.lower()
        }
        self._batch_patterns["quantity"], self._pattern_metadata["quantity"] = \
            BatchPatternMatcher.combine_patterns(pure_quantity_patterns, group_names=True)
    
    def process_words_batch(self, words: List[ReceiptWord], 
                          categories: Optional[List[str]] = None) -> Dict[str, List[BatchMatchResult]]:
        """
        Process all words using batch patterns for maximum efficiency.
        
        Args:
            words: Words to process
            categories: Specific pattern categories to run (None = all)
            
        Returns:
            Dictionary mapping category -> list of batch match results
        """
        if categories is None:
            categories = list(self._batch_patterns.keys())
        
        results = {category: [] for category in categories}
        
        # Extract text for batch processing
        word_texts = [word.text for word in words if not word.is_noise]
        word_map = {word.text: word for word in words if not word.is_noise}
        
        # Process each batch category
        for category in categories:
            if category not in self._batch_patterns:
                continue
                
            batch_pattern = self._batch_patterns[category]
            pattern_metadata = self._pattern_metadata[category]
            
            # Run batch matching
            category_results = BatchPatternMatcher.batch_match_text(
                batch_pattern, pattern_metadata, word_texts
            )
            
            # Convert to BatchMatchResult objects
            for pattern_name, text_matches in category_results.items():
                for text, match in text_matches:
                    word = word_map.get(text)
                    if word:
                        result = BatchMatchResult(
                            word=word,
                            pattern_name=pattern_name,
                            pattern_category=category,
                            match_object=match,
                            confidence=self._calculate_batch_confidence(pattern_name, category, match)
                        )
                        results[category].append(result)
        
        return results
    
    def _calculate_batch_confidence(self, pattern_name: str, category: str, match: re.Match) -> float:
        """Calculate confidence for batch pattern matches."""
        base_confidence = 0.7  # Lower than individual detector confidence
        
        # Boost confidence for specific pattern types
        if category == "currency_and_quantity":
            if "symbol_prefix" in pattern_name or "symbol_suffix" in pattern_name:
                return 0.9  # Currency symbols are highly reliable
            elif "qty_" in pattern_name:
                return 0.8  # Quantity patterns are reliable
        
        elif category == "datetime":
            if "iso" in pattern_name:
                return 0.95  # ISO dates are very reliable
            elif "mdy" in pattern_name or "ymd" in pattern_name:
                return 0.85  # Common date formats
        
        elif category == "contact":
            if "email" in pattern_name:
                return 0.9  # Email patterns are reliable
            elif "us_standard" in pattern_name:
                return 0.8  # Standard phone numbers
        
        return base_confidence
    
    def get_processing_stats(self) -> Dict[str, Any]:
        """Get statistics about batch processing efficiency."""
        return {
            "batch_categories": len(self._batch_patterns),
            "total_patterns": sum(len(metadata) for metadata in self._pattern_metadata.values()),
            "categories": list(self._batch_patterns.keys()),
            "pattern_counts": {
                category: len(metadata) 
                for category, metadata in self._pattern_metadata.items()
            }
        }


class HybridBatchDetector:
    """
    Hybrid detector that uses batch processing for compatible patterns
    and individual detection for complex cases.
    
    This provides a migration path from individual detectors to batch processing.
    """
    
    def __init__(self, use_batch_optimization: bool = True):
        """
        Initialize hybrid detector.
        
        Args:
            use_batch_optimization: Whether to use batch processing (Phase 2 optimization)
        """
        self.use_batch_optimization = use_batch_optimization
        self.batch_processor = AdvancedBatchProcessor() if use_batch_optimization else None
        self._individual_detectors = {}
        
        # Fallback to individual detectors when needed
        if not use_batch_optimization:
            self._initialize_individual_detectors()
    
    def _initialize_individual_detectors(self) -> None:
        """Initialize individual detectors for fallback mode."""
        from receipt_label.pattern_detection.currency import CurrencyPatternDetector
        from receipt_label.pattern_detection.contact import ContactPatternDetector
        from receipt_label.pattern_detection.datetime_patterns import DateTimePatternDetector
        from receipt_label.pattern_detection.quantity import QuantityPatternDetector
        
        self._individual_detectors = {
            "currency": CurrencyPatternDetector(),
            "contact": ContactPatternDetector(),
            "datetime": DateTimePatternDetector(),
            "quantity": QuantityPatternDetector(),
        }
    
    async def detect_patterns_hybrid(self, words: List[ReceiptWord]) -> Dict[str, List[Any]]:
        """
        Detect patterns using hybrid approach: batch for simple patterns,
        individual detectors for complex analysis.
        
        Args:
            words: Words to analyze
            
        Returns:
            Dictionary mapping detector_name -> pattern matches
        """
        start_time = time.time()
        
        if self.use_batch_optimization and self.batch_processor:
            # Use batch processing for most patterns
            batch_results = self.batch_processor.process_words_batch(words)
            
            # Convert batch results to detector format
            detector_results = self._convert_batch_to_detector_format(batch_results)
            
            # Run complex individual analysis only where needed
            detector_results = await self._enhance_with_individual_analysis(
                words, detector_results
            )
        else:
            # Fallback to individual detectors
            detector_results = await self._run_individual_detectors(words)
        
        # Add performance metadata
        detector_results["_metadata"] = {
            "processing_time_ms": (time.time() - start_time) * 1000,
            "batch_optimization": self.use_batch_optimization,
            "word_count": len(words)
        }
        
        return detector_results
    
    def _convert_batch_to_detector_format(self, batch_results: Dict[str, List[BatchMatchResult]]) -> Dict[str, List]:
        """Convert batch results to the format expected by individual detectors."""
        from receipt_label.pattern_detection.base import PatternMatch, PatternType
        
        detector_results = {
            "currency": [],
            "contact": [],
            "datetime": [],
            "quantity": []
        }
        
        # Map batch categories to detector names
        category_mapping = {
            "currency_and_quantity": ["currency", "quantity"],
            "datetime": ["datetime"],
            "contact": ["contact"],
            "quantity": ["quantity"]
        }
        
        for category, batch_matches in batch_results.items():
            target_detectors = category_mapping.get(category, [category])
            
            for batch_match in batch_matches:
                # Determine appropriate pattern type
                pattern_type = self._map_pattern_to_type(
                    batch_match.pattern_name, batch_match.pattern_category
                )
                
                # Create PatternMatch object
                pattern_match = PatternMatch(
                    word=batch_match.word,
                    pattern_type=pattern_type,
                    confidence=batch_match.confidence,
                    matched_text=batch_match.match_object.group(0),
                    extracted_value=self._extract_value_from_match(batch_match),
                    metadata={
                        "batch_processed": True,
                        "pattern_name": batch_match.pattern_name,
                        "pattern_category": batch_match.pattern_category
                    }
                )
                
                # Add to appropriate detector results
                for detector_name in target_detectors:
                    if self._match_belongs_to_detector(batch_match.pattern_name, detector_name):
                        detector_results[detector_name].append(pattern_match)
        
        return detector_results
    
    def _map_pattern_to_type(self, pattern_name: str, category: str) -> 'PatternType':
        """Map batch pattern names to PatternType enum values."""
        from receipt_label.pattern_detection.base import PatternType
        
        # Currency patterns
        if "symbol_prefix" in pattern_name or "symbol_suffix" in pattern_name or "plain_number" in pattern_name:
            return PatternType.CURRENCY
        elif "negative" in pattern_name:
            return PatternType.DISCOUNT
        
        # Quantity patterns  
        elif "qty_" in pattern_name or "quantity" in pattern_name:
            if "at" in pattern_name:
                return PatternType.QUANTITY_AT
            elif "times" in pattern_name or "x" in pattern_name:
                return PatternType.QUANTITY_TIMES
            elif "for" in pattern_name:
                return PatternType.QUANTITY_FOR
            else:
                return PatternType.QUANTITY
        
        # DateTime patterns
        elif category == "datetime":
            if "time" in pattern_name:
                return PatternType.TIME
            else:
                return PatternType.DATE
        
        # Contact patterns
        elif "email" in pattern_name:
            return PatternType.EMAIL
        elif "phone" in pattern_name or "us_" in pattern_name:
            return PatternType.PHONE_NUMBER
        elif "website" in pattern_name:
            return PatternType.WEBSITE
        
        # Default fallback
        return PatternType.CURRENCY
    
    def _match_belongs_to_detector(self, pattern_name: str, detector_name: str) -> bool:
        """Determine if a pattern match belongs to a specific detector."""
        if detector_name == "currency":
            return any(keyword in pattern_name for keyword in 
                      ["symbol_prefix", "symbol_suffix", "plain_number", "negative"])
        elif detector_name == "quantity":
            return "qty_" in pattern_name or "quantity" in pattern_name
        elif detector_name == "datetime":
            return any(keyword in pattern_name for keyword in ["mdy", "ymd", "dmy", "time", "iso"])
        elif detector_name == "contact":
            return any(keyword in pattern_name for keyword in ["email", "phone", "website", "us_"])
        
        return False
    
    def _extract_value_from_match(self, batch_match: BatchMatchResult) -> Any:
        """Extract meaningful value from pattern match."""
        match = batch_match.match_object
        pattern_name = batch_match.pattern_name
        
        # For currency patterns, extract numeric value
        if any(keyword in pattern_name for keyword in ["symbol", "currency", "number"]):
            # Find numeric groups in the match
            for group in match.groups():
                if group and re.search(r'\d+(?:\.\d+)?', group):
                    try:
                        return float(re.sub(r'[^\d.]', '', group))
                    except ValueError:
                        pass
        
        # For datetime patterns, return the full match
        elif "time" in pattern_name or "date" in pattern_name:
            return match.group(0)
        
        # Default: return full matched text
        return match.group(0)
    
    async def _enhance_with_individual_analysis(self, words: List[ReceiptWord], 
                                              batch_results: Dict[str, List]) -> Dict[str, List]:
        """
        Enhance batch results with individual detector analysis for complex cases.
        
        This handles contextual analysis that batch processing cannot perform.
        """
        # For now, return batch results as-is
        # Future enhancement: Run individual detectors for context analysis
        # (position-based classification, keyword context, etc.)
        return batch_results
    
    async def _run_individual_detectors(self, words: List[ReceiptWord]) -> Dict[str, List]:
        """Fallback: run individual detectors in the traditional way."""
        results = {}
        
        for name, detector in self._individual_detectors.items():
            try:
                results[name] = await detector.detect(words)
            except Exception as e:
                print(f"Error in {name} detector: {e}")
                results[name] = []
        
        return results


# Global instance for easy access
BATCH_PROCESSOR = AdvancedBatchProcessor()