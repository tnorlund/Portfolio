"""Label update processing for RECEIPT_WORD_LABEL entities.

Handles label updates for both WORDS and LINES collections:
- WORDS: Direct label metadata updates on word embeddings
- LINES: Label aggregation on row-based line embeddings

For LINES collection, labels are aggregated across all words in a visual row.
The row ID is determined by the primary (leftmost) line in the row.
"""

import json
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

    For WORDS collection: Updates word-specific embeddings with label metadata.
    For LINES collection: Aggregates labels across visual rows and updates
        row-based line embeddings.

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
    # Route to collection-specific handler
    if collection == ChromaDBCollection.LINES:
        return _apply_line_label_updates(
            chroma_client=chroma_client,
            label_messages=label_messages,
            logger=logger,
            metrics=metrics,
            dynamo_client=dynamo_client,
        )
    else:
        return _apply_word_label_updates(
            chroma_client=chroma_client,
            label_messages=label_messages,
            logger=logger,
            metrics=metrics,
            dynamo_client=dynamo_client,
        )


def _apply_word_label_updates(
    chroma_client: ChromaClient,
    label_messages: List[Any],
    logger: Any,
    metrics: Any = None,
    dynamo_client: Optional[DynamoClient] = None,
) -> List[LabelUpdateResult]:
    """Apply label updates to WORDS collection embeddings."""
    results: List[LabelUpdateResult] = []
    database = ChromaDBCollection.WORDS.value

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
                    record_snapshot=getattr(
                        update_msg, "record_snapshot", None
                    ),
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
            logger.exception("Error processing word label update")
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


def _apply_line_label_updates(
    chroma_client: ChromaClient,
    label_messages: List[Any],
    logger: Any,
    metrics: Any = None,
    dynamo_client: Optional[DynamoClient] = None,
) -> List[LabelUpdateResult]:
    """Apply label updates to LINES collection (row-based embeddings).

    For LINES collection, we need to:
    1. Find which visual row contains the word being labeled
    2. Aggregate all labels from words in that row
    3. Update the row embedding's label metadata

    The row ID is determined by the row_line_ids metadata field, which contains
    all line IDs in the visual row.
    """
    results: List[LabelUpdateResult] = []
    database = ChromaDBCollection.LINES.value

    # Get collection object from ChromaDB client
    try:
        collection_obj = chroma_client.get_collection(database)
    except Exception:
        logger.warning("Collection not found for labels", collection=database)
        return results

    # Group messages by (image_id, receipt_id, line_id) to batch updates per row
    # A visual row is identified by its primary line_id
    messages_by_receipt: dict[tuple[str, int], List[Any]] = {}
    for update_msg in label_messages:
        entity_data = update_msg.entity_data
        key = (entity_data["image_id"], entity_data["receipt_id"])
        messages_by_receipt.setdefault(key, []).append(update_msg)

    # Process each receipt's label updates
    for (image_id, receipt_id), receipt_messages in messages_by_receipt.items():
        try:
            # Find all line embeddings for this receipt to identify visual rows
            receipt_prefix = f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#"

            # Query all line embeddings for this receipt
            existing_results = collection_obj.get(
                where={"image_id": image_id, "receipt_id": receipt_id},
                include=["metadatas"],
            )

            if not existing_results["ids"]:
                logger.debug(
                    "No existing line embeddings for receipt",
                    image_id=image_id,
                    receipt_id=receipt_id,
                )
                continue

            # Build a mapping of line_id -> row chromadb_id
            # Parse row_line_ids to find which row each line belongs to
            line_to_row_id: dict[int, str] = {}
            for idx, chromadb_id in enumerate(existing_results["ids"]):
                metadata = existing_results["metadatas"][idx]
                row_line_ids_str = metadata.get("row_line_ids", "[]")
                try:
                    row_line_ids = json.loads(row_line_ids_str)
                except (json.JSONDecodeError, TypeError):
                    # Legacy embedding without row_line_ids
                    line_id = metadata.get("line_id")
                    if line_id is not None:
                        row_line_ids = [line_id]
                    else:
                        continue

                # Map each line_id in this row to the row's chromadb_id
                for lid in row_line_ids:
                    line_to_row_id[lid] = chromadb_id

            # Find unique rows affected by these label updates
            affected_rows: set[str] = set()
            for update_msg in receipt_messages:
                line_id = update_msg.entity_data["line_id"]
                if line_id in line_to_row_id:
                    affected_rows.add(line_to_row_id[line_id])
                else:
                    logger.debug(
                        "Line not found in any visual row",
                        image_id=image_id,
                        receipt_id=receipt_id,
                        line_id=line_id,
                    )

            # Update each affected row's label metadata
            for row_chromadb_id in affected_rows:
                try:
                    # Get all word labels for this receipt from DynamoDB
                    # to aggregate labels for the row
                    if dynamo_client:
                        updated_count = _update_row_labels(
                            collection=collection_obj,
                            chromadb_id=row_chromadb_id,
                            dynamo_client=dynamo_client,
                            logger=logger,
                            metrics=metrics,
                        )
                    else:
                        # Without DynamoDB client, we can't aggregate labels
                        logger.warning(
                            "DynamoDB client not available for row label "
                            "aggregation",
                            chromadb_id=row_chromadb_id,
                        )
                        updated_count = 0

                    results.append(
                        LabelUpdateResult(
                            chromadb_id=row_chromadb_id,
                            updated_count=updated_count,
                            event_name="MODIFY",
                            changes=["labels_aggregated"],
                        )
                    )

                except Exception as e:
                    logger.exception(
                        "Error updating row labels",
                        chromadb_id=row_chromadb_id,
                    )
                    results.append(
                        LabelUpdateResult(
                            chromadb_id=row_chromadb_id,
                            updated_count=0,
                            event_name="MODIFY",
                            changes=[],
                            error=str(e),
                        )
                    )

        except Exception as e:
            logger.exception(
                "Error processing line label updates for receipt",
                image_id=image_id,
                receipt_id=receipt_id,
            )
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


