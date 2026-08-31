"""Minimal interface shared by receipt vector-search backends."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol, TypeAlias, runtime_checkable

FilterValue: TypeAlias = str | int | float | bool
Vector: TypeAlias = Sequence[float]


@dataclass(frozen=True, slots=True)
class ScoredItem:
    """One nearest-neighbor result, ordered by ascending cosine distance."""

    key: str
    distance: float
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class VectorItem:
    """One stored vector used to seed an offline or real backend."""

    key: str
    index: str
    vector: Vector
    metadata: Mapping[str, object] = field(default_factory=dict)


@runtime_checkable
class VectorSearchClient(Protocol):
    """The complete retrieval surface needed by similarity consumers.

    Backends normalize their result scores to cosine distance: lower is
    closer, with the mathematical range 0 through 2. Filters are equality
    predicates joined with AND, matching the DynamoDB vector-index contract.
    """

    def search(
        self,
        vector: Vector,
        index: str,
        top_k: int,
        filters: Mapping[str, FilterValue] | None = None,
    ) -> list[ScoredItem]:
        """Return up to ``top_k`` nearest items from ``index``."""

    def get_vector(self, key: str) -> list[float]:
        """Return a defensive copy of the vector stored under ``key``."""


__all__ = [
    "FilterValue",
    "ScoredItem",
    "Vector",
    "VectorItem",
    "VectorSearchClient",
]
