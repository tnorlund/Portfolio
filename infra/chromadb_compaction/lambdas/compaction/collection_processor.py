"""Collection-level message processing with lock management."""

import os
import time
from typing import Any, Dict, List, Optional

from receipt_dynamo.constants import ChromaDBCollection
from receipt_label.utils.lock_manager import LockManager

from .models import LambdaResponse
from .message_processor import categorize_stream_messages
from .metadata_handler import process_metadata_updates
from .label_handler import process_label_updates
from .compaction_handler import process_compaction_run_messages


def process_stream_messages(
    stream_messages: List[Any],  # StreamMessage type
    logger,
    metrics=None,
    OBSERVABILITY_AVAILABLE=False,
    get_dynamo_client_func=None,
    heartbeat_interval=30,
    lock_duration_minutes=3,
    max_heartbeat_failures=3
) -> Dict[str, Any]:
    """Process DynamoDB stream messages for metadata updates.

    Groups messages by collection and processes each collection in batches
    for improved efficiency with bulk ChromaDB operations.
    """
    logger.info(
        "Processing stream messages with batch optimization",
        message_count=len(stream_messages),
    )

    if OBSERVABILITY_AVAILABLE and metrics:
        metrics.gauge("CompactionBatchSize", len(stream_messages))

    # Group messages by collection
    messages_by_collection = {}
    for msg in stream_messages:
        collection = msg.collection
        if collection not in messages_by_collection:
            messages_by_collection[collection] = []
        messages_by_collection[collection].append(msg)

    if OBSERVABILITY_AVAILABLE:
        logger.info(
            "Messages grouped by collection",
            collections={
                col.value: len(msgs)
                for col, msgs in messages_by_collection.items()
            },
        )
    else:
        logger.info(
            "Messages grouped by collection: %s",
            {
                col.value: len(msgs)
                for col, msgs in messages_by_collection.items()
            },
        )

    # Process each collection separately
    all_metadata_results = []
    all_label_results = []
    total_metadata_updates = 0
    total_label_updates = 0

    for collection, messages in messages_by_collection.items():
        if OBSERVABILITY_AVAILABLE:
            logger.info(
                "Processing collection messages",
                collection=collection.value,
                message_count=len(messages),
            )
        else:
            logger.info(
                "Processing %d messages for %s collection",
                len(messages),
                collection.value,
            )

        result = process_collection_messages(
            collection=collection,
            messages=messages,
            logger=logger,
            metrics=metrics,
            OBSERVABILITY_AVAILABLE=OBSERVABILITY_AVAILABLE,
            get_dynamo_client_func=get_dynamo_client_func,
            heartbeat_interval=heartbeat_interval,
            lock_duration_minutes=lock_duration_minutes,
            max_heartbeat_failures=max_heartbeat_failures
        )

        # If the collection couldn't be processed due to lock contention, and
        # we have SQS message IDs captured, return partial batch failure so the
        # event source will retry only those messages.
        if result.get("error", "").startswith("Could not acquire lock"):
            failed_ids = [m.message_id for m in messages if m.message_id]
            if failed_ids:
                return {
                    "batchItemFailures": [
                        {"itemIdentifier": mid} for mid in failed_ids
                    ]
                }
        # Aggregate results
        all_metadata_results.extend(result.get("metadata_results", []))
        all_label_results.extend(result.get("label_results", []))
        total_metadata_updates += result.get("metadata_updates", 0)
        total_label_updates += result.get("label_updates", 0)

    # Return aggregated results
    response = LambdaResponse(
        status_code=200,
        message=(
            f"Successfully processed {len(stream_messages)} stream messages"
        ),
        stream_messages=len(stream_messages),
        metadata_updates=total_metadata_updates,
        label_updates=total_label_updates,
        metadata_results=all_metadata_results,
        label_results=all_label_results,
    )

    if OBSERVABILITY_AVAILABLE and metrics:
        metrics.count("CompactionMetadataUpdates", total_metadata_updates)
        metrics.count("CompactionLabelUpdates", total_label_updates)

    return response.to_dict()


