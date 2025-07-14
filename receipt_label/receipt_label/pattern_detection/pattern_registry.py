"""
Pattern Detection Registry and Factory.

This module provides a centralized registry for all pattern detectors,
enabling better organization, categorization, and selective invocation.
"""

from typing import Dict, List, Type, Set, Optional
from enum import Enum
from dataclasses import dataclass

from receipt_label.pattern_detection.base import PatternDetector, PatternType
from receipt_label.pattern_detection.pattern_utils import PatternOptimizer
from receipt_dynamo.entities import ReceiptWord


class DetectorCategory(Enum):
    """Categories of pattern detectors for better organization."""

    FINANCIAL = "financial"  # Currency, tax, totals, discounts
    TEMPORAL = "temporal"  # Dates, times
    CONTACT = "contact"  # Phone, email, website
    QUANTITY = "quantity"  # Amounts, weights, volumes, counts
    MERCHANT = "merchant"  # Store-specific patterns
    STRUCTURAL = "structural"  # Headers, footers, line separators


@dataclass
class DetectorMetadata:
    """Metadata about a pattern detector."""

    name: str
    category: DetectorCategory
    detector_class: Type[PatternDetector]
    supported_patterns: List[PatternType]
    priority: int = 5  # 1 = highest priority, 10 = lowest
    can_run_parallel: bool = True
    requires_position_data: bool = False
    description: str = ""


class PatternDetectorRegistry:
    """Registry for managing all pattern detectors."""

    def __init__(self):
        self._detectors: Dict[str, DetectorMetadata] = {}
        self._categories: Dict[DetectorCategory, List[str]] = {}
        self._pattern_to_detectors: Dict[PatternType, List[str]] = {}

        # Initialize with default detectors
        self._register_default_detectors()

    def register_detector(self, metadata: DetectorMetadata) -> None:
        """Register a new pattern detector."""
        self._detectors[metadata.name] = metadata

        # Update category mapping
        if metadata.category not in self._categories:
            self._categories[metadata.category] = []
        self._categories[metadata.category].append(metadata.name)

        # Update pattern mapping
        for pattern_type in metadata.supported_patterns:
            if pattern_type not in self._pattern_to_detectors:
                self._pattern_to_detectors[pattern_type] = []
            self._pattern_to_detectors[pattern_type].append(metadata.name)

    def get_detector_metadata(self, name: str) -> Optional[DetectorMetadata]:
        """Get metadata for a specific detector."""
        return self._detectors.get(name)

    def get_detectors_by_category(
        self, category: DetectorCategory
    ) -> List[DetectorMetadata]:
        """Get all detectors in a specific category."""
        detector_names = self._categories.get(category, [])
        return [self._detectors[name] for name in detector_names]

    def get_detectors_for_pattern(
        self, pattern_type: PatternType
    ) -> List[DetectorMetadata]:
        """Get detectors that can handle a specific pattern type."""
        detector_names = self._pattern_to_detectors.get(pattern_type, [])
        return [self._detectors[name] for name in detector_names]

    def get_all_detectors(
        self, sort_by_priority: bool = True
    ) -> List[DetectorMetadata]:
        """Get all registered detectors."""
        detectors = list(self._detectors.values())
        if sort_by_priority:
            detectors.sort(key=lambda d: d.priority)
        return detectors

    def create_detector(self, name: str) -> Optional[PatternDetector]:
        """Create an instance of a detector by name."""
        metadata = self._detectors.get(name)
        if metadata:
            return metadata.detector_class()
        return None

    def create_selective_detector_list(
        self,
        words: List[ReceiptWord],
        categories: Optional[List[DetectorCategory]] = None,
    ) -> List[PatternDetector]:
        """
        Create a list of detectors that should run based on word analysis.

        This implements Phase 2's selective detector invocation.
        """
        # Filter by categories if specified
        if categories:
            candidate_detectors = []
            for category in categories:
                candidate_detectors.extend(
                    self.get_detectors_by_category(category)
                )
        else:
            candidate_detectors = self.get_all_detectors()

        # Use optimizer to determine which detectors should run
        active_detectors = []
        for metadata in candidate_detectors:
            if PatternOptimizer.should_run_detector(metadata.name, words):
                detector = self.create_detector(metadata.name)
                if detector:
                    active_detectors.append(detector)

        return active_detectors

    def _register_default_detectors(self) -> None:
        """Register the default set of pattern detectors."""
        from receipt_label.pattern_detection.currency import (
            CurrencyPatternDetector,
        )
        from receipt_label.pattern_detection.contact import (
            ContactPatternDetector,
        )
        from receipt_label.pattern_detection.datetime_patterns import (
            DateTimePatternDetector,
        )
        from receipt_label.pattern_detection.quantity import (
            QuantityPatternDetector,
        )

        # Currency/Financial detector
        self.register_detector(
            DetectorMetadata(
                name="currency",
                category=DetectorCategory.FINANCIAL,
                detector_class=CurrencyPatternDetector,
                supported_patterns=[
                    PatternType.CURRENCY,
                    PatternType.GRAND_TOTAL,
                    PatternType.SUBTOTAL,
                    PatternType.TAX,
                    PatternType.DISCOUNT,
                    PatternType.UNIT_PRICE,
                    PatternType.LINE_TOTAL,
                ],
                priority=1,  # High priority - very common
                can_run_parallel=True,
                requires_position_data=True,
                description="Detects currency amounts and classifies them as totals, tax, discounts, etc.",
            )
        )

        # Contact detector
        self.register_detector(
            DetectorMetadata(
                name="contact",
                category=DetectorCategory.CONTACT,
                detector_class=ContactPatternDetector,
                supported_patterns=[
                    PatternType.PHONE_NUMBER,
                    PatternType.EMAIL,
                    PatternType.WEBSITE,
                ],
                priority=3,  # Medium priority
                can_run_parallel=True,
                requires_position_data=False,
                description="Detects phone numbers, email addresses, and website URLs.",
            )
        )

        # DateTime detector
        self.register_detector(
            DetectorMetadata(
                name="datetime",
                category=DetectorCategory.TEMPORAL,
                detector_class=DateTimePatternDetector,
                supported_patterns=[
                    PatternType.DATE,
                    PatternType.TIME,
                    PatternType.DATETIME,
                ],
                priority=2,  # High priority - dates are essential
                can_run_parallel=True,
                requires_position_data=False,
                description="Detects date and time patterns in various formats.",
            )
        )

        # Quantity detector
        self.register_detector(
            DetectorMetadata(
                name="quantity",
                category=DetectorCategory.QUANTITY,
                detector_class=QuantityPatternDetector,
                supported_patterns=[
                    PatternType.QUANTITY,
                    PatternType.QUANTITY_AT,
                    PatternType.QUANTITY_TIMES,
                    PatternType.QUANTITY_FOR,
                ],
                priority=4,  # Lower priority - mainly for line items
                can_run_parallel=True,
                requires_position_data=True,
                description="Detects quantity patterns like '2 @ $5.99' and weight/volume measurements.",
            )
        )


