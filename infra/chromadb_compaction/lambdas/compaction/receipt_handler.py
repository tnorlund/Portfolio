"""Receipt deletion processing for RECEIPT entities."""

import os
import shutil
import tempfile
from typing import Any, Dict, List, Optional

from receipt_chroma import ChromaClient
from receipt_chroma.s3 import download_snapshot_atomic, upload_snapshot_atomic

from receipt_dynamo.constants import ChromaDBCollection

from .models import (
    MetadataUpdateResult,  # Reuse this for receipt deletion results
)
from .operations import delete_receipt_embeddings


def delete_receipt_child_records(
    dynamo_client: Any,
    image_id: str,
    receipt_id: int,
    logger: Any,
    metrics: Any = None,
    OBSERVABILITY_AVAILABLE: bool = False,
) -> Dict[str, int]:
    """Delete all child records for a receipt from DynamoDB.

    Deletes in reverse order of creation:
    1. ReceiptWordLabel
    2. ReceiptWord
    3. ReceiptLine
    4. ReceiptLetter (best effort)
    5. ReceiptMetadata (best effort)
    6. CompactionRun (best effort)

    Args:
        dynamo_client: DynamoDB client instance
        image_id: Image ID
        receipt_id: Receipt ID
        logger: Logger instance
        metrics: Optional metrics collector
        OBSERVABILITY_AVAILABLE: Whether observability features are available

    Returns:
        Dictionary with deletion counts for each entity type
    """
    deletion_counts = {
        "labels": 0,
        "words": 0,
        "lines": 0,
        "letters": 0,
        "metadata": 0,
        "compaction_runs": 0,
    }

    try:
        # 1. Delete ReceiptWordLabel
        try:
            receipt_labels, _ = dynamo_client.list_receipt_word_labels_for_receipt(
                image_id, receipt_id
            )
            if receipt_labels:
                dynamo_client.delete_receipt_word_labels(receipt_labels)
                deletion_counts["labels"] = len(receipt_labels)
                if OBSERVABILITY_AVAILABLE:
                    logger.info(
                        "Deleted receipt word labels",
                        image_id=image_id,
                        receipt_id=receipt_id,
                        count=len(receipt_labels),
                    )
                else:
                    logger.info(
                        f"Deleted {len(receipt_labels)} receipt word labels for receipt {receipt_id}"
                    )
                if metrics:
                    metrics.count("CompactionReceiptLabelsDeleted", len(receipt_labels))
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "Failed to delete receipt word labels",
                image_id=image_id,
                receipt_id=receipt_id,
                error=str(e),
            )

        # 2. Delete ReceiptWord
        try:
            receipt_words = dynamo_client.list_receipt_words_from_receipt(
                image_id, receipt_id
            )
            if receipt_words:
                dynamo_client.delete_receipt_words(receipt_words)
                deletion_counts["words"] = len(receipt_words)
                if OBSERVABILITY_AVAILABLE:
                    logger.info(
                        "Deleted receipt words",
                        image_id=image_id,
                        receipt_id=receipt_id,
                        count=len(receipt_words),
                    )
                else:
                    logger.info(
                        f"Deleted {len(receipt_words)} receipt words for receipt {receipt_id}"
                    )
                if metrics:
                    metrics.count("CompactionReceiptWordsDeleted", len(receipt_words))
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "Failed to delete receipt words",
                image_id=image_id,
                receipt_id=receipt_id,
                error=str(e),
            )

        # 3. Delete ReceiptLine
        try:
            receipt_lines = dynamo_client.list_receipt_lines_from_receipt(
                image_id, receipt_id
            )
            if receipt_lines:
                dynamo_client.delete_receipt_lines(receipt_lines)
                deletion_counts["lines"] = len(receipt_lines)
                if OBSERVABILITY_AVAILABLE:
                    logger.info(
                        "Deleted receipt lines",
                        image_id=image_id,
                        receipt_id=receipt_id,
                        count=len(receipt_lines),
                    )
                else:
                    logger.info(
                        f"Deleted {len(receipt_lines)} receipt lines for receipt {receipt_id}"
                    )
                if metrics:
                    metrics.count("CompactionReceiptLinesDeleted", len(receipt_lines))
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "Failed to delete receipt lines",
                image_id=image_id,
                receipt_id=receipt_id,
                error=str(e),
            )

        # 4. Delete ReceiptLetter (best effort - may not have direct list method)
        try:
            # Try to list letters if method exists
            if hasattr(dynamo_client, "list_receipt_letters_from_image_and_receipt"):
                receipt_letters = (
                    dynamo_client.list_receipt_letters_from_image_and_receipt(
                        image_id, receipt_id
                    )
                )
                if receipt_letters:
                    dynamo_client.delete_receipt_letters(receipt_letters)
                    deletion_counts["letters"] = len(receipt_letters)
                    if OBSERVABILITY_AVAILABLE:
                        logger.info(
                            "Deleted receipt letters",
                            image_id=image_id,
                            receipt_id=receipt_id,
                            count=len(receipt_letters),
                        )
                    else:
                        logger.info(
                            f"Deleted {len(receipt_letters)} receipt letters for receipt {receipt_id}"
                        )
                    if metrics:
                        metrics.count(
                            "CompactionReceiptLettersDeleted",
                            len(receipt_letters),
                        )
        except Exception as e:  # noqa: BLE001
            # Letters deletion is best effort - log but don't fail
            logger.debug(
                "Skipping receipt letters deletion (method may not exist or letters already deleted)",
                image_id=image_id,
                receipt_id=receipt_id,
                error=str(e),
            )

        # 5. Delete ReceiptMetadata (best effort)
        try:
            metadata = dynamo_client.get_receipt_metadata(image_id, receipt_id)
            if metadata:
                dynamo_client.delete_receipt_metadata(metadata)
                deletion_counts["metadata"] = 1
                if OBSERVABILITY_AVAILABLE:
                    logger.info(
                        "Deleted receipt metadata",
                        image_id=image_id,
                        receipt_id=receipt_id,
                    )
                else:
                    logger.info(f"Deleted receipt metadata for receipt {receipt_id}")
                if metrics:
                    metrics.count("CompactionReceiptMetadataDeleted", 1)
        except Exception as e:  # noqa: BLE001
            # Metadata might not exist - log but don't fail
            logger.debug(
                "Skipping receipt metadata deletion (may not exist)",
                image_id=image_id,
                receipt_id=receipt_id,
                error=str(e),
            )

        # 6. Delete CompactionRun (best effort)
        try:
            runs, _ = dynamo_client.list_compaction_runs_for_receipt(
                image_id, receipt_id
            )
            if runs:
                for run in runs:
                    dynamo_client.delete_compaction_run(run)
                deletion_counts["compaction_runs"] = len(runs)
                if OBSERVABILITY_AVAILABLE:
                    logger.info(
                        "Deleted compaction runs",
                        image_id=image_id,
                        receipt_id=receipt_id,
                        count=len(runs),
                    )
                else:
                    logger.info(
                        f"Deleted {len(runs)} compaction runs for receipt {receipt_id}"
                    )
                if metrics:
                    metrics.count("CompactionReceiptCompactionRunsDeleted", len(runs))
        except Exception as e:  # noqa: BLE001
            # Compaction runs might not exist - log but don't fail
            logger.debug(
                "Skipping compaction runs deletion (may not exist)",
                image_id=image_id,
                receipt_id=receipt_id,
                error=str(e),
            )

        total_deleted = sum(deletion_counts.values())
        if OBSERVABILITY_AVAILABLE:
            logger.info(
                "Completed receipt child record deletion",
                image_id=image_id,
                receipt_id=receipt_id,
                total_deleted=total_deleted,
                **deletion_counts,
            )
        else:
            logger.info(
                f"Completed receipt child record deletion for receipt {receipt_id}: "
                f"{total_deleted} total records deleted"
            )

    except Exception as e:  # noqa: BLE001
        # Log error but don't fail - ChromaDB deletion is the primary operation
        logger.error(
            "Error during receipt child record deletion",
            image_id=image_id,
            receipt_id=receipt_id,
            error=str(e),
        )
        if metrics:
            metrics.count(
                "CompactionReceiptChildDeletionError",
                1,
                {"error_type": type(e).__name__},
            )

    return deletion_counts


