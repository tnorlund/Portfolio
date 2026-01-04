"""
TypedDict definitions for Lambda and SQS message processing.

Provides type-safe structures for SQS messages, Lambda responses,
and related types to reduce usage of `Any` throughout the codebase.
"""
# pylint: disable=import-error
# import-error: utils is bundled into Lambda package
from typing import Optional, Protocol, TypedDict, Union


# =============================================================================
# SQS Message Attribute Types
# =============================================================================


class MessageAttributeValue(TypedDict, total=False):
    """SQS message attribute value in Lambda event format."""

    stringValue: str
    numberValue: str
    binaryValue: bytes


# Type alias for message attributes dict
MessageAttributes = dict[str, MessageAttributeValue]


# =============================================================================
# SQS Record Types (Lambda Event Format)
# =============================================================================


class SQSRecord(TypedDict, total=False):
    """SQS record in Lambda event format.

    This is the format received by Lambda from SQS event source mapping.
    """

    messageId: str
    receiptHandle: str
    body: str
    messageAttributes: MessageAttributes
    md5OfBody: str
    eventSource: str
    eventSourceARN: str
    awsRegion: str


class SQSEvent(TypedDict):
    """SQS event passed to Lambda handlers."""

    Records: list[SQSRecord]


# =============================================================================
# Raw SQS Message Types (boto3 format)
# =============================================================================


class RawMessageAttribute(TypedDict, total=False):
    """SQS message attribute in boto3 format (from receive_message)."""

    StringValue: str
    NumberValue: str
    BinaryValue: bytes
    DataType: str


class RawSQSMessage(TypedDict, total=False):
    """Raw SQS message from boto3 receive_message response."""

    MessageId: str
    ReceiptHandle: str
    Body: str
    MessageAttributes: dict[str, RawMessageAttribute]
    MD5OfBody: str
    MD5OfMessageAttributes: str


# =============================================================================
# Lambda Response Types
# =============================================================================


class BatchItemFailure(TypedDict):
    """Single item failure for SQS partial batch response."""

    itemIdentifier: str


class SQSBatchResponse(TypedDict):
    """Response format for SQS batch processing with partial failures."""

    batchItemFailures: list[BatchItemFailure]


class CompactionResponseData(TypedDict, total=False):
    """Response data from compaction Lambda."""

    statusCode: int
    message: str
    processed_count: int
    failed_count: int
    collections_processed: int
    error: str


# =============================================================================
# Compaction Result Types
# =============================================================================


class MetadataUpdateResult(TypedDict, total=False):
    """Result of a metadata update operation."""

    image_id: str
    receipt_id: int
    error: Optional[str]
    success: bool


class LabelUpdateResult(TypedDict, total=False):
    """Result of a label update operation."""

    chromadb_id: str
    error: Optional[str]
    success: bool


class CompactionResult(TypedDict, total=False):
    """Result from process_collection_updates."""

    metadata_updates: list[MetadataUpdateResult]
    label_updates: list[LabelUpdateResult]
    processed_count: int
    failed_count: int


class ProcessCollectionResult(TypedDict, total=False):
    """Return type for process_collection function."""

    status: str
    result: CompactionResult
    failed_message_ids: list[str]


# =============================================================================
# Logger Protocol
# =============================================================================


class OperationLoggerProtocol(Protocol):
    """Protocol for operation logger used in handler functions."""

    def info(
        self, msg: str, **kwargs: Union[str, int, float, bool, None]
    ) -> None:
        """Log info message with structured context."""

    def warning(
        self, msg: str, **kwargs: Union[str, int, float, bool, None]
    ) -> None:
        """Log warning message with structured context."""

    def error(
        self, msg: str, **kwargs: Union[str, int, float, bool, None]
    ) -> None:
        """Log error message with structured context."""

    def exception(
        self, msg: str, **kwargs: Union[str, int, float, bool, None]
    ) -> None:
        """Log exception with traceback."""

    def debug(
        self, msg: str, **kwargs: Union[str, int, float, bool, None]
    ) -> None:
        """Log debug message with structured context."""


# =============================================================================
# Metrics Protocol
# =============================================================================


class MetricsAccumulatorProtocol(Protocol):
    """Protocol for metrics accumulator used in handler functions."""

    def count(
        self,
        metric_name: str,
        value: int = 1,
        dimensions: Optional[dict[str, str]] = None,
    ) -> None:
        """Accumulate count metric."""

    def gauge(
        self,
        metric_name: str,
        value: Union[int, float],
        _unit: str = "None",
        dimensions: Optional[dict[str, str]] = None,
    ) -> None:
        """Accumulate gauge metric."""


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    # Message attribute types
    "MessageAttributeValue",
    "MessageAttributes",
    # SQS record types (Lambda format)
    "SQSRecord",
    "SQSEvent",
    # Raw SQS types (boto3 format)
    "RawMessageAttribute",
    "RawSQSMessage",
    # Response types
    "BatchItemFailure",
    "SQSBatchResponse",
    "CompactionResponseData",
    # Compaction result types
    "MetadataUpdateResult",
    "LabelUpdateResult",
    "CompactionResult",
    "ProcessCollectionResult",
    # Protocols
    "OperationLoggerProtocol",
    "MetricsAccumulatorProtocol",
]
