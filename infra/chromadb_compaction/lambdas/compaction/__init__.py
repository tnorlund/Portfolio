"""Enhanced compaction handler modular components.

This package contains the modular components extracted from the monolithic
enhanced_compaction_handler.py for better maintainability and testability.
"""

from .compaction_run import (
    merge_compaction_deltas,
    process_compaction_run_messages,
)
from .efs_snapshot_manager import get_efs_snapshot_manager
from .label_handler import apply_label_updates_in_memory, process_label_updates
from .message_builder import (
    categorize_stream_messages,
    group_messages_by_collection,
    process_sqs_messages,
)
from .metadata_handler import (
    apply_metadata_updates_in_memory,
    process_metadata_updates,
)
from .models import (
    LabelUpdateResult,
    LambdaResponse,
    MetadataUpdateResult,
    StreamMessage,
)
from .operations import (
    reconstruct_label_metadata,
    remove_receipt_metadata,
    remove_word_labels,
    update_receipt_metadata,
    update_word_labels,
)

__all__ = [
    # Models
    "LambdaResponse",
    "StreamMessage",
    "MetadataUpdateResult",
    "LabelUpdateResult",
    
    # Message processing
    "process_sqs_messages",
    "categorize_stream_messages",
    "group_messages_by_collection",
    
    # Entity handlers
    "process_metadata_updates",
    "process_label_updates", 
    "process_compaction_run_messages",
    "merge_compaction_deltas",
    "apply_metadata_updates_in_memory",
    "apply_label_updates_in_memory",
    "get_efs_snapshot_manager",
    
    # ChromaDB operations
    "update_receipt_metadata",
    "remove_receipt_metadata",
    "update_word_labels",
    "remove_word_labels",
    "reconstruct_label_metadata",
    
]