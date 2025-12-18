"""
Embedding management orchestration for receipts.

This module provides backward-compatible wrappers around the receipt_chroma
embedding orchestration functions.

For new code, prefer using receipt_chroma.create_embeddings_and_compaction_run
directly to get access to EmbeddingResult with local ChromaClients for
immediate querying.
"""

import logging
import os
from typing import Optional

from receipt_dynamo import DynamoClient
from receipt_dynamo.entities import (
    CompactionRun,
    ReceiptLine,
    ReceiptMetadata,
    ReceiptWord,
    ReceiptWordLabel,
)

logger = logging.getLogger(__name__)


class _NoOpDynamoClient:  # pylint: disable=too-few-public-methods
    """No-op client for when add_to_dynamo=False."""

    def add_compaction_run(self, run: CompactionRun) -> None:
        """No-op - don't persist."""


def create_embeddings_and_compaction_run(
    client: DynamoClient,
    chromadb_bucket: str,
    image_id: str,
    receipt_id: int,
    receipt_lines: Optional[list[ReceiptLine]] = None,
    receipt_words: Optional[list[ReceiptWord]] = None,
    receipt_metadata: Optional[ReceiptMetadata] = None,
    receipt_word_labels: Optional[list[ReceiptWordLabel]] = None,
    merchant_name: Optional[str] = None,
    add_to_dynamo: bool = False,
) -> Optional[CompactionRun]:
    """
    Backward-compatible wrapper for embedding creation.

    This function fetches data from DynamoDB if not provided, then delegates
    to receipt_chroma.create_embeddings_and_compaction_run.

    For new code, use receipt_chroma.create_embeddings_and_compaction_run
    directly to get access to EmbeddingResult with local ChromaClients.

    Args:
        client: DynamoDB client (used for fetches and optional CompactionRun write)
        chromadb_bucket: S3 bucket for ChromaDB deltas
        image_id: Image ID
        receipt_id: Receipt ID
        receipt_lines: Optional lines (fetched if None)
        receipt_words: Optional words (fetched if None)
        receipt_metadata: Optional metadata for merchant context
        receipt_word_labels: Optional word labels (fetched if None)
        merchant_name: Explicit merchant name override
        add_to_dynamo: If True, persist the CompactionRun via the client

    Returns:
        CompactionRun entity if successful, None if dependencies unavailable
    """
    try:
        from openai import (  # noqa: F401, pylint: disable=import-outside-toplevel
            OpenAI as _,
        )
    except ImportError as exc:  # pragma: no cover
        logger.warning("Embedding dependencies unavailable: %s", exc)
        return None

    if not os.environ.get("OPENAI_API_KEY"):
        logger.info("OPENAI_API_KEY not set; skipping embeddings")
        return None

    # Fetch data from DynamoDB if not provided
    if receipt_lines is None:
        receipt_lines = client.list_receipt_lines_from_receipt(image_id, receipt_id)
    if receipt_words is None:
        receipt_words = client.list_receipt_words_from_receipt(image_id, receipt_id)
    if receipt_word_labels is None:
        receipt_word_labels, _ = client.list_receipt_word_labels_for_receipt(
            image_id, receipt_id
        )

    if not receipt_lines or not receipt_words:
        logger.info(
            "No lines/words found for receipt %s; skipping embedding",
            receipt_id,
        )
        return None

    # Import and call receipt_chroma implementation
    from receipt_chroma.embedding.orchestration import (
        create_embeddings_and_compaction_run as chroma_create_embeddings,
    )

    try:
        # Use real client if add_to_dynamo, otherwise no-op client
        dynamo_for_chroma = client if add_to_dynamo else _NoOpDynamoClient()

        result = chroma_create_embeddings(
            receipt_lines=receipt_lines,
            receipt_words=receipt_words,
            image_id=image_id,
            receipt_id=receipt_id,
            chromadb_bucket=chromadb_bucket,
            dynamo_client=dynamo_for_chroma,
            receipt_metadata=receipt_metadata,
            receipt_word_labels=receipt_word_labels,
            merchant_name=merchant_name,
        )

        # Extract CompactionRun before closing
        compaction_run = result.compaction_run

        # Close the EmbeddingResult to release resources
        # (backward-compatible callers don't need the local clients)
        result.close()

        logger.info(
            "Created CompactionRun %s for receipt %s",
            compaction_run.run_id,
            receipt_id,
        )
        return compaction_run

    except (ValueError, RuntimeError) as e:
        logger.error("Failed to create embeddings: %s", e)
        return None


def create_embeddings(
    client: DynamoClient,
    chromadb_bucket: str,
    image_id: str,
    receipt_id: int,
    receipt_lines: Optional[list[ReceiptLine]] = None,
    receipt_words: Optional[list[ReceiptWord]] = None,
    receipt_metadata: Optional[ReceiptMetadata] = None,
    merchant_name: Optional[str] = None,
) -> Optional[str]:
    """
    Backwards-compatible wrapper that creates embeddings and persists CompactionRun.
    Returns the run_id if successful.
    """
    compaction_run = create_embeddings_and_compaction_run(
        client=client,
        chromadb_bucket=chromadb_bucket,
        image_id=image_id,
        receipt_id=receipt_id,
        receipt_lines=receipt_lines,
        receipt_words=receipt_words,
        receipt_metadata=receipt_metadata,
        merchant_name=merchant_name,
        add_to_dynamo=True,
    )
    return compaction_run.run_id if compaction_run else None
