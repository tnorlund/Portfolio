"""SQS message processing and categorization for the enhanced compaction handler."""

import json
import os
from typing import Any, Dict, List, Tuple

from receipt_dynamo.constants import ChromaDBCollection

from .models import LambdaResponse, StreamMessage


def process_sqs_messages(
    records: List[Dict[str, Any]],
    logger: Any,
    metrics: Any = None,
    OBSERVABILITY_AVAILABLE: bool = False,
    process_stream_messages_func: Any = None,
    process_delta_messages_func: Any = None,
) -> Dict[str, Any]:
    """Process SQS messages from the compaction queue.

    Handles both:
    1. Stream messages (from DynamoDB stream processor)
    2. Traditional delta notifications (existing functionality)

    Returns partial batch failure response for unprocessed delta messages.
    """
    logger.info(
        "Processing SQS messages",
        message_count=len(records),
        batch_size=len(records),
    )

    stream_messages = []
    delta_message_records = []  # Store full records for delta messages
    processed_count = 0
    failed_message_ids = []  # Track failed message IDs for partial batch failure

    # Categorize messages by type
    for record in records:
        try:
            # Parse message body
            message_body = json.loads(record["body"])

            # Check message attributes for source
            attributes = record.get("messageAttributes", {})
            source = attributes.get("source", {}).get("stringValue", "unknown")

            if source == "dynamodb_stream":
                # Get collection from message attributes
                collection_value = attributes.get("collection", {}).get("stringValue")
                if not collection_value:
                    logger.warning("Stream message missing collection attribute")
                    if OBSERVABILITY_AVAILABLE and metrics:
                        metrics.count("CompactionMessageMissingCollection", 1)
                    continue

                try:
                    collection = ChromaDBCollection(collection_value)
                except ValueError:
                    logger.warning(
                        "Invalid collection value",
                        collection_value=collection_value,
                    )
                    if OBSERVABILITY_AVAILABLE and metrics:
                        metrics.count("CompactionInvalidCollection", 1)
                    continue

                # Parse stream message with collection info
                stream_msg = StreamMessage(
                    entity_type=message_body.get("entity_type", ""),
                    entity_data=message_body.get("entity_data", {}),
                    changes=message_body.get("changes", {}),
                    event_name=message_body.get("event_name", ""),
                    collection=collection,
                    record_snapshot=message_body.get("record_snapshot"),
                    source=source,
                    message_id=record.get("messageId"),
                    receipt_handle=record.get("receiptHandle"),
                    queue_url=os.environ.get(
                        "LINES_QUEUE_URL"
                        if collection.value == "lines"
                        else "WORDS_QUEUE_URL"
                    ),
                )
                stream_messages.append(stream_msg)

                if OBSERVABILITY_AVAILABLE and metrics:
                    metrics.count(
                        "CompactionStreamMessage",
                        1,
                        {
                            "entity_type": stream_msg.entity_type,
                            "collection": collection.value,
                        },
                    )
            else:
                # Traditional delta message or unknown - treat as delta
                # Store the full record to get messageId for partial batch failure
                delta_message_records.append({"record": record, "body": message_body})

                if OBSERVABILITY_AVAILABLE and metrics:
                    metrics.count("CompactionDeltaMessage", 1)

            processed_count += 1

        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Error parsing SQS message", error=str(e))

            if OBSERVABILITY_AVAILABLE and metrics:
                metrics.count(
                    "CompactionMessageParseError",
                    1,
                    {"error_type": type(e).__name__},
                )
            continue

    # Process stream messages if any
    if stream_messages and process_stream_messages_func:
        result = process_stream_messages_func(stream_messages, metrics=metrics)
        # If a collection returned partial-batch failures (due to lock),
        # propagate immediately so the Lambda runtime retries only those.
        if isinstance(result, dict) and "batchItemFailures" in result:
            return result
        logger.info("Processed stream messages", count=len(stream_messages))

    # Process delta messages if any - collect failed message IDs
    if delta_message_records and process_delta_messages_func:
        delta_bodies = [msg["body"] for msg in delta_message_records]
        process_delta_messages_func(delta_bodies, metrics=metrics)

        # Since delta processing is not implemented, mark all delta messages as failed
        # to prevent data loss by forcing SQS to retry them
        for msg_record in delta_message_records:
            message_id = msg_record["record"].get("receiptHandle")
            if message_id:
                failed_message_ids.append(message_id)

        if OBSERVABILITY_AVAILABLE and metrics:
            logger.warning(
                "Delta messages not processed - added to failed list for retry",
                count=len(delta_message_records),
            )
            metrics.count(
                "CompactionDeltaMessagesFailedForRetry",
                len(delta_message_records),
            )
        else:
            logger.warning(
                "Processed delta messages (marked as failed for retry)",
                count=len(delta_message_records),
            )

    # Return partial batch failure response if there are failed messages
    if failed_message_ids:
        response = {
            "batchItemFailures": [
                {"itemIdentifier": msg_id} for msg_id in failed_message_ids
            ]
        }

        if OBSERVABILITY_AVAILABLE and metrics:
            logger.info(
                "Returning partial batch failure response",
                failed_count=len(failed_message_ids),
                stream_processed=len(stream_messages),
            )
            metrics.count("CompactionPartialBatchFailure", 1)
            metrics.gauge("CompactionFailedMessages", len(failed_message_ids))
        else:
            logger.info(
                f"Partial batch failure: {len(failed_message_ids)} failed, "
                f"{len(stream_messages)} stream messages processed"
            )

        return response

    # All messages processed successfully
    success_response = LambdaResponse(
        status_code=200,
        processed_messages=processed_count,
        stream_messages=len(stream_messages),
        delta_messages=len(delta_message_records),
        message="SQS messages processed successfully",
    )

    if OBSERVABILITY_AVAILABLE and metrics:
        metrics.gauge("CompactionProcessedMessages", processed_count)
        metrics.gauge("CompactionStreamMessages", len(stream_messages))
        metrics.gauge("CompactionDeltaMessages", len(delta_message_records))
        metrics.gauge("CompactionBatchProcessedSuccessfully", len(records))
        metrics.count("CompactionBatchProcessingSuccess", 1)

    return success_response.to_dict()


def categorize_stream_messages(
    stream_messages: List[StreamMessage],
) -> Tuple[List[StreamMessage], List[StreamMessage], List[StreamMessage]]:
    """Categorize stream messages by entity type.

    Returns:
        Tuple of (metadata_updates, label_updates, compaction_runs)
    """
    metadata_updates = []
    label_updates = []
    compaction_runs = []

    for message in stream_messages:
        if message.entity_type == "RECEIPT_METADATA":
            metadata_updates.append(message)
        elif message.entity_type == "RECEIPT_WORD_LABEL":
            label_updates.append(message)
        elif message.entity_type == "COMPACTION_RUN":
            compaction_runs.append(message)

    return metadata_updates, label_updates, compaction_runs


def group_messages_by_collection(
    stream_messages: List[StreamMessage],
) -> Dict[ChromaDBCollection, List[StreamMessage]]:
    """Group stream messages by collection for batch processing."""
    messages_by_collection: Dict[ChromaDBCollection, List[StreamMessage]] = {}
    for msg in stream_messages:
        collection = msg.collection
        if collection not in messages_by_collection:
            messages_by_collection[collection] = []
        messages_by_collection[collection].append(msg)
    return messages_by_collection
