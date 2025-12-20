"""
Data models for stream processor.

Contains all dataclasses and enums used throughout the stream processing pipeline.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from receipt_dynamo.entities.receipt_place import ReceiptPlace
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel


class ChromaDBCollection(str, Enum):
    """ChromaDB collection types for receipt embeddings."""

    LINES = "lines"
    WORDS = "words"


@dataclass(frozen=True)
class LambdaResponse:
    """Response from the Lambda handler with processing statistics."""

    status_code: int
    processed_records: int
    queued_messages: int

    def to_dict(self) -> Dict[str, Any]:
        """Convert to AWS Lambda-compatible dictionary."""
        return {
            "statusCode": self.status_code,
            "processed_records": self.processed_records,
            "queued_messages": self.queued_messages,
        }


@dataclass(frozen=True)
class ParsedStreamRecord:
    """Parsed DynamoDB stream record with entity information."""

    entity_type: str  # "RECEIPT_PLACE" or "RECEIPT_WORD_LABEL"
    old_entity: Optional[Union[ReceiptPlace, ReceiptWordLabel]]
    new_entity: Optional[Union[ReceiptPlace, ReceiptWordLabel]]
    pk: str
    sk: str


@dataclass(frozen=True)
class StreamMessage:  # pylint: disable=too-many-instance-attributes
    """Enhanced stream message with collection targeting."""

    entity_type: str
    entity_data: Dict[str, Any]
    changes: Dict[str, Any]
    event_name: str
    collections: List[ChromaDBCollection]  # Which collections this affects
    source: str = "dynamodb_stream"
    timestamp: Optional[str] = None
    stream_record_id: Optional[str] = None
    aws_region: Optional[str] = None


@dataclass(frozen=True)
class FieldChange:
    """Represents a change in a single field."""

    old: Any
    new: Any
