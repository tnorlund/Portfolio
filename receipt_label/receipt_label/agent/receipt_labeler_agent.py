"""
Main receipt labeler agent that orchestrates the labeling pipeline.

This module provides the high-level interface for the agent-based labeling system,
coordinating pattern detection, smart decisions, batch processing, and label storage.
"""

import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from receipt_label.utils.ai_usage_context import ai_usage_tracked
from receipt_label.utils.client_manager import ClientManager

from .batch_processor import BatchProcessor
from .decision_engine import DecisionEngine
from .label_applicator import LabelApplicator
from .pattern_detector import PatternDetector

logger = logging.getLogger(__name__)


class ReceiptLabelerAgent:
    """
    Main orchestrator for agent-based receipt labeling.

    This class coordinates the entire labeling pipeline:
    1. Check for existing merchant metadata
    2. Run parallel pattern detection
    3. Make smart decisions about GPT usage
    4. Batch process unlabeled words if needed
    5. Apply and store labels
    """

    def __init__(self, client_manager: ClientManager):
        """
        Initialize the receipt labeler agent.

        Args:
            client_manager: Client manager for external services
        """
        self.client_manager = client_manager

        # Initialize components
        self.pattern_detector = PatternDetector(client_manager)
        self.decision_engine = DecisionEngine()
        self.batch_processor = BatchProcessor(client_manager)
        self.label_applicator = LabelApplicator(client_manager.dynamo)

        # Track processing statistics
        self.stats = {
            "receipts_processed": 0,
            "pattern_only_receipts": 0,
            "gpt_enhanced_receipts": 0,
            "total_processing_time": 0,
            "total_words_labeled": 0,
        }

    @ai_usage_tracked(operation_type="receipt_labeling")
    async def label_receipt(
        self,
        receipt_id: str,
        receipt_words: List,
        receipt_lines: List,
        receipt_metadata: Optional[Dict] = None,
        validate_labels: bool = False,
        store_labels: bool = True,
    ) -> Dict:
        """
        Label a receipt using the agent-based pipeline.

        Args:
            receipt_id: Receipt identifier
            receipt_words: List of ReceiptWord entities or dictionaries
            receipt_lines: List of ReceiptLine entities or dictionaries
            receipt_metadata: Optional pre-fetched metadata
            validate_labels: Whether to run validation after labeling
            store_labels: Whether to store labels in DynamoDB

        Returns:
            Dictionary with labeling results and statistics
        """
        start_time = datetime.utcnow()
        logger.info(f"Starting agent labeling for receipt {receipt_id}")

        try:
            # Step 1: Get merchant metadata if not provided
            if not receipt_metadata:
                receipt_metadata = await self._get_receipt_metadata(receipt_id)

            merchant_name = None
            if receipt_metadata:
                merchant_name = receipt_metadata.get(
                    "canonical_merchant_name"
                ) or receipt_metadata.get("merchant_name")
                logger.info(f"Using merchant: {merchant_name}")

            # Step 2: Check if embeddings exist (required for pattern queries)
            embeddings_exist = await self._check_embeddings(receipt_id)
            if not embeddings_exist:
                logger.warning(f"No embeddings found for receipt {receipt_id}")

            # Step 3: Run parallel pattern detection
            pattern_results = await self.pattern_detector.detect_all_patterns(
                receipt_words=receipt_words,
                receipt_lines=receipt_lines,
                merchant_name=merchant_name,
            )

            # Step 4: Apply patterns to get initial labels
            pattern_labels = self.pattern_detector.apply_patterns(
                receipt_words, pattern_results
            )

            logger.info(
                f"Pattern detection found {len(pattern_labels)} labels"
            )

            # Step 5: Smart decision - do we need GPT?
            should_call_gpt, reason, unlabeled_words = (
                self.decision_engine.should_call_gpt(
                    receipt_words=receipt_words,
                    labeled_words=pattern_labels,
                    receipt_metadata=receipt_metadata,
                )
            )

            # Step 6: Call GPT if needed
            gpt_labels = {}
            if should_call_gpt:
                logger.info(f"Calling GPT: {reason}")
                gpt_labels = await self._process_with_gpt(
                    receipt_id=receipt_id,
                    unlabeled_words=unlabeled_words,
                    labeled_words=pattern_labels,
                    receipt_metadata=receipt_metadata,
                )
                self.stats["gpt_enhanced_receipts"] += 1
            else:
                logger.info(f"Skipping GPT: {reason}")
                self.stats["pattern_only_receipts"] += 1

            # Step 7: Apply all labels
            word_labels, application_stats = (
                self.label_applicator.apply_labels(
                    receipt_id=receipt_id,
                    receipt_words=receipt_words,
                    pattern_labels=pattern_labels,
                    gpt_labels=gpt_labels,
                    store_in_dynamo=store_labels,
                )
            )

            # Step 8: Optional validation
            validation_results = None
            if validate_labels and word_labels:
                validation_results = await self._validate_labels(word_labels)

            # Calculate processing time
            processing_time = (datetime.utcnow() - start_time).total_seconds()

            # Update statistics
            self.stats["receipts_processed"] += 1
            self.stats["total_processing_time"] += processing_time
            self.stats["total_words_labeled"] += len(word_labels)

            # Prepare results
            results = {
                "receipt_id": receipt_id,
                "success": True,
                "labels_applied": len(word_labels),
                "pattern_labels": application_stats["pattern_labels"],
                "gpt_labels": application_stats["gpt_labels"],
                "coverage_rate": application_stats["coverage_rate"],
                "gpt_called": should_call_gpt,
                "gpt_reason": reason,
                "processing_time": processing_time,
                "pattern_results": {
                    key: len(patterns)
                    for key, patterns in pattern_results.items()
                },
                "validation_results": validation_results,
                "word_labels": (
                    word_labels if not store_labels else None
                ),  # Return if not stored
            }

            logger.info(
                f"Completed labeling for receipt {receipt_id}: "
                f"{results['labels_applied']} labels applied "
                f"({results['coverage_rate']:.1%} coverage) "
                f"in {processing_time:.2f}s"
            )

            return results

        except Exception as e:
            logger.error(
                f"Error labeling receipt {receipt_id}: {e}", exc_info=True
            )
            return {
                "receipt_id": receipt_id,
                "success": False,
                "error": str(e),
                "processing_time": (
                    datetime.utcnow() - start_time
                ).total_seconds(),
            }

    def label_receipt_sync(
        self,
        receipt_id: str,
        receipt_words: List,
        receipt_lines: List,
        receipt_metadata: Optional[Dict] = None,
        validate_labels: bool = False,
        store_labels: bool = True,
    ) -> Dict:
        """
        Synchronous version of label_receipt.

        Args:
            Same as label_receipt

        Returns:
            Same as label_receipt
        """
        # Create event loop if needed
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        # Run async method
        return loop.run_until_complete(
            self.label_receipt(
                receipt_id=receipt_id,
                receipt_words=receipt_words,
                receipt_lines=receipt_lines,
                receipt_metadata=receipt_metadata,
                validate_labels=validate_labels,
                store_labels=store_labels,
            )
        )

    async def _get_receipt_metadata(self, receipt_id: str) -> Optional[Dict]:
        """Fetch receipt metadata from DynamoDB."""
        try:
            # Try to get ReceiptMetadata entity
            metadata = self.client_manager.dynamo.get_receipt_metadata(
                receipt_id
            )
            if metadata:
                return metadata.to_dict()
        except Exception as e:
            logger.warning(
                f"Could not fetch metadata for receipt {receipt_id}: {e}"
            )

        return None

    async def _check_embeddings(self, receipt_id: str) -> bool:
        """Check if embeddings exist for this receipt."""
        # TODO: Implement Pinecone check
        # For now, assume embeddings exist
        return True

    async def _process_with_gpt(
        self,
        receipt_id: str,
        unlabeled_words: List[Dict],
        labeled_words: Dict[int, Dict],
        receipt_metadata: Optional[Dict],
    ) -> Dict[int, Dict]:
        """Process unlabeled words with GPT."""
        if not unlabeled_words:
            return {}

        # Create batches
        batches = self.batch_processor.create_batch(
            receipt_id=receipt_id,
            unlabeled_words=unlabeled_words,
            labeled_words=labeled_words,
            receipt_metadata=receipt_metadata,
        )

        # Process batches (for now, synchronously)
        all_labels = {}
        for batch in batches:
            batch_labels = self.batch_processor.process_batch_sync(batch)
            all_labels.update(batch_labels)

        logger.info(
            f"GPT labeled {len(all_labels)} words across {len(batches)} batches"
        )

        return all_labels

    async def _validate_labels(self, word_labels: List) -> Dict:
        """Run label validation if requested."""
        # TODO: Implement label validation
        # This would use the validation pipelines from label_validation module
        return {"validated": False, "reason": "Validation not yet implemented"}

    def get_statistics(self) -> Dict:
        """Get comprehensive statistics."""
        stats = self.stats.copy()

        # Add component statistics
        stats["pattern_detector"] = (
            self.pattern_detector.get_statistics()
            if hasattr(self.pattern_detector, "get_statistics")
            else {}
        )
        stats["decision_engine"] = self.decision_engine.get_statistics()
        stats["batch_processor"] = self.batch_processor.get_statistics()
        stats["label_applicator"] = self.label_applicator.get_statistics()

        # Calculate derived metrics
        if stats["receipts_processed"] > 0:
            stats["avg_processing_time"] = (
                stats["total_processing_time"] / stats["receipts_processed"]
            )
            stats["avg_words_per_receipt"] = (
                stats["total_words_labeled"] / stats["receipts_processed"]
            )
            stats["gpt_usage_rate"] = (
                stats["gpt_enhanced_receipts"] / stats["receipts_processed"]
            )

        return stats

    def reset_statistics(self):
        """Reset all statistics."""
        self.stats = {
            "receipts_processed": 0,
            "pattern_only_receipts": 0,
            "gpt_enhanced_receipts": 0,
            "total_processing_time": 0,
            "total_words_labeled": 0,
        }

        # Reset component stats
        self.decision_engine.reset_statistics()
        self.label_applicator.reset_statistics()
