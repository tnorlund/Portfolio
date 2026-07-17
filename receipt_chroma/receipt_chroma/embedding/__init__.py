"""Public facade for the ChromaDB embedding pipeline.

The facade stays import-light so pure formatting utilities can be used by
offline tooling without initializing ChromaDB. Public pipeline symbols are
loaded only when a caller requests them.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from receipt_chroma.embedding.delta.line_delta import (
        save_line_embeddings_as_delta,
    )
    from receipt_chroma.embedding.delta.producer import (
        produce_embedding_delta,
    )
    from receipt_chroma.embedding.delta.word_delta import (
        save_word_embeddings_as_delta,
    )
    from receipt_chroma.embedding.orchestration import (
        EmbeddingConfig,
        EmbeddingResult,
        build_words_payload,
        create_compaction_run,
        create_embeddings_and_compaction_run,
        download_and_embed_parallel,
        upload_lines_delta,
        upload_words_delta,
    )

__all__ = [
    "EmbeddingConfig",
    "EmbeddingResult",
    "build_words_payload",
    "create_compaction_run",
    "create_embeddings_and_compaction_run",
    "download_and_embed_parallel",
    "produce_embedding_delta",
    "save_line_embeddings_as_delta",
    "save_word_embeddings_as_delta",
    "upload_lines_delta",
    "upload_words_delta",
]


def __getattr__(name: str) -> Any:
    """Load the requested public pipeline symbol on demand."""

    if name == "produce_embedding_delta":
        from receipt_chroma.embedding.delta.producer import (
            produce_embedding_delta,
        )

        return produce_embedding_delta
    if name == "save_line_embeddings_as_delta":
        from receipt_chroma.embedding.delta.line_delta import (
            save_line_embeddings_as_delta,
        )

        return save_line_embeddings_as_delta
    if name == "save_word_embeddings_as_delta":
        from receipt_chroma.embedding.delta.word_delta import (
            save_word_embeddings_as_delta,
        )

        return save_word_embeddings_as_delta
    if name in {
        "EmbeddingConfig",
        "EmbeddingResult",
        "build_words_payload",
        "create_compaction_run",
        "create_embeddings_and_compaction_run",
        "download_and_embed_parallel",
        "upload_lines_delta",
        "upload_words_delta",
    }:
        from receipt_chroma.embedding import orchestration

        return getattr(orchestration, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
