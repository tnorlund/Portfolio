"""Place update processing for RECEIPT_PLACE entities."""

from typing import Any, List, Optional

from receipt_chroma.compaction.models import MetadataUpdateResult
from receipt_chroma.data.chroma_client import ChromaClient
from receipt_chroma.data.operations import (
    remove_receipt_place,
    update_receipt_place,
)

from receipt_dynamo.constants import ChromaDBCollection
from receipt_dynamo.data.dynamo_client import DynamoClient


def apply_place_updates(
    chroma_client: ChromaClient,
    place_messages: List[Any],
    collection: ChromaDBCollection,
    logger: Any,
    metrics: Any = None,
    dynamo_client: Optional[DynamoClient] = None,
) -> List[MetadataUpdateResult]:
    """Apply place updates to an already-open ChromaDB client without S3 I/O.

    This function processes RECEIPT_PLACE update messages and applies them
    to the given ChromaDB client. The caller is responsible for downloading
    the snapshot, opening the client, uploading the updated snapshot, and
    managing locks.

    Args:
        chroma_client: Open ChromaDB client with snapshot loaded
        place_messages: List of StreamMessage objects for place updates
        collection: Target collection (LINES or WORDS)
        logger: Logger instance for observability
        metrics: Optional metrics collector
        dynamo_client: Optional DynamoDB client for querying entities

    Returns:
        List of MetadataUpdateResult objects with update counts and errors
    """
    results: List[MetadataUpdateResult] = []
    database = collection.value

    # Get collection object from ChromaDB client
    try:
        collection_obj = chroma_client.get_collection(database)
    except Exception as e:
        logger.warning("Collection not found", database=database, error=str(e))
        return results

    # Process each place update message
    for update_msg in place_messages:
        try:
            entity_data = update_msg.entity_data
            changes = update_msg.changes
            event_name = update_msg.event_name
            image_id = entity_data["image_id"]
            receipt_id = entity_data["receipt_id"]

            # Determine whether to update or remove place data
            if event_name == "REMOVE":
                updated_count = remove_receipt_place(
                    collection=collection_obj,
                    image_id=image_id,
                    receipt_id=receipt_id,
                    logger=logger,
                    metrics=metrics,
                    OBSERVABILITY_AVAILABLE=(metrics is not None),
                    get_dynamo_client_func=(
                        lambda: dynamo_client if dynamo_client else None
                    ),
                )
            else:  # MODIFY or INSERT
                updated_count = update_receipt_place(
                    collection=collection_obj,
                    image_id=image_id,
                    receipt_id=receipt_id,
                    changes=changes,
                    logger=logger,
                    metrics=metrics,
                    OBSERVABILITY_AVAILABLE=(metrics is not None),
                    get_dynamo_client_func=(
                        lambda: dynamo_client if dynamo_client else None
                    ),
                )

            results.append(
                MetadataUpdateResult(
                    database=database,
                    collection=database,
                    updated_count=updated_count,
                    image_id=image_id,
                    receipt_id=receipt_id,
                )
            )

        except Exception as e:
            logger.exception("Error processing place update")
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
