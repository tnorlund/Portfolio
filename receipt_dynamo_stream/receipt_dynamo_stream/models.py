"""
Data models for DynamoDB stream processing.
"""

# pylint: disable=import-error
# import-error: receipt_dynamo is a monorepo sibling installed at runtime

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Optional, TypeAlias

from receipt_dynamo.entities.receipt import Receipt
from receipt_dynamo.entities.receipt_line import ReceiptLine
from receipt_dynamo.entities.receipt_place import ReceiptPlace
from receipt_dynamo.entities.receipt_word import ReceiptWord
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel
from receipt_dynamo_stream.stream_types import DynamoDBItem

StreamEntity: TypeAlias = (
    Receipt | ReceiptLine | ReceiptPlace | ReceiptWord | ReceiptWordLabel
)


class ChromaDBCollection(str, Enum):
    """ChromaDB collection types for receipt embeddings."""

    LINES = "lines"
    WORDS = "words"


class TargetQueue(str, Enum):
    """Target queues for stream message routing."""

    LINES = "lines"
    WORDS = "words"
    RECEIPT_SUMMARY = "receipt_summary"


@dataclass(frozen=True)
class LambdaResponse:
    """Response structure for Lambda handlers."""

    status_code: int
    processed_records: int
    queued_messages: int

    def to_dict(self) -> dict[str, int]:
        """Convert to AWS Lambda-compatible dictionary."""
        return {
            "statusCode": self.status_code,
            "processed_records": self.processed_records,
            "queued_messages": self.queued_messages,
        }


@dataclass(frozen=True)
class ParsedStreamRecord:
    """Parsed DynamoDB stream record with entity information."""

    entity_type: str
    old_entity: Optional[StreamEntity]
    new_entity: Optional[StreamEntity]
    pk: str
    sk: str


@dataclass(frozen=True)
class FieldChange:
    """Represents a change in a single field."""

    old: object | None
    new: object | None


@dataclass(frozen=True)
class StreamRecordContext:
    """Metadata about the source DynamoDB stream record."""

    source: str = "dynamodb_stream"
    timestamp: Optional[str] = None
    record_id: Optional[str] = None
    aws_region: Optional[str] = None


@dataclass(frozen=True)
class StreamMessage:
    """Enhanced stream message with collection targeting."""

    entity_type: str
    entity_data: Mapping[str, object]
    changes: Mapping[str, FieldChange]
    event_name: str
    collections: tuple[ChromaDBCollection | TargetQueue, ...]
    context: StreamRecordContext
    record_snapshot: Optional[DynamoDBItem] = None


__all__ = [
    "ChromaDBCollection",
    "FieldChange",
    "LambdaResponse",
    "ParsedStreamRecord",
    "StreamMessage",
    "StreamRecordContext",
    "TargetQueue",
]
