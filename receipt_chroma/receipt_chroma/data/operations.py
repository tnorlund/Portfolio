"""ChromaDB operations for metadata and label updates."""

import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.data.dynamo_client import DynamoClient


def _normalize_labels(labels: list[str]) -> list[str]:
    """Normalize labels to stable, deduplicated arrays."""
    return sorted(
        {lbl.strip().upper() for lbl in labels if lbl and lbl.strip()}
    )


def _labels_from_array_metadata(
    metadata: Dict[str, Any],
    array_field: str,
) -> set[str]:
    """Read labels from canonical array metadata."""
    array_val = metadata.get(array_field)
    if isinstance(array_val, list):
        return {
            str(lbl).strip().upper() for lbl in array_val if str(lbl).strip()
        }
    return set()


def _set_label_array_fields(
    metadata: Dict[str, Any],
    valid_labels: set[str],
    invalid_labels: set[str],
) -> None:
    """Write canonical label arrays with Chroma-safe clear semantics."""
    canonical_valid = _normalize_labels(list(valid_labels))
    canonical_invalid = _normalize_labels(list(invalid_labels))

    if canonical_valid:
        metadata["valid_labels_array"] = canonical_valid
    elif "valid_labels_array" in metadata:
        metadata["valid_labels_array"] = None

    if canonical_invalid:
        metadata["invalid_labels_array"] = canonical_invalid
    elif "invalid_labels_array" in metadata:
        metadata["invalid_labels_array"] = None


