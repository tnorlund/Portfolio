"""Compaction operations for ChromaDB snapshots."""

from receipt_chroma.compaction.processor import process_collection_updates
from receipt_chroma.compaction.models import (
    CollectionUpdateResult,
    MetadataUpdateResult,
    LabelUpdateResult,
)
from receipt_chroma.compaction.metadata import apply_metadata_updates
from receipt_chroma.compaction.labels import apply_label_updates
from receipt_chroma.compaction.deltas import merge_compaction_deltas

__all__ = [
    "process_collection_updates",
    "CollectionUpdateResult",
    "MetadataUpdateResult",
    "LabelUpdateResult",
    "apply_metadata_updates",
    "apply_label_updates",
    "merge_compaction_deltas",
]
