"""Minimal vector-search client used by similarity consumers.

The protocol is the swap point between FakeVectorIndex (exact cosine, local
unit tests), live Chroma, and DynamoDB SearchVectors. Consumers call only
``search`` and ``get_vector``; backends are interchangeable.

Distance is cosine distance as specified in SPEC §3.5a:

    cosine_distance = 1 - cosine_similarity    (range [0, 2], lower closer)

That is the same quantity Chroma returns for a COSINE space and DynamoDB
returns for a COSINE vector index. Merchant-resolution thresholds in
``receipt_upload.merchant_resolution.resolver`` convert distance to a
similarity with ``1 - distance / 2`` (L2-on-unit-sphere scaling); the
harness stores raw cosine distance and applies that conversion only when
deriving a tier/decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

import numpy as np
from numpy.typing import NDArray

INDEX_LINES = "lines"
INDEX_WORDS = "words"

# Spec §4a capability names. Both aliases resolve to the Chroma collection
# names consumers use today so Round A–E ports do not rename at the call site.
INDEX_ALIASES: Mapping[str, str] = {
    INDEX_LINES: INDEX_LINES,
    INDEX_WORDS: INDEX_WORDS,
    "line-embeddings": INDEX_LINES,
    "word-embeddings": INDEX_WORDS,
    "lines-vectors": INDEX_LINES,
    "words-vectors": INDEX_WORDS,
}

# Two capture runs minutes apart must match neighbor ids and distances
# within this absolute tolerance. FakeVectorIndex is bit-stable given the
# same corpus; live ANN backends may jitter at this scale.
DISTANCE_ATOL = 1e-6


def normalize_index_name(index: str) -> str:
    """Map spec / Dynamo aliases onto the canonical lines|words names."""
    try:
        return INDEX_ALIASES[index]
    except KeyError as exc:
        raise ValueError(
            f"Unknown vector index {index!r}; expected one of "
            f"{sorted(INDEX_ALIASES)}"
        ) from exc


def as_float_array(vector: Sequence[float]) -> NDArray[np.float64]:
    """Copy ``vector`` into a 1-d float64 ndarray."""
    array = np.asarray(vector, dtype=np.float64)
    if array.ndim != 1:
        raise ValueError(f"Embedding must be 1-d; got shape {array.shape}")
    if array.size == 0:
        raise ValueError("Embedding must be non-empty")
    return array


def cosine_distance(
    query: Sequence[float],
    document: Sequence[float],
) -> float:
    """Return cosine distance in ``[0, 2]`` (1 - cosine similarity).

    A zero-norm vector is treated as maximally distant (2.0) so it never
    ranks above a real embedding. Cosine is clipped to ``[-1, 1]`` before
    the subtract so float noise cannot produce distances outside the range.
    """
    left = as_float_array(query)
    right = as_float_array(document)
    if left.size != right.size:
        raise ValueError(
            f"Embedding length mismatch: {left.size} vs {right.size}"
        )
    left_norm = float(np.linalg.norm(left))
    right_norm = float(np.linalg.norm(right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 2.0
    similarity = float(np.dot(left, right) / (left_norm * right_norm))
    similarity = float(np.clip(similarity, -1.0, 1.0))
    return float(1.0 - similarity)


def cosine_distances(
    query: Sequence[float],
    documents: NDArray[np.float64],
) -> NDArray[np.float64]:
    """Vectorized cosine distances from ``query`` to each row of ``documents``.

    ``documents`` is ``(n, dim)``. Empty input returns an empty 1-d array.
    """
    if documents.size == 0:
        return np.empty((0,), dtype=np.float64)
    query_array = as_float_array(query)
    if documents.ndim != 2:
        raise ValueError(
            f"Document matrix must be 2-d; got shape {documents.shape}"
        )
    if documents.shape[1] != query_array.size:
        raise ValueError(
            "Embedding length mismatch: "
            f"query {query_array.size} vs corpus {documents.shape[1]}"
        )
    query_norm = float(np.linalg.norm(query_array))
    doc_norms = np.linalg.norm(documents, axis=1)
    distances = np.full(documents.shape[0], 2.0, dtype=np.float64)
    valid = (query_norm > 0.0) & (doc_norms > 0.0)
    if not np.any(valid):
        return distances
    dots = documents[valid] @ query_array
    similarities = dots / (doc_norms[valid] * query_norm)
    similarities = np.clip(similarities, -1.0, 1.0)
    distances[valid] = 1.0 - similarities
    return distances


@dataclass(frozen=True)
class ScoredItem:
    """One neighbor returned by :meth:`VectorSearchClient.search`.

    ``score`` is cosine distance (lower is closer). ``vector`` is optional so
    backends that only project metadata (Dynamo INCLUDE) still satisfy the
    protocol; callers that need the embedding use ``get_vector``.
    """

    key: str
    score: float
    metadata: Mapping[str, Any] = field(default_factory=dict)
    vector: tuple[float, ...] | None = None


@runtime_checkable
class VectorSearchClient(Protocol):
    """Injectable nearest-neighbor + point-lookup surface.

    Implementations: ``FakeVectorIndex`` (exact, local), the Chroma adapter
    in ``scripts/similarity_harness``, and the DynamoDB SearchVectors adapter
    (Round C/D). Swapping backends must not change consumer call sites.
    """

    def search(
        self,
        vector: Sequence[float],
        index: str,
        top_k: int,
        filters: Mapping[str, Any] | None = None,
    ) -> list[ScoredItem]:
        """Return the ``top_k`` nearest items in ``index``.

        ``filters`` are equality predicates on stored metadata (AND). An
        item missing a filter key does not match. ``top_k`` must be >= 1.
        Results are sorted by cosine distance ascending, then by key for
        ties so ranking is deterministic.
        """

    def get_vector(self, key: str) -> Sequence[float] | None:
        """Return the stored embedding for ``key``, or ``None`` if missing.

        Keys are Chroma-shaped ids (``IMAGE#…#LINE#…`` / ``…#WORD#…``) and
        are unique across indexes, so the lookup does not take an index.
        """
