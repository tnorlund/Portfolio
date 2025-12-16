"""
Data models for DynamoDB stream processing.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Optional

from receipt_dynamo.entities.receipt_metadata import ReceiptMetadata
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel

StreamEntity = ReceiptMetadata | ReceiptWordLabel


class ChromaDBCollection(str, Enum):
    """ChromaDB collection types for receipt embeddings."""

    LINES = "lines"
    WORDS = "words"


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
class StreamMessage:  # pylint: disable=too-many-instance-attributes
    """Enhanced stream message with collection targeting."""

    entity_type: str
    entity_data: Mapping[str, object]
    changes: Mapping[str, FieldChange]
    event_name: str
    collections: tuple[ChromaDBCollection, ...]
    source: str = "dynamodb_stream"
    timestamp: Optional[str] = None
    stream_record_id: Optional[str] = None
    aws_region: Optional[str] = None
    record_snapshot: Optional[Mapping[str, object]] = None


__all__ = [
    "ChromaDBCollection",
    "LambdaResponse",
    "ParsedStreamRecord",
    "StreamMessage",
    "FieldChange",
]
