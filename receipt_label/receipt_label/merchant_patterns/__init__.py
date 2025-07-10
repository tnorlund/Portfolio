"""Merchant pattern recognition system for efficient label matching.

This module provides pattern extraction and matching capabilities based on
validated labels from the same merchant. It reduces the need for GPT API
calls by leveraging historical patterns.
"""

from .query import (
    MerchantPatternResult,
    get_merchant_patterns,
    query_patterns_for_words,
)
from .types import (
    MerchantPatterns,
    PatternConfidence,
    PatternMatch,
)

__all__ = [
    "get_merchant_patterns",
    "query_patterns_for_words",
    "MerchantPatternResult",
    "PatternMatch",
    "MerchantPatterns",
    "PatternConfidence",
]
