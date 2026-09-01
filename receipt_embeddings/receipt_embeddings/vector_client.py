"""Minimal interface shared by receipt vector-search backends."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol, TypeAlias, runtime_checkable

FilterValue: TypeAlias = str | int | float | bool
Vector: TypeAlias = Sequence[float]

# The neighbor-metadata fields the real MerchantResolver reads from every
# line-index search result. The Chroma path's metadata shape is the contract
# (Round C fetch-join ruling); every backend must surface exactly these keys
# for a neighbor, with the two normalized_* keys present only when the
# neighbor row carries the corresponding anchor — matching Chroma's sparse
# anchor enrichment.
RESOLVER_NEIGHBOR_METADATA_KEYS = frozenset(
    {
        "image_id",
        "receipt_id",
        "merchant_name",
        "normalized_phone_10",
        "normalized_full_address",
    }
)


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
    "RESOLVER_NEIGHBOR_METADATA_KEYS",
    "ScoredItem",
    "Vector",
    "VectorItem",
    "VectorSearchClient",
]