def process_collection_messages(
    collection: ChromaDBCollection, 
    messages: List[Any],  # StreamMessage type
    logger,
    metrics=None,
    OBSERVABILITY_AVAILABLE=False,
    get_dynamo_client_func=None,
    heartbeat_interval=30,
    lock_duration_minutes=3,
    max_heartbeat_failures=3
) -> Dict[str, Any]:
    """Process messages for a specific collection with collection-specific lock.
    
    Args:
        collection: ChromaDBCollection to process
        messages: List of StreamMessage objects for this collection
        logger: Logger instance
        metrics: Metrics collector (optional)
        OBSERVABILITY_AVAILABLE: Whether observability features are available
        get_dynamo_client_func: Function to get DynamoDB client
        heartbeat_interval: Lock heartbeat interval in seconds
        lock_duration_minutes: Lock duration in minutes
        max_heartbeat_failures: Maximum heartbeat failures before lock release

    Returns:
        Dictionary with processing results
    """
    # Parse and group messages by entity type for efficient processing
    metadata_updates, label_updates, compaction_runs = categorize_stream_messages(messages)

    # Log unknown entity types
    for message in messages:
        if message.entity_type not in ["RECEIPT_METADATA", "RECEIPT_WORD_LABEL", "COMPACTION_RUN"]:
            logger.warning(
                "Unknown entity type", entity_type=message.entity_type
            )

            if OBSERVABILITY_AVAILABLE and metrics:
                metrics.count(
                    "CompactionUnknownEntityType",
                    1,
                    {"entity_type": message.entity_type},
                )

    # Acquire collection-specific lock for metadata updates
    if get_dynamo_client_func:
        dynamo_client = get_dynamo_client_func()
    else:
        from receipt_dynamo.data.dynamo_client import DynamoClient
        dynamo_client = DynamoClient(os.environ["DYNAMODB_TABLE_NAME"])
    
    lock_manager = LockManager(
        dynamo_client,
        collection=collection,
        heartbeat_interval=heartbeat_interval,
        lock_duration_minutes=lock_duration_minutes,
        max_heartbeat_failures=max_heartbeat_failures,
    )

    try:
        # Create collection-specific lock ID
        lock_id = f"chroma-{collection.value}-update"
        lock_acquired = lock_manager.acquire(lock_id)

        if not lock_acquired:
            if OBSERVABILITY_AVAILABLE:
                logger.warning(
                    "Could not acquire collection lock",
                    collection=collection.value,
                )
                if metrics:
                    metrics.count(
                        "CompactionLockAcquisitionFailed",
                        1,
                        {"collection": collection.value},
                    )
            else:
                logger.warning(
                    "Could not acquire lock for %s collection updates",
                    collection.value,
                )
            return {
                "error": (
                    f"Could not acquire lock for {collection.value} collection"
                ),
                "metadata_updates": 0,
                "label_updates": 0,
                "metadata_results": [],
                "label_results": [],
            }

        if OBSERVABILITY_AVAILABLE:
            logger.info(
                "Acquired collection lock",
                collection=collection.value,
                lock_id=lock_id,
            )
            if metrics:
                metrics.count(
                    "CompactionLockAcquired", 1, {"collection": collection.value}
                )
        else:
            logger.info(
                "Acquired lock for %s collection: %s",
                collection.value,
                lock_id,
            )

        # Start heartbeat
        lock_manager.start_heartbeat()

        # Process metadata updates with lock validation
        metadata_results = []
        if metadata_updates:
            # Validate lock ownership before critical operations
            if not lock_manager.validate_ownership():
                if OBSERVABILITY_AVAILABLE:
                    logger.error(
                        "Lock ownership validation failed before metadata updates",
                        collection=collection.value,
                        lock_id=lock_id,
                    )
                    if metrics:
                        metrics.count(
                            "CompactionLockValidationFailed",
                            1,
                            {
                                "collection": collection.value,
                                "operation": "metadata_updates",
                            },
                        )
                else:
                    logger.error(
                        "Lock validation failed before metadata updates for %s",
                        collection.value,
                    )

                return {
                    "error": f"Lock validation failed for {collection.value} collection",
                    "metadata_updates": 0,
                    "label_updates": 0,
                    "metadata_results": [],
                    "label_results": [],
                }

            metadata_results = process_metadata_updates(
                metadata_updates=metadata_updates,
                collection=collection,
                logger=logger,
                metrics=metrics,
                OBSERVABILITY_AVAILABLE=OBSERVABILITY_AVAILABLE,
                lock_manager=lock_manager,
                get_dynamo_client_func=get_dynamo_client_func
            )

        # Process COMPACTION_RUN delta merges with lock validation
        total_merged_vectors = 0
        if compaction_runs:
            if not lock_manager.validate_ownership():
                if OBSERVABILITY_AVAILABLE:
                    logger.error(
                        "Lock ownership validation failed before delta merge",
                        collection=collection.value,
                        operation="compaction_run",
                    )
                    if metrics:
                        metrics.count(
                            "CompactionLockValidationFailed",
                            1,
                            {
                                "collection": collection.value,
                                "operation": "delta_merge",
                            },
                        )
                else:
                    logger.error(
                        "Lock validation failed before delta merge for %s",
                        collection.value,
                    )
                return {
                    "error": f"Lock validation failed for {collection.value} collection (delta merge)",
                    "metadata_updates": (
                        len(metadata_results) if metadata_results else 0
                    ),
                    "label_updates": 0,
                    "metadata_results": metadata_results,
                    "label_results": [],
                }

            total_merged_vectors = process_compaction_run_messages(
                compaction_runs=compaction_runs,
                collection=collection,
                logger=logger,
                metrics=metrics,
                OBSERVABILITY_AVAILABLE=OBSERVABILITY_AVAILABLE,
                lock_manager=lock_manager,
                get_dynamo_client_func=get_dynamo_client_func
            )

        # Process label updates with lock validation
        label_results = []
        if label_updates:
            # Validate lock ownership before critical operations
            logger.info(
                "Validating lock ownership before label updates",
                collection=collection.value,
                lock_id=lock_id,
                label_count=len(label_updates),
            )

            lock_validation_start = time.time()
            is_owner = lock_manager.validate_ownership()
            lock_validation_time = time.time() - lock_validation_start

            logger.info(
                "Lock validation completed",
                collection=collection.value,
                lock_id=lock_id,
                is_owner=is_owner,
                validation_time_ms=lock_validation_time * 1000,
            )

            if not is_owner:
                if OBSERVABILITY_AVAILABLE:
                    logger.error(
                        "Lock ownership validation failed before label updates",
                        collection=collection.value,
                        lock_id=lock_id,
                        validation_time_ms=lock_validation_time * 1000,
                    )
                    if metrics:
                        metrics.count(
                            "CompactionLockValidationFailed",
                            1,
                            {
                                "collection": collection.value,
                                "operation": "label_updates",
                            },
                        )
                else:
                    logger.error(
                        "Lock validation failed before label updates for %s",
                        collection.value,
                    )

                return {
                    "error": f"Lock validation failed for {collection.value} collection",
                    "metadata_updates": (
                        len(metadata_results) if metadata_results else 0
                    ),
                    "label_updates": 0,
                    "metadata_results": metadata_results,
                    "label_results": [],
                }

            label_results = process_label_updates(
                label_updates=label_updates,
                collection=collection,
                logger=logger,
                metrics=metrics,
                OBSERVABILITY_AVAILABLE=OBSERVABILITY_AVAILABLE,
                lock_manager=lock_manager,
                get_dynamo_client_func=get_dynamo_client_func
            )

        total_metadata_updates = sum(
            result.updated_count
            for result in metadata_results
            if result.error is None
        )
        total_label_updates = sum(
            result.updated_count
            for result in label_results
            if result.error is None
        )

        if OBSERVABILITY_AVAILABLE:
            logger.info(
                "Completed collection processing",
                collection=collection.value,
                metadata_updates=total_metadata_updates,
                label_updates=total_label_updates,
            )
        else:
            logger.info(
                "Completed %s collection: %d metadata, %d label updates",
                collection.value,
                total_metadata_updates,
                total_label_updates,
            )

        return {
            "metadata_updates": total_metadata_updates,
            "label_updates": total_label_updates,
            "metadata_results": [
                result.to_dict() for result in metadata_results
            ],
            "label_results": [result.to_dict() for result in label_results],
            "merged_vectors": total_merged_vectors,
        }

    except Exception as e:  # pylint: disable=broad-exception-caught
        if OBSERVABILITY_AVAILABLE:
            logger.error(
                "Error processing collection messages",
                collection=collection.value,
                error=str(e),
            )
            if metrics:
                metrics.count(
                    "CompactionCollectionProcessingError",
                    1,
                    {
                        "collection": collection.value,
                        "error_type": type(e).__name__,
                    },
                )
        else:
            logger.error(
                "Error processing %s collection messages: %s",
                collection.value,
                e,
            )
        return {
            "error": str(e),
            "metadata_updates": 0,
            "label_updates": 0,
            "metadata_results": [],
            "label_results": [],
        }
    finally:
        # Stop heartbeat and release lock
        lock_manager.stop_heartbeat()
        lock_manager.release()

        if OBSERVABILITY_AVAILABLE:
            logger.info(
                "Released collection lock",
                collection=collection.value,
                lock_id=lock_id,
            )
        else:
            logger.info(
                "Released lock for %s collection: %s",
                collection.value,
                lock_id,
            )
