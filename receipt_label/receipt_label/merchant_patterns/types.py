"""Type definitions for merchant pattern matching."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional


class PatternConfidence(Enum):
    """Confidence levels for pattern matches."""

    HIGH = "HIGH"  # > 0.9 - Auto-label, skip GPT
    MEDIUM = "MEDIUM"  # 0.7-0.9 - Suggest to GPT as context
    LOW = "LOW"  # 0.3-0.7 - Pattern found but uncertain
    NONE = "NONE"  # < 0.3 - No reliable pattern


@dataclass
class PatternMatch:
    """A single word-to-label pattern match."""

    word: str
    suggested_label: str
    confidence: float  # 0.0-1.0
    confidence_level: PatternConfidence
    frequency: int  # How often this pattern appears
    last_validated: datetime
    merchant_name: str
    sample_contexts: List[str]  # Example contexts where pattern was found

    @classmethod
    def from_confidence_score(cls, score: float) -> PatternConfidence:
        """Convert numeric confidence to level."""
        if score >= 0.9:
            return PatternConfidence.HIGH
        elif score >= 0.7:
            return PatternConfidence.MEDIUM
        elif score >= 0.3:
            return PatternConfidence.LOW
        else:
            return PatternConfidence.NONE


@dataclass
class MerchantPatterns:
    """Aggregated patterns for a specific merchant."""

    merchant_name: str
    canonical_merchant_name: Optional[str]
    total_receipts: int
    total_validated_words: int
    pattern_map: Dict[str, List[PatternMatch]]  # word -> possible patterns
    common_labels: Dict[str, int]  # label -> frequency
    last_updated: datetime

    def get_best_pattern(self, word: str) -> Optional[PatternMatch]:
        """Get the highest confidence pattern for a word."""
        patterns = self.pattern_map.get(word, [])
        if not patterns:
            return None

        # Sort by confidence and frequency
        sorted_patterns = sorted(
            patterns,
            key=lambda p: (p.confidence, p.frequency),
            reverse=True,
        )

        return sorted_patterns[0]

    def get_patterns_above_threshold(
        self,
        confidence_threshold: float = 0.7,
    ) -> Dict[str, PatternMatch]:
        """Get all patterns above a confidence threshold."""
        high_confidence_patterns = {}

        for word, patterns in self.pattern_map.items():
            best_pattern = self.get_best_pattern(word)
            if (
                best_pattern
                and best_pattern.confidence >= confidence_threshold
            ):
                high_confidence_patterns[word] = best_pattern

        return high_confidence_patterns


@dataclass
class PatternOverride:
    """Manual correction or exception for a pattern."""

    merchant_name: str
    word: str
    correct_label: str
    incorrect_label: Optional[str]
    reason: str
    created_by: str
    created_at: datetime


@dataclass
class PatternStats:
    """Statistics for pattern matching performance."""

    merchant_name: str
    total_pattern_matches: int
    high_confidence_matches: int
    medium_confidence_matches: int
    low_confidence_matches: int
    gpt_calls_saved: int
    accuracy_rate: Optional[float]  # If we have validation data
    cost_savings_usd: float
    last_calculated: datetime
