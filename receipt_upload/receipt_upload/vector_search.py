"""Backend selection for live-ingest vector-search consumers."""

from __future__ import annotations

import os
from typing import Any

from receipt_embeddings import (
    ChromaVectorSearchClient,
    DynamoVectorSearchClient,
    VectorSearchClient,
)


def vector_search_client(
    chroma_client: Any,
    *,
    vector_client: VectorSearchClient | None = None,
) -> VectorSearchClient:
    """Return the configured vector backend, defaulting to Chroma.

    ``vector_client`` is an explicit injection seam for tests and callers that
    already own a backend. Backend construction stays lazy so the default
    Chroma path never initializes an AWS client.
    """

    if vector_client is not None:
        return vector_client
    if isinstance(chroma_client, VectorSearchClient):
        return chroma_client

    backend = os.environ.get("VECTOR_BACKEND", "chroma").strip().lower()
    if backend == "dynamodb":
        return DynamoVectorSearchClient.from_env()
    if backend == "chroma":
        return ChromaVectorSearchClient(chroma_client)
    raise ValueError("VECTOR_BACKEND must be either 'chroma' or 'dynamodb'")


__all__ = ["vector_search_client"]
