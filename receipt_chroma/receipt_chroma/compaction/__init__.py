"""Compaction operations for ChromaDB snapshots."""

from receipt_chroma.compaction.deletions import apply_receipt_deletions
from receipt_chroma.compaction.deltas import merge_compaction_deltas
from receipt_chroma.compaction.dual_write import (
    BulkSyncResult,
    CloudConfig,
    DualWriteResult,
    apply_collection_updates,
    sync_collection_to_cloud,
)
from receipt_chroma.compaction.labels import apply_label_updates
from receipt_chroma.compaction.message_ordering import (
    sort_and_deduplicate_messages,
)
from receipt_chroma.compaction.metadata import apply_place_updates
from receipt_chroma.compaction.models import (
    CollectionUpdateResult,
    LabelUpdateResult,
    MetadataUpdateResult,
    ReceiptDeletionResult,
)
from receipt_chroma.compaction.processor import process_collection_updates
from receipt_chroma.compaction.sections import apply_section_updates

__all__ = [
    "apply_collection_updates",
    "apply_label_updates",
    "apply_place_updates",
    "apply_receipt_deletions",
    "apply_section_updates",
    "BulkSyncResult",
    "CloudConfig",
    "CollectionUpdateResult",
    "DualWriteResult",
    "LabelUpdateResult",
    "merge_compaction_deltas",
    "MetadataUpdateResult",
    "process_collection_updates",
    "ReceiptDeletionResult",
    "sort_and_deduplicate_messages",
    "sync_collection_to_cloud",
]