def update_receipt_place(
    collection: Any,
    image_id: str,
    receipt_id: int,
    changes: Dict[str, Any],
    logger: Any,
    metrics: Any = None,
    OBSERVABILITY_AVAILABLE: bool = False,
    get_dynamo_client_func: Any = None,
) -> int:
    """Update place metadata for all embeddings of a receipt.

    Builds exact ChromaDB IDs via DynamoDB instead of scanning the collection.
    """
    start_time = time.time()

    if OBSERVABILITY_AVAILABLE:
        logger.info(
            "Starting place update",
            image_id=image_id,
            receipt_id=receipt_id,
            changes=changes,
        )
    else:
        logger.info(
            "Starting place update",
            image_id=image_id,
            receipt_id=receipt_id,
        )
        logger.info("Changes to apply", changes=changes)

    # Get DynamoDB client to query for words/lines
    if get_dynamo_client_func:
        dynamo_client = get_dynamo_client_func()
    else:
        dynamo_client = DynamoClient(os.environ["DYNAMODB_TABLE_NAME"])

    # Determine collection type from collection name
    collection_name = collection.name

    logger.info("Processing collection", collection_name=collection_name)

    # Construct ChromaDB IDs by querying DynamoDB for exact entities
    chromadb_ids = []

    if "words" in collection_name:
        try:
            words = dynamo_client.list_receipt_words_from_receipt(
                image_id, receipt_id
            )

            logger.info("Found words in DynamoDB", count=len(words))
            if OBSERVABILITY_AVAILABLE and metrics:
                metrics.gauge("CompactionDynamoDBWords", len(words))

            chromadb_ids = [
                (
                    f"IMAGE#{word.image_id}"
                    f"#RECEIPT#{word.receipt_id:05d}"
                    f"#LINE#{word.line_id:05d}"
                    f"#WORD#{word.word_id:05d}"
                )
                for word in words
            ]
        except Exception as e:
            logger.error("Failed to query words from DynamoDB", error=str(e))

            if OBSERVABILITY_AVAILABLE and metrics:
                metrics.count(
                    "CompactionDynamoDBQueryError",
                    1,
                    {"entity_type": "words", "error_type": type(e).__name__},
                )
            return 0

    elif "lines" in collection_name:
        try:
            lines = dynamo_client.list_receipt_lines_from_receipt(
                image_id, receipt_id
            )

            logger.info("Found lines in DynamoDB", count=len(lines))
            if OBSERVABILITY_AVAILABLE and metrics:
                metrics.gauge("CompactionDynamoDBLines", len(lines))

            chromadb_ids = [
                (
                    f"IMAGE#{line.image_id}"
                    f"#RECEIPT#{line.receipt_id:05d}"
                    f"#LINE#{line.line_id:05d}"
                )
                for line in lines
            ]
        except Exception as e:
            logger.error("Failed to query lines from DynamoDB", error=str(e))

            if OBSERVABILITY_AVAILABLE and metrics:
                metrics.count(
                    "CompactionDynamoDBQueryError",
                    1,
                    {"entity_type": "lines", "error_type": type(e).__name__},
                )
            return 0
    else:
        logger.warning(
            "Unknown collection type", collection_name=collection_name
        )

        if OBSERVABILITY_AVAILABLE and metrics:
            metrics.count(
                "CompactionUnknownCollectionType",
                1,
                {"collection_name": collection_name},
            )
        return 0

    logger.info("Constructed ChromaDB IDs", count=len(chromadb_ids))

    if not chromadb_ids:
        logger.warning(
            "No entities found in DynamoDB",
            image_id=image_id,
            receipt_id=receipt_id,
        )
        return 0

    # Use direct ID lookup
    try:
        results = collection.get(ids=chromadb_ids, include=["metadatas"])
        found_count = len(results.get("ids", []))

        logger.info(
            "Retrieved records from ChromaDB",
            found_count=found_count,
            expected_count=len(chromadb_ids),
        )
        if metrics:
            metrics.gauge("CompactionChromaDBRecordsFound", found_count)

        if found_count == 0:
            # Graceful handling: embedding may have been deleted by a REMOVE
            # that arrived in the same batch (Standard queues don't guarantee
            # ordering, but we sort REMOVE first within each batch)
            logger.info(
                "No matching embeddings found in ChromaDB for place update - "
                "may have been deleted",
                receipt_id=receipt_id,
                image_id=image_id,
                expected=len(chromadb_ids),
            )
            if metrics:
                metrics.count(
                    "CompactionPlaceEmbeddingNotFound",
                    1,
                    {
                        "image_id": image_id,
                        "receipt_id": str(receipt_id),
                        "collection": collection_name,
                    },
                )
            return 0
    except Exception as e:
        logger.error("Failed to query ChromaDB with exact IDs", error=str(e))

        if metrics:
            metrics.count(
                "CompactionChromaDBQueryError",
                1,
                {"error_type": type(e).__name__},
            )
        return 0

    # Prepare updated metadata for found records
    matching_ids = results.get("ids", [])
    matching_metadatas = []

    for i, _ in enumerate(matching_ids):
        existing_metadata = results["metadatas"][i] or {}
        updated_metadata = existing_metadata.copy()

        # Apply field changes, mapping ReceiptPlace fields to ChromaDB fields
        for field, change in changes.items():
            if hasattr(change, "new"):
                new_value = change.new
            else:
                new_value = (
                    change.get("new") if isinstance(change, dict) else change
                )

            # Map ReceiptPlace field names to ChromaDB metadata field names
            chromadb_field = field
            if field == "formatted_address":
                chromadb_field = "address"

            if new_value is not None:
                updated_metadata[chromadb_field] = new_value
            elif chromadb_field in updated_metadata:
                del updated_metadata[chromadb_field]

        # Add update timestamp
        updated_metadata["last_place_update"] = datetime.now(
            timezone.utc
        ).isoformat()
        matching_metadatas.append(updated_metadata)

    # Update records if any found
    if matching_ids:
        try:
            collection.update(ids=matching_ids, metadatas=matching_metadatas)
            elapsed_time = time.time() - start_time

            if OBSERVABILITY_AVAILABLE:
                logger.info(
                    "Successfully updated place metadata",
                    updated_count=len(matching_ids),
                    elapsed_seconds=elapsed_time,
                )
                if metrics:
                    metrics.timer("CompactionPlaceUpdateTime", elapsed_time)
                    metrics.count(
                        "CompactionPlaceUpdatedRecords", len(matching_ids)
                    )
            else:
                logger.info(
                    "Successfully updated place metadata for embeddings",
                    embedding_count=len(matching_ids),
                    elapsed_seconds=elapsed_time,
                )
        except Exception as e:
            logger.error(
                "Failed to update ChromaDB place metadata", error=str(e)
            )

            if OBSERVABILITY_AVAILABLE and metrics:
                metrics.count(
                    "CompactionChromaDBUpdateError",
                    1,
                    {"error_type": type(e).__name__},
                )
            return 0
    else:
        logger.warning(
            "No matching ChromaDB records found",
            dynamodb_ids=len(chromadb_ids),
        )

    elapsed_time = time.time() - start_time

    if OBSERVABILITY_AVAILABLE:
        logger.info(
            "Place update completed",
            elapsed_seconds=elapsed_time,
            approach="DynamoDB-driven",
        )
    else:
        logger.info(
            "Place update completed (DynamoDB-driven approach)",
            elapsed_seconds=elapsed_time,
        )
    return len(matching_ids)


