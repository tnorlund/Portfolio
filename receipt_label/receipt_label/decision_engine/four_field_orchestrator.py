"""
Four Field Decision Engine Orchestrator.

Simplified orchestrator that only cares about 4 fields:
MERCHANT_NAME, DATE, TIME, GRAND_TOTAL
"""

import logging
import time
from typing import Any, Dict, List, Optional

from receipt_label.decision_engine.four_field_config import (
    FourFieldDecisionEngineConfig,
)
from receipt_label.decision_engine.integration import (
    DecisionEngineOrchestrator,
)
from receipt_label.decision_engine.types import (
    ConfidenceLevel,
    DecisionOutcome,
    DecisionResult,
    FourFieldSummary,
)
from receipt_label.models import ReceiptWord
from receipt_label.pattern_detection.fuzzy_merchant_detector import (
    FuzzyMerchantDetector,
)

logger = logging.getLogger(__name__)


class FourFieldOrchestrator(DecisionEngineOrchestrator):
    """
    Orchestrator focused solely on finding 4 required fields.
    """

    def __init__(
        self, config: Optional[FourFieldDecisionEngineConfig] = None, **kwargs
    ):
        if config is None:
            config = FourFieldDecisionEngineConfig()

        super().__init__(config=config, **kwargs)
        self._fuzzy_detector = FuzzyMerchantDetector(min_similarity=75.0)

    async def process_receipt_simple(
        self,
        words: List[ReceiptWord],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> DecisionResult:
        """
        Simple processing focused on 4 fields only.

        Args:
            words: Receipt words
            metadata: Google Places metadata

        Returns:
            Simple decision based on 4 fields
        """
        start_time = time.time()

        # Step 1: Run pattern detection
        receipt_context = {
            "image_id": (
                metadata.get("image_id", "unknown") if metadata else "unknown"
            ),
            "receipt_id": (
                metadata.get("receipt_id", "unknown")
                if metadata
                else "unknown"
            ),
            "merchant_name": (
                metadata.get("merchant_name") if metadata else None
            ),
        }

        result = await self.process_receipt(words, receipt_context)

        # Step 2: Check specifically for our 4 fields
        field_summary = self._check_four_fields(
            result.pattern_results, metadata
        )

        # Step 3: Make simplified decision
        decision = self._make_simple_decision(field_summary, len(words))

        processing_time = (time.time() - start_time) * 1000

        logger.info(
            f"Four Field Decision: {decision.action.value.upper()} - "
            f"Fields: {field_summary.fields_found_count}/4 found - Time: {processing_time:.1f}ms"
        )

        return decision

    def _check_four_fields(
        self,
        pattern_results: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> FourFieldSummary:
        """Extract both presence and values for the 4 essential fields."""

        # Initialize with defaults
        merchant_name_found = False
        merchant_name_value = None
        merchant_name_confidence = 0.0
        
        date_found = False
        date_value = None
        date_confidence = 0.0
        
        time_found = False
        time_value = None
        time_confidence = 0.0
        
        grand_total_found = False
        grand_total_value = None
        grand_total_confidence = 0.0

        # 1. Check MERCHANT_NAME
        # Use metadata first (highest confidence)
        if metadata and metadata.get("merchant_name"):
            merchant_name_found = True
            merchant_name_value = metadata["merchant_name"]
            merchant_name_confidence = 1.0  # Metadata is highest confidence
        # Or check pattern results
        elif "merchant" in pattern_results and pattern_results["merchant"]:
            merchant_matches = pattern_results["merchant"]
            if merchant_matches:
                best_match = merchant_matches[0]  # Take first/best match
                merchant_name_found = True
                merchant_name_value = getattr(best_match, "extracted_value", str(best_match))
                merchant_name_confidence = getattr(best_match, "confidence", 0.8)

        # 2. Check DATE
        datetime_patterns = pattern_results.get("datetime", [])
        date_patterns = pattern_results.get("date", [])
        
        if datetime_patterns:
            best_match = datetime_patterns[0]
            date_found = True
            date_value = getattr(best_match, "extracted_value", str(best_match))
            date_confidence = getattr(best_match, "confidence", 0.9)
        elif date_patterns:
            best_match = date_patterns[0]
            date_found = True
            date_value = getattr(best_match, "extracted_value", str(best_match))
            date_confidence = getattr(best_match, "confidence", 0.8)

        # 3. Check TIME
        # Often included with datetime patterns
        if datetime_patterns:
            for pattern in datetime_patterns:
                if hasattr(pattern, "extracted_value"):
                    # If the extracted value includes time info, mark TIME as found
                    text = str(pattern.extracted_value).lower()
                    if any(
                        time_indicator in text
                        for time_indicator in [":", "am", "pm", "time"]
                    ):
                        time_found = True
                        time_value = pattern.extracted_value
                        time_confidence = getattr(pattern, "confidence", 0.9)
                        break

        # 4. Check GRAND_TOTAL
        # Look in currency patterns for totals
        currency_patterns = pattern_results.get("currency", [])
        if currency_patterns:
            # Look for patterns that might be totals (higher confidence for words like "total")
            for pattern in currency_patterns:
                pattern_text = str(getattr(pattern, "extracted_value", "")).lower()
                # Simple heuristic: if it contains "total" or is a reasonable amount
                if "total" in pattern_text or self._looks_like_total(pattern):
                    grand_total_found = True
                    grand_total_value = getattr(pattern, "extracted_value", str(pattern))
                    grand_total_confidence = getattr(pattern, "confidence", 0.7)
                    break
            
            # If no obvious total found, use first currency pattern as fallback
            if not grand_total_found and currency_patterns:
                best_match = currency_patterns[0]
                grand_total_found = True
                grand_total_value = getattr(best_match, "extracted_value", str(best_match))
                grand_total_confidence = getattr(best_match, "confidence", 0.5)

        return FourFieldSummary(
            merchant_name_found=merchant_name_found,
            date_found=date_found,
            time_found=time_found,
            grand_total_found=grand_total_found,
            merchant_name_value=merchant_name_value,
            date_value=date_value,
            time_value=time_value,
            grand_total_value=grand_total_value,
            merchant_name_confidence=merchant_name_confidence,
            date_confidence=date_confidence,
            time_confidence=time_confidence,
            grand_total_confidence=grand_total_confidence,
        )

    def _looks_like_total(self, pattern) -> bool:
        """Simple heuristic to determine if a currency pattern looks like a total."""
        # This is a placeholder - could be enhanced with more sophisticated logic
        return True  # For now, assume any currency could be a total

    def _make_simple_decision(
        self, field_summary: FourFieldSummary, total_words: int
    ) -> DecisionResult:
        """Make a simple decision based on the 4 field summary."""

        if field_summary.all_fields_found:
            # All 4 fields found - SKIP GPT!
            return DecisionResult(
                action=DecisionOutcome.SKIP,
                confidence=ConfidenceLevel.HIGH,
                reasoning="All 4 required fields found: MERCHANT_NAME, DATE, TIME, GRAND_TOTAL",
                essential_fields_found=field_summary.found_fields_set,
                essential_fields_missing=set(),
                total_words=total_words,
                labeled_words=field_summary.fields_found_count,
                unlabeled_meaningful_words=total_words - field_summary.fields_found_count,
                coverage_percentage=(field_summary.fields_found_count / 4 * 100),
                merchant_name=field_summary.merchant_name_value,
            )

        elif field_summary.fields_found_count == 3:
            # Missing only 1 field - could be suitable for BATCH
            missing_field = field_summary.missing_fields[0]
            if missing_field in ["TIME", "GRAND_TOTAL"]:
                return DecisionResult(
                    action=DecisionOutcome.BATCH,
                    confidence=ConfidenceLevel.MEDIUM,
                    reasoning=f"3/4 fields found, only missing {missing_field} - suitable for batch",
                    essential_fields_found=field_summary.found_fields_set,
                    essential_fields_missing={missing_field},
                    total_words=total_words,
                    labeled_words=field_summary.fields_found_count,
                    unlabeled_meaningful_words=total_words - field_summary.fields_found_count,
                    coverage_percentage=(field_summary.fields_found_count / 4 * 100),
                    merchant_name=field_summary.merchant_name_value,
                )

        # Missing 2+ fields or missing critical field (MERCHANT_NAME, DATE)
        return DecisionResult(
            action=DecisionOutcome.REQUIRED,
            confidence=ConfidenceLevel.HIGH,
            reasoning=f"Missing critical fields: {', '.join(field_summary.missing_fields)}",
            essential_fields_found=field_summary.found_fields_set,
            essential_fields_missing=set(field_summary.missing_fields),
            total_words=total_words,
            labeled_words=field_summary.fields_found_count,
            unlabeled_meaningful_words=total_words - field_summary.fields_found_count,
            coverage_percentage=(field_summary.fields_found_count / 4 * 100),
            merchant_name=field_summary.merchant_name_value,
        )
