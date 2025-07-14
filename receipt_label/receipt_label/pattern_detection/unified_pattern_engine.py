"""
Unified Pattern Engine that integrates all Phase 3 enhancements.

This module combines:
1. Trie-based multi-word pattern detection
2. Optimized keyword lookups with pattern automaton  
3. Merchant-specific pattern integration
4. Dynamic pattern learning and caching

This represents the culmination of Phase 3 optimizations.
"""

import time
from typing import Dict, List, Set, Tuple, Optional, Any, Union
from dataclasses import dataclass, field
from collections import defaultdict
import json

from receipt_label.pattern_detection.trie_matcher import (
    MultiWordPatternDetector, MultiWordMatch, MatchType
)
from receipt_label.pattern_detection.automaton_matcher import (
    OptimizedKeywordMatcher, AutomatonMatch
)
from receipt_label.pattern_detection.base import PatternType, PatternMatch
from receipt_label.pattern_detection.patterns_config import PatternConfig
from receipt_dynamo.entities import ReceiptWord


@dataclass
class UnifiedMatch:
    """Unified representation of all pattern match types."""
    words: List[ReceiptWord]
    pattern_type: PatternType
    confidence: float
    matched_text: str
    extracted_value: Optional[Any] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    # Source information
    source_engine: str = "unified"  # "trie", "automaton", "merchant", "hybrid"
    is_multi_word: bool = False
    is_merchant_specific: bool = False


@dataclass
class MerchantPatternSet:
    """Collection of patterns for a specific merchant."""
    merchant_name: str
    product_names: Set[str] = field(default_factory=set)
    transaction_patterns: Dict[str, str] = field(default_factory=dict)  # pattern -> description
    keyword_categories: Dict[str, Set[str]] = field(default_factory=dict)  # category -> keywords
    confidence_boost: float = 0.1  # Boost for merchant-specific matches
    last_updated: Optional[str] = None


