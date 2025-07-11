"""Pattern detection module for receipt processing.

This module provides high-performance pattern detection for various receipt elements
including currency, dates/times, contact information, and quantities.
"""

from receipt_label.pattern_detection.base import (
    PatternDetector,
    PatternMatch,
    PatternType,
)
from receipt_label.pattern_detection.contact import ContactPatternDetector
from receipt_label.pattern_detection.currency import CurrencyPatternDetector
from receipt_label.pattern_detection.datetime_patterns import (
    DateTimePatternDetector,
)
from receipt_label.pattern_detection.orchestrator import (
    ParallelPatternOrchestrator,
)
from receipt_label.pattern_detection.quantity import QuantityPatternDetector

__all__ = [
    "PatternDetector",
    "PatternMatch",
    "PatternType",
    "CurrencyPatternDetector",
    "DateTimePatternDetector",
    "ContactPatternDetector",
    "QuantityPatternDetector",
    "ParallelPatternOrchestrator",
]
