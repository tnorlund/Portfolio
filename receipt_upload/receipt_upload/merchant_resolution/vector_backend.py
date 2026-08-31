"""Vector-search backends for merchant resolution.

Retrieval is the only thing that changes. Thresholds, tier logic, and
corroboration gating stay in ``resolver.py``.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from typing import Any

from receipt_embeddings.dynamo_client import DynamoVectorSearchClient
from receipt_embeddings.quotas import (
    PROTOCOL_LINE_INDEX,
    PROTOCOL_WORD_INDEX,
    build_chroma_where,
)
from receipt_embeddings.vector_client import (
    FilterValue,
    ScoredItem,
    VectorSearchClient,
)

DEFAULT_VECTOR_BACKEND = "chroma"


def vector_backend_name(value: str | None = None) -> str:
    raw = value or os.environ.get("VECTOR_BACKEND") or DEFAULT_VECTOR_BACKEND
    name = raw.strip().lower()
    if name in {"dynamo", "dynamodb"}:
        return "dynamodb"
    if name == "chroma":
        return "chroma"
    raise ValueError(f"VECTOR_BACKEND must be dynamodb or chroma, got {raw!r}")


class ChromaVectorSearchClient:
    """Adapt ``ChromaClient.query`` onto ``VectorSearchClient``."""

    def __init__(self, chroma_client: Any) -> None:
        self._chroma = chroma_client

    def search(
        self,
        vector: Sequence[float],
        index: str,
        top_k: int,
        filters: Mapping[str, FilterValue] | None = None,
    ) -> list[ScoredItem]:
        collection = "words" if index == PROTOCOL_WORD_INDEX else "lines"
        result = self._chroma.query(
            collection_name=collection,
            query_embeddings=[[float(value) for value in vector]],
            n_results=top_k,
            where=build_chroma_where(filters),
            include=["metadatas", "distances", "documents"],
        )

        def first_batch(name: str) -> list[Any]:
            batches = result.get(name)
            if not batches:
                return []
            return list(batches[0])

        ids = first_batch("ids")
        distances = first_batch("distances")
        metadatas = first_batch("metadatas")
        # Older callers omit ids; synthesize keys so the protocol still holds.
        if not ids and metadatas:
            ids = [f"chroma-{position}" for position in range(len(metadatas))]
        count = min(len(distances), len(metadatas), len(ids) or len(metadatas))
        items: list[ScoredItem] = []
        for position in range(count):
            metadata = dict(metadatas[position] or {})
            key = (
                str(ids[position])
                if position < len(ids)
                else (f"chroma-{position}")
            )
            items.append(
                ScoredItem(
                    key=key,
                    distance=float(distances[position]),
                    metadata=metadata,
                )
            )
        return items

    def get_vector(self, key: str) -> list[float]:
        raise KeyError(f"unknown vector key: {key}")


def vector_search_client(
    lines_client: Any,
    *,
    backend: str | None = None,
) -> VectorSearchClient:
    """Return the retrieval client selected by ``VECTOR_BACKEND``."""

    name = vector_backend_name(backend)
    if name == "dynamodb":
        return DynamoVectorSearchClient.from_env()
    return ChromaVectorSearchClient(lines_client)


__all__ = [
    "DEFAULT_VECTOR_BACKEND",
    "ChromaVectorSearchClient",
    "PROTOCOL_LINE_INDEX",
    "vector_backend_name",
    "vector_search_client",
]
