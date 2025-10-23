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

from .message_processor import (
    process_sqs_messages,
    categorize_stream_messages,
    group_messages_by_collection,
)

from .collection_processor import (
    process_stream_messages,
    process_collection_messages,
)

from .metadata_handler import process_metadata_updates
from .label_handler import process_label_updates
from .compaction_handler import process_compaction_run_messages
from .chromadb_operations import (
    update_receipt_metadata,
    remove_receipt_metadata,
    update_word_labels,
    remove_word_labels,
    reconstruct_label_metadata,
)
from .s3_operations import (
    download_s3_prefix,
    merge_chroma_delta_into_snapshot,
    process_compaction_runs,
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
    # Collection processing
    "process_stream_messages",
    "process_collection_messages",
    
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
    
    # S3 operations
    "download_s3_prefix",
    "merge_chroma_delta_into_snapshot",
    "process_compaction_runs",
]