class MerchantPatternManager:
    """Manages merchant-specific patterns and their integration."""
    
    def __init__(self):
        self.merchant_patterns: Dict[str, MerchantPatternSet] = {}
        self._pattern_cache: Dict[str, List[str]] = {}  # merchant -> compiled patterns
        
        # Initialize with common merchant patterns
        self._initialize_common_merchants()
    
    def _initialize_common_merchants(self):
        """Initialize patterns for well-known merchants."""
        
        # McDonald's patterns
        mcdonalds = MerchantPatternSet(
            merchant_name="McDonald's",
            product_names={
                "big mac", "quarter pounder", "mc nuggets", "mc chicken",
                "apple pie", "hash browns", "egg mcmuffin", "mc flurry",
                "happy meal", "chicken sandwich", "fish filet"
            },
            transaction_patterns={
                r"order\s*#?\s*\d+": "order_number",
                r"store\s*#?\s*\d+": "store_number", 
                r"register\s*#?\s*\d+": "register_number"
            },
            keyword_categories={
                "meal": {"combo", "meal", "happy meal", "value meal"},
                "size": {"small", "medium", "large", "super size"}
            }
        )
        self.add_merchant_patterns(mcdonalds)
        
        # Walmart patterns
        walmart = MerchantPatternSet(
            merchant_name="Walmart",
            transaction_patterns={
                r"tc#?\s*\d+": "transaction_code",
                r"st#?\s*\d+": "store_number",
                r"op#?\s*\d+": "operator_id",
                r"te#?\s*\d+": "terminal_id"
            },
            keyword_categories={
                "department": {"grocery", "electronics", "clothing", "pharmacy"},
                "program": {"great value", "equate", "mainstays"}
            }
        )
        self.add_merchant_patterns(walmart)
        
        # Target patterns
        target = MerchantPatternSet(
            merchant_name="Target",
            transaction_patterns={
                r"ref#?\s*\d+": "reference_number",
                r"store\s*\d+": "store_number"
            },
            keyword_categories={
                "brand": {"up & up", "good & gather", "cat & jack", "all in motion"},
                "program": {"target circle", "redcard"}
            }
        )
        self.add_merchant_patterns(target)
    
    def add_merchant_patterns(self, merchant_set: MerchantPatternSet):
        """Add or update patterns for a merchant."""
        self.merchant_patterns[merchant_set.merchant_name.lower()] = merchant_set
        
        # Clear cache for this merchant
        if merchant_set.merchant_name.lower() in self._pattern_cache:
            del self._pattern_cache[merchant_set.merchant_name.lower()]
    
    def get_merchant_patterns(self, merchant_name: str) -> Optional[MerchantPatternSet]:
        """Get patterns for a specific merchant."""
        return self.merchant_patterns.get(merchant_name.lower())
    
    def detect_merchant_patterns(self, merchant_name: str, words: List[ReceiptWord]) -> List[UnifiedMatch]:
        """Detect patterns specific to a merchant."""
        merchant_patterns = self.get_merchant_patterns(merchant_name)
        if not merchant_patterns:
            return []
        
        matches = []
        word_texts = [w.text for w in words if not w.is_noise]
        combined_text = " ".join(word_texts)
        
        # Detect product names using trie matching
        product_matches = self._detect_merchant_products(
            merchant_patterns, words, combined_text
        )
        matches.extend(product_matches)
        
        # Detect transaction patterns using regex
        transaction_matches = self._detect_transaction_patterns(
            merchant_patterns, words, combined_text
        )
        matches.extend(transaction_matches)
        
        return matches
    
    def _detect_merchant_products(self, merchant_patterns: MerchantPatternSet, 
                                words: List[ReceiptWord], text: str) -> List[UnifiedMatch]:
        """Detect merchant-specific product names."""
        matches = []
        
        # Simple substring matching for now (could be enhanced with trie)
        for product_name in merchant_patterns.product_names:
            if product_name.lower() in text.lower():
                # Find the specific words that match
                matching_words = self._find_matching_words(words, product_name)
                if matching_words:
                    match = UnifiedMatch(
                        words=matching_words,
                        pattern_type=PatternType.PRODUCT_NAME,
                        confidence=0.9 + merchant_patterns.confidence_boost,
                        matched_text=product_name,
                        extracted_value=product_name,
                        metadata={
                            "merchant": merchant_patterns.merchant_name,
                            "pattern_source": "merchant_products"
                        },
                        source_engine="merchant",
                        is_multi_word=len(product_name.split()) > 1,
                        is_merchant_specific=True
                    )
                    matches.append(match)
        
        return matches
    
    def _detect_transaction_patterns(self, merchant_patterns: MerchantPatternSet,
                                   words: List[ReceiptWord], text: str) -> List[UnifiedMatch]:
        """Detect merchant-specific transaction patterns."""
        import re
        matches = []
        
        for pattern, description in merchant_patterns.transaction_patterns.items():
            for match in re.finditer(pattern, text, re.IGNORECASE):
                # Find the specific words that match
                matched_text = match.group(0)
                matching_words = self._find_matching_words(words, matched_text)
                
                if matching_words:
                    unified_match = UnifiedMatch(
                        words=matching_words,
                        pattern_type=PatternType.MERCHANT_NAME,  # Could be more specific
                        confidence=0.85 + merchant_patterns.confidence_boost,
                        matched_text=matched_text,
                        extracted_value=matched_text,
                        metadata={
                            "merchant": merchant_patterns.merchant_name,
                            "pattern_type": description,
                            "pattern_source": "transaction_patterns"
                        },
                        source_engine="merchant",
                        is_multi_word=len(matched_text.split()) > 1,
                        is_merchant_specific=True
                    )
                    matches.append(unified_match)
        
        return matches
    
    def _find_matching_words(self, words: List[ReceiptWord], pattern: str) -> List[ReceiptWord]:
        """Find the specific words that match a pattern."""
        pattern_lower = pattern.lower()
        matching_words = []
        
        # Simple approach: find consecutive words that form the pattern
        for i, word in enumerate(words):
            if word.is_noise:
                continue
                
            # Try to match starting from this word
            candidate_words = []
            candidate_text = ""
            
            for j in range(i, min(i + len(pattern.split()) + 2, len(words))):
                if not words[j].is_noise:
                    candidate_words.append(words[j])
                    candidate_text += " " + words[j].text.lower()
                    
                    if pattern_lower in candidate_text.strip():
                        return candidate_words
        
        return matching_words


