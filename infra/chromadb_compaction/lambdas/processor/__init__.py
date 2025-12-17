"""
Stream processor package for DynamoDB stream processing.

This package provides modular components for processing DynamoDB stream events
and routing them to ChromaDB compaction queues.
"""

from .change_detector import get_chromadb_relevant_changes
from .compaction_run import (
    is_compaction_run,
    parse_compaction_run,
)
from .message_builder import build_messages_from_records
from .models import (
    ChromaDBCollection,
    FieldChange,
    LambdaResponse,
    ParsedStreamRecord,
    StreamMessage,
)
from .parsers import (
    detect_entity_type,
    parse_entity,
    parse_stream_record,
)
from .sqs_publisher import publish_messages

__all__ = [
    # Message building
    "build_messages_from_records",
    # Change detection
    "get_chromadb_relevant_changes",
    # Compaction run
    "is_compaction_run",
    "parse_compaction_run",
    # Models
    "ChromaDBCollection",
    "FieldChange",
    "LambdaResponse",
    "ParsedStreamRecord",
    "StreamMessage",
    # Parsers
    "detect_entity_type",
    "parse_entity",
    "parse_stream_record",
    # SQS publishing
    "publish_messages",
]

# Updated Thu Oct 23 10:27:58 PDT 2025
