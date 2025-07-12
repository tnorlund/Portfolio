"""
Semantic mapping layer that bridges pattern detection to business labels.

This module maps low-level pattern types to high-level business-meaningful labels
required by the decision engine for cost optimization.
"""

from typing import Dict, List, Set, Tuple, Optional
from dataclasses import dataclass
from receipt_label.pattern_detection.base import PatternType, PatternMatch
from receipt_label.constants import CORE_LABELS


@dataclass
class ConfidenceScore:
    """Represents confidence scoring for a semantic label."""
    base_confidence: float  # Raw pattern detection confidence
    semantic_confidence: float  # Confidence in semantic mapping
    context_boost: float = 0.0  # Boost from contextual factors
    merchant_boost: float = 0.0  # Boost from merchant-specific knowledge
    
    @property
    def final_confidence(self) -> float:
        """Calculate final confidence score with all factors."""
        # Weighted combination of confidence factors
        base_weight = 0.4
        semantic_weight = 0.3
        context_weight = 0.2
        merchant_weight = 0.1
        
        score = (
            self.base_confidence * base_weight +
            self.semantic_confidence * semantic_weight +
            self.context_boost * context_weight +
            self.merchant_boost * merchant_weight
        )
        
        # Ensure score is between 0 and 1
        return min(1.0, max(0.0, score))


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
            "high_confidence_labels": 0,
            "low_confidence_labels": 0,
        }
        
        # Confidence thresholds
        self.high_confidence_threshold = 0.85
        self.low_confidence_threshold = 0.5
        
        # Semantic confidence mapping - how well pattern types map to labels
        self.semantic_confidence_map = {
            # High confidence mappings (direct, unambiguous)
            PatternType.GRAND_TOTAL: 0.95,
            PatternType.SUBTOTAL: 0.90,
            PatternType.TAX: 0.95,
            PatternType.DATE: 0.90,
            PatternType.TIME: 0.85,
            PatternType.PHONE_NUMBER: 0.90,
            PatternType.EMAIL: 0.95,
            
            # Medium confidence mappings
            PatternType.DISCOUNT: 0.80,
            PatternType.QUANTITY: 0.75,
            PatternType.UNIT_PRICE: 0.80,
            PatternType.LINE_TOTAL: 0.85,
            
            # Lower confidence mappings (may need context)
            PatternType.CURRENCY: 0.60,
            PatternType.MERCHANT_NAME: 0.85,  # Depends on position
            PatternType.PRODUCT_NAME: 0.70,  # Often ambiguous
        }

    def map_patterns_to_labels(
        self, pattern_matches: List[PatternMatch]
    ) -> Dict[int, Dict]:
        """
        Map pattern matches to semantic labels with confidence scoring.
        
        Args:
            pattern_matches: List of pattern matches from detectors
            
        Returns:
            Dictionary mapping word_id to label information with confidence scores
        """
        labeled_words = {}
        self.stats["total_patterns"] = len(pattern_matches)
        
        for match in pattern_matches:
            # Map pattern type to business label
            business_label = self.PATTERN_TO_LABEL_MAP.get(match.pattern_type)
            
            if business_label:
                self.stats["mapped_patterns"] += 1
                
                # Calculate confidence score
                confidence_score = self._calculate_confidence_score(match)
                
                # Check if this is an essential label
                is_essential = (
                    business_label in self.CORE_ESSENTIAL_LABELS or
                    business_label in self.SECONDARY_ESSENTIAL_LABELS
                )
                
                if is_essential:
                    self.stats["essential_labels_found"] += 1
                
                # Track confidence levels
                if confidence_score.final_confidence >= self.high_confidence_threshold:
                    self.stats["high_confidence_labels"] += 1
                elif confidence_score.final_confidence < self.low_confidence_threshold:
                    self.stats["low_confidence_labels"] += 1
                
                labeled_words[match.word.word_id] = {
                    "label": business_label,
                    "confidence": confidence_score.final_confidence,
                    "confidence_score": confidence_score,  # Full score object
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
            Enhanced labeled words dictionary with updated confidence scores
        """
        # Apply merchant patterns - this would integrate with Pinecone results
        for word_text, pattern_info in merchant_patterns.items():
            # Find words that match this pattern
            # This is a simplified version - real implementation would use fuzzy matching
            for word_id, label_info in labeled_words.items():
                if word_text.lower() in label_info.get("metadata", {}).get("text", "").lower():
                    # Get existing confidence score or create new one
                    existing_score = label_info.get("confidence_score")
                    
                    if existing_score and isinstance(existing_score, ConfidenceScore):
                        # Update merchant boost
                        existing_score.merchant_boost = 0.3  # Higher boost for merchant patterns
                        new_confidence = existing_score.final_confidence
                    else:
                        # Create new confidence score with merchant boost
                        new_score = ConfidenceScore(
                            base_confidence=pattern_info.get("confidence", 0.8),
                            semantic_confidence=0.9,  # High for merchant-specific
                            context_boost=0.0,
                            merchant_boost=0.3
                        )
                        new_confidence = new_score.final_confidence
                    
                    # Override if merchant pattern has higher confidence
                    if new_confidence > label_info.get("confidence", 0):
                        labeled_words[word_id].update({
                            "label": pattern_info["label"],
                            "confidence": new_confidence,
                            "confidence_score": existing_score or new_score,
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
            "high_confidence_labels": 0,
            "low_confidence_labels": 0,
        }
    
    def _calculate_confidence_score(self, match: PatternMatch) -> ConfidenceScore:
        """
        Calculate comprehensive confidence score for a pattern match.
        
        Args:
            match: Pattern match to score
            
        Returns:
            ConfidenceScore with all factors calculated
        """
        # Base confidence from pattern detection
        base_confidence = match.confidence
        
        # Semantic confidence - how well this pattern type maps to labels
        semantic_confidence = self.semantic_confidence_map.get(
            match.pattern_type, 
            0.5  # Default for unknown patterns
        )
        
        # Context boost based on position and metadata
        context_boost = self._calculate_context_boost(match)
        
        # Merchant boost (will be enhanced when merchant patterns are available)
        merchant_boost = 0.0
        if match.metadata.get("source") == "merchant_pattern":
            merchant_boost = 0.2  # Boost for merchant-specific patterns
        
        return ConfidenceScore(
            base_confidence=base_confidence,
            semantic_confidence=semantic_confidence,
            context_boost=context_boost,
            merchant_boost=merchant_boost
        )
    
    def _calculate_context_boost(self, match: PatternMatch) -> float:
        """
        Calculate contextual confidence boost based on position and surrounding patterns.
        
        Args:
            match: Pattern match to evaluate
            
        Returns:
            Context boost value (0.0 to 1.0)
        """
        boost = 0.0
        
        # Position-based boosts
        if hasattr(match.word, 'line_id'):
            # Merchant name typically in first few lines
            if match.pattern_type == PatternType.MERCHANT_NAME and match.word.line_id < 3:
                boost += 0.3
            
            # Total typically at bottom
            elif match.pattern_type == PatternType.GRAND_TOTAL and match.word.line_id > 10:
                boost += 0.2
            
            # Date/time typically at top
            elif match.pattern_type in [PatternType.DATE, PatternType.TIME] and match.word.line_id < 5:
                boost += 0.2
        
        # Pattern-specific boosts
        if match.pattern_type == PatternType.EMAIL and "@" in match.matched_text:
            boost += 0.1  # Valid email format
        
        if match.pattern_type == PatternType.PHONE_NUMBER and len(match.extracted_value) >= 10:
            boost += 0.1  # Valid phone length
        
        # Cap boost at 0.5
        return min(0.5, boost)
    
    def get_confidence_distribution(self, labeled_words: Dict[int, Dict]) -> Dict[str, int]:
        """
        Get distribution of confidence levels across labeled words.
        
        Args:
            labeled_words: Dictionary of labeled words
            
        Returns:
            Distribution of confidence levels
        """
        distribution = {
            "high": 0,  # >= 0.85
            "medium": 0,  # 0.5 - 0.85
            "low": 0,  # < 0.5
        }
        
        for label_info in labeled_words.values():
            confidence = label_info.get("confidence", 0)
            if confidence >= self.high_confidence_threshold:
                distribution["high"] += 1
            elif confidence >= self.low_confidence_threshold:
                distribution["medium"] += 1
            else:
                distribution["low"] += 1
        
        return distribution
    
    def filter_by_confidence(
        self, 
        labeled_words: Dict[int, Dict], 
        min_confidence: float = 0.7
    ) -> Dict[int, Dict]:
        """
        Filter labeled words by minimum confidence threshold.
        
        Args:
            labeled_words: Dictionary of labeled words
            min_confidence: Minimum confidence threshold
            
        Returns:
            Filtered dictionary with only high-confidence labels
        """
        return {
            word_id: label_info
            for word_id, label_info in labeled_words.items()
            if label_info.get("confidence", 0) >= min_confidence
        }