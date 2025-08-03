"""Integration layer for Smart Decision Engine with existing pattern detection.

This module provides adapters and utilities to integrate the decision engine
with the existing pattern detection orchestrator and receipt processing pipeline.
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from receipt_dynamo.entities.receipt_word import ReceiptWord

from ..pattern_detection.orchestrator import ParallelPatternOrchestrator

try:
    from ..pattern_detection.enhanced_orchestrator import (
        EnhancedPatternOrchestrator,
    )
except ImportError:
    EnhancedPatternOrchestrator = None
try:
    from ..client_manager import ClientManager
except ImportError:
    ClientManager = None

from .config import DecisionEngineConfig
from .core import DecisionEngine
from .chroma_integration import ChromaDecisionHelper
from .types import (
    ConfidenceLevel,
    DecisionOutcome,
    DecisionResult,
    EssentialFieldsStatus,
    MerchantReliabilityData,
    PatternDetectionSummary,
)

logger = logging.getLogger(__name__)


@dataclass
class DecisionEngineIntegrationResult:
    """Result from the integrated decision engine + pattern detection."""

    # Pattern detection results
    pattern_results: Dict[str, Any]

    # Decision engine results
    decision: DecisionResult

    # Processing metadata
    processing_time_ms: float
    pattern_detection_time_ms: float
    decision_time_ms: float

    # Actions to take
    should_call_gpt: bool
    use_batch_processing: bool
    final_labels: Optional[Dict[str, Any]] = None


class DecisionEngineOrchestrator:
    """Orchestrates pattern detection + decision engine for receipt processing.

    This class integrates the decision engine with the existing pattern detection
    infrastructure to provide a unified interface for receipt labeling decisions.
    """

    def __init__(
        self,
        config: Optional[DecisionEngineConfig] = None,
        use_enhanced_patterns: bool = True,
        optimization_level: Optional[str] = None,
        client_manager: Optional[ClientManager] = None,
    ):
        """Initialize the orchestrator.

        Args:
            config: Decision engine configuration
            use_enhanced_patterns: Whether to use enhanced pattern orchestrator
            optimization_level: Optimization level for enhanced orchestrator
                                ('legacy', 'basic', 'optimized', 'advanced')
                                Defaults to 'advanced' if None
            client_manager: Client manager for Pinecone access
        """
        self.config = config or DecisionEngineConfig()
        self.decision_engine = DecisionEngine(self.config)

        # Initialize pattern detection orchestrator
        # Use the enhanced orchestrator from PR 217 for proven reliability
        if use_enhanced_patterns and EnhancedPatternOrchestrator is not None:
            from ..pattern_detection.enhanced_orchestrator import (
                OptimizationLevel,
            )

            # Now we can use any optimization level since they all return standardized format
            # Map string to enum, default to ADVANCED for best performance and accuracy
            if optimization_level:
                level_map = {
                    "legacy": OptimizationLevel.LEGACY,
                    "basic": OptimizationLevel.BASIC,
                    "optimized": OptimizationLevel.OPTIMIZED,
                    "advanced": OptimizationLevel.ADVANCED,
                }
                opt_level = level_map.get(
                    optimization_level.lower(), OptimizationLevel.ADVANCED
                )
            else:
                opt_level = OptimizationLevel.ADVANCED

            self.pattern_orchestrator = EnhancedPatternOrchestrator(opt_level)
            self._using_enhanced_orchestrator = True
            logger.info(
                f"Using enhanced pattern orchestrator with {opt_level.value} optimization"
            )
        else:
            self.pattern_orchestrator = ParallelPatternOrchestrator()
            self._using_enhanced_orchestrator = False

        # Initialize ChromaDB helper if available
        self.chroma_helper = None
        if (
            client_manager
            and self.config.enable_pinecone_validation  # Keep config name for compatibility
            and ClientManager is not None
        ):
            try:
                chroma_client = client_manager.chroma
                self.chroma_helper = ChromaDecisionHelper(
                    chroma_client, self.config
                )
                logger.info(
                    "Initialized ChromaDB integration for decision engine"
                )
            except Exception as e:
                logger.warning(
                    f"Failed to initialize ChromaDB integration: {e}"
                )
        
        # Keep pinecone_helper as alias for backward compatibility
        self.pinecone_helper = self.chroma_helper

    async def process_receipt(
        self,
        words: List[ReceiptWord],
        receipt_context: Optional[Dict[str, Any]] = None,
    ) -> DecisionEngineIntegrationResult:
        """Process a receipt with pattern detection + decision engine.

        Args:
            words: List of OCR words from the receipt
            receipt_context: Optional context (receipt ID, metadata, etc.)

        Returns:
            DecisionEngineIntegrationResult with decision and recommendations
        """
        import time

        start_time = time.time()

        try:
            # Step 1: Run pattern detection
            pattern_start = time.time()
            if self._using_enhanced_orchestrator:
                # Enhanced orchestrator from PR 217 - extract merchant name from receipt context
                merchant_name = (
                    receipt_context.get("merchant_name")
                    if receipt_context
                    else None
                )
                enhanced_results = (
                    await self.pattern_orchestrator.detect_patterns(
                        words, merchant_name
                    )
                )
                # Extract the actual pattern results from the enhanced format
                # Now all optimization levels return the same standardized format
                pattern_results = enhanced_results["pattern_results"][
                    "results"
                ]
                pattern_time_ms = enhanced_results.get(
                    "total_processing_time_ms", 0.0
                )
            else:
                # Legacy ParallelPatternOrchestrator
                pattern_results = (
                    await self.pattern_orchestrator.detect_all_patterns(words)
                )
                pattern_time_ms = (time.time() - pattern_start) * 1000

            # Step 2: Convert pattern results to decision engine format
            pattern_summary = self._convert_to_pattern_summary(
                words, pattern_results
            )

            # Step 3: Get merchant reliability data if available
            merchant_reliability = None
            if self.pinecone_helper and pattern_summary.detected_merchant:
                merchant_reliability = (
                    await self.pinecone_helper.get_merchant_reliability(
                        pattern_summary.detected_merchant
                    )
                )

            # Step 4: Make decision
            decision_start = time.time()

            # Debug: Log pattern summary values before decision
            logger.debug(
                f"Pattern summary before decision: "
                f"labeled_words={pattern_summary.labeled_words}, "
                f"coverage={pattern_summary.coverage_percentage:.1f}%, "
                f"essential_fields={pattern_summary.essential_fields.found_essential_fields}"
            )

            decision = self.decision_engine.decide(
                pattern_summary, merchant_reliability, receipt_context
            )
            decision_time_ms = (time.time() - decision_start) * 1000

            # Step 5: Determine actions based on decision
            should_call_gpt = decision.action != DecisionOutcome.SKIP
            use_batch_processing = decision.action == DecisionOutcome.BATCH

            # Step 6: Prepare final labels if skipping GPT
            final_labels = None
            if decision.action == DecisionOutcome.SKIP:
                final_labels = self._finalize_pattern_labels(pattern_results)

            total_time_ms = (time.time() - start_time) * 1000

            # Log decision for monitoring
            self._log_integration_result(
                decision, pattern_summary, total_time_ms
            )

            return DecisionEngineIntegrationResult(
                pattern_results=pattern_results,
                decision=decision,
                processing_time_ms=total_time_ms,
                pattern_detection_time_ms=pattern_time_ms,
                decision_time_ms=decision_time_ms,
                should_call_gpt=should_call_gpt,
                use_batch_processing=use_batch_processing,
                final_labels=final_labels,
            )

        except Exception as e:
            logger.error(
                f"Error in decision engine orchestrator: {e}", exc_info=True
            )

            # Fail safe - return result that forces GPT processing
            total_time_ms = (time.time() - start_time) * 1000
            return self._create_error_result(e, total_time_ms)

    def _convert_to_pattern_summary(
        self, words: List[ReceiptWord], pattern_results: Dict[str, Any]
    ) -> PatternDetectionSummary:
        """Convert pattern orchestrator results to DecisionEngine format."""

        # Count words and labels
        total_words = len(words)
        noise_words = sum(1 for word in words if self._is_noise_word(word))

        # Extract labeled words from pattern results
        labeled_word_positions = set()
        labels_by_type = {}
        confidence_scores = {}

        # Handle the actual format from PR 217 pattern detection
        for detector_name, results in pattern_results.items():
            if detector_name == "_metadata":
                continue  # Skip metadata

            # Results is a list of pattern objects with .word, .extracted_value, .confidence
            if not isinstance(results, list):
                continue

            detector_labels = []
            for pattern_match in results:
                if hasattr(pattern_match, "word") and hasattr(
                    pattern_match, "extracted_value"
                ):
                    # Find the word index for tracking labeled positions
                    word_text = pattern_match.word.text
                    word_id = pattern_match.word.word_id

                    # Find the position of this word in the words list
                    for i, word in enumerate(words):
                        if word.word_id == word_id:
                            labeled_word_positions.add(i)
                            break

                    # Collect label values
                    if hasattr(pattern_match, "extracted_value"):
                        detector_labels.append(
                            str(pattern_match.extracted_value)
                        )

                    # Collect confidence scores
                    if hasattr(pattern_match, "confidence") and hasattr(
                        pattern_match, "extracted_value"
                    ):
                        confidence_scores[
                            str(pattern_match.extracted_value)
                        ] = pattern_match.confidence

            if detector_labels:
                labels_by_type[detector_name.upper()] = detector_labels

        labeled_words = len(labeled_word_positions)
        meaningful_total = total_words - noise_words
        meaningful_unlabeled = max(0, meaningful_total - labeled_words)

        # Determine essential fields status
        essential_fields = self._analyze_essential_fields(
            labels_by_type, pattern_results
        )

        # Extract detected merchant
        detected_merchant = None
        merchant_confidence = None

        if "merchant" in pattern_results:
            merchant_patterns = pattern_results["merchant"]
            if (
                isinstance(merchant_patterns, list)
                and len(merchant_patterns) > 0
            ):
                # Get the highest confidence merchant
                best_match = max(
                    merchant_patterns,
                    key=lambda x: getattr(x, "confidence", 0.0),
                )
                if hasattr(best_match, "extracted_value"):
                    detected_merchant = str(best_match.extracted_value)
                    merchant_confidence = getattr(
                        best_match, "confidence", 0.0
                    )

        return PatternDetectionSummary(
            total_words=total_words,
            labeled_words=labeled_words,
            noise_words=noise_words,
            meaningful_unlabeled_words=meaningful_unlabeled,
            labels_by_type=labels_by_type,
            confidence_scores=confidence_scores,
            essential_fields=essential_fields,
            detected_merchant=detected_merchant,
            merchant_confidence=merchant_confidence,
        )

    def _analyze_essential_fields(
        self,
        labels_by_type: Dict[str, List[str]],
        pattern_results: Dict[str, Any],
    ) -> EssentialFieldsStatus:
        """Analyze whether essential fields are present in pattern results."""

        # Check for merchant name - look for merchant patterns or brand names in currency context
        merchant_found = bool(
            labels_by_type.get("MERCHANT", [])
            or pattern_results.get("merchant", [])
            or
            # TODO: Could detect merchant from header text patterns
            False
        )

        # Check for date - datetime patterns are dates
        date_found = bool(
            labels_by_type.get("DATE", [])
            or labels_by_type.get("DATETIME", [])
            or pattern_results.get("datetime", [])
        )

        # Check for grand total - need to identify total amounts from currency patterns
        # Look for currency patterns that are likely totals (bottom of receipt, larger amounts)
        grand_total_found = bool(
            labels_by_type.get("GRAND_TOTAL", [])
            or labels_by_type.get("TOTAL", [])
            or
            # Currency patterns can include totals - would need position analysis
            (
                pattern_results.get("currency", [])
                and len(pattern_results.get("currency", [])) > 0
            )
        )

        # Check for product name - quantity patterns often indicate products
        product_found = bool(
            labels_by_type.get("PRODUCT_NAME", [])
            or pattern_results.get(
                "quantity", []
            )  # Quantities often indicate products
            or
            # TODO: Could analyze text patterns for product-like words
            False
        )

        return EssentialFieldsStatus(
            merchant_name_found=merchant_found,
            date_found=date_found,
            grand_total_found=grand_total_found,
            product_name_found=product_found,
        )

    def _is_noise_word(self, word: ReceiptWord) -> bool:
        """Determine if a word is noise (punctuation, artifacts, etc.)."""
        if not word.text:
            return True

        text = word.text.strip()
        if not text:
            return True

        # Common noise patterns
        if len(text) == 1 and not text.isalnum():
            return True  # Single punctuation

        if text in {"---", "***", "===", "+++", "..."}:
            return True  # Common separators

        if all(c in "-=*+_.:|" for c in text):
            return True  # Only punctuation/separators

        return False

    def _finalize_pattern_labels(
        self, pattern_results: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Convert pattern results to final label format for storage."""

        finalized = {
            "labels": {},
            "confidence_scores": {},
            "processing_method": "pattern_only",
            "decision_engine_version": "0.1.0",
        }

        # Extract and normalize labels from each detector
        for detector_name, results in pattern_results.items():
            if not isinstance(results, dict) or "matches" not in results:
                continue

            matches = results["matches"]
            if not isinstance(matches, list):
                continue

            detector_labels = []
            for match in matches:
                if isinstance(match, dict):
                    label_value = match.get("value") or match.get("text")
                    if label_value:
                        detector_labels.append(
                            {
                                "value": label_value,
                                "confidence": match.get("confidence", 1.0),
                                "position": {
                                    "word_index": match.get("word_index"),
                                    "x": match.get("x"),
                                    "y": match.get("y"),
                                },
                            }
                        )

            if detector_labels:
                finalized["labels"][detector_name] = detector_labels

        return finalized

    def _create_error_result(
        self, error: Exception, total_time_ms: float
    ) -> DecisionEngineIntegrationResult:
        """Create error result that defaults to GPT processing."""

        # Create a minimal decision that forces GPT
        error_decision = DecisionResult(
            action=DecisionOutcome.REQUIRED,
            confidence=ConfidenceLevel.LOW,
            reasoning=f"Error in decision engine integration: {str(error)}",
            essential_fields_found=set(),
            essential_fields_missing={"MERCHANT_NAME", "DATE", "GRAND_TOTAL"},
            total_words=0,
            labeled_words=0,
            unlabeled_meaningful_words=0,
            coverage_percentage=0.0,
        )

        return DecisionEngineIntegrationResult(
            pattern_results={},
            decision=error_decision,
            processing_time_ms=total_time_ms,
            pattern_detection_time_ms=0.0,
            decision_time_ms=0.0,
            should_call_gpt=True,
            use_batch_processing=False,
            final_labels=None,
        )

    def _log_integration_result(
        self,
        decision: DecisionResult,
        pattern_summary: PatternDetectionSummary,
        total_time_ms: float,
    ) -> None:
        """Log integration result for monitoring."""

        logger.info(
            f"Decision Engine Integration: {decision.action.value.upper()} "
            f"(confidence: {decision.confidence.value}) "
            f"for merchant '{decision.merchant_name}' - "
            f"Coverage: {decision.coverage_percentage:.1f}%, "
            f"Unlabeled: {decision.unlabeled_meaningful_words}, "
            f"Time: {total_time_ms:.1f}ms"
        )

        # Log additional details for skip decisions (important for monitoring cost savings)
        if decision.action == DecisionOutcome.SKIP:
            logger.info(
                f"GPT SKIPPED: Saving API cost for receipt with merchant '{decision.merchant_name}' - "
                f"Reason: {decision.reasoning}"
            )

    def get_performance_stats(self) -> Dict[str, Any]:
        """Get performance statistics from all components."""

        stats = {
            "decision_engine": self.decision_engine.get_performance_stats(),
            "config": {
                "enabled": self.config.enabled,
                "min_coverage_percentage": self.config.min_coverage_percentage,
                "max_unlabeled_words": self.config.max_unlabeled_words,
                "rollout_percentage": self.config.rollout_percentage,
            },
        }

        if self.pinecone_helper:
            stats["pinecone_cache"] = self.pinecone_helper.get_cache_stats()

        return stats


# Convenience function for simple integration
async def process_receipt_with_decision_engine(
    words: List[ReceiptWord],
    config: Optional[DecisionEngineConfig] = None,
    optimization_level: Optional[str] = None,
    client_manager: Optional[Any] = None,
    receipt_context: Optional[Dict[str, Any]] = None,
) -> DecisionEngineIntegrationResult:
    """Convenience function to process a receipt with the decision engine.

    Args:
        words: OCR words from the receipt
        config: Optional decision engine configuration
        optimization_level: Pattern detection optimization level
                           ('legacy', 'basic', 'optimized', 'advanced')
        client_manager: Optional client manager for Pinecone access
        receipt_context: Optional receipt context

    Returns:
        DecisionEngineIntegrationResult with decision and actions
    """
    orchestrator = DecisionEngineOrchestrator(
        config=config,
        use_enhanced_patterns=True,
        optimization_level=optimization_level,
        client_manager=client_manager,
    )

    return await orchestrator.process_receipt(words, receipt_context)
