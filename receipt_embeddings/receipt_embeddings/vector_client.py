"""Vector search interface used by every similarity consumer.

Consumers depend only on :meth:`VectorSearchClient.search` and
:meth:`VectorSearchClient.get_vector`. Fake, Chroma, and Dynamo backends
swap behind this protocol without consumer changes.

Index names are capability names (SPEC §4a), not vendor names:
``line-embeddings`` and ``word-embeddings``. Adapters map those onto
Chroma collections (``lines`` / ``words``) or DynamoDB vector indexes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

# Capability names from SPEC §4a. Not "chroma" / "SearchVectors".
LINE_EMBEDDINGS_INDEX = "line-embeddings"
WORD_EMBEDDINGS_INDEX = "word-embeddings"

INDEX_NAMES = (LINE_EMBEDDINGS_INDEX, WORD_EMBEDDINGS_INDEX)

# Cosine distance = 1 − cosine similarity, range [0, 2], lower is closer.
# Same quantity Chroma returns today and DynamoDB COSINE returns (SPEC §3.5a).
COSINE_DISTANCE_MIN = 0.0
COSINE_DISTANCE_MAX = 2.0


def line_item_key(image_id: str, receipt_id: int, line_id: int) -> str:
    """Stable key for a visual-row line embedding (matches Chroma line ids)."""
    return f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}"


def word_item_key(
    image_id: str, receipt_id: int, line_id: int, word_id: int
) -> str:
    """Stable key for a word embedding (matches Chroma word ids)."""
    return (
        f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}"
        f"#LINE#{line_id:05d}#WORD#{word_id:05d}"
    )


def index_for_key(key: str) -> str:
    """Infer the embeddings index from a stored item key."""
    if "#WORD#" in key:
        return WORD_EMBEDDINGS_INDEX
    return LINE_EMBEDDINGS_INDEX


@dataclass(frozen=True)
class ScoredItem:
    """One neighbor from :meth:`VectorSearchClient.search`.

    ``distance`` is cosine distance in ``[0, 2]`` (lower = closer).
    """

    key: str
    distance: float
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", dict(self.metadata))


@runtime_checkable
class VectorSearchClient(Protocol):
    """Minimal retrieval surface. Do not add methods without a consumer."""

    def search(
        self,
        vector: Sequence[float],
        index: str,
        top_k: int,
        filters: Mapping[str, Any] | None = None,
    ) -> list[ScoredItem]:
        """Return the ``top_k`` nearest items in ``index``, closest first.

        ``filters`` are equality-only (DynamoDB inline-filter semantics).
        Ties must be broken by ``key`` ascending so ranking is deterministic.
        """
        ...

    def get_vector(self, key: str) -> Sequence[float]:
        """Return the stored embedding for ``key``.

        Raises:
            KeyError: if ``key`` is not in the index.
        """
        ...