def _update_row_labels(
    collection: Any,
    chromadb_id: str,
    dynamo_client: DynamoClient,
    logger: Any,
    metrics: Any = None,
) -> int:
    """Update a row embedding's label metadata by aggregating word labels.

    Queries DynamoDB for all word labels in the row's lines and updates
    the embedding's metadata with boolean label flags.

    Args:
        collection: ChromaDB collection object
        chromadb_id: The row's ChromaDB ID
        dynamo_client: DynamoDB client for querying labels
        logger: Logger instance
        metrics: Optional metrics collector

    Returns:
        Number of records updated (0 or 1)
    """
    # Get existing metadata to find row_line_ids
    existing = collection.get(ids=[chromadb_id], include=["metadatas"])
    if not existing["ids"]:
        logger.debug("Row embedding not found", chromadb_id=chromadb_id)
        return 0

    metadata = existing["metadatas"][0]
    image_id = metadata.get("image_id")
    receipt_id = metadata.get("receipt_id")
    row_line_ids_str = metadata.get("row_line_ids", "[]")

    try:
        row_line_ids = json.loads(row_line_ids_str)
    except (json.JSONDecodeError, TypeError):
        # Legacy embedding - use the line_id from metadata
        line_id = metadata.get("line_id")
        row_line_ids = [line_id] if line_id is not None else []

    if not row_line_ids:
        logger.debug("No line IDs for row", chromadb_id=chromadb_id)
        return 0

    # Query all word labels for lines in this row
    # Note: This requires a helper in receipt_dynamo to list labels by line IDs
    try:
        all_labels = dynamo_client.list_receipt_word_labels_for_lines(
            image_id=image_id,
            receipt_id=receipt_id,
            line_ids=row_line_ids,
        )
    except AttributeError:
        # Method doesn't exist yet - fall back to empty labels
        logger.warning(
            "DynamoClient missing list_receipt_word_labels_for_lines method",
            chromadb_id=chromadb_id,
        )
        all_labels = []

    # Aggregate labels into boolean flags
    # Only include VALID labels
    label_flags: dict[str, bool] = {}
    for label_entity in all_labels:
        if label_entity.validation_status == "VALID":
            label_key = f"label_{label_entity.label}"
            label_flags[label_key] = True

    # Update metadata with label flags
    # Remove any existing label_ fields and add new ones
    new_metadata = {
        k: v for k, v in metadata.items() if not k.startswith("label_")
    }
    new_metadata.update(label_flags)

    # Update the embedding
    collection.update(ids=[chromadb_id], metadatas=[new_metadata])

    if metrics:
        metrics.count("RowLabelUpdated", 1)

    logger.debug(
        "Updated row label metadata",
        chromadb_id=chromadb_id,
        label_count=len(label_flags),
    )

    return 1
