"""
Enhanced Validation with Statistical Tracking

This module extends the validation system to track validation statistics
per word, enabling confidence-based decision making.
"""

from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timezone
from collections import defaultdict
import json

from receipt_label.constants import CORE_LABELS


@dataclass
class WordValidationStats:
    """
    Track validation statistics for a specific word-label combination.
    
    This allows us to build confidence over time and identify patterns.
    """
    word_text: str
    label: str
    valid_count: int = 0
    invalid_count: int = 0
    suggested_corrections: Dict[str, int] = field(default_factory=dict)
    last_validated: Optional[datetime] = None
    merchant_contexts: Dict[str, int] = field(default_factory=dict)
    
    @property
    def total_validations(self) -> int:
        """Total number of times this word-label pair was validated"""
        return self.valid_count + self.invalid_count
    
    @property
    def confidence_score(self) -> float:
        """
        Calculate confidence score (0-1) based on validation history.
        
        Uses a Bayesian approach with a prior to handle small sample sizes.
        """
        if self.total_validations == 0:
            return 0.5  # No data = neutral confidence
        
        # Add Laplace smoothing to avoid extreme values with few samples
        alpha = 1.0  # Smoothing parameter
        return (self.valid_count + alpha) / (self.total_validations + 2 * alpha)
    
    @property
    def needs_review(self) -> bool:
        """
        Determine if this word-label pair needs human review.
        
        Returns True if:
        - Confidence is between 0.3 and 0.7 (mixed results)
        - Has been validated fewer than 5 times
        - Has multiple suggested corrections
        """
        if self.total_validations < 5:
            return True
        if 0.3 <= self.confidence_score <= 0.7:
            return True
        if len(self.suggested_corrections) > 1:
            return True
        return False
    
    def update(self, is_valid: bool, merchant: str = None, suggested_label: str = None):
        """Update statistics based on new validation result"""
        if is_valid:
            self.valid_count += 1
        else:
            self.invalid_count += 1
            if suggested_label:
                self.suggested_corrections[suggested_label] = \
                    self.suggested_corrections.get(suggested_label, 0) + 1
        
        if merchant:
            self.merchant_contexts[merchant] = \
                self.merchant_contexts.get(merchant, 0) + 1
        
        self.last_validated = datetime.now(timezone.utc)
    
    def get_most_likely_correction(self) -> Optional[str]:
        """Get the most frequently suggested correction"""
        if not self.suggested_corrections:
            return None
        return max(self.suggested_corrections.items(), key=lambda x: x[1])[0]


@dataclass
class EnhancedValidationResult:
    """
    Enhanced validation result with confidence and historical context.
    """
    word: str
    line_id: int
    word_id: int
    current_label: str
    is_valid: bool
    confidence: float
    suggested_label: Optional[str] = None
    validation_history: Optional[WordValidationStats] = None
    skip_reason: Optional[str] = None
    
    def should_skip_validation(self) -> bool:
        """
        Determine if we should skip LLM validation based on confidence.
        
        Skip if:
        - Confidence > 0.95 and valid (very confident it's correct)
        - Confidence < 0.05 and invalid (very confident it's wrong)
        - Word has been validated 100+ times with consistent results
        """
        if not self.validation_history:
            return False
        
        stats = self.validation_history
        
        # Skip if we're very confident
        if stats.confidence_score > 0.95 and stats.total_validations >= 20:
            self.skip_reason = f"High confidence ({stats.confidence_score:.2f}) from {stats.total_validations} validations"
            return True
        
        # Skip if we know the correct label with high confidence
        if stats.confidence_score < 0.1 and stats.total_validations >= 20:
            correction = stats.get_most_likely_correction()
            if correction:
                self.suggested_label = correction
                self.skip_reason = f"Known correction to {correction} (confidence: {1-stats.confidence_score:.2f})"
                return True
        
        return False


