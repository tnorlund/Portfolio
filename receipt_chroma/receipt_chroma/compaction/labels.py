"""Label update processing for RECEIPT_WORD_LABEL entities."""

from typing import Any, List, Optional

from receipt_chroma.compaction.models import LabelUpdateResult
from receipt_chroma.data.chroma_client import ChromaClient
from receipt_chroma.data.operations import (
    remove_word_labels,
    update_word_labels,
)

from receipt_dynamo.constants import ChromaDBCollection
from receipt_dynamo.data.dynamo_client import DynamoClient


def apply_label_updates(
    chroma_client: ChromaClient,
    label_messages: List[Any],
    collection: ChromaDBCollection,
    logger: Any,
    metrics: Any = None,
    dynamo_client: Optional[DynamoClient] = None,
) -> List[LabelUpdateResult]:
    """Apply label updates to an already-open ChromaDB client without S3 I/O.

    This function processes RECEIPT_WORD_LABEL update messages and applies them
    to the given ChromaDB client. The caller is responsible for downloading
    the snapshot, opening the client, uploading the updated snapshot, and
    managing locks.

    Args:
        chroma_client: Open ChromaDB client with snapshot loaded
        label_messages: List of StreamMessage objects for label updates
        collection: Target collection (LINES or WORDS)
        logger: Logger instance for observability
        metrics: Optional metrics collector
        dynamo_client: Optional DynamoDB client for querying entities

    Returns:
        List of LabelUpdateResult objects with update counts and errors
    """
    results: List[LabelUpdateResult] = []
    database = collection.value

    # Get collection object from ChromaDB client
    try:
        collection_obj = chroma_client.get_collection(database)
    except Exception:
        logger.warning("Collection not found for labels", collection=database)
        return results

    # Process each label update message
    for update_msg in label_messages:
        try:
            entity_data = update_msg.entity_data
            changes = update_msg.changes
            event_name = update_msg.event_name

            image_id = entity_data["image_id"]
            receipt_id = entity_data["receipt_id"]
            line_id = entity_data["line_id"]
            word_id = entity_data["word_id"]

            # Construct ChromaDB ID for this specific word
            chromadb_id = (
                f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#"
                f"LINE#{line_id:05d}#WORD#{word_id:05d}"
            )

            # Determine whether to update or remove labels
            if event_name == "REMOVE":
                updated_count = remove_word_labels(
                    collection=collection_obj,
                    chromadb_id=chromadb_id,
                    logger=logger,
                    metrics=metrics,
                    OBSERVABILITY_AVAILABLE=(metrics is not None),
                )
            else:  # MODIFY or INSERT
                updated_count = update_word_labels(
                    collection=collection_obj,
                    chromadb_id=chromadb_id,
                    changes=changes,
                    record_snapshot=getattr(update_msg, "record_snapshot", None),
                    entity_data=entity_data,
                    logger=logger,
                    metrics=metrics,
                    OBSERVABILITY_AVAILABLE=(metrics is not None),
                    get_dynamo_client_func=(
                        lambda: dynamo_client if dynamo_client else None
                    ),
                )

            results.append(
                LabelUpdateResult(
                    chromadb_id=chromadb_id,
                    updated_count=updated_count,
                    event_name=event_name,
                    changes=list(changes.keys()) if changes else [],
                )
            )

        except Exception as e:
            logger.exception("Error processing label update")
            results.append(
                LabelUpdateResult(
                    chromadb_id="unknown",
                    updated_count=0,
                    event_name="unknown",
                    changes=[],
                    error=str(e),
                )
            )

    return results