def remove_receipt_place(
    collection: Any,
    image_id: str,
    receipt_id: int,
    logger: Any,
    metrics: Any = None,
    OBSERVABILITY_AVAILABLE: bool = False,
    get_dynamo_client_func: Any = None,
) -> int:
    """Remove place metadata fields for a receipt.

    Uses DynamoDB to build exact ChromaDB IDs instead of scanning the
    collection.
    """
    start_time = time.time()

    if OBSERVABILITY_AVAILABLE:
        logger.info(
            "Starting place removal",
            image_id=image_id,
            receipt_id=receipt_id,
        )
    else:
        logger.info(
            "Starting place removal",
            image_id=image_id,
            receipt_id=receipt_id,
        )

    # Get DynamoDB client to query for words/lines
    if get_dynamo_client_func:
        dynamo_client = get_dynamo_client_func()
    else:
        dynamo_client = DynamoClient(os.environ["DYNAMODB_TABLE_NAME"])

    # Determine collection type from collection name
    collection_name = collection.name

    # Construct ChromaDB IDs by querying DynamoDB for exact entities
    chromadb_ids = []

    if "words" in collection_name:
        try:
            words = dynamo_client.list_receipt_words_from_receipt(
                image_id, receipt_id
            )
            chromadb_ids = [
                (
                    f"IMAGE#{word.image_id}"
                    f"#RECEIPT#{word.receipt_id:05d}"
                    f"#LINE#{word.line_id:05d}"
                    f"#WORD#{word.word_id:05d}"
                )
                for word in words
            ]
        except Exception as e:
            logger.error("Failed to query words from DynamoDB", error=str(e))
            return 0

    elif "lines" in collection_name:
        try:
            lines = dynamo_client.list_receipt_lines_from_receipt(
                image_id, receipt_id
            )
            chromadb_ids = [
                (
                    f"IMAGE#{line.image_id}"
                    f"#RECEIPT#{line.receipt_id:05d}"
                    f"#LINE#{line.line_id:05d}"
                )
                for line in lines
            ]
        except Exception as e:
            logger.error("Failed to query lines from DynamoDB", error=str(e))
            return 0
    else:
        logger.warning(
            "Unknown collection type", collection_name=collection_name
        )
        return 0

    if not chromadb_ids:
        logger.warning(
            "No entities found in DynamoDB",
            image_id=image_id,
            receipt_id=receipt_id,
        )
        return 0

    # Fields to remove when place data is deleted
    # Same as receipt_metadata plus any additional ReceiptPlace fields
    fields_to_remove = [
        "canonical_merchant_name",
        "merchant_name",
        "merchant_category",
        "address",  # Maps from formatted_address
        "phone_number",
        "place_id",
    ]

    # Use direct ID lookup instead of scanning entire collection
    try:
        results = collection.get(ids=chromadb_ids, include=["metadatas"])
        found_count = len(results.get("ids", []))

        logger.info(
            "Retrieved records from ChromaDB for removal", count=found_count
        )
    except Exception as e:
        logger.error(
            "Failed to query ChromaDB for place removal", error=str(e)
        )
        return 0

    matching_ids = results.get("ids", [])
    matching_metadatas = []

    for i, _ in enumerate(matching_ids):
        existing_metadata = results["metadatas"][i] or {}
        updated_metadata = existing_metadata.copy()

        # Set fields to None to remove them
        for field in fields_to_remove:
            if field in updated_metadata:
                updated_metadata[field] = None

        # Add removal timestamp
        updated_metadata["place_removed_at"] = datetime.now(
            timezone.utc
        ).isoformat()
        matching_metadatas.append(updated_metadata)

    # Update records if any found
    if matching_ids:
        try:
            collection.update(ids=matching_ids, metadatas=matching_metadatas)
            elapsed_time = time.time() - start_time

            if OBSERVABILITY_AVAILABLE:
                logger.info(
                    "Removed place metadata",
                    removed_count=len(matching_ids),
                    elapsed_seconds=elapsed_time,
                )
                if metrics:
                    metrics.timer("CompactionPlaceRemovalTime", elapsed_time)
                    metrics.count(
                        "CompactionPlaceRemovedRecords", len(matching_ids)
                    )
            else:
                logger.info(
                    "Removed place metadata from embeddings",
                    embedding_count=len(matching_ids),
                    elapsed_seconds=elapsed_time,
                )
        except Exception as e:
            logger.error(
                "Failed to remove ChromaDB place metadata", error=str(e)
            )
            return 0
    else:
        logger.warning(
            "No matching ChromaDB records found for removal",
            dynamodb_ids=len(chromadb_ids),
        )

    elapsed_time = time.time() - start_time

    if OBSERVABILITY_AVAILABLE:
        logger.info(
            "Place removal completed",
            elapsed_seconds=elapsed_time,
            approach="DynamoDB-driven",
        )
    else:
        logger.info(
            "Place removal completed (DynamoDB-driven approach)",
            elapsed_seconds=elapsed_time,
        )
    return len(matching_ids)