class ValidationStatsManager:
    """
    Manages validation statistics across all words and labels.
    
    This could be backed by DynamoDB, Redis, or a local cache.
    """
    
    def __init__(self, storage_backend: str = "memory"):
        """
        Initialize the stats manager.
        
        Args:
            storage_backend: "memory", "dynamodb", or "redis"
        """
        self.storage_backend = storage_backend
        self.stats: Dict[Tuple[str, str], WordValidationStats] = {}
        
        # Track global patterns
        self.merchant_patterns: Dict[str, Dict[str, str]] = defaultdict(dict)
        self.label_patterns: Dict[str, List[str]] = defaultdict(list)
    
    def get_stats(self, word_text: str, label: str) -> WordValidationStats:
        """Get or create statistics for a word-label pair"""
        key = (word_text.upper(), label)
        if key not in self.stats:
            self.stats[key] = WordValidationStats(
                word_text=word_text.upper(),
                label=label
            )
        return self.stats[key]
    
    def update_validation(
        self,
        word_text: str,
        label: str,
        is_valid: bool,
        merchant: str = None,
        suggested_label: str = None
    ):
        """Update statistics for a validation result"""
        stats = self.get_stats(word_text, label)
        stats.update(is_valid, merchant, suggested_label)
        
        # Update merchant patterns
        if merchant and is_valid:
            self.merchant_patterns[merchant][word_text.upper()] = label
    
    def get_confidence_threshold_labels(
        self,
        labels_to_validate: List[dict],
        confidence_threshold: float = 0.9
    ) -> Tuple[List[dict], List[dict]]:
        """
        Split labels into high-confidence (skip) and need-validation groups.
        
        Returns:
            (high_confidence_labels, need_validation_labels)
        """
        high_confidence = []
        need_validation = []
        
        for label_dict in labels_to_validate:
            word_text = label_dict.get("text", "")
            label = label_dict.get("label", "")
            
            stats = self.get_stats(word_text, label)
            
            if stats.confidence_score >= confidence_threshold and stats.total_validations >= 10:
                # High confidence - can skip validation
                label_dict["confidence"] = stats.confidence_score
                label_dict["validation_count"] = stats.total_validations
                label_dict["skip_validation"] = True
                high_confidence.append(label_dict)
            else:
                # Needs validation
                label_dict["confidence"] = stats.confidence_score
                label_dict["validation_count"] = stats.total_validations
                need_validation.append(label_dict)
        
        return high_confidence, need_validation
    
    def get_merchant_specific_patterns(self, merchant: str) -> Dict[str, str]:
        """Get known word-label patterns for a specific merchant"""
        return dict(self.merchant_patterns.get(merchant, {}))
    
    def suggest_label_based_on_history(
        self,
        word_text: str,
        merchant: str = None
    ) -> Optional[Tuple[str, float]]:
        """
        Suggest a label based on historical data.
        
        Returns:
            (suggested_label, confidence) or None
        """
        word_upper = word_text.upper()
        
        # First check merchant-specific patterns
        if merchant:
            merchant_patterns = self.get_merchant_specific_patterns(merchant)
            if word_upper in merchant_patterns:
                label = merchant_patterns[word_upper]
                stats = self.get_stats(word_upper, label)
                if stats.confidence_score > 0.8:
                    return (label, stats.confidence_score)
        
        # Check all labels for this word
        best_label = None
        best_confidence = 0.0
        
        for label in CORE_LABELS.keys():
            stats = self.get_stats(word_upper, label)
            if stats.confidence_score > best_confidence and stats.total_validations >= 5:
                best_label = label
                best_confidence = stats.confidence_score
        
        if best_confidence > 0.7:  # Only suggest if reasonably confident
            return (best_label, best_confidence)
        
        return None
    
    def export_statistics(self) -> dict:
        """Export all statistics for analysis or backup"""
        return {
            "stats": {
                f"{word}|{label}": {
                    "valid_count": stats.valid_count,
                    "invalid_count": stats.invalid_count,
                    "confidence": stats.confidence_score,
                    "corrections": stats.suggested_corrections,
                    "merchants": stats.merchant_contexts,
                    "needs_review": stats.needs_review
                }
                for (word, label), stats in self.stats.items()
            },
            "merchant_patterns": dict(self.merchant_patterns),
            "summary": {
                "total_word_label_pairs": len(self.stats),
                "high_confidence_pairs": sum(
                    1 for s in self.stats.values() 
                    if s.confidence_score > 0.9 and s.total_validations >= 10
                ),
                "needs_review": sum(1 for s in self.stats.values() if s.needs_review),
                "total_validations": sum(s.total_validations for s in self.stats.values())
            }
        }
    
    def import_statistics(self, data: dict):
        """Import statistics from a backup or another instance"""
        for key_str, stats_dict in data.get("stats", {}).items():
            word, label = key_str.split("|")
            stats = WordValidationStats(
                word_text=word,
                label=label,
                valid_count=stats_dict["valid_count"],
                invalid_count=stats_dict["invalid_count"],
                suggested_corrections=stats_dict.get("corrections", {}),
                merchant_contexts=stats_dict.get("merchants", {})
            )
            self.stats[(word, label)] = stats
        
        self.merchant_patterns = defaultdict(dict, data.get("merchant_patterns", {}))