# Global registry instance
PATTERN_REGISTRY = PatternDetectorRegistry()


class PatternDetectorFactory:
    """Factory for creating optimized detector configurations."""

    @staticmethod
    def create_essential_detectors() -> List[PatternDetector]:
        """
        Create detectors for essential receipt fields only.

        Essential fields: MERCHANT_NAME, DATE, GRAND_TOTAL, at least one PRODUCT_NAME
        """
        return [
            PATTERN_REGISTRY.create_detector("currency"),
            PATTERN_REGISTRY.create_detector("datetime"),
        ]

    @staticmethod
    def create_full_detectors() -> List[PatternDetector]:
        """Create all available detectors."""
        return [
            detector
            for detector in [
                PATTERN_REGISTRY.create_detector(name)
                for name in PATTERN_REGISTRY._detectors.keys()
            ]
            if detector is not None
        ]

    @staticmethod
    def create_category_detectors(
        categories: List[DetectorCategory],
    ) -> List[PatternDetector]:
        """Create detectors for specific categories only."""
        detectors = []
        for category in categories:
            for metadata in PATTERN_REGISTRY.get_detectors_by_category(
                category
            ):
                detector = PATTERN_REGISTRY.create_detector(metadata.name)
                if detector:
                    detectors.append(detector)
        return detectors

    @staticmethod
    def create_adaptive_detectors(
        words: List[ReceiptWord],
    ) -> List[PatternDetector]:
        """
        Create an adaptive set of detectors based on word analysis.

        This is the main entry point for Phase 2's selective invocation.
        """
        return PATTERN_REGISTRY.create_selective_detector_list(words)


def get_detector_summary() -> Dict:
    """Get a summary of all registered detectors for debugging/monitoring."""
    summary = {
        "total_detectors": len(PATTERN_REGISTRY._detectors),
        "categories": {},
        "pattern_coverage": {},
        "detectors": [],
    }

    # Category breakdown
    for category, detector_names in PATTERN_REGISTRY._categories.items():
        summary["categories"][category.value] = len(detector_names)

    # Pattern coverage
    for (
        pattern_type,
        detector_names,
    ) in PATTERN_REGISTRY._pattern_to_detectors.items():
        summary["pattern_coverage"][pattern_type.value] = len(detector_names)

    # Detailed detector info
    for metadata in PATTERN_REGISTRY.get_all_detectors():
        summary["detectors"].append(
            {
                "name": metadata.name,
                "category": metadata.category.value,
                "priority": metadata.priority,
                "patterns": [p.value for p in metadata.supported_patterns],
                "parallel": metadata.can_run_parallel,
                "needs_position": metadata.requires_position_data,
            }
        )

    return summary
