"""Metadata update processing for RECEIPT_METADATA entities."""

import os
import shutil
import tempfile
from typing import Any, Dict, List, Optional

from receipt_dynamo.constants import ChromaDBCollection
from receipt_label.utils.chroma_client import ChromaDBClient
from receipt_label.utils.chroma_s3_helpers import (
    download_snapshot_atomic,
    upload_snapshot_atomic,
)

from .models import MetadataUpdateResult
from .chromadb_operations import update_receipt_metadata, remove_receipt_metadata


def process_metadata_updates(
    metadata_updates: List[Any],  # StreamMessage type
    collection: ChromaDBCollection,
    logger: Any,
    metrics: Any = None,
    OBSERVABILITY_AVAILABLE: bool = False,
    lock_manager: Optional[Any] = None,
    get_dynamo_client_func: Any = None
) -> List[MetadataUpdateResult]:
    """Process RECEIPT_METADATA updates for a specific collection.

    Updates merchant information for embeddings in the specified collection.
    """
    logger.info("Processing metadata updates", count=len(metadata_updates))
    results = []

    bucket = os.environ["CHROMADB_BUCKET"]

    for update_msg in metadata_updates:
        try:
            entity_data = update_msg.entity_data
            changes = update_msg.changes
            event_name = update_msg.event_name

            image_id = entity_data["image_id"]
            receipt_id = entity_data["receipt_id"]

            if OBSERVABILITY_AVAILABLE:
                logger.info(
                    "Processing metadata update",
                    event_name=event_name,
                    image_id=image_id,
                    receipt_id=receipt_id,
                    changes=list(changes.keys()),
                )
            else:
                logger.info(
                    "Processing %s for metadata: image_id=%s, receipt_id=%s",
                    event_name,
                    image_id,
                    receipt_id,
                )

            # Update metadata for the specified collection
            database = collection.value
            snapshot_key = f"{database}/snapshot/latest/"

            try:
                # Validate lock ownership before S3 operations
                if lock_manager and not lock_manager.validate_ownership():
                    error_msg = f"Lock validation failed during metadata update for {collection.value}"
                    if OBSERVABILITY_AVAILABLE:
                        logger.error(
                            "Lock ownership lost during metadata processing",
                            collection=collection.value,
                            receipt_id=receipt_id,
                        )
                        if metrics:
                            metrics.count(
                                "CompactionLockValidationFailed",
                                1,
                                {
                                    "collection": collection.value,
                                    "operation": "s3_download",
                                },
                            )
                    else:
                        logger.error(error_msg)

                    results.append(
                        MetadataUpdateResult(
                            database=database,
                            collection=database,
                            image_id=image_id,
                            receipt_id=receipt_id,
                            error=error_msg,
                            updated_count=0,
                        )
                    )
                    continue

                # Download current snapshot using atomic helper
                temp_dir = tempfile.mkdtemp()
                download_result = download_snapshot_atomic(
                    bucket=bucket,
                    collection=collection.value,  # "lines" or "words"
                    local_path=temp_dir,
                    verify_integrity=True,
                )

                if download_result["status"] != "downloaded":
                    logger.error(
                        "Failed to download snapshot", result=download_result
                    )

                    if OBSERVABILITY_AVAILABLE and metrics:
                        metrics.count(
                            "CompactionSnapshotDownloadError",
                            1,
                            {"collection": collection.value},
                        )
                    continue

                # Load ChromaDB using helper in metadata-only mode
                chroma_client = ChromaDBClient(
                    persist_directory=temp_dir,
                    mode="read",
                    metadata_only=True,  # No embeddings needed for metadata updates
                )

                # Get appropriate collection
                try:
                    logger.info(
                        "Getting ChromaDB collection", collection=database
                    )
                    collection_obj = chroma_client.get_collection(database)

                    logger.info(
                        "Successfully got ChromaDB collection",
                        collection=database,
                    )
                except (
                    Exception
                ) as e:  # pylint: disable=broad-exception-caught
                    if OBSERVABILITY_AVAILABLE:
                        logger.warning(
                            "ChromaDB collection not found",
                            collection=database,
                            error=str(e),
                        )
                        if metrics:
                            metrics.count(
                                "CompactionCollectionNotFound",
                                1,
                                {"collection": database},
                            )
                    else:
                        logger.warning(
                            "Collection not found",
                            database=database,
                            error=str(e),
                        )
                    continue

                # Update metadata for this receipt
                if event_name == "REMOVE":
                    updated_count = remove_receipt_metadata(
                        collection_obj, 
                        image_id, 
                        receipt_id,
                        logger,
                        metrics,
                        OBSERVABILITY_AVAILABLE,
                        get_dynamo_client_func
                    )
                else:  # MODIFY
                    updated_count = update_receipt_metadata(
                        collection_obj, 
                        image_id, 
                        receipt_id, 
                        changes,
                        logger,
                        metrics,
                        OBSERVABILITY_AVAILABLE,
                        get_dynamo_client_func
                    )

                if updated_count > 0:
                    # Validate lock ownership before S3 upload
                    if lock_manager and not lock_manager.validate_ownership():
                        error_msg = f"Lock validation failed before snapshot upload for {collection.value}"
                        if OBSERVABILITY_AVAILABLE:
                            logger.error(
                                "Lock ownership lost before snapshot upload",
                                collection=collection.value,
                                receipt_id=receipt_id,
                            )
                            if metrics:
                                metrics.count(
                                    "CompactionLockValidationFailed",
                                    1,
                                    {
                                        "collection": collection.value,
                                        "operation": "s3_upload",
                                    },
                                )
                        else:
                            logger.error(error_msg)

                        results.append(
                            MetadataUpdateResult(
                                database=database,
                                collection=database,
                                image_id=image_id,
                                receipt_id=receipt_id,
                                error=error_msg,
                                updated_count=0,
                            )
                        )
                        shutil.rmtree(temp_dir, ignore_errors=True)
                        continue

                    # Upload updated snapshot atomically with lock validation
                    upload_result = upload_snapshot_atomic(
                        local_path=temp_dir,
                        bucket=bucket,
                        collection=collection.value,  # "lines" or "words"
                        lock_manager=lock_manager,
                        metadata={
                            "update_type": "metadata_update",
                            "image_id": image_id,
                            "receipt_id": str(receipt_id),
                            "updated_count": str(updated_count),
                        },
                    )

                    if upload_result["status"] == "uploaded":
                        if OBSERVABILITY_AVAILABLE:
                            logger.info(
                                "Updated ChromaDB metadata",
                                updated_count=updated_count,
                                database=database,
                                hash=upload_result.get(
                                    "hash", "not_calculated"
                                ),
                            )
                            if metrics:
                                metrics.count(
                                    "CompactionSnapshotUploaded",
                                    1,
                                    {"collection": database},
                                )
                        else:
                            logger.info(
                                "Updated %d records in receipt_%s, hash: %s",
                                updated_count,
                                database,
                                upload_result.get("hash", "not_calculated"),
                            )
                    else:
                        logger.error(
                            "Failed to upload snapshot", result=upload_result
                        )

                        if OBSERVABILITY_AVAILABLE and metrics:
                            metrics.count(
                                "CompactionSnapshotUploadError",
                                1,
                                {"collection": database},
                            )

                result = MetadataUpdateResult(
                    database=database,
                    collection=database,
                    updated_count=updated_count,
                    image_id=image_id,
                    receipt_id=receipt_id,
                )
                results.append(result)

            except Exception as e:  # pylint: disable=broad-exception-caught
                logger.error(
                    "Error updating metadata", database=database, error=str(e)
                )

                if OBSERVABILITY_AVAILABLE and metrics:
                    metrics.count(
                        "CompactionMetadataUpdateError",
                        1,
                        {
                            "collection": database,
                            "error_type": type(e).__name__,
                        },
                    )

                result = MetadataUpdateResult(
                    database=database,
                    collection=database,
                    updated_count=0,
                    image_id=image_id,
                    receipt_id=receipt_id,
                    error=str(e),
                )
                results.append(result)
            finally:
                if "temp_dir" in locals():
                    shutil.rmtree(temp_dir, ignore_errors=True)

        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Error processing metadata update", error=str(e))

            if OBSERVABILITY_AVAILABLE and metrics:
                metrics.count(
                    "CompactionMetadataProcessingError",
                    1,
                    {"error_type": type(e).__name__},
                )

            # Create error result with minimal info available
            result = MetadataUpdateResult(
                database="unknown",
                collection="unknown",
                updated_count=0,
                image_id="unknown",
                receipt_id=0,
                error=f"{str(e)} - message: {update_msg}",
            )
            results.append(result)

    return results
