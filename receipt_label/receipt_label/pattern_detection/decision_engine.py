"""
Smart Decision Engine for Receipt Labeling.

This module implements the decision logic that determines when pattern detection
is sufficient and when GPT is needed for labeling receipt words.

The engine evaluates:
1. Essential label coverage (MERCHANT_NAME, DATE, GRAND_TOTAL)
2. Ambiguity thresholds (unlabeled meaningful words)
3. Pattern confidence scores
4. Cost optimization opportunities
"""

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from receipt_dynamo.entities.receipt_word import ReceiptWord
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel

from ..constants import CORE_LABELS
from ..utils.noise_detection import is_noise_word

logger = logging.getLogger(__name__)


@dataclass
class LabelingDecision:
    """Result of the decision engine's evaluation."""
    
    should_use_gpt: bool
    reason: str
    pattern_labels: List[ReceiptWordLabel]
    unlabeled_words: List[ReceiptWord]
    confidence_score: float
    essential_labels_found: Dict[str, bool]
    coverage_percentage: float
    
    @property
    def cost_savings_estimate(self) -> float:
        """Estimate cost savings from avoiding GPT."""
        return 0.0 if self.should_use_gpt else self.coverage_percentage


class SmartDecisionEngine:
    """
    Decides whether pattern detection is sufficient or if GPT is needed.
    
    This implements the core logic for the 84% cost reduction strategy by
    intelligently determining when patterns provide enough information.
    """
    
    # Essential labels that must be found
    ESSENTIAL_LABELS = {"MERCHANT_NAME", "DATE", "GRAND_TOTAL"}
    
    # Nice-to-have labels
    DESIRED_LABELS = {"PRODUCT_NAME", "SUBTOTAL", "TAX"}
    
    # Minimum thresholds
    MIN_MEANINGFUL_WORDS_FOR_GPT = 5
    MIN_COVERAGE_PERCENTAGE = 90.0
    
    def __init__(
        self,
        min_meaningful_words: int = 5,
        min_coverage_percentage: float = 90.0,
        require_essential_labels: bool = True
    ):
        """
        Initialize the decision engine.
        
        Args:
            min_meaningful_words: Minimum unlabeled meaningful words to justify GPT
            min_coverage_percentage: Minimum pattern coverage to skip GPT
            require_essential_labels: Whether all essential labels must be found
        """
        self.min_meaningful_words = min_meaningful_words
        self.min_coverage_percentage = min_coverage_percentage
        self.require_essential_labels = require_essential_labels
    
    def evaluate(
        self,
        receipt_words: List[ReceiptWord],
        pattern_results: Dict[str, any]
    ) -> LabelingDecision:
        """
        Evaluate whether to use GPT based on pattern detection results.
        
        Args:
            receipt_words: All words in the receipt
            pattern_results: Results from pattern detection orchestrator
            
        Returns:
            LabelingDecision with recommendation and analysis
        """
        # Extract pattern labels
        pattern_labels = self._extract_pattern_labels(pattern_results)
        
        # Map labels by word ID for quick lookup
        labeled_word_ids = {label.receipt_word_id for label in pattern_labels}
        
        # Separate meaningful vs noise words
        meaningful_words = []
        noise_words = []
        
        for word in receipt_words:
            if is_noise_word(word.text):
                noise_words.append(word)
            else:
                meaningful_words.append(word)
        
        # Find unlabeled meaningful words
        unlabeled_words = [
            word for word in meaningful_words 
            if word.receipt_word_id not in labeled_word_ids
        ]
        
        # Check essential labels
        essential_labels_found = self._check_essential_labels(pattern_labels)
        all_essential_found = all(essential_labels_found.values())
        
        # Calculate coverage
        total_meaningful = len(meaningful_words)
        labeled_meaningful = len(meaningful_words) - len(unlabeled_words)
        coverage_percentage = (
            (labeled_meaningful / total_meaningful * 100) 
            if total_meaningful > 0 else 100.0
        )
        
        # Make decision
        should_use_gpt, reason = self._make_decision(
            all_essential_found=all_essential_found,
            unlabeled_count=len(unlabeled_words),
            coverage_percentage=coverage_percentage,
            pattern_labels=pattern_labels
        )
        
        # Calculate confidence score
        confidence_score = self._calculate_confidence(
            pattern_labels, 
            coverage_percentage,
            all_essential_found
        )
        
        return LabelingDecision(
            should_use_gpt=should_use_gpt,
            reason=reason,
            pattern_labels=pattern_labels,
            unlabeled_words=unlabeled_words,
            confidence_score=confidence_score,
            essential_labels_found=essential_labels_found,
            coverage_percentage=coverage_percentage
        )
    
    def _extract_pattern_labels(
        self, 
        pattern_results: Dict[str, any]
    ) -> List[ReceiptWordLabel]:
        """Extract labels from pattern detection results."""
        labels = []
        
        # Get detected patterns
        detected_patterns = pattern_results.get("detected_patterns", [])
        
        for pattern in detected_patterns:
            word_id = pattern.get("receipt_word_id")
            label = pattern.get("label")
            confidence = pattern.get("confidence", 0.8)
            
            if word_id and label and label in CORE_LABELS:
                labels.append(
                    ReceiptWordLabel(
                        receipt_word_id=word_id,
                        label=label,
                        confidence=confidence,
                        source="pattern_detection"
                    )
                )
        
        return labels
    
    def _check_essential_labels(
        self, 
        pattern_labels: List[ReceiptWordLabel]
    ) -> Dict[str, bool]:
        """Check which essential labels were found."""
        found_labels = {label.label for label in pattern_labels}
        
        return {
            label: label in found_labels 
            for label in self.ESSENTIAL_LABELS
        }
    
    def _make_decision(
        self,
        all_essential_found: bool,
        unlabeled_count: int,
        coverage_percentage: float,
        pattern_labels: List[ReceiptWordLabel]
    ) -> Tuple[bool, str]:
        """
        Make the decision whether to use GPT.
        
        Returns:
            Tuple of (should_use_gpt, reason)
        """
        # Check essential labels requirement
        if self.require_essential_labels and not all_essential_found:
            return True, "Missing essential labels (MERCHANT_NAME, DATE, or GRAND_TOTAL)"
        
        # Check if too few unlabeled words to bother with GPT
        if unlabeled_count < self.min_meaningful_words:
            return False, f"Only {unlabeled_count} meaningful words unlabeled"
        
        # Check coverage threshold
        if coverage_percentage >= self.min_coverage_percentage:
            return False, f"Pattern coverage {coverage_percentage:.1f}% exceeds threshold"
        
        # Check if we have good product coverage
        product_labels = [l for l in pattern_labels if l.label == "PRODUCT_NAME"]
        if len(product_labels) >= 1 and unlabeled_count <= 10:
            return False, f"Sufficient product labels found with minimal unlabeled words"
        
        # Default to using GPT
        return True, f"Coverage {coverage_percentage:.1f}% below threshold with {unlabeled_count} words unlabeled"
    
    def _calculate_confidence(
        self,
        pattern_labels: List[ReceiptWordLabel],
        coverage_percentage: float,
        all_essential_found: bool
    ) -> float:
        """Calculate overall confidence score for the labeling."""
        # Base confidence from coverage
        confidence = coverage_percentage / 100.0
        
        # Boost for essential labels
        if all_essential_found:
            confidence = min(1.0, confidence + 0.1)
        
        # Average pattern confidence
        if pattern_labels:
            avg_pattern_confidence = sum(
                l.confidence for l in pattern_labels
            ) / len(pattern_labels)
            confidence = (confidence + avg_pattern_confidence) / 2
        
        return min(1.0, confidence)
    
    def apply_labels(
        self,
        receipt_words: List[ReceiptWord],
        pattern_labels: List[ReceiptWordLabel],
        gpt_labels: Optional[List[ReceiptWordLabel]] = None
    ) -> List[ReceiptWordLabel]:
        """
        Apply labels to receipt words, merging pattern and GPT results.
        
        Args:
            receipt_words: All receipt words
            pattern_labels: Labels from pattern detection
            gpt_labels: Optional labels from GPT
            
        Returns:
            Final list of labels to apply
        """
        # Start with pattern labels
        final_labels = {
            label.receipt_word_id: label 
            for label in pattern_labels
        }
        
        # Merge GPT labels if provided
        if gpt_labels:
            for gpt_label in gpt_labels:
                word_id = gpt_label.receipt_word_id
                
                # GPT overrides patterns for low-confidence matches
                if word_id in final_labels:
                    pattern_label = final_labels[word_id]
                    if pattern_label.confidence < 0.8:
                        logger.debug(
                            "GPT override for word %s: %s -> %s",
                            word_id, pattern_label.label, gpt_label.label
                        )
                        final_labels[word_id] = gpt_label
                else:
                    final_labels[word_id] = gpt_label
        
        return list(final_labels.values())
    
    def get_gpt_prompt_context(
        self,
        unlabeled_words: List[ReceiptWord],
        pattern_labels: List[ReceiptWordLabel]
    ) -> Dict[str, any]:
        """
        Generate context for GPT prompt focusing on unlabeled words.
        
        This helps GPT understand what's already labeled and focus on
        the remaining ambiguous words.
        """
        # Group pattern labels by type
        labels_by_type = {}
        for label in pattern_labels:
            label_type = label.label
            if label_type not in labels_by_type:
                labels_by_type[label_type] = []
            labels_by_type[label_type].append(label)
        
        return {
            "already_labeled": {
                label_type: len(labels)
                for label_type, labels in labels_by_type.items()
            },
            "focus_words": [
                {
                    "id": word.receipt_word_id,
                    "text": word.text,
                    "line_id": word.receipt_line_id
                }
                for word in unlabeled_words
            ],
            "hint": "Focus only on the unlabeled words listed above"
        }