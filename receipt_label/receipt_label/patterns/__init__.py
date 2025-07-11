"""
Pattern detection modules for receipt labeling.

This module contains various pattern detectors that identify common patterns
in receipt text without requiring GPT calls, including dates, times, phone numbers,
email addresses, websites, and quantity patterns.
"""

from .contact_patterns import ContactPatternDetector
from .date_patterns import DatePatternDetector
from .quantity_patterns import QuantityPatternDetector

__all__ = [
    "DatePatternDetector",
    "ContactPatternDetector",
    "QuantityPatternDetector",
]
