"""Vector-backend selection for merchant resolution (spec §3.5a).

Retrieval — and only retrieval — goes through the Round A
``VectorSearchClient`` protocol. ``VECTOR_BACKEND`` picks the backend:

* ``chroma`` (default): the ChromaDB lines client the caller already
  built, adapted to the protocol. Distances and metadata pass through
  untouched, so default behavior is byte-identical to the direct
  ``lines_client.query`` call it replaces.
* ``dynamodb``: ``DynamoVectorSearchClient`` over the receipts table's
  ``line-embeddings`` vector index.

Thresholds, tier logic, and corroboration gating stay in the resolver,
byte-for-byte unchanged.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from typing import Any

from receipt_embeddings import (
    PROTOCOL_LINE_INDEX,
    FilterValue,
    ScoredItem,
    VectorSearchClient,
    build_chroma_where,
    ensure_query_embeddings_within_quota,
)
from receipt_embeddings.dynamo_client import DynamoVectorSearchClient

VECTOR_BACKEND_ENV = "VECTOR_BACKEND"
CHROMA_BACKEND = "chroma"
DYNAMODB_BACKEND = "dynamodb"
_VALID_BACKENDS = (CHROMA_BACKEND, DYNAMODB_BACKEND)

# The protocol-level index name merchant resolution queries.
LINES_VECTOR_INDEX = PROTOCOL_LINE_INDEX


def resolve_vector_backend() -> str:
    """Read VECTOR_BACKEND, defaulting to chroma; reject unknown values."""

    backend = os.environ.get(VECTOR_BACKEND_ENV, CHROMA_BACKEND).strip()
    if backend not in _VALID_BACKENDS:
        raise ValueError(
            f"{VECTOR_BACKEND_ENV} must be one of {list(_VALID_BACKENDS)}; "
            f"got {backend!r}"
        )
    return backend


class ChromaLinesSearchClient:
    """Adapt the ChromaDB lines client to ``VectorSearchClient``.

    Chroma's cosine distance and metadata dictionaries pass through
    unmodified — consumers see exactly what ``lines_client.query``
    returned before the seam existed.
    """

    def __init__(self, lines_client: Any) -> None:
        self._lines_client = lines_client

    def search(
        self,
        vector: Sequence[float],
        index: str,
        top_k: int,
        filters: Mapping[str, FilterValue] | None = None,
    ) -> list[ScoredItem]:
        query_embeddings = [[float(value) for value in vector]]
        ensure_query_embeddings_within_quota(query_embeddings)
        results = self._lines_client.query(
            collection_name="lines",
            query_embeddings=query_embeddings,
            n_results=top_k,
            where=build_chroma_where(filters),
            include=["metadatas", "distances"],
        )
        if not results:
            return []

        def first_batch(name: str) -> list[Any]:
            batches = results.get(name)
            if batches is None or len(batches) == 0:
                return []
            return list(batches[0])

        # Chroma always returns ids, but the resolver never consumed
        # them, so results without ids (test doubles included) must
        # still flow through: zip over metadatas/distances exactly as
        # the pre-seam code did and synthesize positional keys.
        ids = first_batch("ids")
        distances = first_batch("distances")
        metadatas = first_batch("metadatas")
        return [
            ScoredItem(
                key=str(ids[position]) if position < len(ids) else (
                    f"chroma-result-{position:05d}"
                ),
                distance=float(distance),
                metadata=dict(metadata or {}),
            )
            for position, (metadata, distance) in enumerate(
                zip(metadatas, distances)
            )
        ]

    def get_vector(self, key: str) -> list[float]:
        result = self._lines_client.get(
            collection_name="lines",
            ids=[key],
            include=["embeddings"],
        )
        embeddings = result.get("embeddings")
        if not list(result.get("ids") or []) or embeddings is None:
            raise KeyError(f"unknown vector key: {key}")
        return [float(value) for value in embeddings[0]]


def build_lines_search_client(lines_client: Any) -> VectorSearchClient:
    """Return the VectorSearchClient the resolver should query.

    Args:
        lines_client: The ChromaDB lines client the caller already
            holds; used only when the backend is chroma.
    """

    if resolve_vector_backend() == DYNAMODB_BACKEND:
        return DynamoVectorSearchClient.from_env()
    return ChromaLinesSearchClient(lines_client)


__all__ = [
    "CHROMA_BACKEND",
    "ChromaLinesSearchClient",
    "DYNAMODB_BACKEND",
    "LINES_VECTOR_INDEX",
    "VECTOR_BACKEND_ENV",
    "build_lines_search_client",
    "resolve_vector_backend",
]
