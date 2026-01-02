"""Main coordinator for compaction operations."""

import os
from typing import Any, List, Optional

from receipt_chroma.compaction.deletions import apply_receipt_deletions
from receipt_chroma.compaction.deltas import merge_compaction_deltas
from receipt_chroma.compaction.labels import apply_label_updates
from receipt_chroma.compaction.metadata import apply_place_updates
from receipt_chroma.compaction.models import CollectionUpdateResult
from receipt_chroma.data.chroma_client import ChromaClient

from receipt_dynamo.constants import ChromaDBCollection
from receipt_dynamo.data.dynamo_client import DynamoClient


def process_collection_updates(
    stream_messages: List[Any],
    collection: ChromaDBCollection,
    chroma_client: ChromaClient,
    logger: Any,
    metrics: Optional[Any] = None,
    dynamo_client: Optional[DynamoClient] = None,
) -> CollectionUpdateResult:
    """Process all update types for a collection in-memory.

    This is the main entry point for processing compaction updates. It
    categorizes messages by type and applies them in the correct order:
    1. Delta merges (COMPACTION_RUN) - must be applied first
    2. Place updates (RECEIPT_PLACE)
    3. Label updates (RECEIPT_WORD_LABEL)

    The caller is responsible for:
    - Downloading the snapshot
    - Opening the ChromaDB client
    - Uploading the updated snapshot
    - Managing locks

    Args:
        stream_messages: List of StreamMessage objects to process
        collection: Target collection (LINES or WORDS)
        chroma_client: Open ChromaDB client with snapshot loaded
        logger: Logger instance for observability
        metrics: Optional metrics collector
        dynamo_client: Optional DynamoDB client for operations

    Returns:
        CollectionUpdateResult with counts and per-message results
    """
    # Categorize messages by entity type
    place_msgs = [
        m for m in stream_messages if m.entity_type == "RECEIPT_PLACE"
    ]
    label_msgs = [
        m for m in stream_messages if m.entity_type == "RECEIPT_WORD_LABEL"
    ]
    delta_msgs = [
        m for m in stream_messages if m.entity_type == "COMPACTION_RUN"
    ]
    # Entity deletions: when entities are deleted from DynamoDB, we need
    # to delete corresponding embeddings from ChromaDB
    # - RECEIPT: delete all embeddings for the receipt
    # - RECEIPT_WORD: delete the specific word embedding
    # - RECEIPT_LINE: delete the specific line embedding
    deletion_entity_types = {"RECEIPT", "RECEIPT_WORD", "RECEIPT_LINE"}
    entity_deletion_msgs = [
        m for m in stream_messages
        if m.entity_type in deletion_entity_types and m.event_name == "REMOVE"
    ]

    logger.info(
        "Categorized stream messages",
        collection=collection.value,
        total_messages=len(stream_messages),
        place_count=len(place_msgs),
        label_count=len(label_msgs),
        delta_count=len(delta_msgs),
        entity_deletion_count=len(entity_deletion_msgs),
    )

    # Apply updates in order: deltas first, then place updates, then labels
    # This ensures new embeddings from deltas get proper place data/labels

    # 1. Merge compaction deltas
    bucket = os.environ.get("CHROMADB_BUCKET", "")
    if delta_msgs and not bucket:
        logger.warning(
            "CHROMADB_BUCKET not set, skipping delta processing",
            collection=collection.value,
            delta_count=len(delta_msgs),
        )
        delta_count = 0
        delta_results = []
    else:
        delta_count, delta_results = merge_compaction_deltas(
            chroma_client=chroma_client,
            compaction_runs=delta_msgs,
            collection=collection,
            logger=logger,
            bucket=bucket,
        )

    if delta_count > 0:
        logger.info(
            "Merged compaction deltas",
            collection=collection.value,
            total_merged=delta_count,
            run_count=len(delta_results),
        )

    # 2. Apply place updates
    metadata_results = apply_place_updates(
        chroma_client=chroma_client,
        place_messages=place_msgs,
        collection=collection,
        logger=logger,
        metrics=metrics,
        dynamo_client=dynamo_client,
    )

    if metadata_results:
        total_metadata = sum(
            r.updated_count for r in metadata_results if r.error is None
        )
        logger.info(
            "Applied place updates",
            collection=collection.value,
            total_updated=total_metadata,
            message_count=len(metadata_results),
        )

    # 3. Apply label updates
    label_results = apply_label_updates(
        chroma_client=chroma_client,
        label_messages=label_msgs,
        collection=collection,
        logger=logger,
        metrics=metrics,
        dynamo_client=dynamo_client,
    )

    if label_results:
        total_labels = sum(
            r.updated_count for r in label_results if r.error is None
        )
        logger.info(
            "Applied label updates",
            collection=collection.value,
            total_updated=total_labels,
            message_count=len(label_results),
        )

    # 4. Apply entity deletions (receipts, words, lines)
    deletion_results = apply_receipt_deletions(
        chroma_client=chroma_client,
        receipt_messages=entity_deletion_msgs,
        collection=collection,
        logger=logger,
        metrics=metrics,
        dynamo_client=dynamo_client,
    )

    if deletion_results:
        total_deleted = sum(
            r.deleted_count for r in deletion_results if r.error is None
        )
        logger.info(
            "Applied entity deletions",
            collection=collection.value,
            total_deleted=total_deleted,
            deletion_count=len(deletion_results),
        )

    # Build aggregate result
    result = CollectionUpdateResult(
        collection=collection,
        metadata_updates=metadata_results,
        label_updates=label_results,
        delta_merge_count=delta_count,
        delta_merge_results=delta_results,
        receipt_deletions=deletion_results,
    )

    # Log summary
    logger.info(
        "Completed collection updates",
        collection=collection.value,
        metadata_updated=result.total_metadata_updated,
        labels_updated=result.total_labels_updated,
        deltas_merged=delta_count,
        embeddings_deleted=result.total_deletions,
        has_errors=result.has_errors,
    )

    if metrics:
        metrics.count(
            "CompactionCollectionUpdatesProcessed",
            1,
            {"collection": collection.value},
        )
        metrics.gauge(
            "CompactionMetadataUpdatedCount",
            result.total_metadata_updated,
            {"collection": collection.value},
        )
        metrics.gauge(
            "CompactionLabelsUpdatedCount",
            result.total_labels_updated,
            {"collection": collection.value},
        )
        metrics.gauge(
            "CompactionDeltasMergedCount",
            delta_count,
            {"collection": collection.value},
        )
        metrics.gauge(
            "CompactionEmbeddingsDeletedCount",
            result.total_deletions,
            {"collection": collection.value},
        )

    return result
