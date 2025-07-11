"""
Pattern detector orchestrator for parallel pattern detection.

This module coordinates multiple pattern detectors to run in parallel
and aggregates their results for the receipt labeling pipeline.
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Set, Tuple

from receipt_label.constants import CORE_LABELS
from receipt_label.data.pattern_analyzer import enhanced_analyzer
from receipt_label.patterns import (
    ContactPatternDetector,
    DatePatternDetector,
    QuantityPatternDetector,
)
from receipt_label.utils.client_manager import ClientManager

logger = logging.getLogger(__name__)


class PatternDetector:
    """Orchestrates parallel pattern detection for receipt labeling."""

    def __init__(self, client_manager: ClientManager):
        """
        Initialize pattern detector with client manager.

        Args:
            client_manager: Client manager for accessing external services
        """
        self.client_manager = client_manager

        # Initialize pattern detectors and set their labels
        self.date_detector = DatePatternDetector()
        self.date_detector.DATE_LABEL = CORE_LABELS["DATE"]

        self.contact_detector = ContactPatternDetector()
        self.contact_detector.PHONE_NUMBER_LABEL = CORE_LABELS["PHONE_NUMBER"]
        self.contact_detector.EMAIL_LABEL = CORE_LABELS["EMAIL"]
        self.contact_detector.WEBSITE_LABEL = CORE_LABELS["WEBSITE"]

        self.quantity_detector = QuantityPatternDetector()
        self.quantity_detector.QUANTITY_LABEL = CORE_LABELS["QUANTITY"]
        self.quantity_detector.UNIT_PRICE_LABEL = CORE_LABELS["UNIT_PRICE"]

        # Currency analyzer is already initialized as singleton
        self.currency_analyzer = enhanced_analyzer

    async def detect_all_patterns(
        self,
        receipt_words: List[Dict],
        receipt_lines: List[Dict],
        merchant_name: Optional[str] = None,
    ) -> Dict[str, List[Dict]]:
        """
        Run all pattern detectors in parallel.

        Args:
            receipt_words: List of word dictionaries
            receipt_lines: List of line dictionaries
            merchant_name: Optional merchant name for pattern queries

        Returns:
            Dictionary with pattern type as key and detected patterns as value
        """
        logger.info(
            f"Running parallel pattern detection on {len(receipt_words)} words"
        )

        # Prepare word format for detectors
        word_dicts = self._prepare_word_dicts(receipt_words)

        # Run pattern detectors in parallel
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor(max_workers=5) as executor:
            # Submit all detection tasks
            date_future = loop.run_in_executor(
                executor, self.date_detector.detect, word_dicts
            )
            contact_future = loop.run_in_executor(
                executor, self.contact_detector.detect, word_dicts
            )
            quantity_future = loop.run_in_executor(
                executor, self.quantity_detector.detect, word_dicts
            )
            currency_future = loop.run_in_executor(
                executor,
                self._detect_currency_patterns,
                receipt_words,
                receipt_lines,
            )
            merchant_future = loop.run_in_executor(
                executor,
                self._query_merchant_patterns,
                merchant_name,
                receipt_words,
            )

            # Wait for all to complete
            date_patterns = await date_future
            contact_patterns = await contact_future
            quantity_patterns = await quantity_future
            currency_patterns = await currency_future
            merchant_patterns = await merchant_future

        # Aggregate results
        results = {
            "date": date_patterns,
            "contact": contact_patterns,
            "quantity": quantity_patterns,
            "currency": currency_patterns,
            "merchant": merchant_patterns,
        }

        # Log summary
        total_patterns = sum(len(patterns) for patterns in results.values())
        logger.info(f"Detected {total_patterns} patterns across all detectors")

        return results

    def _prepare_word_dicts(self, receipt_words: List) -> List[Dict]:
        """Convert receipt word objects to dictionaries for pattern detectors."""
        word_dicts = []
        for word in receipt_words:
            # Handle both dictionary and object formats
            if isinstance(word, dict):
                word_dicts.append(word)
            else:
                # Assume it's a ReceiptWord object
                word_dicts.append(
                    {
                        "word_id": getattr(word, "word_id", None),
                        "text": getattr(word, "text", ""),
                        "line_id": getattr(word, "line_id", None),
                        "x": getattr(word, "x", 0),
                        "y": getattr(word, "y", 0),
                    }
                )
        return word_dicts

    def _detect_currency_patterns(
        self, receipt_words: List, receipt_lines: List
    ) -> List[Dict]:
        """
        Detect and classify currency patterns.

        Returns list of patterns with classification (line_item, tax, total, etc.)
        """
        # Extract currency contexts from words
        currency_contexts = self._extract_currency_contexts(
            receipt_words, receipt_lines
        )

        if not currency_contexts:
            return []

        # Use enhanced analyzer to classify
        result, _, _ = self.currency_analyzer.analyze_spatial_currency(
            receipt=None,  # Not needed for pattern analysis
            receipt_lines=receipt_lines,
            receipt_words=receipt_words,
            currency_contexts=currency_contexts,
        )

        # Convert to pattern format
        patterns = []

        # Process classified currency amounts
        for item in result.get("currency_amounts", []):
            classification = item.get("classification", "unknown")

            # Map classification to CORE_LABELS
            label_map = {
                "line_item": CORE_LABELS.get("LINE_TOTAL"),
                "subtotal": CORE_LABELS.get("SUBTOTAL"),
                "tax": CORE_LABELS.get("TAX"),
                "total": CORE_LABELS.get("GRAND_TOTAL"),
                "discount": CORE_LABELS.get("DISCOUNT"),
            }

            label = label_map.get(classification)
            if label:
                patterns.append(
                    {
                        "word_id": item.get("word_id"),
                        "text": str(item.get("amount", "")),
                        "label": label,
                        "confidence": item.get("confidence", 0.75),
                        "pattern_type": f"currency_{classification}",
                        "classification": classification,
                        "amount": item.get("amount"),
                    }
                )

        return patterns

    def _extract_currency_contexts(
        self, receipt_words: List, receipt_lines: List
    ) -> List[Dict]:
        """Extract currency amounts with their contexts."""
        contexts = []
        currency_pattern = r"\$?[\d,]+\.?\d*"

        for word in receipt_words:
            text = (
                getattr(word, "text", "")
                if hasattr(word, "text")
                else word.get("text", "")
            )

            # Check if word contains currency
            import re

            if re.match(currency_pattern, text):
                try:
                    # Extract amount
                    amount_str = re.sub(r"[^\d.]", "", text)
                    amount = float(amount_str)

                    # Get context from same line
                    line_id = (
                        getattr(word, "line_id", None)
                        if hasattr(word, "line_id")
                        else word.get("line_id")
                    )
                    full_line = self._get_line_text(line_id, receipt_lines)

                    contexts.append(
                        {
                            "word_id": (
                                getattr(word, "word_id", None)
                                if hasattr(word, "word_id")
                                else word.get("word_id")
                            ),
                            "amount": amount,
                            "text": text,
                            "line_id": line_id,
                            "full_line": full_line,
                            "left_text": self._get_left_text(
                                word, receipt_words
                            ),
                            "x_position": (
                                getattr(word, "x", 0)
                                if hasattr(word, "x")
                                else word.get("x", 0)
                            ),
                            "y_position": (
                                getattr(word, "y", 0)
                                if hasattr(word, "y")
                                else word.get("y", 0)
                            ),
                        }
                    )
                except (ValueError, AttributeError):
                    continue

        return contexts

    def _get_line_text(self, line_id: int, receipt_lines: List) -> str:
        """Get full text of a line."""
        for line in receipt_lines:
            if hasattr(line, "line_id") and line.line_id == line_id:
                return getattr(line, "text", "")
            elif isinstance(line, dict) and line.get("line_id") == line_id:
                return line.get("text", "")
        return ""

    def _get_left_text(self, target_word, receipt_words: List) -> str:
        """Get text to the left of target word on same line."""
        target_line_id = (
            getattr(target_word, "line_id", None)
            if hasattr(target_word, "line_id")
            else target_word.get("line_id")
        )
        target_x = (
            getattr(target_word, "x", 0)
            if hasattr(target_word, "x")
            else target_word.get("x", 0)
        )

        left_words = []
        for word in receipt_words:
            word_line_id = (
                getattr(word, "line_id", None)
                if hasattr(word, "line_id")
                else word.get("line_id")
            )
            word_x = (
                getattr(word, "x", 0)
                if hasattr(word, "x")
                else word.get("x", 0)
            )

            if word_line_id == target_line_id and word_x < target_x:
                word_text = (
                    getattr(word, "text", "")
                    if hasattr(word, "text")
                    else word.get("text", "")
                )
                left_words.append((word_x, word_text))

        # Sort by x position and join
        left_words.sort(key=lambda x: x[0])
        return " ".join(text for _, text in left_words)

    def _query_merchant_patterns(
        self, merchant_name: Optional[str], receipt_words: List
    ) -> List[Dict]:
        """
        Query Pinecone for merchant-specific patterns.

        This is a placeholder for the full implementation that will
        query validated labels from similar merchant receipts.
        """
        if not merchant_name:
            return []

        # TODO: Implement Pinecone query for merchant patterns
        # For now, return empty list
        logger.info(
            f"Merchant pattern query for '{merchant_name}' - not yet implemented"
        )
        return []

    def apply_patterns(
        self, receipt_words: List, pattern_results: Dict[str, List[Dict]]
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
