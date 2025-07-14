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
        fields_found = self._check_four_fields(
            result.pattern_results, metadata
        )

        # Step 3: Make simplified decision
        decision = self._make_simple_decision(fields_found, len(words))

        processing_time = (time.time() - start_time) * 1000

        logger.info(
            f"Four Field Decision: {decision.action.value.upper()} - "
            f"Fields: {fields_found} - Time: {processing_time:.1f}ms"
        )

        return decision

    def _check_four_fields(
        self,
        pattern_results: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, bool]:
        """Check if we found each of the 4 required fields."""

        fields = {
            "MERCHANT_NAME": False,
            "DATE": False,
            "TIME": False,
            "GRAND_TOTAL": False,
        }

        # 1. Check MERCHANT_NAME
        # Use metadata first (highest confidence)
        if metadata and metadata.get("merchant_name"):
            fields["MERCHANT_NAME"] = True
        # Or check pattern results
        elif "merchant" in pattern_results and pattern_results["merchant"]:
            fields["MERCHANT_NAME"] = True

        # 2. Check DATE
        if ("datetime" in pattern_results and pattern_results["datetime"]) or (
            "date" in pattern_results and pattern_results["date"]
        ):
            fields["DATE"] = True

        # 3. Check TIME
        # Often included with datetime patterns
        if "datetime" in pattern_results and pattern_results["datetime"]:
            # Check if any datetime pattern includes time information
            for pattern in pattern_results["datetime"]:
                if hasattr(pattern, "extracted_value"):
                    # If the extracted value includes time info, mark TIME as found
                    text = str(pattern.extracted_value).lower()
                    if any(
                        time_indicator in text
                        for time_indicator in [":", "am", "pm", "time"]
                    ):
                        fields["TIME"] = True
                        break

        # 4. Check GRAND_TOTAL
        # Look in currency patterns for totals
        if "currency" in pattern_results and pattern_results["currency"]:
            # Currency patterns often include totals
            # For simplicity, if we have currency patterns, assume we found some total
            fields["GRAND_TOTAL"] = True

        return fields

    def _make_simple_decision(
        self, fields_found: Dict[str, bool], total_words: int
    ) -> DecisionResult:
        """Make a simple decision based only on the 4 fields."""

        # Count how many fields we found
        found_count = sum(fields_found.values())
        missing_fields = [
            field for field, found in fields_found.items() if not found
        ]

        if found_count == 4:
            # All 4 fields found - SKIP GPT!
            return DecisionResult(
                action=DecisionOutcome.SKIP,
                confidence=ConfidenceLevel.HIGH,
                reasoning="All 4 required fields found: MERCHANT_NAME, DATE, TIME, GRAND_TOTAL",
                essential_fields_found=set(fields_found.keys()),
                essential_fields_missing=set(),
                total_words=total_words,
                labeled_words=found_count,  # Simplified
                unlabeled_meaningful_words=total_words
                - found_count,  # Simplified
                coverage_percentage=(
                    found_count / 4 * 100
                ),  # Percentage of required fields found
                merchant_name=fields_found.get("MERCHANT_NAME", "Unknown"),
            )

        elif found_count == 3:
            # Missing only 1 field - could be suitable for BATCH
            missing_field = missing_fields[0]
            if missing_field in ["TIME", "GRAND_TOTAL"]:
                return DecisionResult(
                    action=DecisionOutcome.BATCH,
                    confidence=ConfidenceLevel.MEDIUM,
                    reasoning=f"3/4 fields found, only missing {missing_field} - suitable for batch",
                    essential_fields_found={
                        field for field, found in fields_found.items() if found
                    },
                    essential_fields_missing={missing_field},
                    total_words=total_words,
                    labeled_words=found_count,
                    unlabeled_meaningful_words=total_words - found_count,
                    coverage_percentage=(found_count / 4 * 100),
                    merchant_name=fields_found.get("MERCHANT_NAME", "Unknown"),
                )

        # Missing 2+ fields or missing critical field (MERCHANT_NAME, DATE)
        return DecisionResult(
            action=DecisionOutcome.REQUIRED,
            confidence=ConfidenceLevel.HIGH,
            reasoning=f"Missing critical fields: {', '.join(missing_fields)}",
            essential_fields_found={
                field for field, found in fields_found.items() if found
            },
            essential_fields_missing=set(missing_fields),
            total_words=total_words,
            labeled_words=found_count,
            unlabeled_meaningful_words=total_words - found_count,
            coverage_percentage=(found_count / 4 * 100),
            merchant_name=fields_found.get("MERCHANT_NAME", "Unknown"),
        )