def update_word_labels(
    collection: Any,
    chromadb_id: str,
    changes: Dict[str, Any],
    record_snapshot: Optional[Dict[str, Any]] = None,
    entity_data: Optional[Dict[str, Any]] = None,
    logger: Any = None,
    metrics: Any = None,
    OBSERVABILITY_AVAILABLE: bool = False,
    get_dynamo_client_func: Any = None,
) -> int:
    """Update word label metadata using snapshot when available.

    Falls back to DynamoDB reconstruction if snapshot is missing.
    """
    try:
        # Parse ChromaDB ID to extract word identifiers.
        # Format:
        # IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#WORD#{
        # word_id:05d}
        parts = chromadb_id.split("#")
        if len(parts) < 8 or "WORD" not in parts:
            if logger:
                logger.error(
                    "Invalid ChromaDB ID format for word",
                    chromadb_id=chromadb_id,
                )
            if OBSERVABILITY_AVAILABLE and metrics:
                metrics.count("CompactionInvalidChromaDBID", 1)
            return 0

        image_id = parts[1]
        receipt_id = int(parts[3])
        line_id = int(parts[5])
        word_id = int(parts[7])

        # Get the specific record from ChromaDB
        result = collection.get(ids=[chromadb_id], include=["metadatas"])
        if not result["ids"]:
            # Graceful handling: embedding may have been deleted by a REMOVE
            # that arrived in the same batch (Standard queues don't guarantee
            # ordering, but we sort REMOVE first within each batch)
            if logger:
                logger.info(
                    "Word embedding not found in snapshot - may have been deleted",
                    chromadb_id=chromadb_id,
                    image_id=image_id,
                    receipt_id=receipt_id,
                    line_id=line_id,
                    word_id=word_id,
                )
            if metrics:
                metrics.count(
                    "CompactionWordEmbeddingNotFound",
                    1,
                    {
                        "image_id": image_id,
                        "receipt_id": str(receipt_id),
                        "line_id": str(line_id),
                        "word_id": str(word_id),
                    },
                )
            return 0

        # Prefer snapshot data if available to avoid DynamoDB race conditions
        if record_snapshot:
            # Build minimal fields for label metadata from snapshot
            reconstructed_metadata = {
                "label_status": None,  # will be derived below if needed
            }
        else:
            # Reconstruct complete label metadata using the same logic as step
            # function
            if get_dynamo_client_func:
                dynamo_client = get_dynamo_client_func()
            else:
                dynamo_client = DynamoClient(os.environ["DYNAMODB_TABLE_NAME"])

            reconstructed_metadata = reconstruct_label_metadata(
                image_id=image_id,
                receipt_id=receipt_id,
                line_id=line_id,
                word_id=word_id,
                dynamo_client=dynamo_client,
            )

        # Get existing metadata and update with reconstructed label fields
        existing_metadata = result["metadatas"][0] or {}
        updated_metadata = existing_metadata.copy()

        # Update with reconstructed/snapshot-derived label fields
        if record_snapshot:
            # Apply targeted changes directly based on the incoming change set
            # for this word
            if changes:
                # validation_status
                if "validation_status" in changes:
                    change = changes["validation_status"]
                    # Handle both FieldChange objects and plain dicts
                    if hasattr(change, "new"):
                        new_status = change.new
                    else:
                        new_status = (
                            change.get("new")
                            if isinstance(change, dict)
                            else change
                        )

                    if new_status is not None:
                        updated_metadata["label_status"] = (
                            "validated"
                            if new_status == "VALID"
                            else (
                                "invalidated"
                                if new_status == "INVALID"
                                else (
                                    "auto_suggested"
                                    if new_status == "PENDING"
                                    else updated_metadata.get("label_status")
                                )
                            )
                        )
                # label_proposed_by
                if "label_proposed_by" in changes:
                    change = changes["label_proposed_by"]
                    # Handle both FieldChange objects and plain dicts
                    if hasattr(change, "new"):
                        val = change.new
                    else:
                        val = (
                            change.get("new")
                            if isinstance(change, dict)
                            else change
                        )

                    if val is not None:
                        updated_metadata["label_proposed_by"] = val

            # Add/update current label in canonical valid/invalid arrays.
            if changes and "validation_status" in changes:
                change = changes["validation_status"]
                # Handle both FieldChange objects and plain dicts
                if hasattr(change, "new"):
                    status = change.new
                else:
                    status = (
                        change.get("new")
                        if isinstance(change, dict)
                        else change
                    )
            else:
                status = None
            current_label = None
            if entity_data and isinstance(entity_data, dict):
                current_label = entity_data.get("label")
            if current_label and status:
                normalized_label = str(current_label).strip().upper()
                if normalized_label:
                    valid_set = _labels_from_array_metadata(
                        updated_metadata,
                        array_field="valid_labels_array",
                    )
                    invalid_set = _labels_from_array_metadata(
                        updated_metadata,
                        array_field="invalid_labels_array",
                    )
                    if status == "VALID":
                        valid_set.add(normalized_label)
                        invalid_set.discard(normalized_label)
                    elif status == "INVALID":
                        if normalized_label not in valid_set:
                            invalid_set.add(normalized_label)
                    elif status == "PENDING":
                        valid_set.discard(normalized_label)
                        invalid_set.discard(normalized_label)

                    _set_label_array_fields(
                        updated_metadata,
                        valid_labels=valid_set,
                        invalid_labels=invalid_set,
                    )
        else:
            updated_metadata.update(reconstructed_metadata)

        # Add update timestamp
        updated_metadata["last_label_update"] = datetime.now(
            timezone.utc
        ).isoformat()

        # Update the ChromaDB record
        collection.update(ids=[chromadb_id], metadatas=[updated_metadata])

        valid_labels_array = updated_metadata.get("valid_labels_array", [])
        if isinstance(valid_labels_array, list):
            valid_count = len(valid_labels_array)
        else:
            valid_count = 0

        if logger:
            logger.info(
                "Updated labels for word with reconstructed metadata",
                chromadb_id=chromadb_id,
                label_status=reconstructed_metadata.get("label_status"),
                validated_labels_count=valid_count,
            )

        if OBSERVABILITY_AVAILABLE and metrics:
            metrics.count("CompactionWordLabelUpdated", 1)
            metrics.gauge(
                "CompactionValidatedLabelsCount",
                valid_count,
            )

        return 1

    except Exception as e:  # pylint: disable=broad-exception-caught
        if logger:
            logger.error(
                "Error updating word labels",
                chromadb_id=chromadb_id,
                error=str(e),
            )

        if OBSERVABILITY_AVAILABLE and metrics:
            metrics.count(
                "CompactionWordLabelUpdateError",
                1,
                {"error_type": type(e).__name__},
            )
        return 0


