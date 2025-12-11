"""ChromaDB operations for metadata and label updates."""

import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.constants import ValidationStatus


def update_receipt_metadata(
    collection: Any,
    image_id: str,
    receipt_id: int,
    changes: Dict[str, Any],
    logger: Any,
    metrics: Any = None,
    OBSERVABILITY_AVAILABLE: bool = False,
    get_dynamo_client_func: Any = None
) -> int:
    """Update metadata for all embeddings of a specific receipt.

    Uses DynamoDB to construct exact ChromaDB IDs instead of scanning entire collection.
    This is much more efficient for large collections.
    """
    start_time = time.time()

    if OBSERVABILITY_AVAILABLE:
        logger.info(
            "Starting metadata update",
            image_id=image_id,
            receipt_id=receipt_id,
            changes=changes,
        )
    else:
        logger.info(
            "Starting metadata update",
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
                f"IMAGE#{word.image_id}#RECEIPT#{word.receipt_id:05d}#LINE#{word.line_id:05d}#WORD#{word.word_id:05d}"
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
                f"IMAGE#{line.image_id}#RECEIPT#{line.receipt_id:05d}#LINE#{line.line_id:05d}"
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

    # Use direct ID lookup - much faster than scanning entire collection
    try:
        results = collection.get(ids=chromadb_ids, include=["metadatas"])
        found_count = len(results.get("ids", []))

        if OBSERVABILITY_AVAILABLE:
            logger.info(
                "Retrieved records from ChromaDB",
                found_count=found_count,
                expected_count=len(chromadb_ids),
            )
            if metrics:
                metrics.gauge("CompactionChromaDBRecordsFound", found_count)
        else:
            logger.info(
                "Retrieved records from ChromaDB",
                found_count=found_count,
                expected_count=len(chromadb_ids),
            )
    except Exception as e:
        logger.error("Failed to query ChromaDB with exact IDs", error=str(e))

        if OBSERVABILITY_AVAILABLE and metrics:
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
        # Get existing metadata and apply changes
        existing_metadata = results["metadatas"][i] or {}
        updated_metadata = existing_metadata.copy()

        # Apply field changes
        for field, change in changes.items():
            _ = change.get("old")  # old_value not used
            new_value = change["new"]
            if new_value is not None:
                updated_metadata[field] = new_value
            elif field in updated_metadata:
                # Remove field if new value is None
                del updated_metadata[field]

        # Add update timestamp
        updated_metadata["last_metadata_update"] = datetime.now(
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
                    "Successfully updated metadata",
                    updated_count=len(matching_ids),
                    elapsed_seconds=elapsed_time,
                )
                if metrics:
                    metrics.timer("CompactionMetadataUpdateTime", elapsed_time)
                    metrics.count(
                        "CompactionMetadataUpdatedRecords", len(matching_ids)
                    )
            else:
                logger.info(
                    "Successfully updated metadata for embeddings",
                    embedding_count=len(matching_ids),
                    elapsed_seconds=elapsed_time,
                )
        except Exception as e:
            logger.error("Failed to update ChromaDB metadata", error=str(e))

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

        # If no records found in ChromaDB but DynamoDB has entities, this might indicate
        # that embeddings haven't been created yet
        if chromadb_ids:
            logger.info(
                "DynamoDB entities exist but no ChromaDB embeddings found - embeddings may not be created yet"
            )

    elapsed_time = time.time() - start_time

    if OBSERVABILITY_AVAILABLE:
        logger.info(
            "Metadata update completed",
            elapsed_seconds=elapsed_time,
            approach="DynamoDB-driven",
        )
    else:
        logger.info(
            "Metadata update completed (DynamoDB-driven approach)",
            elapsed_seconds=elapsed_time,
        )
    return len(matching_ids)


def remove_receipt_metadata(
    collection: Any,
    image_id: str,
    receipt_id: int,
    logger: Any,
    metrics: Any = None,
    OBSERVABILITY_AVAILABLE: bool = False,
    get_dynamo_client_func: Any = None
) -> int:
    """Remove merchant metadata fields from all embeddings of a specific receipt.

    Uses DynamoDB to construct exact ChromaDB IDs instead of scanning entire collection.
    """
    start_time = time.time()

    if OBSERVABILITY_AVAILABLE:
        logger.info(
            "Starting metadata removal",
            image_id=image_id,
            receipt_id=receipt_id,
        )
    else:
        logger.info(
            "Starting metadata removal",
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
        # Get all words for this receipt from DynamoDB
        try:
            words = dynamo_client.list_receipt_words_from_receipt(
                image_id, receipt_id
            )
            chromadb_ids = [
                f"IMAGE#{word.image_id}#RECEIPT#{word.receipt_id:05d}#LINE#{word.line_id:05d}#WORD#{word.word_id:05d}"
                for word in words
            ]
        except Exception as e:
            logger.error("Failed to query words from DynamoDB", error=str(e))
            return 0

    elif "lines" in collection_name:
        # Get all lines for this receipt from DynamoDB
        try:
            lines = dynamo_client.list_receipt_lines_from_receipt(
                image_id, receipt_id
            )
            chromadb_ids = [
                f"IMAGE#{line.image_id}#RECEIPT#{line.receipt_id:05d}#LINE#{line.line_id:05d}"
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

    # Fields to remove when metadata is deleted
    fields_to_remove = [
        "canonical_merchant_name",
        "merchant_name",
        "merchant_category",
        "address",
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
            "Failed to query ChromaDB for metadata removal", error=str(e)
        )
        return 0

    matching_ids = results.get("ids", [])
    matching_metadatas = []

    for i, _ in enumerate(matching_ids):
        # Remove merchant fields from metadata
        existing_metadata = results["metadatas"][i] or {}
        updated_metadata = existing_metadata.copy()

        for field in fields_to_remove:
            if field in updated_metadata:
                del updated_metadata[field]

        # Add removal timestamp
        updated_metadata["metadata_removed_at"] = datetime.now(
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
                    "Removed metadata",
                    removed_count=len(matching_ids),
                    elapsed_seconds=elapsed_time,
                )
                if metrics:
                    metrics.timer("CompactionMetadataRemovalTime", elapsed_time)
                    metrics.count(
                        "CompactionMetadataRemovedRecords", len(matching_ids)
                    )
            else:
                logger.info(
                    "Removed metadata from embeddings",
                    embedding_count=len(matching_ids),
                    elapsed_seconds=elapsed_time,
                )
        except Exception as e:
            logger.error("Failed to remove ChromaDB metadata", error=str(e))
            return 0
    else:
        logger.warning(
            "No matching ChromaDB records found for removal",
            dynamodb_ids=len(chromadb_ids),
        )

    elapsed_time = time.time() - start_time

    if OBSERVABILITY_AVAILABLE:
        logger.info(
            "Metadata removal completed",
            elapsed_seconds=elapsed_time,
            approach="DynamoDB-driven",
        )
    else:
        logger.info(
            "Metadata removal completed (DynamoDB-driven approach)",
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
    get_dynamo_client_func: Any = None
) -> int:
    """Update label metadata for a specific word embedding using message snapshot when available.

    Falls back to reconstructing from DynamoDB only if snapshot is missing.
    """
    try:
        # Parse ChromaDB ID to extract word identifiers
        # Format: IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#WORD#{word_id:05d}
        parts = chromadb_id.split("#")
        if len(parts) < 8 or "WORD" not in parts:
            if logger:
                logger.error(
                    "Invalid ChromaDB ID format for word", chromadb_id=chromadb_id
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
            if logger:
                logger.warning("Word embedding not found", chromadb_id=chromadb_id)
            if OBSERVABILITY_AVAILABLE and metrics:
                metrics.count("CompactionWordEmbeddingNotFound", 1)
            return 0

        # Prefer snapshot data if available to avoid DynamoDB race conditions
        if record_snapshot:
            # Build the minimal fields needed for label metadata derivation from snapshot
            reconstructed_metadata = {
                "label_status": None,  # will be derived below if needed
            }
        else:
            # Reconstruct complete label metadata using the same logic as step function
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
            # Apply targeted changes directly based on the incoming change set for this word
            if changes:
                # validation_status
                if "validation_status" in changes:
                    new_status = changes["validation_status"].get("new")
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
                    val = changes["label_proposed_by"].get("new")
                    if val is not None:
                        updated_metadata["label_proposed_by"] = val

            # Always add/update the current label in validated/invalid sets based on status when provided
            status = (
                changes.get("validation_status", {}).get("new")
                if changes
                else None
            )
            current_label = None
            if entity_data and isinstance(entity_data, dict):
                current_label = entity_data.get("label")
            if current_label:
                # Initialize fields if missing
                validated = updated_metadata.get("valid_labels", "") or ""
                invalid = updated_metadata.get("invalid_labels", "") or ""

                def _as_set(csv: str) -> set:
                    return {x for x in csv.strip(",").split(",") if x}

                val_set = _as_set(validated)
                inv_set = _as_set(invalid)
                if status == "VALID":
                    inv_set.discard(current_label)
                    val_set.add(current_label)
                elif status == "INVALID":
                    val_set.discard(current_label)
                    inv_set.add(current_label)
                # Write back with delimiters for exact-match semantics
                updated_metadata["valid_labels"] = (
                    f",{','.join(sorted(val_set))}," if val_set else ""
                )
                updated_metadata["invalid_labels"] = (
                    f",{','.join(sorted(inv_set))}," if inv_set else ""
                )
        else:
            updated_metadata.update(reconstructed_metadata)

        # Add update timestamp
        updated_metadata["last_label_update"] = datetime.now(
            timezone.utc
        ).isoformat()

        # Update the ChromaDB record
        collection.update(ids=[chromadb_id], metadatas=[updated_metadata])

        if logger:
            logger.info(
                "Updated labels for word with reconstructed metadata",
                chromadb_id=chromadb_id,
                label_status=reconstructed_metadata.get("label_status"),
                validated_labels_count=(
                    len(
                        reconstructed_metadata.get("valid_labels", "").split(
                            ","
                        )
                    )
                    - 2
                    if reconstructed_metadata.get("valid_labels")
                    else 0
                ),
            )

        if OBSERVABILITY_AVAILABLE and metrics:
            metrics.count("CompactionWordLabelUpdated", 1)
            metrics.gauge(
                "CompactionValidatedLabelsCount",
                (
                    len(
                        reconstructed_metadata.get(
                            "valid_labels", ""
                        ).split(",")
                    )
                    - 2
                    if reconstructed_metadata.get("valid_labels")
                    else 0
                ),
            )

        return 1

    except Exception as e:  # pylint: disable=broad-exception-caught
        if logger:
            logger.error(
                "Error updating word labels", chromadb_id=chromadb_id, error=str(e)
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
    OBSERVABILITY_AVAILABLE: bool = False
) -> int:
    """Remove label metadata from a specific word embedding."""
    try:
        # Get the specific record
        result = collection.get(ids=[chromadb_id], include=["metadatas"])

        if not result["ids"]:
            if logger:
                logger.warning("Word embedding not found", chromadb_id=chromadb_id)

            if OBSERVABILITY_AVAILABLE and metrics:
                metrics.count("CompactionWordEmbeddingNotFoundForRemoval", 1)
            return 0

        # Remove all label fields from metadata
        existing_metadata = result["metadatas"][0] or {}
        updated_metadata = existing_metadata.copy()

        # Remove all label-related fields (matching step function structure)
        label_fields_to_remove = [
            "label_status",
            "label_confidence",
            "label_proposed_by",
            "valid_labels",
            "invalid_labels",
            "label_validated_at",
        ]

        # Remove standard label fields
        for field in label_fields_to_remove:
            if field in updated_metadata:
                del updated_metadata[field]

        # Remove any remaining fields that start with "label_" (legacy cleanup)
        legacy_label_fields = [
            key for key in updated_metadata.keys() if key.startswith("label_")
        ]
        for field in legacy_label_fields:
            del updated_metadata[field]

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
                "Error removing word labels", chromadb_id=chromadb_id, error=str(e)
            )

        if OBSERVABILITY_AVAILABLE and metrics:
            metrics.count(
                "CompactionWordLabelRemovalError",
                1,
                {"error_type": type(e).__name__},
            )
        return 0


def reconstruct_label_metadata(
    image_id: str, receipt_id: int, line_id: int, word_id: int, dynamo_client: Any
) -> Dict[str, Any]:
    """
    Reconstruct all label-related metadata fields exactly as the step function does.

    Args:
        image_id: Image ID
        receipt_id: Receipt ID
        line_id: Line ID
        word_id: Word ID
        dynamo_client: DynamoDB client instance

    Returns:
        Dictionary with reconstructed label metadata fields:
        - valid_labels: comma-delimited string of valid labels
        - invalid_labels: comma-delimited string of invalid labels
        - label_status: overall status (validated/auto_suggested/unvalidated)
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
    label_metadata = {
        "label_status": label_status,
    }

    # Add optional fields only if they have values
    if label_confidence is not None:
        label_metadata["label_confidence"] = label_confidence
    if label_proposed_by is not None:
        label_metadata["label_proposed_by"] = label_proposed_by

    # Store valid labels with delimiters for exact matching
    if valid_labels_list:
        label_metadata["valid_labels"] = f",{','.join(valid_labels_list)},"
    else:
        label_metadata["valid_labels"] = ""

    # Store invalid labels with delimiters for exact matching
    if invalid_labels:
        label_metadata["invalid_labels"] = f",{','.join(invalid_labels)},"
    else:
        label_metadata["invalid_labels"] = ""

    if label_validated_at is not None:
        label_metadata["label_validated_at"] = label_validated_at

    return label_metadata