def create_enhanced_validator(stats_manager: ValidationStatsManager = None):
    """
    Create an enhanced validator that uses historical statistics.
    
    This wraps the existing LangChain validator with statistical tracking.
    """
    if stats_manager is None:
        stats_manager = ValidationStatsManager()
    
    async def validate_with_stats(
        image_id: str,
        receipt_id: int,
        labels_to_validate: List[dict],
        merchant_name: str = None,
        skip_high_confidence: bool = True,
        confidence_threshold: float = 0.95
    ) -> dict:
        """
        Validate labels with statistical optimization.
        
        Args:
            image_id: Image identifier
            receipt_id: Receipt identifier
            labels_to_validate: Labels to validate
            merchant_name: Merchant name for context
            skip_high_confidence: Skip validation for high-confidence labels
            confidence_threshold: Threshold for skipping (0.95 = 95% confidence)
        
        Returns:
            Validation results with statistics
        """
        results = {
            "validated_by_llm": [],
            "skipped_high_confidence": [],
            "suggested_by_history": [],
            "statistics": {}
        }
        
        # Split labels based on confidence
        if skip_high_confidence:
            high_confidence, need_validation = stats_manager.get_confidence_threshold_labels(
                labels_to_validate,
                confidence_threshold
            )
            
            # Add high-confidence labels directly to results
            for label in high_confidence:
                label["validation_source"] = "historical_confidence"
                results["skipped_high_confidence"].append(label)
        else:
            need_validation = labels_to_validate
        
        # For labels needing validation, check if we can suggest based on history
        final_validation_needed = []
        for label in need_validation:
            word_text = label.get("text", "")
            suggestion = stats_manager.suggest_label_based_on_history(
                word_text,
                merchant_name
            )
            
            if suggestion and suggestion[0] != label.get("label"):
                # We have a different suggestion based on history
                label["suggested_label"] = suggestion[0]
                label["suggestion_confidence"] = suggestion[1]
                label["validation_source"] = "historical_suggestion"
                results["suggested_by_history"].append(label)
            else:
                final_validation_needed.append(label)
        
        # Only call LLM for labels that truly need it
        if final_validation_needed:
            # Import the original validator
            from receipt_label.langchain_validation import validate_receipt_labels
            
            llm_results = await validate_receipt_labels(
                image_id,
                receipt_id,
                final_validation_needed
            )
            
            # Update statistics based on LLM results
            for result in llm_results.get("validation_results", []):
                word_text = result.get("text", "")
                label = result.get("label", "")
                is_valid = result.get("is_valid", False)
                suggested = result.get("correct_label")
                
                stats_manager.update_validation(
                    word_text,
                    label,
                    is_valid,
                    merchant_name,
                    suggested
                )
                
                result["validation_source"] = "llm"
                results["validated_by_llm"].append(result)
        
        # Add statistics summary
        results["statistics"] = {
            "total_labels": len(labels_to_validate),
            "skipped_count": len(results["skipped_high_confidence"]),
            "suggested_count": len(results["suggested_by_history"]),
            "llm_validated_count": len(results["validated_by_llm"]),
            "cost_savings": f"{(1 - len(final_validation_needed)/max(len(labels_to_validate), 1)) * 100:.1f}%"
        }
        
        return results
    
    return validate_with_stats, stats_manager


# Example usage
async def demo_enhanced_validation():
    """Demonstrate enhanced validation with statistics"""
    
    # Create stats manager
    stats_manager = ValidationStatsManager()
    
    # Simulate some historical data
    stats_manager.update_validation("WALMART", "MERCHANT_NAME", True, "Walmart")
    stats_manager.update_validation("WALMART", "MERCHANT_NAME", True, "Walmart")
    stats_manager.update_validation("WALMART", "MERCHANT_NAME", True, "Walmart")
    stats_manager.update_validation("12.99", "UNIT_PRICE", True, "Walmart")
    stats_manager.update_validation("12.99", "UNIT_PRICE", True, "Walmart")
    stats_manager.update_validation("TOTAL", "GRAND_TOTAL", False, "Walmart", "LABEL_INDICATOR")
    stats_manager.update_validation("TOTAL", "GRAND_TOTAL", False, "Walmart", "LABEL_INDICATOR")
    
    # Create enhanced validator
    validate_with_stats, _ = create_enhanced_validator(stats_manager)
    
    # Test labels
    test_labels = [
        {"text": "WALMART", "label": "MERCHANT_NAME"},  # High confidence, will skip
        {"text": "12.99", "label": "UNIT_PRICE"},  # High confidence, will skip
        {"text": "TOTAL", "label": "GRAND_TOTAL"},  # Low confidence, will validate
        {"text": "BREAD", "label": "PRODUCT_NAME"},  # No history, will validate
    ]
    
    # Run validation
    results = await validate_with_stats(
        "IMG_001",
        1,
        test_labels,
        merchant_name="Walmart",
        skip_high_confidence=True,
        confidence_threshold=0.8
    )
    
    print("Enhanced Validation Results:")
    print(f"- Skipped (high confidence): {len(results['skipped_high_confidence'])}")
    print(f"- Suggested corrections: {len(results['suggested_by_history'])}")
    print(f"- Validated by LLM: {len(results['validated_by_llm'])}")
    print(f"- Cost savings: {results['statistics']['cost_savings']}")
    
    # Export statistics for analysis
    stats_export = stats_manager.export_statistics()
    print(f"\nStatistics Summary:")
    print(f"- Total word-label pairs tracked: {stats_export['summary']['total_word_label_pairs']}")
    print(f"- High confidence pairs: {stats_export['summary']['high_confidence_pairs']}")
    print(f"- Needs review: {stats_export['summary']['needs_review']}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(demo_enhanced_validation())