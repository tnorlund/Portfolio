"""Orchestrator for label validation."""

import logging
from typing import List, Optional, Tuple

from receipt_dynamo.entities import (
    ReceiptLine,
    ReceiptMetadata,
    ReceiptWord,
    ReceiptWordLabel,
)

from receipt_label.label_validation.data import LabelValidationResult, update_labels
from receipt_label.label_validation.prompt_builder import build_validation_prompt
from receipt_label.label_validation.registry import get_validator
from receipt_label.utils.client_manager import ClientManager

logger = logging.getLogger(__name__)


class ValidationOrchestrator:
    """Orchestrates validation for multiple labels."""

    def __init__(self, client_manager: Optional[ClientManager] = None):
        self.client_manager = client_manager

    def validate_labels(
        self,
        labels_and_words: List[Tuple[ReceiptWordLabel, ReceiptWord]],
        receipt_lines: List[ReceiptLine],
        receipt_metadata: Optional[ReceiptMetadata] = None,
        merchant_name: Optional[str] = None,
        receipt_count: int = 0,
        all_labels: Optional[List[ReceiptWordLabel]] = None,
        all_words: Optional[List[ReceiptWord]] = None,
    ) -> List[Tuple[LabelValidationResult, ReceiptWordLabel]]:
        """
        Validate a batch of labels.

        Args:
            labels_and_words: List of (label, word) tuples to validate
            receipt_lines: All receipt lines for context
            receipt_metadata: ReceiptMetadata if available
            merchant_name: Canonical merchant name
            receipt_count: Number of receipts for this merchant
            all_labels: All labels on receipt (for consistency checking)
            all_words: All words on receipt (to find other labeled words)

        Returns:
            List of (validation_result, label) tuples
        """
        results = []

        for label, word in labels_and_words:
            validator = get_validator(label.label)

            if not validator:
                logger.warning(
                    f"No validator found for label: {label.label}. Skipping."
                )
                continue

            # Prepare kwargs based on validator requirements
            kwargs = {}
            if validator.config.requires_receipt_metadata and receipt_metadata:
                kwargs["receipt_metadata"] = receipt_metadata
            if validator.config.requires_merchant_name and merchant_name:
                kwargs["merchant_name"] = merchant_name
                kwargs["receipt_count"] = receipt_count

            # Get ChromaDB neighbors if enabled
            chroma_neighbors = None
            if (
                validator.config.enable_llm_validation
                and validator.config.use_chroma_examples
                and self.client_manager
            ):
                chroma_neighbors = validator.get_chroma_neighbors(
                    word, label, self.client_manager
                )

            # Build LLM prompt if enabled
            llm_prompt = None
            if validator.config.enable_llm_validation:
                label_confidence = getattr(label, "confidence", None)
                llm_prompt = build_validation_prompt(
                    word=word,
                    label=label,
                    lines=receipt_lines,
                    receipt_metadata=receipt_metadata,
                    chroma_neighbors=chroma_neighbors,
                    all_labels=all_labels,
                    words=all_words,
                    receipt_count=receipt_count,
                    label_confidence=label_confidence,
                )
                kwargs["llm_prompt"] = llm_prompt

            try:
                result = validator.validate(
                    word=word,
                    label=label,
                    client_manager=self.client_manager,
                    **kwargs,
                )
                results.append((result, label))
            except Exception as e:
                logger.error(
                    f"Error validating label {label.label} for word {word.text}: {e}",
                    exc_info=True,
                )
                # Create error result
                from receipt_label.label_validation.utils import chroma_id_from_label

                error_result = LabelValidationResult(
                    image_id=label.image_id,
                    receipt_id=label.receipt_id,
                    line_id=label.line_id,
                    word_id=label.word_id,
                    label=label.label,
                    status="ERROR",
                    is_consistent=False,
                    avg_similarity=0.0,
                    neighbors=[],
                    pinecone_id=chroma_id_from_label(label),
                )
                results.append((error_result, label))

        return results

    def validate_and_update(
        self,
        labels_and_words: List[Tuple[ReceiptWordLabel, ReceiptWord]],
        receipt_lines: List[ReceiptLine],
        receipt_metadata: Optional[ReceiptMetadata] = None,
        merchant_name: Optional[str] = None,
        receipt_count: int = 0,
        all_labels: Optional[List[ReceiptWordLabel]] = None,
        all_words: Optional[List[ReceiptWord]] = None,
    ) -> List[Tuple[LabelValidationResult, ReceiptWordLabel]]:
        """
        Validate labels and update DynamoDB/ChromaDB.

        Args:
            labels_and_words: List of (label, word) tuples to validate
            receipt_lines: All receipt lines for context
            receipt_metadata: ReceiptMetadata if available
            merchant_name: Canonical merchant name
            receipt_count: Number of receipts for this merchant
            all_labels: All labels on receipt (for consistency checking)
            all_words: All words on receipt (to find other labeled words)

        Returns:
            List of (validation_result, label) tuples
        """
        results = self.validate_labels(
            labels_and_words=labels_and_words,
            receipt_lines=receipt_lines,
            receipt_metadata=receipt_metadata,
            merchant_name=merchant_name,
            receipt_count=receipt_count,
            all_labels=all_labels,
            all_words=all_words,
        )
        if results:
            update_labels(results, self.client_manager)
        return results

