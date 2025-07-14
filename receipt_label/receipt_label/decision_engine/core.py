"""Smart Decision Engine Core Implementation.

This module contains the core DecisionEngine class that implements the
pattern-first decision logic for receipt labeling.
"""

import logging
import time
from typing import Any, Dict, List, Optional, Set

from .config import DEFAULT_CONFIG, DecisionEngineConfig
from .types import (
    ConfidenceLevel,
    DecisionOutcome,
    DecisionResult,
    EssentialFieldsStatus,
    MerchantReliabilityData,
    PatternDetectionSummary,
)

logger = logging.getLogger(__name__)


class DecisionEngine:
    """Smart Decision Engine for determining when pattern detection is sufficient.

    Implements the Phase 1 baseline strategy with static thresholds and
    conservative decision making. The engine analyzes pattern detection results
    and decides whether to:
    - SKIP: Pattern detection is sufficient, no GPT needed
    - BATCH: Some fields missing, queue for batch GPT processing
    - REQUIRED: Critical fields missing, immediate GPT required
    """

    def __init__(self, config: Optional[DecisionEngineConfig] = None):
        """Initialize the Decision Engine with configuration.

        Args:
            config: Configuration object. If None, uses default config.
        """
        self.config = config or DEFAULT_CONFIG
        self.config.validate()

        # Performance tracking
        self._decisions_made = 0
        self._total_decision_time_ms = 0.0

        logger.info(
            f"DecisionEngine initialized with config: "
            f"coverage_threshold={self.config.min_coverage_percentage}%, "
            f"max_unlabeled={self.config.max_unlabeled_words}, "
            f"enabled={self.config.enabled}"
        )

    def decide(
        self,
        pattern_summary: PatternDetectionSummary,
        merchant_reliability: Optional[MerchantReliabilityData] = None,
        receipt_context: Optional[Dict[str, Any]] = None,
    ) -> DecisionResult:
        """Make decision on whether GPT is needed for this receipt.

        Args:
            pattern_summary: Summary of pattern detection results
            merchant_reliability: Optional merchant reliability data from Pinecone
            receipt_context: Optional additional context (receipt ID, etc.)

        Returns:
            DecisionResult with action, confidence, and reasoning
        """
        start_time = time.time()

        try:
            # Check if decision engine is enabled
            if not self.config.enabled:
                return self._create_bypass_result(
                    pattern_summary, "Decision engine disabled"
                )

            # Check if this receipt should be processed (gradual rollout)
            if not self.config.should_process_receipt():
                return self._create_bypass_result(
                    pattern_summary, "Outside rollout percentage"
                )

            # Core decision logic
            result = self._make_decision(
                pattern_summary, merchant_reliability, receipt_context
            )

            # Add performance timing
            if self.config.enable_performance_timing:
                decision_time_ms = (time.time() - start_time) * 1000
                result.decision_time_ms = decision_time_ms

                # Update statistics
                self._decisions_made += 1
                self._total_decision_time_ms += decision_time_ms

                # Log if decision took too long
                if decision_time_ms > self.config.max_decision_time_ms:
                    logger.warning(
                        f"Decision took {decision_time_ms:.2f}ms, "
                        f"exceeds threshold of {self.config.max_decision_time_ms}ms"
                    )

            # Log decision if enabled
            if (
                self.config.enable_detailed_logging
                or self.config.log_all_decisions
            ):
                self._log_decision(result, pattern_summary)

            return result

        except Exception as e:
            logger.error(f"Error in decision engine: {e}", exc_info=True)
            # Fail safe - default to GPT required
            return self._create_error_result(pattern_summary, str(e))

    def _make_decision(
        self,
        pattern_summary: PatternDetectionSummary,
        merchant_reliability: Optional[MerchantReliabilityData],
        receipt_context: Optional[Dict[str, Any]],
    ) -> DecisionResult:
        """Core decision making logic."""

        # Step 1: Check essential fields (critical requirement)
        essential_status = pattern_summary.essential_fields
        missing_critical = essential_status.missing_critical_fields

        if missing_critical:
            return DecisionResult(
                action=DecisionOutcome.REQUIRED,
                confidence=ConfidenceLevel.HIGH,
                reasoning=f"Missing critical fields: {', '.join(missing_critical)}",
                essential_fields_found=essential_status.found_essential_fields,
                essential_fields_missing=missing_critical,
                total_words=pattern_summary.total_words,
                labeled_words=pattern_summary.labeled_words,
                unlabeled_meaningful_words=pattern_summary.meaningful_unlabeled_words,
                coverage_percentage=pattern_summary.coverage_percentage,
                merchant_name=pattern_summary.detected_merchant,
                merchant_reliability_score=(
                    merchant_reliability.reliability_score
                    if merchant_reliability
                    else None
                ),
            )

        # Step 2: Get thresholds (potentially adjusted for merchant)
        merchant_score = (
            merchant_reliability.reliability_score
            if merchant_reliability
            else None
        )
        coverage_threshold = self.config.get_coverage_threshold_for_merchant(
            merchant_score
        )
        unlabeled_threshold = self.config.get_unlabeled_threshold_for_merchant(
            merchant_score
        )

        # Step 3: Check coverage and unlabeled words
        coverage_ok = pattern_summary.coverage_percentage >= coverage_threshold
        unlabeled_ok = (
            pattern_summary.meaningful_unlabeled_words <= unlabeled_threshold
        )

        # Step 4: Check if we have at least one product name (preferred but not critical)
        has_product = essential_status.product_name_found

        # Step 5: Apply merchant-specific logic
        merchant_confidence_boost = self._get_merchant_confidence_boost(
            pattern_summary, merchant_reliability
        )

        # Step 6: Make decision based on all factors
        if coverage_ok and unlabeled_ok and has_product:
            # Best case - can skip GPT entirely
            confidence = (
                ConfidenceLevel.HIGH
                if merchant_confidence_boost > 0
                else ConfidenceLevel.MEDIUM
            )
            return DecisionResult(
                action=DecisionOutcome.SKIP,
                confidence=confidence,
                reasoning=f"High coverage ({pattern_summary.coverage_percentage:.1f}%), "
                f"few unlabeled words ({pattern_summary.meaningful_unlabeled_words}), "
                f"all essential fields present",
                essential_fields_found=essential_status.found_essential_fields,
                essential_fields_missing=essential_status.missing_essential_fields,
                total_words=pattern_summary.total_words,
                labeled_words=pattern_summary.labeled_words,
                unlabeled_meaningful_words=pattern_summary.meaningful_unlabeled_words,
                coverage_percentage=pattern_summary.coverage_percentage,
                merchant_name=pattern_summary.detected_merchant,
                merchant_reliability_score=merchant_score,
            )

        elif coverage_ok and unlabeled_ok:
            # Good coverage but missing product name - can batch process
            return DecisionResult(
                action=DecisionOutcome.BATCH,
                confidence=ConfidenceLevel.MEDIUM,
                reasoning=f"Good coverage ({pattern_summary.coverage_percentage:.1f}%) "
                f"but missing product name - safe for batch processing",
                essential_fields_found=essential_status.found_essential_fields,
                essential_fields_missing=essential_status.missing_essential_fields,
                total_words=pattern_summary.total_words,
                labeled_words=pattern_summary.labeled_words,
                unlabeled_meaningful_words=pattern_summary.meaningful_unlabeled_words,
                coverage_percentage=pattern_summary.coverage_percentage,
                merchant_name=pattern_summary.detected_merchant,
                merchant_reliability_score=merchant_score,
            )

        else:
            # Poor coverage or too many unlabeled words - need GPT
            issues = []
            if not coverage_ok:
                issues.append(
                    f"low coverage ({pattern_summary.coverage_percentage:.1f}% < {coverage_threshold}%)"
                )
            if not unlabeled_ok:
                issues.append(
                    f"too many unlabeled words ({pattern_summary.meaningful_unlabeled_words} > {unlabeled_threshold})"
                )

            return DecisionResult(
                action=DecisionOutcome.REQUIRED,
                confidence=ConfidenceLevel.MEDIUM,
                reasoning=f"GPT required due to: {', '.join(issues)}",
                essential_fields_found=essential_status.found_essential_fields,
                essential_fields_missing=essential_status.missing_essential_fields,
                total_words=pattern_summary.total_words,
                labeled_words=pattern_summary.labeled_words,
                unlabeled_meaningful_words=pattern_summary.meaningful_unlabeled_words,
                coverage_percentage=pattern_summary.coverage_percentage,
                merchant_name=pattern_summary.detected_merchant,
                merchant_reliability_score=merchant_score,
            )

    def _get_merchant_confidence_boost(
        self,
        pattern_summary: PatternDetectionSummary,
        merchant_reliability: Optional[MerchantReliabilityData],
    ) -> float:
        """Calculate confidence boost based on merchant reliability.

        Returns:
            Confidence boost value (0.0 to 1.0)
        """
        if not merchant_reliability:
            return 0.0

        # Phase 1: Simple boost based on reliability score
        if merchant_reliability.is_reliable:
            return 0.2  # 20% confidence boost for reliable merchants

        return 0.0

    def _create_bypass_result(
        self, pattern_summary: PatternDetectionSummary, reason: str
    ) -> DecisionResult:
        """Create result for bypassed decisions (engine disabled, etc.)."""
        return DecisionResult(
            action=DecisionOutcome.REQUIRED,  # Default to GPT when bypassed
            confidence=ConfidenceLevel.HIGH,
            reasoning=f"Bypassed: {reason}",
            essential_fields_found=pattern_summary.essential_fields.found_essential_fields,
            essential_fields_missing=pattern_summary.essential_fields.missing_essential_fields,
            total_words=pattern_summary.total_words,
            labeled_words=pattern_summary.labeled_words,
            unlabeled_meaningful_words=pattern_summary.meaningful_unlabeled_words,
            coverage_percentage=pattern_summary.coverage_percentage,
            merchant_name=pattern_summary.detected_merchant,
        )

    def _create_error_result(
        self, pattern_summary: PatternDetectionSummary, error: str
    ) -> DecisionResult:
        """Create result for error cases (fail safe to GPT)."""
        return DecisionResult(
            action=DecisionOutcome.REQUIRED,  # Fail safe to GPT
            confidence=ConfidenceLevel.LOW,
            reasoning=f"Error in decision engine: {error}",
            essential_fields_found=set(),
            essential_fields_missing={
                "MERCHANT_NAME",
                "DATE",
                "GRAND_TOTAL",
                "PRODUCT_NAME",
            },
            total_words=pattern_summary.total_words,
            labeled_words=pattern_summary.labeled_words,
            unlabeled_meaningful_words=pattern_summary.meaningful_unlabeled_words,
            coverage_percentage=pattern_summary.coverage_percentage,
            merchant_name=pattern_summary.detected_merchant,
        )

    def _log_decision(
        self, result: DecisionResult, pattern_summary: PatternDetectionSummary
    ) -> None:
        """Log decision details for monitoring and debugging."""
        if (
            result.action == DecisionOutcome.SKIP
            or self.config.log_all_decisions
        ):
            logger.info(
                f"Decision: {result.action.value.upper()} "
                f"(confidence: {result.confidence.value}) "
                f"for merchant '{result.merchant_name}' - "
                f"{result.reasoning} "
                f"[Coverage: {result.coverage_percentage:.1f}%, "
                f"Unlabeled: {result.unlabeled_meaningful_words}]"
            )

    def get_performance_stats(self) -> Dict[str, Any]:
        """Get performance statistics for monitoring."""
        if self._decisions_made == 0:
            return {
                "decisions_made": 0,
                "average_decision_time_ms": 0.0,
                "total_decision_time_ms": 0.0,
            }

        return {
            "decisions_made": self._decisions_made,
            "average_decision_time_ms": self._total_decision_time_ms
            / self._decisions_made,
            "total_decision_time_ms": self._total_decision_time_ms,
            "max_allowed_time_ms": self.config.max_decision_time_ms,
        }

    def reset_stats(self) -> None:
        """Reset performance statistics."""
        self._decisions_made = 0
        self._total_decision_time_ms = 0.0
