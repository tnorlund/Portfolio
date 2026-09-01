"""Protocol adapter for the incumbent Chroma receipt collections."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from receipt_embeddings.quotas import build_chroma_where
from receipt_embeddings.service_limits import (
    LINE_INDEX,
    WORD_INDEX,
    physical_index_name,
    validate_top_k,
)
from receipt_embeddings.vector_client import FilterValue, ScoredItem


def _metadata_key(
    metadata: Mapping[str, Any], *, index: str, position: int
) -> str:
    if not {"image_id", "receipt_id", "line_id"}.issubset(metadata):
        return f"CHROMA_RESULT#{position:05d}"
    prefix = (
        f"IMAGE#{metadata['image_id']}#"
        f"RECEIPT#{int(metadata['receipt_id']):05d}#"
        f"LINE#{int(metadata['line_id']):05d}"
    )
    if index == WORD_INDEX:
        return f"{prefix}#WORD#{int(metadata['word_id']):05d}"
    return prefix


class ChromaVectorSearchClient:
    """Normalize the current ChromaClient to ``VectorSearchClient``."""

    def __init__(self, chroma_client: Any) -> None:
        if not callable(getattr(chroma_client, "query", None)):
            raise TypeError("chroma_client must provide query()")
        self._client = chroma_client

    def search(
        self,
        vector: Sequence[float],
        index: str,
        top_k: int,
        filters: Mapping[str, FilterValue] | None = None,
    ) -> list[ScoredItem]:
        validate_top_k(top_k)
        physical = physical_index_name(index)
        collection_name = "words" if physical == WORD_INDEX else "lines"
        query: dict[str, Any] = {
            "collection_name": collection_name,
            "query_embeddings": [list(vector)],
            "n_results": top_k,
            "include": ["metadatas", "distances", "documents"],
        }
        where = build_chroma_where(filters)
        if where is not None:
            query["where"] = where
        response = self._client.query(**query)
        metadatas = (response.get("metadatas") or [[]])[0]
        distances = (response.get("distances") or [[]])[0]
        ids = (response.get("ids") or [[]])[0]
        results: list[ScoredItem] = []
        for position, (metadata, distance) in enumerate(
            zip(metadatas, distances, strict=False)
        ):
            if not isinstance(metadata, Mapping):
                continue
            try:
                key = (
                    str(ids[position])
                    if position < len(ids) and ids[position]
                    else _metadata_key(
                        metadata, index=physical, position=position
                    )
                )
                results.append(
                    ScoredItem(
                        key=key,
                        distance=float(distance),
                        metadata=dict(metadata),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return results

    def get_vector(self, key: str) -> list[float]:
        collection_name = "words" if "#WORD#" in key else "lines"
        response = self._client.get(
            collection_name=collection_name,
            ids=[key],
            include=["embeddings"],
        )
        embeddings = response.get("embeddings") or []
        if not embeddings:
            raise KeyError(f"unknown vector key: {key}")
        return [float(value) for value in embeddings[0]]


__all__ = ["ChromaVectorSearchClient"]
