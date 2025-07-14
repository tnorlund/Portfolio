"""Smart Decision Engine Types and Enums.

This module defines the core types and enums used by the Smart Decision Engine
for receipt labeling decisions.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Set


class DecisionOutcome(Enum):
    """Three-tier decision outcomes for receipt processing."""

    SKIP = "skip"  # Pattern detection sufficient, skip GPT entirely
    BATCH = "batch"  # Some fields missing, queue for batch GPT processing
    REQUIRED = "required"  # Critical fields missing, immediate GPT required


class ConfidenceLevel(Enum):
    """Confidence levels for decision reasoning."""

    HIGH = "high"  # Very confident in decision (>90%)
    MEDIUM = "medium"  # Moderately confident (70-90%)
    LOW = "low"  # Low confidence (<70%)


@dataclass
class FourFieldSummary:
    """Summary of the 4 essential fields for decision making.

    This dataclass provides a clean interface between pattern detection
    and decision making, containing both presence flags and actual values.
    """

    # Field presence (boolean flags)
    merchant_name_found: bool
    date_found: bool
    time_found: bool
    grand_total_found: bool

    # Actual extracted values (for use in DecisionResult)
    merchant_name_value: Optional[str] = None
    date_value: Optional[str] = None
    time_value: Optional[str] = None
    grand_total_value: Optional[str] = None

    # Confidence scores for each field
    merchant_name_confidence: float = 0.0
    date_confidence: float = 0.0
    time_confidence: float = 0.0
    grand_total_confidence: float = 0.0

    @property
    def fields_found_count(self) -> int:
        """Count of fields found."""
        return sum(
            [
                self.merchant_name_found,
                self.date_found,
                self.time_found,
                self.grand_total_found,
            ]
        )

    @property
    def missing_fields(self) -> List[str]:
        """List of missing field names."""
        missing = []
        if not self.merchant_name_found:
            missing.append("MERCHANT_NAME")
        if not self.date_found:
            missing.append("DATE")
        if not self.time_found:
            missing.append("TIME")
        if not self.grand_total_found:
            missing.append("GRAND_TOTAL")
        return missing

    @property
    def all_fields_found(self) -> bool:
        """True if all 4 fields were found."""
        return self.fields_found_count == 4

    @property
    def found_fields_set(self) -> Set[str]:
        """Set of found field names."""
        found = set()
        if self.merchant_name_found:
            found.add("MERCHANT_NAME")
        if self.date_found:
            found.add("DATE")
        if self.time_found:
            found.add("TIME")
        if self.grand_total_found:
            found.add("GRAND_TOTAL")
        return found


@dataclass
class DecisionResult:
    """Structured result from the Decision Engine."""

    action: DecisionOutcome
    confidence: ConfidenceLevel
    reasoning: str

    # Statistics used in decision
    essential_fields_found: Set[str]
    essential_fields_missing: Set[str]
    total_words: int
    labeled_words: int
    unlabeled_meaningful_words: int
    coverage_percentage: float

    # Merchant context
    merchant_name: Optional[str] = None
    merchant_reliability_score: Optional[float] = None

    # Performance metrics
    decision_time_ms: Optional[float] = None
    pinecone_queries_made: int = 0

    @property
    def skip_rate_contribution(self) -> float:
        """Returns 1.0 if this decision skips GPT, 0.0 otherwise."""
        return 1.0 if self.action == DecisionOutcome.SKIP else 0.0


@dataclass
class EssentialFieldsStatus:
    """Status of essential labels required for valid receipt processing."""

    merchant_name_found: bool = False
    date_found: bool = False
    grand_total_found: bool = False
    product_name_found: bool = False  # At least one product

    @property
    def all_critical_found(self) -> bool:
        """Returns True if all absolutely critical fields are present."""
        return (
            self.merchant_name_found
            and self.date_found
            and self.grand_total_found
        )

    @property
    def all_essential_found(self) -> bool:
        """Returns True if all essential fields (including product) are present."""
        return self.all_critical_found and self.product_name_found

    @property
    def missing_critical_fields(self) -> Set[str]:
        """Returns set of missing critical field names."""
        missing = set()
        if not self.merchant_name_found:
            missing.add("MERCHANT_NAME")
        if not self.date_found:
            missing.add("DATE")
        if not self.grand_total_found:
            missing.add("GRAND_TOTAL")
        return missing

    @property
    def missing_essential_fields(self) -> Set[str]:
        """Returns set of all missing essential field names."""
        missing = self.missing_critical_fields.copy()
        if not self.product_name_found:
            missing.add("PRODUCT_NAME")
        return missing

    @property
    def found_essential_fields(self) -> Set[str]:
        """Returns set of all found essential field names."""
        found = set()
        if self.merchant_name_found:
            found.add("MERCHANT_NAME")
        if self.date_found:
            found.add("DATE")
        if self.grand_total_found:
            found.add("GRAND_TOTAL")
        if self.product_name_found:
            found.add("PRODUCT_NAME")
        return found


@dataclass
class MerchantReliabilityData:
    """Merchant-specific reliability data from Pinecone."""

    merchant_name: str
    total_receipts_processed: int
    pattern_only_success_rate: float
    common_labels: Set[str]
    rarely_present_labels: Set[str]
    typical_receipt_structure: Dict[str, any]  # Position info, etc.
    last_updated: datetime

    @property
    def is_reliable(self) -> bool:
        """Returns True if merchant has sufficient data and high success rate."""
        return (
            self.total_receipts_processed >= 5
            and self.pattern_only_success_rate >= 0.8
        )

    @property
    def reliability_score(self) -> float:
        """Returns normalized reliability score (0.0 to 1.0)."""
        if self.total_receipts_processed == 0:
            return 0.0

        # Base score from success rate
        base_score = self.pattern_only_success_rate

        # Boost for more data points (asymptotic to 1.0)
        data_confidence = min(1.0, self.total_receipts_processed / 20.0)

        return base_score * data_confidence


@dataclass
class PatternDetectionSummary:
    """Summary of pattern detection results for decision making."""

    total_words: int
    labeled_words: int
    noise_words: int
    meaningful_unlabeled_words: int

    # Label categories found
    labels_by_type: Dict[
        str, List[str]
    ]  # e.g., {"CURRENCY": ["12.99", "1.50"], ...}
    confidence_scores: Dict[str, float]  # Per-label confidence

    # Essential fields status
    essential_fields: EssentialFieldsStatus

    # Merchant context
    detected_merchant: Optional[str] = None
    merchant_confidence: Optional[float] = None

    @property
    def coverage_percentage(self) -> float:
        """Returns percentage of meaningful words that are labeled."""
        meaningful_total = self.total_words - self.noise_words
        if meaningful_total == 0:
            return 0.0
        return (self.labeled_words / meaningful_total) * 100.0

    def is_high_coverage(self, threshold: float = 90.0) -> bool:
        """Returns True if coverage exceeds threshold."""
        return self.coverage_percentage >= threshold

    def has_few_unlabeled_words(self, max_unlabeled: int = 5) -> bool:
        """Returns True if few meaningful words remain unlabeled."""
        return self.meaningful_unlabeled_words <= max_unlabeled
