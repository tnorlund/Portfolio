"""SQS batching utilities for Lambda compaction handlers.

Provides Phase 2 optimization for fetching and deleting SQS messages
to amortize S3 transfer costs across larger batches.
"""
# pylint: disable=import-error
# import-error: aws_clients is bundled into Lambda package
import logging
import os
from dataclasses import dataclass
from typing import Optional

from botocore.exceptions import ClientError

from .aws_clients import get_sqs_client
from .lambda_types import (
    MessageAttributes,
    MetricsAccumulatorProtocol,
    OperationLoggerProtocol,
    RawSQSMessage,
    SQSRecord,
)


logger = logging.getLogger(__name__)


def _parse_int_env(name: str, default: int) -> int:
    """Parse integer environment variable with fallback."""
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


MAX_MESSAGES_PER_COMPACTION = _parse_int_env(
    "MAX_MESSAGES_PER_COMPACTION", 500
)
ADDITIONAL_FETCH_VISIBILITY_TIMEOUT = _parse_int_env(
    "ADDITIONAL_FETCH_VISIBILITY_TIMEOUT", 120
)  # Should match Lambda timeout


# Use get_sqs_client directly from aws_clients (already returns singleton)
_get_sqs_client = get_sqs_client


@dataclass
class Phase2FetchResult:
    """Result of Phase 2 additional message fetching."""

    all_records: list[SQSRecord]
    queue_url: Optional[str]
    manually_fetched_handles: list[str]


@dataclass
class CollectionProcessingResult:
    """Result of processing all collections."""

    failed_message_ids: list[str]
    processing_successful: bool


def _convert_sqs_message_to_record(msg: RawSQSMessage) -> SQSRecord:
    """Convert SQS message to Lambda event record format.

    Args:
        msg: Raw SQS message from receive_message response

    Returns:
        SQSRecord in Lambda event record format
    """
    message_attributes: MessageAttributes = {}
    for key, attr in msg.get("MessageAttributes", {}).items():
        if "StringValue" in attr:
            val = attr.get("StringValue", "")
            message_attributes[key] = {"stringValue": val}
        elif "NumberValue" in attr:
            val = attr.get("NumberValue", "")
            message_attributes[key] = {"numberValue": val}
        elif "BinaryValue" in attr:
            val = attr.get("BinaryValue", b"")
            message_attributes[key] = {"binaryValue": val}

    return {
        "messageId": msg.get("MessageId", ""),
        "receiptHandle": msg.get("ReceiptHandle", ""),
        "body": msg.get("Body", ""),
        "messageAttributes": message_attributes,
    }


def fetch_additional_messages(
    queue_url: str,
    current_count: int,
    max_messages: int = MAX_MESSAGES_PER_COMPACTION,
) -> tuple[list[SQSRecord], list[str]]:
    """Greedily fetch additional messages from SQS queue.

    Phase 2 optimization: Since FIFO queues limit batch size to 10,
    we fetch more messages within the handler to process them in
    a single compaction cycle, amortizing the S3 transfer cost.

    Args:
        queue_url: The SQS queue URL to fetch from
        current_count: Number of messages already received
        max_messages: Maximum total messages to process

    Returns:
        Tuple of (additional_records, receipt_handles_to_delete)
    """
    if current_count >= max_messages:
        return [], []

    sqs = _get_sqs_client()
    additional_records: list[SQSRecord] = []
    receipt_handles: list[str] = []
    remaining = max_messages - current_count

    while remaining > 0:
        # FIFO queues allow max 10 messages per receive
        batch_size = min(10, remaining)

        try:
            response = sqs.receive_message(
                QueueUrl=queue_url,
                MaxNumberOfMessages=batch_size,
                VisibilityTimeout=ADDITIONAL_FETCH_VISIBILITY_TIMEOUT,
                WaitTimeSeconds=0,  # Don't wait, just grab what's available
                MessageAttributeNames=["All"],
            )
        except ClientError as e:
            logger.warning(
                "Failed to fetch additional messages",
                extra={
                    "error": str(e),
                    "fetched_so_far": len(additional_records),
                },
            )
            break

        messages = response.get("Messages", [])
        if not messages:
            # No more messages available
            break

        for msg in messages:
            record = _convert_sqs_message_to_record(msg)
            additional_records.append(record)
            receipt_handles.append(msg.get("ReceiptHandle", ""))

        remaining -= len(messages)

        logger.debug(
            "Fetched additional messages",
            extra={
                "batch_fetched": len(messages),
                "total_additional": len(additional_records),
            },
        )

    return additional_records, receipt_handles


