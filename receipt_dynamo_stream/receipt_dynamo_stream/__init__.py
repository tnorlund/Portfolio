"""
Lightweight DynamoDB stream processing utilities.

This package owns stream parsing, change detection, and stream message models
so Lambdas can stay minimal while sharing business logic with other services.
"""

__version__ = "0.1.0"

from receipt_dynamo_stream.change_detection.detector import (
    CHROMADB_RELEVANT_FIELDS,
    get_chromadb_relevant_changes,
)
from receipt_dynamo_stream.message_builder import build_messages_from_records
from receipt_dynamo_stream.models import (
    ChromaDBCollection,
    FieldChange,
    LambdaResponse,
    ParsedStreamRecord,
    StreamMessage,
)
from receipt_dynamo_stream.parsing.compaction_run import (
    is_compaction_run,
    is_embeddings_completed,
    parse_compaction_run,
)
from receipt_dynamo_stream.parsing.parsers import (
    detect_entity_type,
    parse_entity,
    parse_stream_record,
)
from receipt_dynamo_stream.sqs_publisher import (
    publish_messages,
    send_batch_to_queue,
)

__all__ = [
    "__version__",
    "CHROMADB_RELEVANT_FIELDS",
    "ChromaDBCollection",
    "FieldChange",
    "LambdaResponse",
    "ParsedStreamRecord",
    "StreamMessage",
    "build_messages_from_records",
    "detect_entity_type",
    "get_chromadb_relevant_changes",
    "is_compaction_run",
    "is_embeddings_completed",
    "parse_compaction_run",
    "parse_entity",
    "parse_stream_record",
    "publish_messages",
    "send_batch_to_queue",
]
