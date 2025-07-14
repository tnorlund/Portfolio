"""
Enhanced Decision Engine Integration with Google Places Metadata.

This module extends the standard integration to leverage Google Places metadata
for improved pattern detection and decision making, including fuzzy merchant matching.
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from receipt_label.decision_engine import DecisionEngineConfig
from receipt_label.decision_engine.enhanced_config import (
    EnhancedDecisionEngineConfig,
)
from receipt_label.decision_engine.integration import (
    DecisionEngineIntegrationResult,
    DecisionEngineOrchestrator,
)
from receipt_label.models import ReceiptWord
from receipt_label.pattern_detection.enhanced_orchestrator import (
    StandardizedPatternMatch,
)
from receipt_label.pattern_detection.fuzzy_merchant_detector import (
    FuzzyMerchantDetector,
)

logger = logging.getLogger(__name__)


@dataclass
class EnhancedReceiptContext:
    """Extended receipt context with Google Places metadata."""

    image_id: str
    receipt_id: str
    merchant_name: Optional[str] = None
    merchant_category: Optional[str] = None
    address: Optional[str] = None
    phone_number: Optional[str] = None
    place_id: Optional[str] = None
    validated_by: Optional[str] = None  # ADDRESS_LOOKUP, PHONE_LOOKUP, etc.

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for passing to orchestrator."""
        return {
            "image_id": self.image_id,
            "receipt_id": self.receipt_id,
            "merchant_name": self.merchant_name,
            "merchant_category": self.merchant_category,
            "address": self.address,
            "phone_number": self.phone_number,
            "place_id": self.place_id,
            "validated_by": self.validated_by,
        }

    @classmethod
    def from_metadata(
        cls, image_id: str, receipt_id: str, metadata: Dict[str, Any]
    ) -> "EnhancedReceiptContext":
        """Create from receipt metadata dictionary."""
        return cls(
            image_id=image_id,
            receipt_id=receipt_id,
            merchant_name=metadata.get("merchant_name"),
            merchant_category=metadata.get("merchant_category"),
            address=metadata.get("address"),
            phone_number=metadata.get("phone_number"),
            place_id=metadata.get("place_id"),
            validated_by=metadata.get("validated_by"),
        )


