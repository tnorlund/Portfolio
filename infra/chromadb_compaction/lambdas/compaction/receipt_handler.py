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

    Deletes all embeddings for the receipt from the specified collection.
    """
    logger.info("Processing receipt deletions", count=len(receipt_deletions))
    results: List[MetadataUpdateResult] = []

    bucket = os.environ["CHROMADB_BUCKET"]
    # Use the specific collection instead of hardcoded "words"
    database = collection.value
    snapshot_key = f"{database}/snapshot/latest/"

    try:
        # Download current snapshot using atomic helper
        logger.info(
            "Downloading snapshot for receipt deletion", collection=database
        )
        temp_dir = tempfile.mkdtemp(
            prefix=f"chroma_receipt_deletion_{database}_"
        )

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

                    result = MetadataUpdateResult(
                        database=database,
                        collection=database,
                        updated_count=deleted_count,
                        image_id=image_id,
                        receipt_id=receipt_id,
                    )
                    results.append(result)

                except Exception as e:  # noqa: BLE001
                    logger.error(
                        "Error processing receipt deletion", error=str(e)
                    )
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
            if results and any(
                r.updated_count > 0 for r in results if r.error is None
            ):
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
                    # Mark all results as having upload errors
                    for result in results:
                        if result.error is None:
                            result.error = "Failed to upload snapshot"
                else:
                    logger.info(
                        "Successfully uploaded snapshot", collection=database
                    )

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
    """
    logger.info(
        "Applying receipt deletions in memory", count=len(receipt_deletions)
    )
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
