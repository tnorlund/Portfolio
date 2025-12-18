"""Data models and result types for compaction operations."""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from receipt_dynamo.constants import ChromaDBCollection


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


@dataclass(frozen=True)
class CollectionUpdateResult:
    """Aggregate result from processing all updates for a collection."""

    collection: ChromaDBCollection
    metadata_updates: List[MetadataUpdateResult]
    label_updates: List[LabelUpdateResult]
    delta_merge_count: int
    delta_merge_results: List[Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format."""
        return {
            "collection": self.collection.value,
            "metadata_updates": [r.to_dict() for r in self.metadata_updates],
            "label_updates": [r.to_dict() for r in self.label_updates],
            "delta_merge_count": self.delta_merge_count,
            "delta_merge_results": self.delta_merge_results,
        }

    @property
    def total_metadata_updated(self) -> int:
        """Total number of metadata updates (excluding errors)."""
        return sum(r.updated_count for r in self.metadata_updates if r.error is None)

    @property
    def total_labels_updated(self) -> int:
        """Total number of label updates (excluding errors)."""
        return sum(r.updated_count for r in self.label_updates if r.error is None)

    @property
    def has_errors(self) -> bool:
        """Whether any updates had errors."""
        return any(
            r.error is not None for r in (self.metadata_updates + self.label_updates)
        )
