"""Deterministic, exact cosine-nearest-neighbor vector index."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from numbers import Real

import numpy as np
from numpy.typing import NDArray

from receipt_embeddings.vector_client import (
    FilterValue,
    ScoredItem,
    VectorItem,
)

_MAX_TOP_K = 100


@dataclass(frozen=True, slots=True)
class _StoredItem:
    key: str
    vector: NDArray[np.float64]
    norm: float
    metadata: dict[str, object]


def _as_vector(vector: Sequence[float], *, name: str) -> NDArray[np.float64]:
    array = np.asarray(vector, dtype=np.float64)
    if array.ndim != 1 or array.size == 0:
        raise ValueError(f"{name} must be a non-empty one-dimensional vector")
    if not np.isfinite(array).all():
        raise ValueError(f"{name} must contain only finite numbers")
    return array.copy()


def _filter_equal(actual: object, expected: FilterValue) -> bool:
    """Match DynamoDB equality without treating bool as the number 0/1."""

    if isinstance(actual, bool) or isinstance(expected, bool):
        return type(actual) is type(expected) and actual == expected
    if isinstance(actual, Real) and isinstance(expected, Real):
        return float(actual) == float(expected)
    return type(actual) is type(expected) and actual == expected


class FakeVectorIndex:
    """An in-memory exact-NN implementation of ``VectorSearchClient``.

    Results are sorted by cosine distance and then key. The key tie-breaker
    makes byte-for-byte fixture and test results stable across runs and NumPy
    versions.
    """

    def __init__(self, items: Iterable[VectorItem] = ()) -> None:
        self._indexes: dict[str, list[_StoredItem]] = {}
        self._by_key: dict[str, _StoredItem] = {}
        self._dimension: int | None = None
        # Optional harness telemetry. These are not part of the consumer
        # protocol; they let evaluate.py report a complete zero-cost offline
        # scorecard without timing noise.
        self.last_latency_ms = 0.0
        self.last_request_units = 0.0
        for item in items:
            self.add(item)

    def add(self, item: VectorItem) -> None:
        """Add one unique item while enforcing a single vector dimension."""

        if not item.key:
            raise ValueError("item key must not be empty")
        if not item.index:
            raise ValueError("item index must not be empty")
        if item.key in self._by_key:
            raise ValueError(f"duplicate vector key: {item.key}")

        vector = _as_vector(item.vector, name=f"vector for {item.key!r}")
        if self._dimension is None:
            self._dimension = int(vector.size)
        elif vector.size != self._dimension:
            raise ValueError(
                f"vector for {item.key!r} has dimension {vector.size}; "
                f"expected {self._dimension}"
            )
        norm = float(np.linalg.norm(vector))
        if norm == 0.0:
            raise ValueError(f"vector for {item.key!r} must not be zero")

        stored = _StoredItem(
            key=item.key,
            vector=vector,
            norm=norm,
            metadata=dict(item.metadata),
        )
        self._indexes.setdefault(item.index, []).append(stored)
        self._by_key[item.key] = stored

    def search(
        self,
        vector: Sequence[float],
        index: str,
        top_k: int,
        filters: Mapping[str, FilterValue] | None = None,
    ) -> list[ScoredItem]:
        """Return exact cosine neighbors with deterministic tie ordering."""

        if isinstance(top_k, bool) or not isinstance(top_k, int):
            raise TypeError("top_k must be an integer")
        if not 1 <= top_k <= _MAX_TOP_K:
            raise ValueError(f"top_k must be between 1 and {_MAX_TOP_K}")

        # Same contract as build_chroma_where: filters are flat equality
        # predicates; where-operator syntax never reaches a backend.
        for filter_key in filters or ():
            if filter_key.startswith("$"):
                raise ValueError(
                    f"filters are flat equality predicates; operator key "
                    f"{filter_key!r} belongs to the adapter, not the caller"
                )

        query = _as_vector(vector, name="query vector")
        if self._dimension is not None and query.size != self._dimension:
            raise ValueError(
                f"query vector has dimension {query.size}; "
                f"expected {self._dimension}"
            )
        query_norm = float(np.linalg.norm(query))
        if query_norm == 0.0:
            raise ValueError("query vector must not be zero")

        candidates = [
            item
            for item in self._indexes.get(index, [])
            if not filters
            or all(
                key in item.metadata
                and _filter_equal(item.metadata[key], expected)
                for key, expected in filters.items()
            )
        ]
        ranked = []
        for item in candidates:
            similarity = float(np.dot(query, item.vector)) / (
                query_norm * item.norm
            )
            # Numerical drift can put theoretically bounded cosine similarity
            # a few ulps outside [-1, 1]. Clamp before converting to distance.
            distance = 1.0 - max(-1.0, min(1.0, similarity))
            ranked.append((distance, item.key, item))
        ranked.sort(key=lambda value: (value[0], value[1]))
        return [
            ScoredItem(
                key=item.key,
                distance=distance,
                metadata=dict(item.metadata),
            )
            for distance, _, item in ranked[:top_k]
        ]

    def get_vector(self, key: str) -> list[float]:
        """Return a copy so callers cannot mutate the fake's stored corpus."""

        try:
            return [float(value) for value in self._by_key[key].vector]
        except KeyError as exc:
            raise KeyError(f"unknown vector key: {key}") from exc


__all__ = ["FakeVectorIndex"]
