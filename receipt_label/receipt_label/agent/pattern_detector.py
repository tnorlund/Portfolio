"""
Pattern detector adapter for agent-based labeling system.

This module adapts Epic #190's ParallelPatternOrchestrator for use
in the agent-based receipt labeling pipeline.
"""

import asyncio
import logging
from typing import Dict, List, Optional

from receipt_dynamo.entities import ReceiptWord

from receipt_label.constants import CORE_LABELS
from receipt_label.pattern_detection import (
    ParallelPatternOrchestrator,
    PatternMatch,
    PatternType,
)
from receipt_label.utils.client_manager import ClientManager

logger = logging.getLogger(__name__)


class PatternDetector:
    """Adapts Epic #190's pattern detection for agent-based labeling."""

    def __init__(self, client_manager: ClientManager):
        """
        Initialize pattern detector with client manager.

        Args:
            client_manager: Client manager for accessing external services
        """
        self.client_manager = client_manager
        self.orchestrator = ParallelPatternOrchestrator(
            timeout=0.2
        )  # 200ms for agent usage

        # Map PatternType to CORE_LABELS
        self.pattern_to_label_map = {
            PatternType.GRAND_TOTAL: CORE_LABELS["GRAND_TOTAL"],
            PatternType.SUBTOTAL: CORE_LABELS["SUBTOTAL"],
            PatternType.TAX: CORE_LABELS["TAX"],
            PatternType.DISCOUNT: CORE_LABELS["DISCOUNT"],
            PatternType.LINE_TOTAL: CORE_LABELS["LINE_TOTAL"],
            PatternType.UNIT_PRICE: CORE_LABELS["UNIT_PRICE"],
            PatternType.DATE: CORE_LABELS["DATE"],
            PatternType.TIME: CORE_LABELS["TIME"],
            PatternType.PHONE_NUMBER: CORE_LABELS["PHONE_NUMBER"],
            PatternType.EMAIL: CORE_LABELS["EMAIL"],
            PatternType.WEBSITE: CORE_LABELS["WEBSITE"],
            PatternType.QUANTITY: CORE_LABELS["QUANTITY"],
        }

    async def detect_all_patterns(
        self,
        receipt_words: List[ReceiptWord],
        receipt_lines: List[Dict],
        merchant_name: Optional[str] = None,
        merchant_patterns: Optional[Dict] = None,
    ) -> Dict[str, List[Dict]]:
        """
        Run all pattern detectors in parallel.

        Args:
            receipt_words: List of ReceiptWord entities
            receipt_lines: List of line dictionaries (unused but kept for compatibility)
            merchant_name: Optional merchant name for pattern queries
            merchant_patterns: Optional merchant-specific patterns from Epic #189

        Returns:
            Dictionary with pattern type as key and detected patterns as value
        """
        logger.info(
            f"Running parallel pattern detection on {len(receipt_words)} words"
        )

        # Filter out noise words if they have is_noise attribute
        filtered_words = [
            word
            for word in receipt_words
            if not getattr(word, "is_noise", False)
        ]

        # Run Epic #190's orchestrator
        pattern_results = await self.orchestrator.detect_all_patterns(
            filtered_words, merchant_patterns
        )

        # Convert PatternMatch objects to our format
        converted_results = self._convert_pattern_matches(pattern_results)

        # Log summary
        total_patterns = sum(
            len(patterns)
            for name, patterns in converted_results.items()
            if name != "_metadata"
        )
        logger.info(f"Detected {total_patterns} patterns across all detectors")

        return converted_results

    def _convert_pattern_matches(
        self, pattern_results: Dict[str, List[PatternMatch]]
    ) -> Dict[str, List[Dict]]:
        """Convert Epic #190's PatternMatch objects to our dictionary format."""
        converted = {}

        for detector_name, matches in pattern_results.items():
            if detector_name == "_metadata":
                converted[detector_name] = matches
                continue

            converted_patterns = []
            for match in matches:
                # Get the appropriate label for this pattern type
                label = self.pattern_to_label_map.get(match.pattern_type)
                if not label:
                    logger.warning(
                        f"No label mapping for pattern type: {match.pattern_type}"
                    )
                    continue

                pattern_dict = {
                    "word_id": match.word.word_id,
                    "text": match.matched_text,
                    "label": label,
                    "confidence": match.confidence,
                    "pattern_type": match.pattern_type.name.lower(),
                    "extracted_value": match.extracted_value,
                    "metadata": match.metadata,
                }

                # Add pattern-specific fields
                if match.pattern_type in [
                    PatternType.GRAND_TOTAL,
                    PatternType.SUBTOTAL,
                    PatternType.TAX,
                    PatternType.DISCOUNT,
                    PatternType.LINE_TOTAL,
                    PatternType.UNIT_PRICE,
                ]:
                    pattern_dict["amount"] = match.extracted_value
                    pattern_dict["classification"] = (
                        match.pattern_type.name.lower()
                    )

                converted_patterns.append(pattern_dict)

            converted[detector_name] = converted_patterns

        return converted

    def apply_patterns(
        self,
        receipt_words: List[ReceiptWord],
        pattern_results: Dict[str, List[Dict]],
    ) -> Dict[int, Dict]:
        """
        Apply detected patterns to receipt words.

        Args:
            receipt_words: Original receipt words
            pattern_results: Results from detect_all_patterns

        Returns:
            Dictionary mapping word_id to label info
        """
        word_labels = {}

        # Process each pattern type
        for pattern_type, patterns in pattern_results.items():
            if pattern_type == "_metadata":
                continue

            for pattern in patterns:
                word_id = pattern.get("word_id")
                if word_id is not None:
                    # Keep highest confidence if multiple patterns match
                    if (
                        word_id not in word_labels
                        or pattern["confidence"]
                        > word_labels[word_id]["confidence"]
                    ):
                        word_labels[word_id] = {
                            "label": pattern["label"],
                            "confidence": pattern["confidence"],
                            "pattern_type": pattern["pattern_type"],
                            "source": f"pattern_{pattern_type}",
                        }

        return word_labels

    async def get_essential_fields_status(
        self, pattern_results: Dict[str, List[Dict]]
    ) -> Dict[str, bool]:
        """
        Check if essential fields were found in patterns.

        Essential fields for smart GPT decision:
        - MERCHANT_NAME (from metadata/merchant patterns)
        - DATE
        - GRAND_TOTAL
        - At least one PRODUCT (from merchant patterns or quantity)

        Args:
            pattern_results: Results from detect_all_patterns

        Returns:
            Dictionary indicating which essential fields were found
        """
        # Check for each essential field
        has_date = any(
            p.get("label") == CORE_LABELS["DATE"]
            for patterns in pattern_results.values()
            if isinstance(patterns, list)
            for p in patterns
        )

        has_total = any(
            p.get("label") == CORE_LABELS["GRAND_TOTAL"]
            for patterns in pattern_results.values()
            if isinstance(patterns, list)
            for p in patterns
        )

        has_merchant = bool(
            pattern_results.get("merchant")
            and len(pattern_results["merchant"]) > 0
        )

        # Product can be inferred from quantity patterns or merchant patterns
        has_product = False
        if pattern_results.get("quantity"):
            has_product = True
        elif pattern_results.get("merchant"):
            # Check if any merchant patterns are for products
            has_product = any(
                p.get("metadata", {}).get("label") == "PRODUCT_NAME"
                for p in pattern_results["merchant"]
            )

        return {
            "has_date": has_date,
            "has_total": has_total,
            "has_merchant": has_merchant,
            "has_product": has_product,
        }
