"""Chroma-like ``.query`` / ``.get`` adapter → :class:`VectorSearchClient`.

This module does not import ``chromadb``. The live Cloud client is
constructed by the harness CLI when ``CHROMA_CLOUD_*`` is set.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from receipt_embeddings.vector_client import (
    LINE_EMBEDDINGS_INDEX,
    WORD_EMBEDDINGS_INDEX,
    ScoredItem,
    index_for_key,
)

INDEX_TO_COLLECTION = {
    LINE_EMBEDDINGS_INDEX: "lines",
    WORD_EMBEDDINGS_INDEX: "words",
}
COLLECTION_TO_INDEX = {v: k for k, v in INDEX_TO_COLLECTION.items()}


class ChromaQueryClient(Protocol):
    """The slice of ChromaClient the adapter needs (query + get)."""

    def query(self, **kwargs: Any) -> dict[str, Any]: ...

    def get(self, **kwargs: Any) -> dict[str, Any]: ...


def _chroma_where(
    filters: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if not filters:
        return None
    if len(filters) == 1:
        key, value = next(iter(filters.items()))
        return {key: value}
    return {"$and": [{key: value} for key, value in filters.items()]}


def _row(result: Mapping[str, Any], field: str, default: Any) -> Any:
    rows = result.get(field, default)
    if not rows:
        return default[0] if isinstance(default, list) else default
    return rows[0]


class ChromaVectorSearchClient:
    """:class:`VectorSearchClient` over a Chroma ``query``/``get`` client."""

    def __init__(self, chroma: ChromaQueryClient) -> None:
        self._chroma = chroma

    def search(
        self,
        vector: Sequence[float],
        index: str,
        top_k: int,
        filters: Mapping[str, Any] | None = None,
    ) -> list[ScoredItem]:
        if top_k < 1:
            return []
        collection = INDEX_TO_COLLECTION.get(index)
        if collection is None:
            raise ValueError(f"Unknown index {index!r}")
        kwargs: dict[str, Any] = {
            "collection_name": collection,
            "query_embeddings": [list(vector)],
            "n_results": top_k,
            "include": ["metadatas", "distances"],
        }
        where = _chroma_where(filters)
        if where is not None:
            kwargs["where"] = where
        result = self._chroma.query(**kwargs)
        ids = _row(result, "ids", [[]])
        distances = _row(result, "distances", [[]])
        metadatas = _row(result, "metadatas", [[]])
        scored: list[ScoredItem] = []
        for key, distance, metadata in zip(
            ids, distances, metadatas, strict=False
        ):
            scored.append(
                ScoredItem(
                    key=str(key),
                    distance=float(distance),
                    metadata=metadata if isinstance(metadata, dict) else {},
                )
            )
        scored.sort(key=lambda item: (item.distance, item.key))
        return scored

    def get_vector(self, key: str) -> Sequence[float]:
        collection = INDEX_TO_COLLECTION[index_for_key(key)]
        result = self._chroma.get(
            collection_name=collection,
            ids=[key],
            include=["embeddings"],
        )
        embeddings = result.get("embeddings") or []
        if not embeddings:
            raise KeyError(f"no vector stored for {key!r}")
        return list(embeddings[0])
