"""Backend-agnostic vector search interface.

Every consumer of receipt-embedding similarity (merchant resolution, section
verification, semantic search, consensus tools) codes against
:class:`VectorSearchClient` and nothing else. Backends — live Chroma during
the migration, DynamoDB SearchVectors after it, :class:`FakeVectorIndex` in
unit tests — swap without consumer changes.

Semantics shared by all backends:

- ``search`` returns items ranked by **cosine distance** (``1 - cosine
  similarity``, range 0-2, lower is closer). Chroma's cosine space and
  DynamoDB's COSINE metric both return this quantity, so thresholds carry
  across backends unchanged.
- ``filters`` are equality-only (the DynamoDB constraint; Chroma ``where``
  equality maps onto it). Anything richer is post-filtered client-side.
- ``index`` names a capability-scoped index (``lines`` / ``words``), never a
  technology (SPEC §4a).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import (
    Any,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    runtime_checkable,
)

# Canonical index names. These match today's Chroma collection names and the
# planned DynamoDB vector indexes (`lines-vectors` / `words-vectors` map onto
# them at the backend adapter, not in consumer code).
LINES_INDEX = "lines"
WORDS_INDEX = "words"


@dataclass(frozen=True)
class ScoredItem:
    """One search hit: a stored vector's key, distance, and metadata.

    ``distance`` is cosine distance (lower = closer). ``metadata`` carries the
    backend's projected attributes (merchant_name, image/receipt/line ids,
    ...); consumers must treat it as read-only.
    """

    key: str
    distance: float
    metadata: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class VectorSearchClient(Protocol):
    """Minimal retrieval surface every backend implements."""

    def search(
        self,
        vector: Sequence[float],
        index: str,
        top_k: int,
        filters: Optional[Mapping[str, Any]] = None,
    ) -> list[ScoredItem]:
        """Return the ``top_k`` nearest stored items to ``vector``.

        Results are sorted ascending by cosine distance. ``filters`` is an
        equality-only metadata filter (all pairs must match) or ``None``.
        """
        raise NotImplementedError  # pragma: no cover - protocol

    def get_vector(
        self, key: str, index: str = LINES_INDEX
    ) -> Optional[Sequence[float]]:
        """Return the stored vector for ``key``, or ``None`` if absent."""
        raise NotImplementedError  # pragma: no cover - protocol


__all__ = [
    "LINES_INDEX",
    "WORDS_INDEX",
    "ScoredItem",
    "VectorSearchClient",
]
