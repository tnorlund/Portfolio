"""Exact nearest-neighbor fake for :class:`VectorSearchClient`.

SearchVectors has no local emulator and moto cannot mock it (SPEC §6 H).
``FakeVectorIndex`` is the unit-test stand-in: numpy exact cosine over an
in-memory corpus, deterministic tie-break by key. It is not an ANN
approximation — recall against a corpus it owns is 1.0 by construction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
from numpy.typing import NDArray

from receipt_embeddings.vector_client import (
    ScoredItem,
    cosine_distances,
    normalize_index_name,
)


@dataclass
class _IndexedItem:
    key: str
    index: str
    vector: NDArray[np.float64]
    metadata: dict[str, Any] = field(default_factory=dict)


def _matches_filters(
    metadata: Mapping[str, Any],
    filters: Mapping[str, Any] | None,
) -> bool:
    if not filters:
        return True
    for key, expected in filters.items():
        if key not in metadata or metadata[key] != expected:
            return False
    return True


class FakeVectorIndex:
    """In-memory exact cosine index implementing ``VectorSearchClient``.

    Items are stored per canonical index name (``lines`` / ``words``).
    ``upsert`` replaces an existing key. ``search`` scans every item in
    the requested index — fine for fixture-sized corpora, not a production
    backend.
    """

    def __init__(
        self,
        items: (
            Iterable[
                tuple[str, Sequence[float], str, Mapping[str, Any] | None]
            ]
            | None
        ) = None,
    ) -> None:
        self._items: dict[str, _IndexedItem] = {}
        if items:
            for key, vector, index, metadata in items:
                self.upsert(
                    key=key,
                    vector=vector,
                    index=index,
                    metadata=metadata,
                )

    def upsert(
        self,
        *,
        key: str,
        vector: Sequence[float],
        index: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        """Insert or replace one item. ``key`` is unique across indexes."""
        if not key:
            raise ValueError("item key must be non-empty")
        canonical = normalize_index_name(index)
        array = np.asarray(vector, dtype=np.float64)
        if array.ndim != 1 or array.size == 0:
            raise ValueError(
                f"Embedding for {key!r} must be a non-empty 1-d vector"
            )
        existing = self._items.get(key)
        if existing is not None and existing.index != canonical:
            raise ValueError(
                f"key {key!r} already stored on index {existing.index!r}"
            )
        self._items[key] = _IndexedItem(
            key=key,
            index=canonical,
            vector=np.array(array, copy=True),
            metadata=dict(metadata or {}),
        )

    def __len__(self) -> int:
        return len(self._items)

    def keys(self) -> list[str]:
        return list(self._items)

    def search(
        self,
        vector: Sequence[float],
        index: str,
        top_k: int,
        filters: Mapping[str, Any] | None = None,
    ) -> list[ScoredItem]:
        if top_k < 1:
            raise ValueError(f"top_k must be >= 1; got {top_k}")
        canonical = normalize_index_name(index)
        candidates = [
            item
            for item in self._items.values()
            if item.index == canonical
            and _matches_filters(item.metadata, filters)
        ]
        if not candidates:
            return []
        matrix = np.stack([item.vector for item in candidates])
        distances = cosine_distances(vector, matrix)
        # Stable order: distance ascending, then key. np.argsort is not
        # keyed, so decorate explicitly.
        ranked = sorted(
            zip(distances.tolist(), candidates, strict=True),
            key=lambda pair: (pair[0], pair[1].key),
        )
        scored: list[ScoredItem] = []
        for distance, item in ranked[:top_k]:
            scored.append(
                ScoredItem(
                    key=item.key,
                    score=float(distance),
                    metadata=dict(item.metadata),
                    vector=tuple(float(x) for x in item.vector.tolist()),
                )
            )
        return scored

    def get_vector(self, key: str) -> Sequence[float] | None:
        item = self._items.get(key)
        if item is None:
            return None
        return tuple(float(x) for x in item.vector.tolist())
