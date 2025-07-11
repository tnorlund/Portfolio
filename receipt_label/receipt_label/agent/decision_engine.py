"""
Smart decision engine for determining when GPT calls are necessary.

This module implements the logic to decide whether a receipt needs GPT processing
based on essential labels, noise filtering, and unlabeled word thresholds.
"""

import logging
from typing import Dict, List, Optional, Set, Tuple

from receipt_label.constants import CORE_LABELS
from receipt_label.utils.noise_detection import is_noise_word

logger = logging.getLogger(__name__)


class DecisionEngine:
    """Makes intelligent decisions about when to use GPT for labeling."""

    # Essential labels that must be found
    ESSENTIAL_LABELS = {
        CORE_LABELS["MERCHANT_NAME"],
        CORE_LABELS["DATE"],
        CORE_LABELS["GRAND_TOTAL"],
        CORE_LABELS["PRODUCT_NAME"],
    }

    # Threshold for meaningful unlabeled words
    UNLABELED_THRESHOLD = 5

    def __init__(self):
        """Initialize the decision engine."""
        self.stats = {
            "total_receipts": 0,
            "gpt_required": 0,
            "pattern_only": 0,
            "essential_missing": 0,
            "threshold_exceeded": 0,
        }

    def should_call_gpt(
        self,
        receipt_words: List[Dict],
        labeled_words: Dict[int, Dict],
        receipt_metadata: Optional[Dict] = None,
    ) -> Tuple[bool, str, List[Dict]]:
        """
        Determine if GPT should be called for labeling.

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
        self.stats["total_receipts"] += 1

        # Step 1: Check if essential labels are found
        missing_essential = self._check_essential_labels(
            receipt_words, labeled_words
        )
        if missing_essential:
            self.stats["gpt_required"] += 1
            self.stats["essential_missing"] += 1
            reason = (
                f"Missing essential labels: {', '.join(missing_essential)}"
            )
            logger.info(f"GPT required - {reason}")

            # Return all meaningful words for GPT to process
            unlabeled = self._get_meaningful_unlabeled_words(
                receipt_words, labeled_words
            )
            return True, reason, unlabeled

        # Step 2: Filter noise and get meaningful unlabeled words
        unlabeled_words = self._get_meaningful_unlabeled_words(
            receipt_words, labeled_words
        )

        # Step 3: Check threshold
        if len(unlabeled_words) >= self.UNLABELED_THRESHOLD:
            self.stats["gpt_required"] += 1
            self.stats["threshold_exceeded"] += 1
            reason = f"Found {len(unlabeled_words)} meaningful unlabeled words (threshold: {self.UNLABELED_THRESHOLD})"
            logger.info(f"GPT required - {reason}")
            return True, reason, unlabeled_words

        # No GPT needed
        self.stats["pattern_only"] += 1
        reason = f"Patterns sufficient - only {len(unlabeled_words)} unlabeled words remain"
        logger.info(f"GPT not required - {reason}")
        return False, reason, unlabeled_words

    def _check_essential_labels(
        self, receipt_words: List[Dict], labeled_words: Dict[int, Dict]
    ) -> Set[str]:
        """
        Check which essential labels are missing.

        Returns:
            Set of missing essential label names
        """
        found_labels = set()

        # Check labeled words
        for word_id, label_info in labeled_words.items():
            label = label_info.get("label")
            if label in self.ESSENTIAL_LABELS:
                found_labels.add(label)

        # Return missing labels
        missing = self.ESSENTIAL_LABELS - found_labels
        return missing

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
            stats["gpt_rate"] = stats["gpt_required"] / stats["total_receipts"]
            stats["pattern_rate"] = (
                stats["pattern_only"] / stats["total_receipts"]
            )
        else:
            stats["gpt_rate"] = 0
            stats["pattern_rate"] = 0

        return stats

    def reset_statistics(self):
        """Reset statistics counters."""
        self.stats = {
            "total_receipts": 0,
            "gpt_required": 0,
            "pattern_only": 0,
            "essential_missing": 0,
            "threshold_exceeded": 0,
        }
