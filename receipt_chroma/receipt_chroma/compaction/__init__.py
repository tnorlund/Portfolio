"""Compaction operations for ChromaDB snapshots."""

from receipt_chroma.compaction.deletions import apply_receipt_deletions
from receipt_chroma.compaction.deltas import merge_compaction_deltas
from receipt_chroma.compaction.labels import apply_label_updates
from receipt_chroma.compaction.metadata import apply_place_updates
from receipt_chroma.compaction.models import (
    CollectionUpdateResult,
    LabelUpdateResult,
    MetadataUpdateResult,
    ReceiptDeletionResult,
)
from receipt_chroma.compaction.processor import process_collection_updates

__all__ = [
    "apply_label_updates",
    "apply_place_updates",
    "apply_receipt_deletions",
    "CollectionUpdateResult",
    "LabelUpdateResult",
    "merge_compaction_deltas",
    "MetadataUpdateResult",
    "process_collection_updates",
    "ReceiptDeletionResult",
]
