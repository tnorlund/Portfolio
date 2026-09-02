"""Protocol adapter for the incumbent Chroma receipt collections."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from receipt_embeddings.keys import line_canonical_key, word_canonical_key
from receipt_embeddings.protocols import ChromaQueryClient
from receipt_embeddings.quotas import build_chroma_where
from receipt_embeddings.service_limits import (
    WORD_INDEX,
    physical_index_name,
    validate_top_k,
)
from receipt_embeddings.vector_client import FilterValue, ScoredItem


def _metadata_key(
    metadata: Mapping[str, object], *, index: str, position: int
) -> str:
    if not {"image_id", "receipt_id", "line_id"}.issubset(metadata):
        return f"CHROMA_RESULT#{position:05d}"
    if index == WORD_INDEX:
        return word_canonical_key(
            str(metadata["image_id"]),
            int(metadata["receipt_id"]),
            int(metadata["line_id"]),
            int(metadata["word_id"]),
        )
    return line_canonical_key(
        str(metadata["image_id"]),
        int(metadata["receipt_id"]),
        int(metadata["line_id"]),
    )


class ChromaVectorSearchClient:
    """Normalize the current ChromaClient to ``VectorSearchClient``."""

    def __init__(self, chroma_client: ChromaQueryClient) -> None:
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
        query: dict[str, object] = {
            "collection_name": collection_name,
            "query_embeddings": [list(vector)],
            "n_results": top_k,
            "include": ["metadatas", "distances", "documents"],
        }
        where = build_chroma_where(filters)
        if where is not None:
            query["where"] = where
        response = self._client.query(**query)

        # Same ndarray-truthiness hazard as get_vector (review P1-A):
        # chromadb may return these as numpy arrays, so never boolean-
        # test them — take the first query's row with explicit checks.
        def _first_row(name: str) -> Sequence[object]:
            value = response.get(name)
            if value is None or len(value) == 0:
                return []
            row = value[0]
            return [] if row is None else row

        metadatas = _first_row("metadatas")
        distances = _first_row("distances")
        ids = _first_row("ids")
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
        # chromadb returns embeddings as a numpy ndarray, whose truth
        # value is ambiguous — ``or []`` / ``if not`` raise ValueError
        # and turned every default-backend consensus call into a
        # degraded answer (E3 review P1-A). Check None/length instead.
        embeddings = response.get("embeddings")
        if embeddings is None or len(embeddings) == 0:
            raise KeyError(f"unknown vector key: {key}")
        vector = embeddings[0]
        if vector is None or len(vector) == 0:
            raise KeyError(f"unknown vector key: {key}")
        return [float(value) for value in vector]


__all__ = ["ChromaVectorSearchClient"]
