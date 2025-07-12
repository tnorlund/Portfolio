"""
Label applicator for applying detected and generated labels to receipt words.

This module handles the application of labels from various sources (patterns, GPT)
and stores them in DynamoDB.
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

from receipt_dynamo.constants import LabelStatus
from receipt_dynamo.entities import ReceiptWordLabel

logger = logging.getLogger(__name__)


class LabelApplicator:
    """Applies labels to receipt words and manages storage."""

    def __init__(self, dynamo_client):
        """
        Initialize label applicator.

        Args:
            dynamo_client: DynamoDB client for storing labels
        """
        self.dynamo_client = dynamo_client
        self.stats = {
            "total_labels_applied": 0,
            "pattern_labels": 0,
            "gpt_labels": 0,
            "conflicts_resolved": 0,
            "labels_stored": 0,
        }

    def apply_labels(
        self,
        receipt_id: str,
        receipt_words: List[Dict],
        pattern_labels: Dict[int, Dict],
        gpt_labels: Optional[Dict[int, Dict]] = None,
        store_in_dynamo: bool = True,
    ) -> Tuple[List[ReceiptWordLabel], Dict]:
        """
        Apply labels from all sources and optionally store in DynamoDB.

        Args:
            receipt_id: Receipt identifier
            receipt_words: All receipt words
            pattern_labels: Labels from pattern detection
            gpt_labels: Optional labels from GPT
            store_in_dynamo: Whether to store labels in DynamoDB

        Returns:
            Tuple of:
            - List of ReceiptWordLabel entities
            - Dictionary of application statistics
        """
        logger.info(f"Applying labels for receipt {receipt_id}")

        # Merge labels with conflict resolution
        merged_labels = self._merge_labels(pattern_labels, gpt_labels)

        # Create ReceiptWordLabel entities
        word_labels = []

        for word in receipt_words:
            word_id = self._get_word_id(word)

            if word_id in merged_labels:
                label_info = merged_labels[word_id]

                # Create entity
                word_label = ReceiptWordLabel(
                    receipt_id=(
                        int(receipt_id)
                        if isinstance(receipt_id, str)
                        else receipt_id
                    ),
                    word_id=word_id,
                    image_id=self._get_word_attr(word, "image_id"),
                    line_id=self._get_word_attr(word, "line_id"),
                    text=self._get_word_attr(word, "text"),
                    label=label_info["label"],
                    confidence=label_info.get("confidence", 0.0),
                    source=label_info.get("source", "unknown"),
                    pattern_type=label_info.get("pattern_type"),
                    label_status=(
                        LabelStatus.VALIDATED
                        if label_info.get("confidence", 0) > 0.9
                        else LabelStatus.PROPOSED
                    ),
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )

                # Add optional fields
                if "reasoning" in label_info:
                    word_label.reasoning = label_info["reasoning"]

                word_labels.append(word_label)

                # Update stats
                self.stats["total_labels_applied"] += 1
                if label_info["source"] == "gpt":
                    self.stats["gpt_labels"] += 1
                else:
                    self.stats["pattern_labels"] += 1

        # Store in DynamoDB if requested
        if store_in_dynamo and word_labels:
            stored_count = self._store_labels(word_labels)
            self.stats["labels_stored"] += stored_count

        # Prepare statistics
        application_stats = {
            "total_words": len(receipt_words),
            "labeled_words": len(word_labels),
            "pattern_labels": sum(
                1 for l in merged_labels.values() if l["source"] != "gpt"
            ),
            "gpt_labels": sum(
                1 for l in merged_labels.values() if l["source"] == "gpt"
            ),
            "coverage_rate": (
                len(word_labels) / len(receipt_words) if receipt_words else 0
            ),
        }

        logger.info(
            f"Applied {len(word_labels)} labels ({application_stats['coverage_rate']:.1%} coverage)"
        )

        return word_labels, application_stats

    def _merge_labels(
        self,
        pattern_labels: Dict[int, Dict],
        gpt_labels: Optional[Dict[int, Dict]],
    ) -> Dict[int, Dict]:
        """
        Merge labels from different sources with conflict resolution.

        Strategy:
        1. If only one source has a label, use it
        2. If both have labels, use the one with higher confidence
        3. Track conflicts for analysis
        """
        merged = {}

        # Start with pattern labels
        for word_id, label_info in pattern_labels.items():
            merged[word_id] = label_info.copy()

        # Merge GPT labels if available
        if gpt_labels:
            for word_id, gpt_info in gpt_labels.items():
                if word_id in merged:
                    # Conflict - resolve by confidence
                    pattern_conf = merged[word_id].get("confidence", 0)
                    gpt_conf = gpt_info.get("confidence", 0)

                    if gpt_conf > pattern_conf:
                        # GPT wins
                        old_label = merged[word_id]["label"]
                        merged[word_id] = gpt_info.copy()
                        merged[word_id][
                            "pattern_label"
                        ] = old_label  # Track what pattern thought
                        self.stats["conflicts_resolved"] += 1

                        logger.debug(
                            f"Label conflict for word {word_id}: "
                            f"Pattern='{old_label}' ({pattern_conf:.2f}), "
                            f"GPT='{gpt_info['label']}' ({gpt_conf:.2f}) - GPT wins"
                        )
                else:
                    # No conflict
                    merged[word_id] = gpt_info.copy()

        return merged

    def _store_labels(self, word_labels: List[ReceiptWordLabel]) -> int:
        """
        Store labels in DynamoDB with error handling.

        Args:
            word_labels: List of ReceiptWordLabel entities

        Returns:
            Number of successfully stored labels
        """
        stored = 0

        # Batch put for efficiency
        batch_size = 25  # DynamoDB batch write limit

        for i in range(0, len(word_labels), batch_size):
            batch = word_labels[i : i + batch_size]

            try:
                # Use batch write through dynamo client
                for label in batch:
                    self.dynamo_client.put_receipt_word_label(label)
                    stored += 1

            except Exception as e:
                logger.error(f"Error storing label batch: {e}")
                # Try individual puts as fallback
                for label in batch:
                    try:
                        self.dynamo_client.put_receipt_word_label(label)
                        stored += 1
                    except Exception as individual_error:
                        logger.error(
                            f"Failed to store label for word {label.word_id}: {individual_error}"
                        )

        logger.info(f"Stored {stored}/{len(word_labels)} labels in DynamoDB")
        return stored

    def update_label_validation(
        self,
        receipt_id: str,
        word_id: int,
        validation_status: str,
        validator: Optional[str] = None,
        corrected_label: Optional[str] = None,
    ) -> bool:
        """
        Update validation status for a label.

        Args:
            receipt_id: Receipt identifier
            word_id: Word identifier
            validation_status: New validation status
            validator: Optional validator identifier
            corrected_label: Optional corrected label if invalid

        Returns:
            Success boolean
        """
        try:
            # Fetch existing label
            existing = self.dynamo_client.get_receipt_word_label(
                receipt_id, word_id
            )
            if not existing:
                logger.warning(
                    f"No label found for receipt {receipt_id}, word {word_id}"
                )
                return False

            # Update fields
            existing.label_status = validation_status
            existing.updated_at = datetime.utcnow()

            if validator:
                existing.validated_by = validator
                existing.validated_at = datetime.utcnow()

            if corrected_label:
                existing.corrected_label = corrected_label

            # Save back
            self.dynamo_client.put_receipt_word_label(existing)
            return True

        except Exception as e:
            logger.error(f"Error updating label validation: {e}")
            return False

    def get_receipt_labels(self, receipt_id: str) -> List[ReceiptWordLabel]:
        """
        Get all labels for a receipt.

        Args:
            receipt_id: Receipt identifier

        Returns:
            List of ReceiptWordLabel entities
        """
        try:
            return self.dynamo_client.list_receipt_word_labels_by_receipt(
                receipt_id
            )
        except Exception as e:
            logger.error(
                f"Error fetching labels for receipt {receipt_id}: {e}"
            )
            return []

    def _get_word_id(self, word) -> Optional[int]:
        """Extract word_id from word object or dictionary."""
        if isinstance(word, dict):
            return word.get("word_id")
        else:
            return getattr(word, "word_id", None)

    def _get_word_attr(self, word, attr: str):
        """Extract attribute from word object or dictionary."""
        if isinstance(word, dict):
            return word.get(attr)
        else:
            return getattr(word, attr, None)

    def get_statistics(self) -> Dict:
        """Get label applicator statistics."""
        return self.stats.copy()

    def reset_statistics(self):
        """Reset statistics counters."""
        self.stats = {
            "total_labels_applied": 0,
            "pattern_labels": 0,
            "gpt_labels": 0,
            "conflicts_resolved": 0,
            "labels_stored": 0,
        }
