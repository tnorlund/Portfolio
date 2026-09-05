"""
Lightweight DynamoDB stream processing utilities.

This package owns stream parsing, change detection, and stream message models
so Lambdas can stay minimal while sharing business logic with other services.
"""

# pylint: disable=duplicate-code
# Re-exports from submodules for clean top-level API (e.g., `from
# receipt_dynamo_stream import X` instead of deeper imports).

__version__ = "0.1.0"

from receipt_dynamo_stream.change_detection.detector import (
    UPDATE_RELEVANT_FIELDS,
    get_update_relevant_changes,
)
from receipt_dynamo_stream.exceptions import (
    QueueBatchFailureError,
    QueueConfigurationError,
    QueuePublishError,
    QueueServiceError,
    ReceiptDynamoStreamError,
)
from receipt_dynamo_stream.message_builder import build_messages_from_records
from receipt_dynamo_stream.models import (
    FieldChange,
    LambdaResponse,
    ParsedStreamRecord,
    StreamMessage,
    StreamRecordContext,
    TargetQueue,
)
from receipt_dynamo_stream.parsing.parsers import (
    detect_entity_type,
    is_embedding_sk,
    parse_entity,
    parse_stream_record,
)
from receipt_dynamo_stream.sqs_publisher import (
    publish_messages,
    send_batch_to_queue,
)
from receipt_dynamo_stream.stream_types import (
    APIGatewayResponse,
    AttributeValue,
    AttributeValueS,
    DynamoDBItem,
    DynamoDBKeys,
    DynamoDBStreamEvent,
    DynamoDBStreamRecord,
    LambdaContext,
    MetricsRecorder,
    StreamProcessorResponseData,
    StreamRecordDynamoDB,
)
from receipt_dynamo_stream.vector_freshening import (
    FresheningStats,
    apply_vector_freshening,
)

__all__ = [
    "__version__",
    "UPDATE_RELEVANT_FIELDS",
    "FieldChange",
    "FresheningStats",
    "LambdaResponse",
    "ParsedStreamRecord",
    "QueueBatchFailureError",
    "QueueConfigurationError",
    "QueuePublishError",
    "QueueServiceError",
    "ReceiptDynamoStreamError",
    "StreamMessage",
    "StreamRecordContext",
    "TargetQueue",
    "apply_vector_freshening",
    "build_messages_from_records",
    "detect_entity_type",
    "get_update_relevant_changes",
    "is_embedding_sk",
    "parse_entity",
    "parse_stream_record",
    "publish_messages",
    "send_batch_to_queue",
    # Type definitions
    "APIGatewayResponse",
    "AttributeValue",
    "AttributeValueS",
    "DynamoDBItem",
    "DynamoDBKeys",
    "DynamoDBStreamEvent",
    "DynamoDBStreamRecord",
    "LambdaContext",
    "MetricsRecorder",
    "StreamProcessorResponseData",
    "StreamRecordDynamoDB",
]
