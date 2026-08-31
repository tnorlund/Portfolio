"""Exact nearest-neighbor fake for :class:`VectorSearchClient`.

Uses numpy cosine distance over fixture vectors. Ranking is deterministic:
distance ascending, then key ascending. There is no ANN noise.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from receipt_embeddings.vector_client import (
    INDEX_NAMES,
    ScoredItem,
    index_for_key,
)

_ZERO_NORM_DISTANCE = 2.0


def cosine_distance(query: np.ndarray, corpus: np.ndarray) -> np.ndarray:
    """Cosine distance ``1 - (q·v) / (|q||v|)`` for each row of ``corpus``.

    Zero-norm vectors are treated as maximally far (distance 2). Results
    are clipped into ``[0, 2]`` so float error cannot escape the range
    DynamoDB COSINE / Chroma cosine use (SPEC §3.5a).
    """
    query = np.asarray(query, dtype=np.float64).reshape(-1)
    corpus = np.asarray(corpus, dtype=np.float64)
    if corpus.ndim == 1:
        corpus = corpus.reshape(1, -1)
    q_norm = float(np.linalg.norm(query))
    row_norms = np.linalg.norm(corpus, axis=1)
    dots = corpus @ query
    denom = row_norms * q_norm
    sims = np.zeros(corpus.shape[0], dtype=np.float64)
    valid = denom > 0.0
    sims[valid] = dots[valid] / denom[valid]
    sims = np.clip(sims, -1.0, 1.0)
    distances = 1.0 - sims
    distances[~valid] = _ZERO_NORM_DISTANCE
    return distances


def _filters_match(
    metadata: Mapping[str, Any], filters: Mapping[str, Any] | None
) -> bool:
    if not filters:
        return True
    for field, expected in filters.items():
        if metadata.get(field) != expected:
            return False
    return True


@dataclass(frozen=True)
class _StoredItem:
    key: str
    index: str
    vector: np.ndarray
    metadata: dict[str, Any]


class FakeVectorIndex:
    """In-memory exact-NN index. Implements :class:`VectorSearchClient`."""

    def __init__(self) -> None:
        self._items: dict[str, _StoredItem] = {}

    def add(
        self,
        key: str,
        vector: Sequence[float],
        index: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        """Insert or replace one vector. ``index`` must be a known name."""
        if index not in INDEX_NAMES:
            raise ValueError(
                f"Unknown index {index!r}; expected one of {INDEX_NAMES}"
            )
        arr = np.asarray(list(vector), dtype=np.float64)
        if arr.ndim != 1 or arr.size == 0:
            raise ValueError("vector must be a non-empty 1-d sequence")
        self._items[key] = _StoredItem(
            key=key,
            index=index,
            vector=arr,
            metadata=dict(metadata or {}),
        )

    def add_many(
        self,
        records: Iterable[
            tuple[str, Sequence[float], str, Mapping[str, Any] | None]
        ],
    ) -> None:
        """Insert many ``(key, vector, index, metadata)`` records."""
        for key, vector, index, metadata in records:
            self.add(key, vector, index, metadata)

    @classmethod
    def from_fixture_items(
        cls, items: Sequence[Mapping[str, Any]]
    ) -> FakeVectorIndex:
        """Build an index from ``vectors.json`` ``items`` entries."""
        index = cls()
        for item in items:
            index.add(
                key=str(item["key"]),
                vector=item["vector"],
                index=str(item.get("index") or index_for_key(item["key"])),
                metadata=item.get("metadata") or {},
            )
        return index

    def search(
        self,
        vector: Sequence[float],
        index: str,
        top_k: int,
        filters: Mapping[str, Any] | None = None,
    ) -> list[ScoredItem]:
        if top_k < 1:
            return []
        if index not in INDEX_NAMES:
            raise ValueError(
                f"Unknown index {index!r}; expected one of {INDEX_NAMES}"
            )
        keys: list[str] = []
        vectors: list[np.ndarray] = []
        metadatas: list[dict[str, Any]] = []
        for item in self._items.values():
            if item.index != index:
                continue
            if not _filters_match(item.metadata, filters):
                continue
            keys.append(item.key)
            vectors.append(item.vector)
            metadatas.append(item.metadata)
        if not keys:
            return []
        # Stable candidate order (not dict-insertion) + quantized distances
        # so BLAS ulps cannot swap near-ties across process runs.
        order_keys = np.argsort(np.asarray(keys))
        keys = [keys[i] for i in order_keys]
        metadatas = [metadatas[i] for i in order_keys]
        matrix = np.stack([vectors[i] for i in order_keys], axis=0)
        distances = np.round(
            cosine_distance(np.asarray(vector, dtype=np.float64), matrix),
            decimals=8,
        )
        # lexsort: last key is primary. Sort by distance, then key.
        order = np.lexsort((np.asarray(keys), distances))
        scored: list[ScoredItem] = []
        for pos in order[:top_k]:
            scored.append(
                ScoredItem(
                    key=keys[pos],
                    distance=float(distances[pos]),
                    metadata=metadatas[pos],
                )
            )
        return scored

    def get_vector(self, key: str) -> Sequence[float]:
        try:
            item = self._items[key]
        except KeyError:
            raise KeyError(f"no vector stored for {key!r}") from None
        return item.vector.tolist()

    def stored_items(self) -> list[_StoredItem]:
        """Stable dump order for fixture serialization."""
        return [self._items[key] for key in sorted(self._items)]

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, key: object) -> bool:
        return key in self._items
