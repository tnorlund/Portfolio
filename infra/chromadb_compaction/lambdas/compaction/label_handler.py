"""Label update processing for RECEIPT_WORD_LABEL entities."""

import os
import shutil
import tempfile
import time
from typing import Any, Dict, List, Optional

from receipt_dynamo.constants import ChromaDBCollection
from receipt_label.utils.chroma_client import ChromaDBClient
from receipt_label.utils.chroma_s3_helpers import (
    download_snapshot_atomic,
    upload_snapshot_atomic,
)

from .models import LabelUpdateResult
from .chromadb_operations import update_word_labels, remove_word_labels


def process_label_updates(
    label_updates: List[Any],  # StreamMessage type
    collection: ChromaDBCollection,
    logger,
    metrics=None,
    OBSERVABILITY_AVAILABLE=False,
    lock_manager: Optional[Any] = None,
    get_dynamo_client_func=None
) -> List[LabelUpdateResult]:
    """Process RECEIPT_WORD_LABEL updates for a specific collection.

    Updates label metadata for specific word embeddings in the collection.
    """
    logger.info("Processing label updates", count=len(label_updates))
    results = []

    bucket = os.environ["CHROMADB_BUCKET"]
    # Use the specific collection instead of hardcoded "words"
    database = collection.value
    snapshot_key = f"{database}/snapshot/latest/"

    try:
        # Download current snapshot using atomic helper
        logger.info(
            "Starting atomic snapshot download for label updates",
            collection=collection.value,
            bucket=bucket,
        )
        temp_dir = tempfile.mkdtemp()
        download_start_time = time.time()

        download_result = download_snapshot_atomic(
            bucket=bucket,
            collection=collection.value,  # "lines" or "words"
            local_path=temp_dir,
            verify_integrity=True,
        )

        download_time = time.time() - download_start_time
        logger.info(
            "Atomic snapshot download completed",
            collection=collection.value,
            status=download_result.get("status"),
            download_time_ms=download_time * 1000,
            version_id=download_result.get("version_id"),
        )

        if download_result["status"] != "downloaded":
            logger.error("Failed to download snapshot", result=download_result)

            if OBSERVABILITY_AVAILABLE and metrics:
                metrics.count(
                    "CompactionLabelSnapshotDownloadError",
                    1,
                    {"collection": collection.value},
                )
            return results

        # Load ChromaDB using helper in metadata-only mode
        chroma_client = ChromaDBClient(
            persist_directory=temp_dir,
            mode="read",
            metadata_only=True,  # No embeddings needed for metadata updates
        )

        # Get words collection
        try:
            logger.info(
                "Getting ChromaDB collection for labels", collection=database
            )
            collection_obj = chroma_client.get_collection(database)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.warning(
                "Collection not found for labels", collection=database
            )

            if OBSERVABILITY_AVAILABLE and metrics:
                metrics.count(
                    "CompactionLabelCollectionNotFound",
                    1,
                    {"collection": database},
                )
            return results

        # Process each label update
        for update_msg in label_updates:
            try:
                entity_data = update_msg.entity_data
                changes = update_msg.changes
                event_name = update_msg.event_name

                image_id = entity_data["image_id"]
                receipt_id = entity_data["receipt_id"]
                line_id = entity_data["line_id"]
                word_id = entity_data["word_id"]

                # Create ChromaDB ID for this specific word
                chromadb_id = (
                    f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#"
                    f"LINE#{line_id:05d}#WORD#{word_id:05d}"
                )

                if OBSERVABILITY_AVAILABLE:
                    logger.info(
                        "Processing label update",
                        event_name=event_name,
                        chromadb_id=chromadb_id,
                    )
                else:
                    logger.info(
                        "Processing %s for label: %s", event_name, chromadb_id
                    )

                if event_name == "REMOVE":
                    updated_count = remove_word_labels(
                        collection_obj, 
                        chromadb_id,
                        logger,
                        metrics,
                        OBSERVABILITY_AVAILABLE
                    )
                else:  # MODIFY
                    updated_count = update_word_labels(
                        collection_obj,
                        chromadb_id,
                        changes,
                        update_msg.record_snapshot,
                        entity_data,
                        logger,
                        metrics,
                        OBSERVABILITY_AVAILABLE,
                        get_dynamo_client_func
                    )

                result = LabelUpdateResult(
                    chromadb_id=chromadb_id,
                    updated_count=updated_count,
                    event_name=event_name,
                    changes=list(changes.keys()) if changes else [],
                )
                results.append(result)

            except Exception as e:  # pylint: disable=broad-exception-caught
                logger.error("Error processing label update", error=str(e))

                if OBSERVABILITY_AVAILABLE and metrics:
                    metrics.count(
                        "CompactionLabelProcessingError",
                        1,
                        {"error_type": type(e).__name__},
                    )

                result = LabelUpdateResult(
                    chromadb_id="unknown",
                    updated_count=0,
                    event_name="unknown",
                    changes=[],
                    error=f"{str(e)} - message: {update_msg}",
                )
                results.append(result)

        # Upload updated snapshot with hash if any updates occurred
        total_updates = sum(
            r.updated_count for r in results if r.error is None
        )
        logger.info(
            "DEBUG: Label update summary",
            total_updates=total_updates,
            results_count=len(results),
        )
        if total_updates > 0:
            logger.info(
                "DEBUG: Total updates > 0, proceeding with atomic S3 upload"
            )
            # Validate lock ownership before final S3 upload
            if lock_manager and not lock_manager.validate_ownership():
                error_msg = f"Lock validation failed before label snapshot upload for {collection.value}"
                if OBSERVABILITY_AVAILABLE:
                    logger.error(
                        "Lock ownership lost before label snapshot upload",
                        collection=collection.value,
                    )
                    if metrics:
                        metrics.count(
                            "CompactionLockValidationFailed",
                            1,
                            {
                                "collection": collection.value,
                                "operation": "label_upload",
                            },
                        )
                else:
                    logger.error(error_msg)

                # Replace successful results with error results
                results = [
                    LabelUpdateResult(
                        chromadb_id=result.chromadb_id,
                        updated_count=0,
                        event_name=result.event_name,
                        changes=result.changes,
                        error=(
                            error_msg if result.error is None else result.error
                        ),
                    )
                    for result in results
                ]

                shutil.rmtree(temp_dir, ignore_errors=True)
                return results

            logger.info(
                "DEBUG: Starting atomic snapshot upload for label updates",
                collection=collection.value,
                bucket=bucket,
                total_updates=total_updates,
                temp_dir=temp_dir,
            )
            upload_start_time = time.time()

            upload_result = upload_snapshot_atomic(
                local_path=temp_dir,
                bucket=bucket,
                collection=collection.value,  # "lines" or "words"
                lock_manager=lock_manager,
                metadata={
                    "update_type": "label_update",
                    "total_updates": str(total_updates),
                },
            )

            upload_time = time.time() - upload_start_time
            logger.info(
                "DEBUG: Atomic snapshot upload completed",
                collection=collection.value,
                status=upload_result.get("status"),
                upload_time_ms=upload_time * 1000,
                version_id=upload_result.get("version_id"),
                versioned_key=upload_result.get("versioned_key"),
                pointer_key=upload_result.get("pointer_key"),
                hash=upload_result.get("hash"),
            )

            if upload_result["status"] == "uploaded":
                if OBSERVABILITY_AVAILABLE:
                    logger.info(
                        "Updated ChromaDB labels",
                        total_updates=total_updates,
                        hash=upload_result.get("hash", "not_calculated"),
                    )
                    if metrics:
                        metrics.count(
                            "CompactionLabelSnapshotUploaded",
                            1,
                            {"collection": database},
                        )
                else:
                    logger.info(
                        "Updated %d word labels in ChromaDB, hash: %s",
                        total_updates,
                        upload_result.get("hash", "not_calculated"),
                    )
            else:
                logger.error("Failed to upload snapshot", result=upload_result)

                if OBSERVABILITY_AVAILABLE and metrics:
                    metrics.count(
                        "CompactionLabelSnapshotUploadError",
                        1,
                        {"collection": database},
                    )

    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error processing label updates", error=str(e))

        if OBSERVABILITY_AVAILABLE and metrics:
            metrics.count(
                "CompactionLabelUpdatesError",
                1,
                {"error_type": type(e).__name__},
            )

        result = LabelUpdateResult(
            chromadb_id="unknown",
            updated_count=0,
            event_name="unknown",
            changes=[],
            error=str(e),
        )
        results.append(result)
    finally:
        if "temp_dir" in locals():
            shutil.rmtree(temp_dir, ignore_errors=True)

    return results
