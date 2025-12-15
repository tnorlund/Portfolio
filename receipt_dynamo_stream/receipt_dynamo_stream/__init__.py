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

__all__ = [
    "__version__",
    "ChromaDBCollection",
    "FieldChange",
    "LambdaResponse",
    "ParsedStreamRecord",
    "StreamMessage",
    "detect_entity_type",
    "parse_entity",
    "parse_stream_record",
    "is_compaction_run",
    "is_embeddings_completed",
    "parse_compaction_run",
    "CHROMADB_RELEVANT_FIELDS",
    "get_chromadb_relevant_changes",
]
