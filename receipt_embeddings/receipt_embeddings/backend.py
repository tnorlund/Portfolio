"""Lazy ``VECTOR_BACKEND`` selection shared by vector-search consumers.

Promoted from ``receipt_upload.vector_search`` (card E2) so consumers that
cannot depend on receipt_upload — receipt_agent's QA tools and both MCP
servers — share the one selector instead of duplicating it.
``receipt_upload.vector_search`` re-exports this function unchanged.
"""

from __future__ import annotations

import os
from typing import Any

from receipt_embeddings.chroma_client import ChromaVectorSearchClient
from receipt_embeddings.dynamo_client import DynamoVectorSearchClient
from receipt_embeddings.vector_client import VectorSearchClient


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
