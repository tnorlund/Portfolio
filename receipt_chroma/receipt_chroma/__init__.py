"""ChromaDB utility package for receipt vector storage."""

__version__ = "0.1.0"

# Compaction operations
from receipt_chroma.compaction import (
    CollectionUpdateResult,
    LabelUpdateResult,
    MetadataUpdateResult,
    process_collection_updates,
)
from receipt_chroma.data.chroma_client import ChromaClient

# Embedding pipeline exports
from receipt_chroma.embedding.delta import (
    produce_embedding_delta,
    save_line_embeddings_as_delta,
    save_word_embeddings_as_delta,
)
from receipt_chroma.lock_manager import LockManager

# S3 operations are available via receipt_chroma.s3 submodule
# Import here for convenience
from receipt_chroma.s3 import (
    download_snapshot_atomic,
    initialize_empty_snapshot,
    upload_snapshot_atomic,
)

# Storage abstraction
from receipt_chroma.storage import StorageManager, StorageMode

__all__ = [
    "__version__",
    "ChromaClient",
    "LockManager",
    "download_snapshot_atomic",
    "upload_snapshot_atomic",
    "initialize_empty_snapshot",
    "produce_embedding_delta",
    "save_line_embeddings_as_delta",
    "save_word_embeddings_as_delta",
    "process_collection_updates",
    "CollectionUpdateResult",
    "MetadataUpdateResult",
    "LabelUpdateResult",
    "StorageManager",
    "StorageMode",
]