def remove_word_labels(
    collection: Any,
    chromadb_id: str,
    logger: Any = None,
    metrics: Any = None,
    OBSERVABILITY_AVAILABLE: bool = False,
) -> int:
    """Remove label metadata from a specific word embedding."""
    try:
        # Get the specific record
        result = collection.get(ids=[chromadb_id], include=["metadatas"])

        if not result["ids"]:
            if logger:
                logger.warning(
                    "Word embedding not found", chromadb_id=chromadb_id
                )

            if OBSERVABILITY_AVAILABLE and metrics:
                metrics.count("CompactionWordEmbeddingNotFoundForRemoval", 1)
            return 0

        # Remove all label fields from metadata
        existing_metadata = result["metadatas"][0] or {}
        updated_metadata = existing_metadata.copy()

        # Remove all label-related fields (matching step function structure)
        # NOTE: ChromaDB merges metadata on update, so we must set fields to
        # None instead of deleting them from the dict
        base_label_fields = [
            "label_status",
            "label_confidence",
            "label_proposed_by",
            "valid_labels_array",
            "invalid_labels_array",
            "label_validated_at",
        ]

        # Set base label fields to None (ChromaDB will remove them)
        for field in base_label_fields:
            if field in updated_metadata:
                updated_metadata[field] = None

        # Remove any remaining fields starting with "label_".
        for key in list(updated_metadata.keys()):
            if (
                key.startswith("label_")
                and updated_metadata.get(key) is not None
            ):
                updated_metadata[key] = None

        # Add removal timestamp
        updated_metadata["labels_removed_at"] = datetime.now(
            timezone.utc
        ).isoformat()

        # Update the record
        collection.update(ids=[chromadb_id], metadatas=[updated_metadata])

        if logger:
            logger.info("Removed labels from word", chromadb_id=chromadb_id)
        if OBSERVABILITY_AVAILABLE and metrics:
            metrics.count("CompactionWordLabelRemoved", 1)
        return 1

    except Exception as e:  # pylint: disable=broad-exception-caught
        if logger:
            logger.error(
                "Error removing word labels",
                chromadb_id=chromadb_id,
                error=str(e),
            )

        if OBSERVABILITY_AVAILABLE and metrics:
            metrics.count(
                "CompactionWordLabelRemovalError",
                1,
                {"error_type": type(e).__name__},
            )
        return 0