class UnifiedPatternEngine:
    """
    Unified pattern engine that combines all Phase 3 enhancements
    into a single, cohesive pattern detection system.
    """
    
    def __init__(self):
        # Core engines
        self.multi_word_detector = MultiWordPatternDetector()
        self.keyword_matcher = OptimizedKeywordMatcher()
        self.merchant_manager = MerchantPatternManager()
        
        # Performance tracking
        self.performance_stats = {
            "total_detections": 0,
            "multi_word_matches": 0,
            "keyword_matches": 0, 
            "merchant_matches": 0,
            "avg_processing_time_ms": 0.0
        }
    
    async def detect_all_patterns(self, words: List[ReceiptWord], 
                                merchant_name: Optional[str] = None) -> Dict[str, List[UnifiedMatch]]:
        """
        Detect all patterns using the unified engine.
        
        Args:
            words: Receipt words to analyze
            merchant_name: Optional merchant name for merchant-specific patterns
            
        Returns:
            Dictionary mapping pattern categories to unified matches
        """
        start_time = time.time()
        
        # Combine text for keyword/phrase analysis
        clean_words = [w for w in words if not w.is_noise]
        combined_text = " ".join(w.text for w in clean_words)
        
        all_matches = []
        
        # 1. Multi-word pattern detection (trie-based)
        multi_word_matches = self.multi_word_detector.detect_multi_word_patterns(words)
        unified_multi_word = self._convert_multi_word_matches(multi_word_matches)
        all_matches.extend(unified_multi_word)
        self.performance_stats["multi_word_matches"] += len(unified_multi_word)
        
        # 2. Keyword/phrase detection (automaton-based)
        keyword_matches = self.keyword_matcher.find_all_matches(combined_text)
        unified_keywords = self._convert_keyword_matches(keyword_matches, words)
        all_matches.extend(unified_keywords)
        self.performance_stats["keyword_matches"] += len(unified_keywords)
        
        # 3. Merchant-specific pattern detection
        if merchant_name:
            merchant_matches = self.merchant_manager.detect_merchant_patterns(merchant_name, words)
            all_matches.extend(merchant_matches)
            self.performance_stats["merchant_matches"] += len(merchant_matches)
        
        # 4. Resolve conflicts and optimize matches
        final_matches = self._resolve_and_optimize_matches(all_matches)
        
        # 5. Group by pattern type for compatibility
        grouped_matches = self._group_matches_by_type(final_matches)
        
        # Update performance stats
        processing_time = (time.time() - start_time) * 1000
        self.performance_stats["total_detections"] += 1
        self.performance_stats["avg_processing_time_ms"] = (
            (self.performance_stats["avg_processing_time_ms"] * (self.performance_stats["total_detections"] - 1) +
             processing_time) / self.performance_stats["total_detections"]
        )
        
        # Add metadata
        grouped_matches["_metadata"] = {
            "processing_time_ms": processing_time,
            "total_matches": len(final_matches),
            "engines_used": ["trie", "automaton"] + (["merchant"] if merchant_name else []),
            "performance_stats": self.performance_stats.copy()
        }
        
        return grouped_matches
    
    def _convert_multi_word_matches(self, multi_word_matches: List[MultiWordMatch]) -> List[UnifiedMatch]:
        """Convert MultiWordMatch objects to UnifiedMatch objects."""
        unified_matches = []
        
        for match in multi_word_matches:
            unified = UnifiedMatch(
                words=match.words,
                pattern_type=match.pattern_type,
                confidence=match.confidence,
                matched_text=" ".join(w.text for w in match.words),
                extracted_value=match.pattern,
                metadata={
                    "algorithm": "aho_corasick",
                    "match_type": match.match_type.value,
                    "original_pattern": match.pattern,
                    **match.metadata
                },
                source_engine="trie",
                is_multi_word=True,
                is_merchant_specific=False
            )
            unified_matches.append(unified)
        
        return unified_matches
    
    def _convert_keyword_matches(self, keyword_matches: List[AutomatonMatch], 
                               words: List[ReceiptWord]) -> List[UnifiedMatch]:
        """Convert AutomatonMatch objects to UnifiedMatch objects."""
        unified_matches = []
        
        for match in keyword_matches:
            # Find the corresponding words
            matching_words = self._find_words_for_text_range(words, match.text, match.start_pos, match.end_pos)
            
            # Map category to PatternType
            pattern_type = self._map_category_to_pattern_type(match.category)
            
            unified = UnifiedMatch(
                words=matching_words,
                pattern_type=pattern_type,
                confidence=match.confidence,
                matched_text=match.text,
                extracted_value=match.pattern,
                metadata={
                    "algorithm": "automaton",
                    "automaton_type": match.automaton_type.value,
                    "category": match.category,
                    "text_position": (match.start_pos, match.end_pos)
                },
                source_engine="automaton",
                is_multi_word=len(match.text.split()) > 1,
                is_merchant_specific=False
            )
            unified_matches.append(unified)
        
        return unified_matches
    
    def _find_words_for_text_range(self, words: List[ReceiptWord], 
                                 matched_text: str, start_pos: int, end_pos: int) -> List[ReceiptWord]:
        """Find the ReceiptWord objects corresponding to a text range."""
        # This is a simplified approach - in practice, you'd need to track
        # character positions more precisely
        matched_words = []
        text_words = matched_text.lower().split()
        
        for word in words:
            if word.is_noise:
                continue
            if word.text.lower() in text_words:
                matched_words.append(word)
                if len(matched_words) >= len(text_words):
                    break
        
        return matched_words
    
    def _map_category_to_pattern_type(self, category: str) -> PatternType:
        """Map keyword category to PatternType enum."""
        mapping = {
            "total": PatternType.GRAND_TOTAL,
            "subtotal": PatternType.SUBTOTAL,
            "tax": PatternType.TAX,
            "discount": PatternType.DISCOUNT,
            "currency": PatternType.CURRENCY,
        }
        return mapping.get(category, PatternType.CURRENCY)
    
    def _resolve_and_optimize_matches(self, matches: List[UnifiedMatch]) -> List[UnifiedMatch]:
        """Resolve conflicts and optimize matches across all engines."""
        if not matches:
            return matches
        
        # Sort by priority: merchant > trie > automaton, then by confidence
        priority_order = {"merchant": 0, "trie": 1, "automaton": 2}
        
        matches.sort(key=lambda m: (
            priority_order.get(m.source_engine, 3),
            -m.confidence,
            -len(m.words)  # Prefer longer matches
        ))
        
        # Remove overlapping matches
        final_matches = []
        used_word_ids = set()
        
        for match in matches:
            # Check if any words in this match are already used
            match_word_ids = {w.word_id for w in match.words}
            
            if not match_word_ids.intersection(used_word_ids):
                final_matches.append(match)
                used_word_ids.update(match_word_ids)
        
        return final_matches
    
    def _group_matches_by_type(self, matches: List[UnifiedMatch]) -> Dict[str, List[UnifiedMatch]]:
        """Group matches by pattern type for compatibility with existing code."""
        grouped = defaultdict(list)
        
        for match in matches:
            # Map PatternType to string categories
            if match.pattern_type in [PatternType.GRAND_TOTAL, PatternType.SUBTOTAL, 
                                    PatternType.TAX, PatternType.DISCOUNT, PatternType.CURRENCY,
                                    PatternType.UNIT_PRICE, PatternType.LINE_TOTAL]:
                grouped["currency"].append(match)
            elif match.pattern_type in [PatternType.DATE, PatternType.TIME, PatternType.DATETIME]:
                grouped["datetime"].append(match)
            elif match.pattern_type in [PatternType.PHONE_NUMBER, PatternType.EMAIL, PatternType.WEBSITE]:
                grouped["contact"].append(match)
            elif match.pattern_type in [PatternType.QUANTITY, PatternType.QUANTITY_AT, 
                                      PatternType.QUANTITY_TIMES, PatternType.QUANTITY_FOR]:
                grouped["quantity"].append(match)
            elif match.pattern_type in [PatternType.MERCHANT_NAME, PatternType.PRODUCT_NAME]:
                grouped["merchant"].append(match)
            else:
                grouped["other"].append(match)
        
        return dict(grouped)
    
    def add_merchant_patterns(self, merchant_name: str, patterns: Dict[str, Any]):
        """Add patterns for a new merchant."""
        merchant_set = MerchantPatternSet(merchant_name=merchant_name)
        
        if "product_names" in patterns:
            merchant_set.product_names.update(patterns["product_names"])
        
        if "transaction_patterns" in patterns:
            merchant_set.transaction_patterns.update(patterns["transaction_patterns"])
        
        if "keyword_categories" in patterns:
            merchant_set.keyword_categories.update(patterns["keyword_categories"])
        
        self.merchant_manager.add_merchant_patterns(merchant_set)
        
        # Also add to keyword matcher for cross-engine optimization
        if "keyword_categories" in patterns:
            self.keyword_matcher.add_merchant_keywords(merchant_name, patterns["keyword_categories"])
    
    def get_comprehensive_stats(self) -> Dict[str, Any]:
        """Get comprehensive statistics about the unified engine."""
        trie_stats = self.multi_word_detector.get_statistics()
        automaton_stats = self.keyword_matcher.get_statistics()
        
        return {
            "unified_engine": {
                "performance": self.performance_stats,
                "engines": {
                    "trie_based": trie_stats,
                    "automaton_based": automaton_stats,
                    "merchant_patterns": len(self.merchant_manager.merchant_patterns)
                },
                "capabilities": [
                    "multi_word_detection",
                    "fuzzy_matching",
                    "merchant_specific_patterns",
                    "keyword_optimization",
                    "conflict_resolution",
                    "performance_monitoring"
                ]
            }
        }


# Global unified pattern engine instance
UNIFIED_PATTERN_ENGINE = UnifiedPatternEngine()