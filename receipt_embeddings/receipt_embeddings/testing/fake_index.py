"""Exact-nearest-neighbor in-memory fake for :class:`VectorSearchClient`.

Unit-test double for the vector backends: brute-force exact cosine distance
in float64 over every stored vector, deterministic ordering (distance, then
key), equality-only metadata filters. SearchVectors has no local emulator and
moto cannot mock it — this fake is the unit tier of the test pyramid
(AGENT_PLAN "Test tiers").
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional, Sequence

import numpy as np

from receipt_embeddings.vector_client import LINES_INDEX, ScoredItem

# Distances are rounded before ordering so float noise cannot flip the
# (distance, key) sort between runs or platforms.
_TIE_DECIMALS = 12


class FakeVectorIndex:
    """In-memory exact cosine NN implementing ``VectorSearchClient``."""

    def __init__(self) -> None:
        self._items: dict[
            str, dict[str, tuple[np.ndarray, dict[str, Any]]]
        ] = {}

    def add(
        self,
        index: str,
        key: str,
        vector: Sequence[float],
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> None:
        """Store ``vector`` under ``key`` in ``index`` (upsert semantics)."""
        arr = np.asarray(list(vector), dtype=np.float64)
        if arr.ndim != 1 or arr.size == 0:
            raise ValueError(
                f"vector for {key!r} must be a non-empty 1-D sequence"
            )
        self._items.setdefault(index, {})[key] = (arr, dict(metadata or {}))

    def add_many(
        self,
        index: str,
        items: Iterable[
            tuple[str, Sequence[float], Optional[Mapping[str, Any]]]
        ],
    ) -> None:
        """Bulk :meth:`add` of ``(key, vector, metadata)`` triples."""
        for key, vector, metadata in items:
            self.add(index, key, vector, metadata)

    def count(self, index: str) -> int:
        """Number of vectors stored in ``index``."""
        return len(self._items.get(index, {}))

    # -- VectorSearchClient ------------------------------------------------

    def search(
        self,
        vector: Sequence[float],
        index: str,
        top_k: int,
        filters: Optional[Mapping[str, Any]] = None,
    ) -> list[ScoredItem]:
        """Exact cosine-distance top-k over the stored corpus."""
        if top_k <= 0:
            return []
        stored = self._items.get(index, {})
        if not stored:
            return []
        query = np.asarray(list(vector), dtype=np.float64)
        query_norm = float(np.linalg.norm(query))

        scored: list[ScoredItem] = []
        for key, (candidate, metadata) in stored.items():
            if filters and any(
                metadata.get(name) != value for name, value in filters.items()
            ):
                continue
            norm = float(np.linalg.norm(candidate)) * query_norm
            # Zero-norm on either side: undefined similarity, treated as 0
            # (distance 1.0) so degenerate vectors never rank as neighbors.
            similarity = float(candidate @ query) / norm if norm > 0.0 else 0.0
            distance = round(1.0 - similarity, _TIE_DECIMALS)
            scored.append(
                ScoredItem(key=key, distance=distance, metadata=metadata)
            )

        scored.sort(key=lambda item: (item.distance, item.key))
        return scored[:top_k]

    def get_vector(
        self, key: str, index: str = LINES_INDEX
    ) -> Optional[Sequence[float]]:
        """Return the stored vector for ``key``, or ``None``."""
        entry = self._items.get(index, {}).get(key)
        return None if entry is None else entry[0].tolist()


__all__ = ["FakeVectorIndex"]