def reconstruct_label_metadata(
    image_id: str,
    receipt_id: int,
    line_id: int,
    word_id: int,
    dynamo_client: Any,
) -> Dict[str, Any]:
    """
    Reconstruct label-related metadata fields exactly as the step
    function does.

    Args:
        image_id: Image ID
        receipt_id: Receipt ID
        line_id: Line ID
        word_id: Word ID
        dynamo_client: DynamoDB client instance

    Returns:
        Dictionary with reconstructed label metadata fields:
        - valid_labels_array: array of valid labels (None if empty)
        - invalid_labels_array: array of invalid labels (None if empty)
        - label_status: overall status
          (validated/auto_suggested/unvalidated)
        - label_confidence: confidence from latest pending label
        - label_proposed_by: proposer of latest pending label
        - label_validated_at: timestamp of most recent validation
    """
    # Get all labels for this specific word directly
    word_labels, _ = dynamo_client.list_receipt_word_labels_for_word(
        image_id=image_id,
        receipt_id=receipt_id,
        line_id=line_id,
        word_id=word_id,
        limit=None,  # Get all labels for this word
    )

    # Calculate label_status - overall state for this word
    if any(
        lbl.validation_status == ValidationStatus.VALID.value
        for lbl in word_labels
    ):
        label_status = "validated"
    elif any(
        lbl.validation_status == ValidationStatus.PENDING.value
        for lbl in word_labels
    ):
        label_status = "auto_suggested"
    else:
        label_status = "unvalidated"

    # Get auto suggestions for confidence and proposed_by
    auto_suggestions = [
        lbl
        for lbl in word_labels
        if lbl.validation_status == ValidationStatus.PENDING.value
    ]

    # label_confidence & label_proposed_by from latest auto suggestion
    if auto_suggestions:
        latest = sorted(auto_suggestions, key=lambda l: l.timestamp_added)[-1]
        label_confidence = getattr(latest, "confidence", None)
        label_proposed_by = latest.label_proposed_by
    else:
        label_confidence = None
        label_proposed_by = None

    # valid_labels - all labels with status VALID
    valid_labels_list = [
        lbl.label
        for lbl in word_labels
        if lbl.validation_status == ValidationStatus.VALID.value
    ]

    # invalid_labels - all labels with status INVALID
    invalid_labels = [
        lbl.label
        for lbl in word_labels
        if lbl.validation_status == ValidationStatus.INVALID.value
    ]

    # label_validated_at - timestamp of most recent VALID label
    valid_labels = [
        lbl
        for lbl in word_labels
        if lbl.validation_status == ValidationStatus.VALID.value
    ]
    label_validated_at = (
        sorted(valid_labels, key=lambda l: l.timestamp_added)[
            -1
        ].timestamp_added
        if valid_labels
        else None
    )

    # Build metadata dictionary matching step function structure
    label_metadata: Dict[str, Any] = {
        "label_status": label_status,
    }

    # Add optional fields only if they have values
    if label_confidence is not None:
        label_metadata["label_confidence"] = label_confidence
    if label_proposed_by is not None:
        label_metadata["label_proposed_by"] = label_proposed_by

    _set_label_array_fields(
        label_metadata,
        valid_labels=set(valid_labels_list),
        invalid_labels=set(invalid_labels),
    )

    if label_validated_at is not None:
        label_metadata["label_validated_at"] = label_validated_at

    return label_metadata


