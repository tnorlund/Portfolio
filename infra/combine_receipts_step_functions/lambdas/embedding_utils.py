"""
Embedding utilities for receipt combination.

This module provides backward-compatible wrappers around the receipt_chroma
embedding orchestration functions for the combine_receipts workflow.

For new code, prefer using receipt_chroma.create_embeddings_and_compaction_run
directly to get access to EmbeddingResult with local ChromaClients for
immediate querying.
"""

import logging
import os
from typing import List, Optional

from receipt_dynamo.entities import (
    CompactionRun,
    ReceiptLine,
    ReceiptMetadata,
    ReceiptWord,
)

logger = logging.getLogger(__name__)


class _NoOpDynamoClient:  # pylint: disable=too-few-public-methods
    """No-op client for combine_receipts workflow.

    The combine_receipts workflow persists the CompactionRun separately
    after the function returns, so we use a no-op client here.
    """

    def add_compaction_run(self, run: CompactionRun) -> None:
        """No-op - don't persist."""


def create_embeddings_and_compaction_run(
    receipt_lines: List[ReceiptLine],
    receipt_words: List[ReceiptWord],
    receipt_metadata: Optional[ReceiptMetadata],
    image_id: str,
    new_receipt_id: int,
    chromadb_bucket: str,
) -> Optional[CompactionRun]:
    """
    Create embeddings and ChromaDB deltas for combined receipt.

    This is a backward-compatible wrapper that delegates to the canonical
    receipt_chroma.create_embeddings_and_compaction_run implementation.

    SQS notifications are handled by DynamoDB streams when the CompactionRun
    record is inserted, so no direct SQS messaging is needed here.

    Note: This function does NOT persist the CompactionRun to DynamoDB.
    The caller is responsible for persisting it via client.add_compaction_run().

    Args:
        receipt_lines: Lines to embed
        receipt_words: Words to embed
        receipt_metadata: Optional metadata for merchant context
        image_id: Image identifier
        new_receipt_id: Receipt identifier (maps to receipt_id in receipt_chroma)
        chromadb_bucket: S3 bucket for ChromaDB deltas

    Returns:
        CompactionRun entity if embeddings were created, None otherwise.
    """
    if not os.environ.get("OPENAI_API_KEY"):
        logger.info("OPENAI_API_KEY not set; skipping embeddings")
        return None

    if not receipt_lines or not receipt_words:
        logger.info("No lines/words provided; skipping embeddings")
        return None

    # Import and call receipt_chroma implementation
    from receipt_chroma.embedding.orchestration import (
        create_embeddings_and_compaction_run as chroma_create_embeddings,
    )

    try:
        # Use no-op DynamoDB client - caller persists CompactionRun separately
        result = chroma_create_embeddings(
            receipt_lines=receipt_lines,
            receipt_words=receipt_words,
            image_id=image_id,
            receipt_id=new_receipt_id,  # Map new_receipt_id -> receipt_id
            chromadb_bucket=chromadb_bucket,
            dynamo_client=_NoOpDynamoClient(),
            receipt_metadata=receipt_metadata,
            receipt_word_labels=None,  # combine_receipts doesn't use labels
            merchant_name=None,  # Let receipt_chroma extract from metadata
        )

        # Extract CompactionRun before closing
        compaction_run = result.compaction_run

        # Close the EmbeddingResult to release resources
        # (combine_receipts doesn't need the local ChromaClients)
        result.close()

        logger.info(
            "Created CompactionRun %s for receipt %s",
            compaction_run.run_id,
            new_receipt_id,
        )
        return compaction_run

    except (ValueError, RuntimeError) as e:
        logger.exception("Failed to create embeddings: %s", e)
        return None
