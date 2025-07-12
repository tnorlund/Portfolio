"""
Smart decision engine for determining when GPT calls are necessary.

This module implements the logic to decide whether a receipt needs GPT processing
based on essential labels, noise filtering, and unlabeled word thresholds.
"""

import logging
from typing import Dict, List, Optional, Set, Tuple
from enum import Enum

from receipt_label.constants import CORE_LABELS
from receipt_label.config import decision_engine_config
from receipt_label.utils.noise_detection import is_noise_word
from receipt_label.agent.merchant_essential_labels import MerchantEssentialLabels

logger = logging.getLogger(__name__)


class GPTDecision(Enum):
    """Decision outcomes for GPT usage."""
    SKIP = "skip"           # No GPT needed - patterns found all core essentials
    BATCH = "batch"         # Defer GPT for secondary labels (background processing)
    REQUIRED = "required"   # Immediate GPT needed - core essentials missing


class DecisionEngine:
    """Makes intelligent decisions about when to use GPT for labeling."""

    # Level 1: Core essential labels (must be found to skip GPT entirely)
    CORE_ESSENTIAL_LABELS = {
        CORE_LABELS["MERCHANT_NAME"],
        CORE_LABELS["DATE"],
        CORE_LABELS["GRAND_TOTAL"],
    }

    # Level 2: Secondary essential labels (can be deferred to batch processing)
    SECONDARY_ESSENTIAL_LABELS = {
        CORE_LABELS["PRODUCT_NAME"],
    }

    # All essential labels (for backward compatibility)
    ESSENTIAL_LABELS = CORE_ESSENTIAL_LABELS | SECONDARY_ESSENTIAL_LABELS

    # Threshold for meaningful unlabeled words (now configurable)
    UNLABELED_THRESHOLD = decision_engine_config.MEANINGFUL_UNLABELED_WORDS_THRESHOLD

    def __init__(self):
        """Initialize the decision engine."""
        self.merchant_essential_labels = MerchantEssentialLabels()
        self.stats = {
            "total_receipts": 0,
            "skip": 0,              # Complete patterns coverage
            "batch": 0,             # Defer secondary labels to batch
            "required": 0,          # Immediate GPT needed
            "core_missing": 0,      # Core essentials missing
            "threshold_exceeded": 0, # Too many unlabeled words
            "merchant_overrides": 0, # Times merchant-specific requirements were used
        }

    def should_call_gpt(
        self,
        receipt_words: List[Dict],
        labeled_words: Dict[int, Dict],
        receipt_metadata: Optional[Dict] = None,
    ) -> Tuple[bool, str, List[Dict]]:
        """
        Determine if GPT should be called for labeling (legacy method).
        
        This method maintains backward compatibility by returning boolean.
        Use make_smart_gpt_decision() for enhanced tri-state logic.

        Args:
            receipt_words: All words in the receipt
            labeled_words: Words already labeled by patterns (word_id -> label info)
            receipt_metadata: Optional metadata about the receipt

        Returns:
            Tuple of:
            - should_call: Boolean indicating if GPT is needed
            - reason: String explaining the decision
            - unlabeled_words: List of meaningful unlabeled words
        """
        decision, reason, unlabeled_words, missing_fields = self.make_smart_gpt_decision(
            receipt_words, labeled_words, receipt_metadata
        )
        
        # Convert to boolean for backward compatibility
        should_call = decision != GPTDecision.SKIP
        return should_call, reason, unlabeled_words

    def make_smart_gpt_decision(
        self,
        receipt_words: List[Dict],
        labeled_words: Dict[int, Dict],
        receipt_metadata: Optional[Dict] = None,
    ) -> Tuple[GPTDecision, str, List[Dict], List[str]]:
        """
        Make intelligent tri-state decision about GPT usage.

        Args:
            receipt_words: All words in the receipt
            labeled_words: Words already labeled by patterns (word_id -> label info)
            receipt_metadata: Optional metadata about the receipt

        Returns:
            Tuple of:
            - decision: GPTDecision enum (SKIP/BATCH/REQUIRED)
            - reason: String explaining the decision
            - unlabeled_words: List of meaningful unlabeled words
            - missing_fields: List of missing label types for targeted GPT calls
        """
        self.stats["total_receipts"] += 1

        # Step 1: Extract merchant information for essential labels configuration
        merchant_name = None
        merchant_category = None
        if receipt_metadata:
            merchant_name = (
                receipt_metadata.get("merchant_name") or
                receipt_metadata.get("canonical_merchant_name") or
                receipt_metadata.get("business_name")
            )
            merchant_category = receipt_metadata.get("merchant_category")
        
        # Step 2: Check what essential labels are found using merchant-specific requirements
        missing_core, missing_secondary = self._check_tiered_essential_labels(
            receipt_words, labeled_words, merchant_name, merchant_category
        )

        # Step 3: Get meaningful unlabeled words early for threshold check
        unlabeled_words = self._get_meaningful_unlabeled_words(
            receipt_words, labeled_words
        )

        # Step 4: Apply decision logic
        
        # If core essentials are missing, require immediate GPT
        if missing_core:
            self.stats["required"] += 1
            self.stats["core_missing"] += 1
            reason = f"Missing core essential labels: {', '.join(missing_core)}"
            logger.info(f"GPT required - {reason}")
            return GPTDecision.REQUIRED, reason, unlabeled_words, list(missing_core)

        # If too many unlabeled words, require immediate GPT regardless
        if len(unlabeled_words) >= self.UNLABELED_THRESHOLD:
            self.stats["required"] += 1
            self.stats["threshold_exceeded"] += 1
            reason = f"Found {len(unlabeled_words)} meaningful unlabeled words (threshold: {self.UNLABELED_THRESHOLD})"
            logger.info(f"GPT required - {reason}")
            return GPTDecision.REQUIRED, reason, unlabeled_words, []

        # Core essentials found and unlabeled words under threshold
        if missing_secondary:
            # Defer secondary labels to batch processing
            self.stats["batch"] += 1
            reason = f"Core essentials found, deferring secondary labels to batch: {', '.join(missing_secondary)}"
            logger.info(f"GPT deferred to batch - {reason}")
            return GPTDecision.BATCH, reason, unlabeled_words, list(missing_secondary)

        # All essentials found, no GPT needed
        self.stats["skip"] += 1
        reason = f"All essential labels found by patterns, {len(unlabeled_words)} unlabeled words remain"
        logger.info(f"GPT skipped - {reason}")
        return GPTDecision.SKIP, reason, unlabeled_words, []

    def _check_essential_labels(
        self, receipt_words: List[Dict], labeled_words: Dict[int, Dict]
    ) -> Set[str]:
        """
        Check which essential labels are missing (legacy method).

        Returns:
            Set of missing essential label names
        """
        missing_core, missing_secondary = self._check_tiered_essential_labels(
            receipt_words, labeled_words
        )
        return missing_core | missing_secondary

    def _check_tiered_essential_labels(
        self, 
        receipt_words: List[Dict], 
        labeled_words: Dict[int, Dict],
        merchant_name: Optional[str] = None,
        merchant_category: Optional[str] = None
    ) -> Tuple[Set[str], Set[str]]:
        """
        Check which essential labels are missing, separated by tier.
        
        Uses merchant-specific essential label requirements when available,
        falling back to default requirements for unknown merchants.

        Args:
            receipt_words: All words in the receipt
            labeled_words: Words already labeled by patterns
            merchant_name: Name of the merchant (for category detection)
            merchant_category: Pre-determined merchant category
            
        Returns:
            Tuple of:
            - missing_core: Set of missing core essential labels
            - missing_secondary: Set of missing secondary essential labels
        """
        # Get merchant-specific essential label configuration
        essential_config, category_used = self.merchant_essential_labels.get_essential_labels_for_merchant(
            merchant_name, merchant_category
        )
        
        # Track if we used merchant-specific configuration
        if category_used != "unknown":
            self.stats["merchant_overrides"] += 1
            logger.debug(f"Using {category_used} essential labels for merchant: {merchant_name}")
        
        found_labels = set()

        # Check labeled words for any essential labels found
        for word_id, label_info in labeled_words.items():
            label = label_info.get("label")
            if label in essential_config.all_labels:
                found_labels.add(label)

        # Calculate missing labels by tier using merchant-specific requirements
        missing_core = essential_config.core_labels - found_labels
        missing_secondary = essential_config.secondary_labels - found_labels
        
        return missing_core, missing_secondary

    def _get_meaningful_unlabeled_words(
        self, receipt_words: List[Dict], labeled_words: Dict[int, Dict]
    ) -> List[Dict]:
        """
        Get unlabeled words that are not noise.

        Returns:
            List of meaningful unlabeled word dictionaries
        """
        unlabeled = []

        for word in receipt_words:
            word_id = self._get_word_id(word)

            # Skip if already labeled
            if word_id in labeled_words:
                continue

            # Get word text
            text = self._get_word_text(word)

            # Skip noise words
            if is_noise_word(text):
                continue

            # Skip very short words (likely punctuation or artifacts)
            if len(text) < 2 and not text.isdigit():
                continue

            # This is a meaningful unlabeled word
            unlabeled.append(word)

        return unlabeled

    def _get_word_id(self, word) -> int:
        """Extract word_id from word object or dictionary."""
        if isinstance(word, dict):
            return word.get("word_id")
        else:
            return getattr(word, "word_id", None)

    def _get_word_text(self, word) -> str:
        """Extract text from word object or dictionary."""
        if isinstance(word, dict):
            return word.get("text", "")
        else:
            return getattr(word, "text", "")

    def group_words_by_line(self, words: List[Dict]) -> Dict[int, List[Dict]]:
        """
        Group words by their line_id for batch processing.

        Args:
            words: List of word dictionaries

        Returns:
            Dictionary mapping line_id to list of words
        """
        lines = {}

        for word in words:
            line_id = (
                word.get("line_id")
                if isinstance(word, dict)
                else getattr(word, "line_id", None)
            )
            if line_id is not None:
                if line_id not in lines:
                    lines[line_id] = []
                lines[line_id].append(word)

        # Sort words within each line by x position
        for line_id, line_words in lines.items():
            lines[line_id] = sorted(
                line_words,
                key=lambda w: (
                    w.get("x", 0)
                    if isinstance(w, dict)
                    else getattr(w, "x", 0)
                ),
            )

        return lines

    def get_context_for_words(
        self,
        unlabeled_words: List[Dict],
        labeled_words: Dict[int, Dict],
        receipt_words: List[Dict],
    ) -> Dict:
        """
        Get context information for unlabeled words to help GPT.

        Returns:
            Dictionary with context information
        """
        # Group by line
        unlabeled_by_line = self.group_words_by_line(unlabeled_words)

        # Get labeled words for context
        labeled_examples = []
        for word_id, label_info in labeled_words.items():
            # Find the word
            word = next(
                (w for w in receipt_words if self._get_word_id(w) == word_id),
                None,
            )
            if word:
                labeled_examples.append(
                    {
                        "text": self._get_word_text(word),
                        "label": label_info["label"],
                        "confidence": label_info.get("confidence", 0),
                    }
                )

        # Sort labeled examples by confidence
        labeled_examples.sort(key=lambda x: x["confidence"], reverse=True)

        return {
            "unlabeled_by_line": unlabeled_by_line,
            "labeled_examples": labeled_examples[:20],  # Top 20 examples
            "total_unlabeled": len(unlabeled_words),
            "total_labeled": len(labeled_words),
        }

    def get_statistics(self) -> Dict:
        """Get decision engine statistics."""
        stats = self.stats.copy()

        # Calculate percentages
        if stats["total_receipts"] > 0:
            stats["skip_rate"] = stats["skip"] / stats["total_receipts"]
            stats["batch_rate"] = stats["batch"] / stats["total_receipts"]
            stats["required_rate"] = stats["required"] / stats["total_receipts"]
            
            # Legacy metrics for backward compatibility
            stats["gpt_rate"] = (stats["batch"] + stats["required"]) / stats["total_receipts"]
            stats["pattern_rate"] = stats["skip"] / stats["total_receipts"]
        else:
            stats["skip_rate"] = 0
            stats["batch_rate"] = 0
            stats["required_rate"] = 0
            stats["gpt_rate"] = 0
            stats["pattern_rate"] = 0

        return stats

    def get_merchant_essential_labels_statistics(self) -> Dict:
        """Get merchant essential labels system statistics."""
        return self.merchant_essential_labels.get_statistics()
    
    def get_merchant_category_summary(self) -> Dict:
        """Get summary of all merchant category configurations."""
        return self.merchant_essential_labels.get_category_summary()

    def reset_statistics(self):
        """Reset statistics counters."""
        self.stats = {
            "total_receipts": 0,
            "skip": 0,              # Complete patterns coverage
            "batch": 0,             # Defer secondary labels to batch
            "required": 0,          # Immediate GPT needed
            "core_missing": 0,      # Core essentials missing
            "threshold_exceeded": 0, # Too many unlabeled words
            "merchant_overrides": 0, # Times merchant-specific requirements were used
        }
