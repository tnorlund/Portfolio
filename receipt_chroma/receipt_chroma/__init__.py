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
from receipt_chroma.embedding.orchestration import (
    EmbeddingConfig,
    EmbeddingResult,
)
from receipt_chroma.embedding.orchestration import (
    _build_lines_payload_traced as build_lines_payload,  # Parallel pipeline helpers
)
from receipt_chroma.embedding.orchestration import (
    _build_words_payload_traced as build_words_payload,
)
from receipt_chroma.embedding.orchestration import (
    _create_compaction_run_traced as create_compaction_run,
)
from receipt_chroma.embedding.orchestration import (
    _download_and_embed_parallel as download_and_embed_parallel,
)
from receipt_chroma.embedding.orchestration import (
    _upload_lines_delta_traced as upload_lines_delta,
)
from receipt_chroma.embedding.orchestration import (
    _upload_words_delta_traced as upload_words_delta,
)
from receipt_chroma.embedding.orchestration import (
    _upsert_lines_local_traced as upsert_lines_local,
)
from receipt_chroma.embedding.orchestration import (
    _upsert_words_local_traced as upsert_words_local,
)
from receipt_chroma.embedding.orchestration import (
    create_embeddings_and_compaction_run,
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
    "CollectionUpdateResult",
    "EmbeddingConfig",
    "EmbeddingResult",
    "LabelUpdateResult",
    "LockManager",
    "MetadataUpdateResult",
    "StorageManager",
    "StorageMode",
    # Parallel pipeline helpers
    "build_lines_payload",
    "build_words_payload",
    "create_compaction_run",
    "create_embeddings_and_compaction_run",
    "download_and_embed_parallel",
    "download_snapshot_atomic",
    "initialize_empty_snapshot",
    "process_collection_updates",
    "produce_embedding_delta",
    "save_line_embeddings_as_delta",
    "save_word_embeddings_as_delta",
    "upload_lines_delta",
    "upload_snapshot_atomic",
    "upload_words_delta",
    "upsert_lines_local",
    "upsert_words_local",
]