def delete_messages_batch(
    queue_url: str, receipt_handles: list[str]
) -> list[str]:
    """Delete messages from SQS in batches.

    Args:
        queue_url: The SQS queue URL
        receipt_handles: List of receipt handles to delete

    Returns:
        List of receipt handles that failed to delete
    """
    if not receipt_handles:
        return []

    sqs = _get_sqs_client()
    failed_handles: list[str] = []

    # SQS delete_message_batch allows max 10 entries
    for i in range(0, len(receipt_handles), 10):
        batch = receipt_handles[i : i + 10]
        entries = [
            {"Id": str(idx), "ReceiptHandle": handle}
            for idx, handle in enumerate(batch)
        ]

        try:
            response = sqs.delete_message_batch(
                QueueUrl=queue_url, Entries=entries
            )

            # Track failed deletions
            for failure in response.get("Failed", []):
                idx = int(failure["Id"])
                failed_handles.append(batch[idx])
                logger.warning(
                    "Failed to delete message",
                    extra={
                        "receipt_handle": batch[idx][:50],
                        "error": failure.get("Message"),
                    },
                )

        except ClientError:
            logger.exception(
                "Batch delete failed",
                extra={"batch_size": len(batch)},
            )
            failed_handles.extend(batch)

    return failed_handles


def fetch_phase2_messages(
    records: list[SQSRecord],
    op_logger: OperationLoggerProtocol,
    metrics: Optional[MetricsAccumulatorProtocol] = None,
) -> Phase2FetchResult:
    """Determine queue URL and fetch additional messages for Phase 2 batching.

    Args:
        records: Initial SQS records from Lambda trigger
        op_logger: Logger instance
        metrics: Optional metrics accumulator

    Returns:
        Phase2FetchResult with all records and fetch metadata
    """
    if not records:
        return Phase2FetchResult(records, None, [])

    # Determine queue from first record's collection attribute
    first_record = records[0]
    collection_attr = first_record.get("messageAttributes", {}).get(
        "collection", {}
    )
    collection_value = collection_attr.get("stringValue", "words")

    if collection_value == "lines":
        queue_url = os.environ.get("LINES_QUEUE_URL")
    else:
        queue_url = os.environ.get("WORDS_QUEUE_URL")

    if not queue_url:
        return Phase2FetchResult(records, None, [])

    # Fetch additional messages
    op_logger.info(
        "Phase 2: attempting to fetch additional messages",
        queue_url=queue_url[-50:],
        current_count=len(records),
        max_messages=MAX_MESSAGES_PER_COMPACTION,
    )

    additional_records, manually_fetched_handles = fetch_additional_messages(
        queue_url=queue_url,
        current_count=len(records),
        max_messages=MAX_MESSAGES_PER_COMPACTION,
    )

    if additional_records:
        op_logger.info(
            "Phase 2 batching: fetched additional messages",
            initial_count=len(records),
            additional_count=len(additional_records),
            total_count=len(records) + len(additional_records),
        )
        all_records = records + additional_records

        if metrics:
            metrics.count(
                "CompactionAdditionalMessagesFetched",
                len(additional_records),
            )
    else:
        op_logger.info(
            "Phase 2: no additional messages available",
            initial_count=len(records),
        )
        all_records = records

    return Phase2FetchResult(all_records, queue_url, manually_fetched_handles)


def cleanup_manual_messages(
    phase2: Phase2FetchResult,
    result: CollectionProcessingResult,
    op_logger: OperationLoggerProtocol,
    metrics: Optional[MetricsAccumulatorProtocol] = None,
) -> None:
    """Delete manually-fetched messages after successful processing.

    Lambda-triggered messages are auto-deleted by the event source mapping,
    but manually-fetched messages must be explicitly deleted.
    """
    if not phase2.manually_fetched_handles or not phase2.queue_url:
        return

    if result.processing_successful and not result.failed_message_ids:
        failed_deletes = delete_messages_batch(
            phase2.queue_url, phase2.manually_fetched_handles
        )
        if failed_deletes:
            op_logger.warning(
                "Some manually-fetched messages failed to delete",
                failed_count=len(failed_deletes),
                total_fetched=len(phase2.manually_fetched_handles),
            )
            if metrics:
                metrics.count(
                    "CompactionMessageDeleteFailures", len(failed_deletes)
                )
        else:
            op_logger.info(
                "Deleted manually-fetched messages",
                count=len(phase2.manually_fetched_handles),
            )
            if metrics:
                metrics.count(
                    "CompactionManualMessagesDeleted",
                    len(phase2.manually_fetched_handles),
                )
    else:
        op_logger.info(
            "Skipping deletion of manually-fetched messages due to failures",
            failed_ids=len(result.failed_message_ids),
            fetched_handles=len(phase2.manually_fetched_handles),
        )
