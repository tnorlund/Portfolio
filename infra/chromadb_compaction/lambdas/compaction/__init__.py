"""Enhanced compaction handler modular components.

This package contains the modular components extracted from the monolithic
enhanced_compaction_handler.py for better maintainability and testability.
"""

from .models import (
    LambdaResponse,
    StreamMessage,
    MetadataUpdateResult,
    LabelUpdateResult,
)

from .message_builder import (
    process_sqs_messages,
    categorize_stream_messages,
    group_messages_by_collection,
)

from .metadata_handler import process_metadata_updates
from .label_handler import process_label_updates
from .compaction_run import process_compaction_run_messages
from .operations import (
    update_receipt_metadata,
    remove_receipt_metadata,
    update_word_labels,
    remove_word_labels,
    reconstruct_label_metadata,
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
    
    # ChromaDB operations
    "update_receipt_metadata",
    "remove_receipt_metadata",
    "update_word_labels",
    "remove_word_labels",
    "reconstruct_label_metadata",
    
]