def process_receipt_deletions(
    receipt_deletions: List[Any],  # StreamMessage type
    collection: ChromaDBCollection,
    logger: Any,
    metrics: Any = None,
    OBSERVABILITY_AVAILABLE: bool = False,
    lock_manager: Optional[Any] = None,
    get_dynamo_client_func: Any = None,
) -> List[MetadataUpdateResult]:
    """Process RECEIPT deletion events for a specific collection.

    Deletes all embeddings for the receipt from ChromaDB and all child records
    from DynamoDB (ReceiptWordLabel, ReceiptWord, ReceiptLine, ReceiptLetter,
    ReceiptMetadata, CompactionRun).

    The compactor is responsible for complete cleanup when a Receipt is deleted.
    """
    logger.info("Processing receipt deletions", count=len(receipt_deletions))
    results: List[MetadataUpdateResult] = []

    bucket = os.environ["CHROMADB_BUCKET"]
    # Use the specific collection instead of hardcoded "words"
    database = collection.value

    try:
        # Download current snapshot using atomic helper
        logger.info("Downloading snapshot for receipt deletion", collection=database)
        temp_dir = tempfile.mkdtemp(prefix=f"chroma_receipt_deletion_{database}_")

        try:
            download_result = download_snapshot_atomic(
                bucket=bucket,
                collection=database,
                local_path=temp_dir,
            )

            if download_result.get("status") != "downloaded":
                logger.error(
                    "Failed to download snapshot",
                    result=download_result,
                    collection=database,
                )
                # Return error results for all messages
                for deletion_msg in receipt_deletions:
                    entity_data = deletion_msg.entity_data
                    results.append(
                        MetadataUpdateResult(
                            database=database,
                            collection=database,
                            updated_count=0,
                            image_id=entity_data.get("image_id", "unknown"),
                            receipt_id=entity_data.get("receipt_id", 0),
                            error="Failed to download snapshot",
                        )
                    )
                return results

            # Open collection
            chroma_client = ChromaClient(
                persist_directory=temp_dir, mode="write", metadata_only=True
            )
            collection_obj = chroma_client.get_collection(database)

            # Process each receipt deletion
            for deletion_msg in receipt_deletions:
                try:
                    entity_data = deletion_msg.entity_data
                    event_name = deletion_msg.event_name

                    image_id = entity_data["image_id"]
                    receipt_id = entity_data["receipt_id"]

                    if event_name != "REMOVE":
                        logger.warning(
                            "Unexpected event name for receipt deletion",
                            event_name=event_name,
                            image_id=image_id,
                            receipt_id=receipt_id,
                        )
                        continue

                    if OBSERVABILITY_AVAILABLE:
                        logger.info(
                            "Processing receipt deletion",
                            event_name=event_name,
                            image_id=image_id,
                            receipt_id=receipt_id,
                            collection=database,
                        )
                    else:
                        logger.info(
                            f"Processing receipt deletion: {event_name} for receipt {receipt_id} in {database}"
                        )

                    # Delete embeddings for this receipt
                    deleted_count = delete_receipt_embeddings(
                        collection_obj,
                        image_id,
                        receipt_id,
                        logger,
                        metrics,
                        OBSERVABILITY_AVAILABLE,
                        get_dynamo_client_func,
                    )

                    # After successfully deleting ChromaDB embeddings, delete child records from DynamoDB
                    # This ensures child records exist when we query for them to construct ChromaDB IDs
                    if get_dynamo_client_func:
                        dynamo_client = get_dynamo_client_func()
                        if dynamo_client:
                            deletion_counts = delete_receipt_child_records(
                                dynamo_client,
                                image_id,
                                receipt_id,
                                logger,
                                metrics,
                                OBSERVABILITY_AVAILABLE,
                            )
                            if OBSERVABILITY_AVAILABLE:
                                logger.info(
                                    "Deleted receipt child records from DynamoDB",
                                    image_id=image_id,
                                    receipt_id=receipt_id,
                                    **deletion_counts,
                                )

                    result = MetadataUpdateResult(
                        database=database,
                        collection=database,
                        updated_count=deleted_count,
                        image_id=image_id,
                        receipt_id=receipt_id,
                    )
                    results.append(result)

                except Exception as e:  # noqa: BLE001
                    logger.error("Error processing receipt deletion", error=str(e))
                    entity_data = deletion_msg.entity_data
                    results.append(
                        MetadataUpdateResult(
                            database=database,
                            collection=database,
                            updated_count=0,
                            image_id=entity_data.get("image_id", "unknown"),
                            receipt_id=entity_data.get("receipt_id", 0),
                            error=str(e),
                        )
                    )

            # Upload updated snapshot
            if results and any(r.updated_count > 0 for r in results if r.error is None):
                logger.info(
                    "Uploading updated snapshot after receipt deletion",
                    collection=database,
                )
                upload_result = upload_snapshot_atomic(
                    local_path=temp_dir,
                    bucket=bucket,
                    collection=database,
                    lock_manager=lock_manager,
                    metadata={
                        "update_type": "receipt_deletion",
                    },
                )

                if upload_result.get("status") != "uploaded":
                    logger.error(
                        "Failed to upload snapshot after receipt deletion",
                        result=upload_result,
                        collection=database,
                    )
                    # Mark all results as having upload errors by recreating them
                    for i, result in enumerate(results):
                        if result.error is None:
                            results[i] = MetadataUpdateResult(
                                database=result.database,
                                collection=result.collection,
                                updated_count=result.updated_count,
                                image_id=result.image_id,
                                receipt_id=result.receipt_id,
                                error="Failed to upload snapshot",
                            )
                else:
                    logger.info("Successfully uploaded snapshot", collection=database)

        finally:
            # Cleanup temp directory
            shutil.rmtree(temp_dir, ignore_errors=True)

    except Exception as e:  # noqa: BLE001
        logger.error("Error in receipt deletion processing", error=str(e))
        # Return error results for all messages
        for deletion_msg in receipt_deletions:
            entity_data = deletion_msg.entity_data
            results.append(
                MetadataUpdateResult(
                    database=database,
                    collection=database,
                    updated_count=0,
                    image_id=entity_data.get("image_id", "unknown"),
                    receipt_id=entity_data.get("receipt_id", 0),
                    error=str(e),
                )
            )

    return results