class EnhancedDecisionEngineOrchestrator(DecisionEngineOrchestrator):
    """
    Enhanced orchestrator that leverages Google Places metadata for better decisions.
    """

    def __init__(
        self, config: Optional[DecisionEngineConfig] = None, **kwargs
    ):
        # Use enhanced config if not provided
        if config is None:
            config = EnhancedDecisionEngineConfig(
                enabled=True,
                rollout_percentage=100.0,
                min_coverage_percentage=30.0,
                max_unlabeled_words=100,
            )
        super().__init__(config=config, **kwargs)
        self._metadata_boost_enabled = True
        self._fuzzy_detector = FuzzyMerchantDetector(min_similarity=75.0)

    async def process_receipt_with_metadata(
        self, words: List[ReceiptWord], context: EnhancedReceiptContext
    ) -> DecisionEngineIntegrationResult:
        """
        Process receipt with enhanced metadata context.

        This method enhances the standard processing by:
        1. Using merchant name for merchant-specific patterns
        2. Injecting validated metadata fields as high-confidence patterns
        3. Boosting confidence for patterns that match metadata
        """

        # Get standard results first
        result = await self.process_receipt(words, context.to_dict())

        if self._metadata_boost_enabled and context.merchant_name:
            # Enhance pattern results with metadata
            enhanced_results = self._enhance_with_metadata(
                result.pattern_results, context, words
            )

            # Re-evaluate decision with enhanced results
            pattern_summary = self._convert_to_pattern_summary(
                words, enhanced_results
            )

            # Update merchant detection if metadata provides it
            if context.merchant_name and not pattern_summary.detected_merchant:
                pattern_summary.detected_merchant = context.merchant_name
                pattern_summary.essential_fields.found_essential_fields[
                    "MERCHANT_NAME"
                ] = True

            # Re-run decision with enhanced data
            merchant_reliability = None
            if self.pinecone_helper and pattern_summary.detected_merchant:
                merchant_reliability = (
                    await self.pinecone_helper.get_merchant_reliability(
                        pattern_summary.detected_merchant
                    )
                )

            decision = self.decision_engine.decide(
                pattern_summary, merchant_reliability, context.to_dict()
            )

            # Update result with enhanced decision
            result.decision = decision
            result.pattern_results = enhanced_results
            result.should_call_gpt = decision.action != "SKIP"
            result.use_batch_processing = decision.action == "BATCH"

            # Log enhancement impact
            logger.info(
                f"Metadata enhancement impact: "
                f"merchant='{context.merchant_name}', "
                f"category='{context.merchant_category}', "
                f"decision={decision.action}"
            )

        return result

    def _enhance_with_metadata(
        self,
        pattern_results: Dict[str, Any],
        context: EnhancedReceiptContext,
        words: List[ReceiptWord],
    ) -> Dict[str, Any]:
        """
        Enhance pattern results with validated metadata.

        This uses fuzzy matching to find the merchant name in the receipt text
        and adds high-confidence patterns based on validated Google Places data.
        """
        enhanced = pattern_results.copy()

        # Use fuzzy matching to find merchant name in receipt
        if context.merchant_name:
            # First, try fuzzy matching to find the merchant in the actual text
            fuzzy_matches = self._fuzzy_detector.detect_merchant(
                words=words,
                known_merchant=context.merchant_name,
                metadata={
                    "merchant_name": context.merchant_name,
                    "merchant_category": context.merchant_category,
                    "place_id": context.place_id,
                },
            )

            if fuzzy_matches:
                # Found the merchant name in the receipt text
                if "merchant" not in enhanced:
                    enhanced["merchant"] = []
                enhanced["merchant"].extend(fuzzy_matches)
                logger.info(
                    f"Fuzzy matched merchant '{context.merchant_name}' "
                    f"with {len(fuzzy_matches)} matches"
                )
            else:
                # Couldn't find it in text, but we know it from metadata
                if "merchant" not in enhanced:
                    enhanced["merchant"] = []

                enhanced["merchant"].append(
                    StandardizedPatternMatch(
                        word=words[0],  # Placeholder
                        extracted_value=context.merchant_name,
                        confidence=0.95,  # High confidence but not 1.0 since not found in text
                        pattern_type="merchant_metadata",
                        metadata={
                            "source": "google_places_metadata_only",
                            "place_id": context.place_id,
                            "category": context.merchant_category,
                            "note": "Merchant name from metadata, not found in receipt text",
                        },
                    )
                )

        # Add phone as high-confidence pattern if not already detected
        if context.phone_number and "contact" not in enhanced:
            enhanced["contact"] = []

        if context.phone_number and "contact" in enhanced:
            # Check if phone already detected
            phone_detected = any(
                p.extracted_value == context.phone_number
                for p in enhanced["contact"]
                if hasattr(p, "extracted_value")
            )

            if not phone_detected:
                enhanced["contact"].append(
                    StandardizedPatternMatch(
                        word=words[0],  # Placeholder
                        extracted_value=context.phone_number,
                        confidence=1.0,
                        pattern_type="phone_metadata",
                        metadata={
                            "source": "google_places",
                            "validated": True,
                        },
                    )
                )

        # Boost confidence for patterns that match metadata
        if context.address:
            # Look for address patterns and boost their confidence
            for category in ["contact", "address"]:
                if category in enhanced:
                    for pattern in enhanced[category]:
                        if hasattr(pattern, "extracted_value"):
                            # Simple check if pattern value is part of the address
                            if (
                                str(pattern.extracted_value).lower()
                                in context.address.lower()
                            ):
                                # Boost confidence to at least 0.9
                                pattern.confidence = max(
                                    pattern.confidence, 0.9
                                )
                                if hasattr(pattern, "metadata"):
                                    pattern.metadata[
                                        "validated_by_metadata"
                                    ] = True

        return enhanced

    def enable_metadata_boost(self, enabled: bool = True):
        """Enable or disable metadata confidence boosting."""
        self._metadata_boost_enabled = enabled
        logger.info(f"Metadata boost {'enabled' if enabled else 'disabled'}")


# Convenience function
async def process_receipt_with_google_metadata(
    words: List[ReceiptWord],
    metadata: Dict[str, Any],
    image_id: str,
    receipt_id: str,
    config: Optional[DecisionEngineConfig] = None,
) -> DecisionEngineIntegrationResult:
    """
    Process a receipt with Google Places metadata.

    Args:
        words: OCR words from receipt
        metadata: Google Places metadata dictionary
        image_id: Image ID
        receipt_id: Receipt ID
        config: Optional decision engine configuration

    Returns:
        DecisionEngineIntegrationResult with enhanced pattern detection
    """
    orchestrator = EnhancedDecisionEngineOrchestrator(
        config=config or DecisionEngineConfig(enabled=True),
        optimization_level="advanced",
    )

    context = EnhancedReceiptContext.from_metadata(
        image_id=image_id, receipt_id=receipt_id, metadata=metadata
    )

    return await orchestrator.process_receipt_with_metadata(words, context)