def delete_receipt_embeddings(
    collection: Any,
    image_id: str,
    receipt_id: int,
    logger: Any,
    metrics: Any = None,
    OBSERVABILITY_AVAILABLE: bool = False,
    get_dynamo_client_func: Any = None,
) -> int:
    """Delete all embeddings for a specific receipt from ChromaDB.

    Uses DynamoDB to construct exact ChromaDB IDs instead of scanning entire
    collection. This is much more efficient for large collections.

    Args:
        collection: ChromaDB collection object
        image_id: Image ID
        receipt_id: Receipt ID
        logger: Logger instance
        metrics: Optional metrics collector
        OBSERVABILITY_AVAILABLE: Whether observability features are available
        get_dynamo_client_func: Optional function to get DynamoDB client

    Returns:
        Number of embeddings deleted
    """
    start_time = time.time()

    if OBSERVABILITY_AVAILABLE:
        logger.info(
            "Starting receipt embedding deletion",
            image_id=image_id,
            receipt_id=receipt_id,
        )
    else:
        logger.info(
            "Starting receipt embedding deletion",
            image_id=image_id,
            receipt_id=receipt_id,
        )

    # Get DynamoDB client to query for words/lines
    if get_dynamo_client_func:
        dynamo_client = get_dynamo_client_func()
    else:
        dynamo_client = DynamoClient(os.environ["DYNAMODB_TABLE_NAME"])

    # Determine collection type from collection name
    collection_name = collection.name

    logger.info("Processing collection", collection_name=collection_name)

    # Construct ChromaDB IDs by querying DynamoDB for exact entities
    chromadb_ids = []

    if "words" in collection_name:
        # Get all words for this receipt from DynamoDB
        try:
            words = dynamo_client.list_receipt_words_from_receipt(
                image_id, receipt_id
            )

            logger.info("Found words in DynamoDB", count=len(words))
            if OBSERVABILITY_AVAILABLE and metrics:
                metrics.gauge("CompactionDynamoDBWords", len(words))

            # Construct exact ChromaDB IDs for words
            chromadb_ids = [
                (
                    f"IMAGE#{word.image_id}"
                    f"#RECEIPT#{word.receipt_id:05d}"
                    f"#LINE#{word.line_id:05d}"
                    f"#WORD#{word.word_id:05d}"
                )
                for word in words
            ]
        except Exception as e:
            logger.error("Failed to query words from DynamoDB", error=str(e))

            if OBSERVABILITY_AVAILABLE and metrics:
                metrics.count(
                    "CompactionDynamoDBQueryError",
                    1,
                    {"entity_type": "words", "error_type": type(e).__name__},
                )
            return 0

    elif "lines" in collection_name:
        # Get all lines for this receipt from DynamoDB
        try:
            lines = dynamo_client.list_receipt_lines_from_receipt(
                image_id, receipt_id
            )

            logger.info("Found lines in DynamoDB", count=len(lines))
            if OBSERVABILITY_AVAILABLE and metrics:
                metrics.gauge("CompactionDynamoDBLines", len(lines))

            # Construct exact ChromaDB IDs for lines
            chromadb_ids = [
                (
                    f"IMAGE#{line.image_id}"
                    f"#RECEIPT#{line.receipt_id:05d}"
                    f"#LINE#{line.line_id:05d}"
                )
                for line in lines
            ]
        except Exception as e:
            logger.error("Failed to query lines from DynamoDB", error=str(e))

            if OBSERVABILITY_AVAILABLE and metrics:
                metrics.count(
                    "CompactionDynamoDBQueryError",
                    1,
                    {"entity_type": "lines", "error_type": type(e).__name__},
                )
            return 0
    else:
        logger.warning(
            "Unknown collection type", collection_name=collection_name
        )

        if OBSERVABILITY_AVAILABLE and metrics:
            metrics.count(
                "CompactionUnknownCollectionType",
                1,
                {"collection_name": collection_name},
            )
        return 0

    logger.info("Constructed ChromaDB IDs", count=len(chromadb_ids))

    if not chromadb_ids:
        logger.warning(
            "No entities found in DynamoDB",
            image_id=image_id,
            receipt_id=receipt_id,
        )
        return 0

    # Delete embeddings using direct ID lookup
    try:
        # First verify the IDs exist in ChromaDB
        results = collection.get(ids=chromadb_ids, include=["metadatas"])
        found_ids = results.get("ids", [])
        found_count = len(found_ids)

        if OBSERVABILITY_AVAILABLE:
            logger.info(
                "Found embeddings in ChromaDB",
                found_count=found_count,
                expected_count=len(chromadb_ids),
            )
            if metrics:
                metrics.gauge("CompactionChromaDBRecordsFound", found_count)
        else:
            logger.info(
                "Found embeddings in ChromaDB",
                found_count=found_count,
                expected_count=len(chromadb_ids),
            )

        # Delete the embeddings
        if found_ids:
            collection.delete(ids=found_ids)
            elapsed_time = time.time() - start_time

            if OBSERVABILITY_AVAILABLE:
                logger.info(
                    "Successfully deleted embeddings",
                    deleted_count=found_count,
                    elapsed_seconds=elapsed_time,
                )
                if metrics:
                    metrics.timer(
                        "CompactionEmbeddingDeletionTime", elapsed_time
                    )
                    metrics.count("CompactionEmbeddingsDeleted", found_count)
            else:
                logger.info(
                    "Successfully deleted embeddings",
                    deleted_count=found_count,
                    elapsed_seconds=elapsed_time,
                )
        else:
            logger.warning(
                "No matching ChromaDB records found for deletion",
                dynamodb_ids=len(chromadb_ids),
            )

            # DynamoDB has entities but embeddings may not exist or were
            # deleted
            if chromadb_ids:
                logger.info(
                    (
                        "DynamoDB entities exist but no ChromaDB embeddings "
                        "found - embeddings may not exist or were already "
                        "deleted"
                    )
                )

        elapsed_time = time.time() - start_time

        if OBSERVABILITY_AVAILABLE:
            logger.info(
                "Embedding deletion completed",
                elapsed_seconds=elapsed_time,
                approach="DynamoDB-driven",
            )
        else:
            logger.info(
                "Embedding deletion completed (DynamoDB-driven approach)",
                elapsed_seconds=elapsed_time,
            )
        return found_count

    except Exception as e:
        logger.error("Failed to delete ChromaDB embeddings", error=str(e))

        if OBSERVABILITY_AVAILABLE and metrics:
            metrics.count(
                "CompactionChromaDBDeletionError",
                1,
                {"error_type": type(e).__name__},
            )
        return 0
