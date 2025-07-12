"""
Semantic mapping layer that bridges pattern detection to business labels.

This module maps low-level pattern types to high-level business-meaningful labels
required by the decision engine for cost optimization.
"""

from typing import Dict, List, Set
from receipt_label.pattern_detection.base import PatternType, PatternMatch
from receipt_label.constants import CORE_LABELS


class SemanticMapper:
    """Maps pattern detection results to essential business labels."""
    
    # Mapping from PatternType to CORE_LABELS
    PATTERN_TO_LABEL_MAP = {
        # Currency patterns
        PatternType.GRAND_TOTAL: CORE_LABELS["GRAND_TOTAL"],
        PatternType.SUBTOTAL: CORE_LABELS["SUBTOTAL"],
        PatternType.TAX: CORE_LABELS["TAX"],
        PatternType.DISCOUNT: CORE_LABELS["DISCOUNT"],
        PatternType.UNIT_PRICE: CORE_LABELS["UNIT_PRICE"],
        PatternType.LINE_TOTAL: CORE_LABELS["LINE_TOTAL"],
        
        # Date/Time patterns
        PatternType.DATE: CORE_LABELS["DATE"],
        PatternType.TIME: CORE_LABELS["TIME"],
        PatternType.DATETIME: CORE_LABELS["DATE"],  # Prefer DATE for business logic
        
        # Contact patterns
        PatternType.PHONE_NUMBER: CORE_LABELS["PHONE_NUMBER"],
        PatternType.EMAIL: CORE_LABELS["EMAIL"],
        PatternType.WEBSITE: CORE_LABELS["WEBSITE"],
        
        # Quantity patterns
        PatternType.QUANTITY: CORE_LABELS["QUANTITY"],
        PatternType.QUANTITY_AT: CORE_LABELS["QUANTITY"],
        PatternType.QUANTITY_TIMES: CORE_LABELS["QUANTITY"],
        PatternType.QUANTITY_FOR: CORE_LABELS["QUANTITY"],
        
        # Business entity patterns
        PatternType.MERCHANT_NAME: CORE_LABELS["MERCHANT_NAME"],
        PatternType.PRODUCT_NAME: CORE_LABELS["PRODUCT_NAME"],
        PatternType.STORE_ADDRESS: CORE_LABELS["ADDRESS_LINE"],
        PatternType.STORE_PHONE: CORE_LABELS["PHONE_NUMBER"],
    }
    
    # Essential labels for business logic (from decision engine)
    CORE_ESSENTIAL_LABELS = {
        CORE_LABELS["MERCHANT_NAME"],
        CORE_LABELS["DATE"],
        CORE_LABELS["GRAND_TOTAL"],
    }
    
    SECONDARY_ESSENTIAL_LABELS = {
        CORE_LABELS["PRODUCT_NAME"],
    }

    def __init__(self):
        """Initialize the semantic mapper."""
        self.stats = {
            "total_patterns": 0,
            "mapped_patterns": 0,
            "essential_labels_found": 0,
        }

    def map_patterns_to_labels(
        self, pattern_matches: List[PatternMatch]
    ) -> Dict[int, Dict]:
        """
        Map pattern matches to semantic labels.
        
        Args:
            pattern_matches: List of pattern matches from detectors
            
        Returns:
            Dictionary mapping word_id to label information
        """
        labeled_words = {}
        self.stats["total_patterns"] = len(pattern_matches)
        
        for match in pattern_matches:
            # Map pattern type to business label
            business_label = self.PATTERN_TO_LABEL_MAP.get(match.pattern_type)
            
            if business_label:
                self.stats["mapped_patterns"] += 1
                
                # Check if this is an essential label
                is_essential = (
                    business_label in self.CORE_ESSENTIAL_LABELS or
                    business_label in self.SECONDARY_ESSENTIAL_LABELS
                )
                
                if is_essential:
                    self.stats["essential_labels_found"] += 1
                
                labeled_words[match.word.word_id] = {
                    "label": business_label,
                    "confidence": match.confidence,
                    "pattern_type": match.pattern_type.name,
                    "extracted_value": match.extracted_value,
                    "is_essential": is_essential,
                    "metadata": match.metadata,
                }
        
        return labeled_words

    def get_essential_fields_status(
        self, labeled_words: Dict[int, Dict]
    ) -> Dict[str, bool]:
        """
        Get status of essential fields for decision engine.
        
        Args:
            labeled_words: Dictionary of labeled words
            
        Returns:
            Dictionary with essential field status
        """
        found_labels = {
            label_info["label"] 
            for label_info in labeled_words.values()
            if label_info.get("is_essential", False)
        }
        
        return {
            "has_merchant": CORE_LABELS["MERCHANT_NAME"] in found_labels,
            "has_date": CORE_LABELS["DATE"] in found_labels,
            "has_total": CORE_LABELS["GRAND_TOTAL"] in found_labels,
            "has_product": CORE_LABELS["PRODUCT_NAME"] in found_labels,
        }

    def consolidate_overlapping_matches(
        self, pattern_matches: List[PatternMatch]
    ) -> List[PatternMatch]:
        """
        Consolidate overlapping pattern matches, keeping the highest confidence.
        
        Args:
            pattern_matches: Raw pattern matches that may overlap
            
        Returns:
            Consolidated list with best match per word
        """
        # Group by word_id
        word_matches = {}
        for match in pattern_matches:
            word_id = match.word.word_id
            if word_id not in word_matches:
                word_matches[word_id] = []
            word_matches[word_id].append(match)
        
        # Keep best match per word
        consolidated = []
        for word_id, matches in word_matches.items():
            if len(matches) == 1:
                consolidated.append(matches[0])
            else:
                # Choose best match based on priority and confidence
                best_match = self._choose_best_match(matches)
                consolidated.append(best_match)
        
        return consolidated

    def _choose_best_match(self, matches: List[PatternMatch]) -> PatternMatch:
        """
        Choose the best match from multiple overlapping matches.
        
        Business label patterns have higher priority than generic patterns.
        """
        # Priority order (higher is better)
        PATTERN_PRIORITY = {
            PatternType.GRAND_TOTAL: 10,
            PatternType.MERCHANT_NAME: 9,
            PatternType.DATE: 8,
            PatternType.PRODUCT_NAME: 7,
            PatternType.TAX: 6,
            PatternType.SUBTOTAL: 5,
            PatternType.PHONE_NUMBER: 4,
            PatternType.EMAIL: 3,
            PatternType.QUANTITY: 2,
            PatternType.CURRENCY: 1,  # Generic, lower priority
        }
        
        def match_score(match):
            priority = PATTERN_PRIORITY.get(match.pattern_type, 0)
            return (priority, match.confidence)
        
        return max(matches, key=match_score)

    def enhance_merchant_patterns(
        self, labeled_words: Dict[int, Dict], merchant_patterns: Dict[str, any]
    ) -> Dict[int, Dict]:
        """
        Enhance pattern results with merchant-specific patterns from Pinecone.
        
        Args:
            labeled_words: Existing labeled words from pattern detection
            merchant_patterns: Merchant-specific patterns from Pinecone
            
        Returns:
            Enhanced labeled words dictionary
        """
        # Apply merchant patterns - this would integrate with Pinecone results
        for word_text, pattern_info in merchant_patterns.items():
            # Find words that match this pattern
            # This is a simplified version - real implementation would use fuzzy matching
            for word_id, label_info in labeled_words.items():
                if word_text.lower() in label_info.get("metadata", {}).get("text", "").lower():
                    # Override or enhance with merchant-specific label
                    if pattern_info.get("confidence", 0) > label_info.get("confidence", 0):
                        labeled_words[word_id].update({
                            "label": pattern_info["label"],
                            "confidence": pattern_info["confidence"],
                            "source": "merchant_patterns",
                        })
        
        return labeled_words

    def get_statistics(self) -> Dict:
        """Get semantic mapping statistics."""
        stats = self.stats.copy()
        
        if stats["total_patterns"] > 0:
            stats["mapping_rate"] = stats["mapped_patterns"] / stats["total_patterns"]
            stats["essential_rate"] = stats["essential_labels_found"] / stats["total_patterns"]
        else:
            stats["mapping_rate"] = 0
            stats["essential_rate"] = 0
        
        return stats

    def reset_statistics(self):
        """Reset statistics counters."""
        self.stats = {
            "total_patterns": 0,
            "mapped_patterns": 0,
            "essential_labels_found": 0,
        }