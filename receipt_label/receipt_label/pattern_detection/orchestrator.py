"""Orchestrates parallel pattern detection for high performance."""

import asyncio
import time
from typing import Dict, List, Optional

from receipt_dynamo.entities import ReceiptWord

from receipt_label.pattern_detection.base import PatternMatch, PatternType
from receipt_label.pattern_detection.contact import ContactPatternDetector
from receipt_label.pattern_detection.currency import CurrencyPatternDetector
from receipt_label.pattern_detection.datetime_patterns import (
    DateTimePatternDetector,
)
from receipt_label.pattern_detection.quantity import QuantityPatternDetector


class ParallelPatternOrchestrator:
    """Orchestrates parallel execution of pattern detectors for optimal performance."""

    def __init__(self, timeout: float = 0.1):  # 100ms timeout
        """Initialize the orchestrator.

        Args:
            timeout: Maximum time in seconds to wait for pattern detection
        """
        self.timeout = timeout
        self._detectors = {
            "currency": CurrencyPatternDetector(),
            "datetime": DateTimePatternDetector(),
            "contact": ContactPatternDetector(),
            "quantity": QuantityPatternDetector(),
        }

    async def detect_all_patterns(
        self,
        words: List[ReceiptWord],
        merchant_patterns: Optional[Dict] = None,
    ) -> Dict[str, List[PatternMatch]]:
        """Run all pattern detectors in parallel.

        Args:
            words: List of receipt words to analyze (already filtered for noise)
            merchant_patterns: Optional merchant-specific patterns from Epic #189

        Returns:
            Dictionary mapping pattern detector names to their matches
        """
        start_time = time.time()

        # Create detection tasks
        tasks = {
            name: asyncio.create_task(
                self._run_detector_with_timeout(detector, words)
            )
            for name, detector in self._detectors.items()
        }

        # If merchant patterns provided, apply them as well
        if merchant_patterns:
            tasks["merchant"] = asyncio.create_task(
                self._apply_merchant_patterns(words, merchant_patterns)
            )

        # Wait for all tasks with timeout
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*tasks.values(), return_exceptions=True),
                timeout=self.timeout,
            )
        except asyncio.TimeoutError:
            # Collect whatever completed
            results = []
            for task in tasks.values():
                if task.done() and not task.cancelled():
                    try:
                        results.append(task.result())
                    except (asyncio.CancelledError, RuntimeError, AttributeError):
                        results.append([])
                else:
                    task.cancel()
                    results.append([])

        # Map results back to detector names
        pattern_results = {}
        for i, (name, task) in enumerate(tasks.items()):
            if i < len(results) and isinstance(results[i], list):
                pattern_results[name] = results[i]
            else:
                pattern_results[name] = []

        # Add timing metadata
        elapsed_time = time.time() - start_time
        pattern_results["_metadata"] = {
            "execution_time_ms": elapsed_time * 1000,
            "word_count": len(words),
            "timeout_occurred": elapsed_time >= self.timeout,
        }

        return pattern_results

    async def _run_detector_with_timeout(
        self, detector, words: List[ReceiptWord]
    ) -> List[PatternMatch]:
        """Run a single detector with error handling."""
        try:
            return await detector.detect(words)
        except (asyncio.TimeoutError, RuntimeError, AttributeError, ValueError) as e:
            # Log error but don't fail entire detection
            print(f"Error in {detector.__class__.__name__}: {e}")
            return []

    async def _apply_merchant_patterns(
        self, words: List[ReceiptWord], merchant_patterns: Dict
    ) -> List[PatternMatch]:
        """Apply merchant-specific patterns from Epic #189.

        This is a placeholder for integration with the merchant pattern system.
        """
        matches = []

        # Example structure of merchant_patterns:
        # {
        #     "word_patterns": {
        #         "big mac": "PRODUCT_NAME",
        #         "sales tax": "TAX",
        #         "visa": "PAYMENT_METHOD"
        #     },
        #     "confidence_threshold": 0.8
        # }

        if "word_patterns" in merchant_patterns:
            for word in words:
                word_lower = word.text.lower()

                for pattern, label in merchant_patterns[
                    "word_patterns"
                ].items():
                    if pattern in word_lower:
                        # Map label to pattern type
                        pattern_type = self._label_to_pattern_type(label)
                        if pattern_type:
                            match = PatternMatch(
                                word=word,
                                pattern_type=pattern_type,
                                confidence=merchant_patterns.get(
                                    "confidence_threshold", 0.8
                                ),
                                matched_text=word.text,
                                extracted_value=word.text,
                                metadata={
                                    "source": "merchant_pattern",
                                    "pattern": pattern,
                                    "label": label,
                                },
                            )
                            matches.append(match)

        return matches

    def _label_to_pattern_type(self, label: str) -> Optional[PatternType]:
        """Map receipt label to pattern type."""
        # This mapping connects Epic #189 labels to our pattern types
        label_mapping = {
            "GRAND_TOTAL": PatternType.GRAND_TOTAL,
            "SUBTOTAL": PatternType.SUBTOTAL,
            "TAX": PatternType.TAX,
            "DISCOUNT": PatternType.DISCOUNT,
            "DATE": PatternType.DATE,
            "TIME": PatternType.TIME,
            "PHONE_NUMBER": PatternType.PHONE_NUMBER,
            "EMAIL": PatternType.EMAIL,
            "WEBSITE": PatternType.WEBSITE,
            "QUANTITY": PatternType.QUANTITY,
        }
        return label_mapping.get(label)

    def aggregate_patterns(
        self, pattern_results: Dict[str, List[PatternMatch]]
    ) -> Dict[str, Dict]:
        """Aggregate pattern results for easier consumption.

        Args:
            pattern_results: Raw results from detect_all_patterns

        Returns:
            Aggregated results organized by pattern type
        """
        aggregated = {}

        # Collect all matches by pattern type
        for detector_name, matches in pattern_results.items():
            if detector_name == "_metadata":
                continue

            for match in matches:
                pattern_type = match.pattern_type.name

                if pattern_type not in aggregated:
                    aggregated[pattern_type] = {
                        "matches": [],
                        "count": 0,
                        "high_confidence_count": 0,
                    }

                aggregated[pattern_type]["matches"].append(match)
                aggregated[pattern_type]["count"] += 1

                if match.confidence >= 0.8:
                    aggregated[pattern_type]["high_confidence_count"] += 1

        return aggregated

    def get_essential_fields_status(
        self, pattern_results: Dict[str, List[PatternMatch]]
    ) -> Dict[str, bool]:
        """Check if essential fields were found.

        Essential fields for Epic #191 smart decision:
        - MERCHANT_NAME (from metadata/merchant patterns)
        - DATE
        - GRAND_TOTAL
        - At least one PRODUCT (from merchant patterns)

        Args:
            pattern_results: Results from detect_all_patterns

        Returns:
            Dictionary indicating which essential fields were found
        """
        aggregated = self.aggregate_patterns(pattern_results)

        return {
            "has_date": "DATE" in aggregated
            and aggregated["DATE"]["count"] > 0,
            "has_total": "GRAND_TOTAL" in aggregated
            and aggregated["GRAND_TOTAL"]["count"] > 0,
            "has_merchant": "merchant" in pattern_results
            and len(pattern_results["merchant"]) > 0,
            "has_product": self._has_product_patterns(pattern_results),
        }

    def _has_product_patterns(
        self, pattern_results: Dict[str, List[PatternMatch]]
    ) -> bool:
        """Check if any product-related patterns were found."""
        # Check merchant patterns for product labels
        if "merchant" in pattern_results:
            for match in pattern_results["merchant"]:
                if match.metadata.get("label") == "PRODUCT_NAME":
                    return True

        # Check for quantity patterns (usually associated with products)
        for detector_name in ["quantity"]:
            if (
                detector_name in pattern_results
                and pattern_results[detector_name]
            ):
                return True

        return False

    async def benchmark_performance(
        self, word_counts: List[int] = None
    ) -> Dict[str, Dict[str, float]]:
        """Benchmark detection performance with different word counts.

        Args:
            word_counts: List of word counts to test (default: [10, 50, 100, 200])

        Returns:
            Performance metrics for each word count
        """
        if word_counts is None:
            word_counts = [10, 50, 100, 200]

        results = {}

        for count in word_counts:
            # Create dummy words for testing
            dummy_words = [
                ReceiptWord(
                    receipt_id=1,
                    image_id="550e8400-e29b-41d4-a716-446655440000",
                    line_id=i // 5,
                    word_id=i,
                    text=f"WORD{i}",
                    bounding_box={
                        "x": 0,
                        "y": i * 10,
                        "width": 50,
                        "height": 10,
                    },
                    top_left={"x": 0, "y": i * 10},
                    top_right={"x": 50, "y": i * 10},
                    bottom_left={"x": 0, "y": i * 10 + 10},
                    bottom_right={"x": 50, "y": i * 10 + 10},
                    angle_degrees=0.0,
                    angle_radians=0.0,
                    confidence=0.95,
                )
                for i in range(count)
            ]

            # Add some patterns to detect
            if count > 0:
                dummy_words[0].text = "$19.99"
            if count > 1:
                dummy_words[1].text = "2024-01-15"
            if count > 2:
                dummy_words[2].text = "(555) 123-4567"
            if count > 3:
                dummy_words[3].text = "3 @ $5.99"

            # Run detection
            start_time = time.time()
            pattern_results = await self.detect_all_patterns(dummy_words)
            elapsed_time = time.time() - start_time

            results[f"{count}_words"] = {
                "execution_time_ms": elapsed_time * 1000,
                "patterns_found": sum(
                    len(matches)
                    for name, matches in pattern_results.items()
                    if name != "_metadata"
                ),
                "timeout_occurred": pattern_results["_metadata"][
                    "timeout_occurred"
                ],
            }

        return results
