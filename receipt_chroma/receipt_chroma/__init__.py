"""ChromaDB utility package for receipt vector storage."""

__version__ = "0.1.0"

from receipt_chroma.data.chroma_client import ChromaClient
from receipt_chroma.lock_manager import LockManager

# S3 operations are available via receipt_chroma.s3 submodule
# Import here for convenience
from receipt_chroma.s3 import (
    download_snapshot_atomic,
    initialize_empty_snapshot,
    upload_snapshot_atomic,
)

# Embedding pipeline exports
from receipt_chroma.embedding.delta import (
    produce_embedding_delta,
    save_line_embeddings_as_delta,
    save_word_embeddings_as_delta,
)

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
]
