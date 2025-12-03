"""Data models and response types for the enhanced compaction handler."""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from receipt_dynamo.constants import ChromaDBCollection


@dataclass(frozen=True)
class LambdaResponse:
    """Response from the Lambda handler with processing statistics."""

    status_code: int
    message: str
    processed_messages: Optional[int] = None
    stream_messages: Optional[int] = None
    delta_messages: Optional[int] = None
    metadata_updates: Optional[int] = None
    label_updates: Optional[int] = None
    metadata_results: Optional[List[Dict[str, Any]]] = None
    label_results: Optional[List[Dict[str, Any]]] = None
    processed_deltas: Optional[int] = None
    skipped_deltas: Optional[int] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to AWS Lambda-compatible dictionary."""
        result = {"statusCode": self.status_code, "message": self.message}

        # Add optional fields if they have values
        optional_fields = [
            "processed_messages",
            "stream_messages",
            "delta_messages",
            "metadata_updates",
            "label_updates",
            "metadata_results",
            "label_results",
            "processed_deltas",
            "skipped_deltas",
            "error",
        ]

        for field in optional_fields:
            value = getattr(self, field)
            if value is not None:
                result[field] = value

        return result


@dataclass(frozen=True)
class StreamMessage:
    """Parsed stream message from SQS."""

    entity_type: str
    entity_data: Dict[str, Any]
    changes: Dict[str, Any]
    event_name: str
    collection: ChromaDBCollection
    record_snapshot: Optional[Dict[str, Any]] = None
    source: str = "dynamodb_stream"
    message_id: Optional[str] = None
    receipt_handle: Optional[str] = None
    queue_url: Optional[str] = None


@dataclass(frozen=True)
class MetadataUpdateResult:
    """Result from processing a metadata update."""

    database: str
    collection: str
    updated_count: int
    image_id: str
    receipt_id: int
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format."""
        result = {
            "database": self.database,
            "collection": self.collection,
            "updated_count": self.updated_count,
            "image_id": self.image_id,
            "receipt_id": self.receipt_id,
        }
        if self.error:
            result["error"] = self.error
        return result


@dataclass(frozen=True)
class LabelUpdateResult:
    """Result from processing a label update."""

    chromadb_id: str
    updated_count: int
    event_name: str
    changes: List[str]
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format."""
        result = {
            "chromadb_id": self.chromadb_id,
            "updated_count": self.updated_count,
            "event_name": self.event_name,
            "changes": self.changes,
        }
        if self.error:
            result["error"] = self.error
        return result