def apply_receipt_deletions_in_memory(
    chroma_client: ChromaClient,
    receipt_deletions: List[Any],
    collection: ChromaDBCollection,
    logger: Any,
    metrics: Any = None,
    OBSERVABILITY_AVAILABLE: bool = False,
    get_dynamo_client_func: Any = None,
) -> List[MetadataUpdateResult]:
    """Apply receipt deletions directly to an in-memory ChromaDB collection.

    This is used when the collection is already loaded in memory (e.g., during compaction).

    Also deletes all child records from DynamoDB (ReceiptWordLabel, ReceiptWord,
    ReceiptLine, ReceiptLetter, ReceiptMetadata, CompactionRun).
    """
    logger.info("Applying receipt deletions in memory", count=len(receipt_deletions))
    results: List[MetadataUpdateResult] = []

    database = collection.value
    collection_obj = chroma_client.get_collection(database)

    for deletion_msg in receipt_deletions:
        try:
            entity_data = deletion_msg.entity_data
            event_name = deletion_msg.event_name

            image_id = entity_data["image_id"]
            receipt_id = entity_data["receipt_id"]

            if event_name != "REMOVE":
                logger.warning(
                    "Unexpected event name for receipt deletion",
                    event_name=event_name,
                    image_id=image_id,
                    receipt_id=receipt_id,
                )
                continue

            # Delete embeddings for this receipt
            deleted_count = delete_receipt_embeddings(
                collection_obj,
                image_id,
                receipt_id,
                logger,
                metrics,
                OBSERVABILITY_AVAILABLE,
                get_dynamo_client_func,
            )

            # After successfully deleting ChromaDB embeddings, delete child records from DynamoDB
            # This ensures child records exist when we query for them to construct ChromaDB IDs
            if get_dynamo_client_func:
                dynamo_client = get_dynamo_client_func()
                if dynamo_client:
                    deletion_counts = delete_receipt_child_records(
                        dynamo_client,
                        image_id,
                        receipt_id,
                        logger,
                        metrics,
                        OBSERVABILITY_AVAILABLE,
                    )
                    if OBSERVABILITY_AVAILABLE:
                        logger.info(
                            "Deleted receipt child records from DynamoDB",
                            image_id=image_id,
                            receipt_id=receipt_id,
                            **deletion_counts,
                        )

            result = MetadataUpdateResult(
                database=database,
                collection=database,
                updated_count=deleted_count,
                image_id=image_id,
                receipt_id=receipt_id,
            )
            results.append(result)

        except Exception as e:  # noqa: BLE001
            logger.error("Error processing receipt deletion", error=str(e))
            entity_data = deletion_msg.entity_data
            results.append(
                MetadataUpdateResult(
                    database=database,
                    collection=database,
                    updated_count=0,
                    image_id=entity_data.get("image_id", "unknown"),
                    receipt_id=entity_data.get("receipt_id", 0),
                    error=str(e),
                )
            )

    return results
