"""Duck-typed seams for the vector-search and embedding-write stack.

``VectorSearchClient`` lives in ``vector_client.py`` (the retrieval
surface). This module names the *other* ducks: low-level DynamoDB
clients, the Chroma query/get wrapper, the dual-write table handle,
and the line/word/section attributes the write-request builder reads.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol, runtime_checkable

DynamoItem = Mapping[str, Mapping[str, object]]
DynamoResponse = Mapping[str, object]


@runtime_checkable
class DynamoBatchClient(Protocol):
    """Writer seam: strongly-consistent BatchGet + BatchWrite."""

    def batch_get_item(self, **kwargs: object) -> DynamoResponse:
        """Return existing keys for skip-existing."""

    def batch_write_item(self, **kwargs: object) -> DynamoResponse:
        """Put embedding items; may return UnprocessedItems."""


@runtime_checkable
class DynamoQueryWriteClient(Protocol):
    """Sweeper seam: prefix query + BatchWrite deletes."""

    def query(self, **kwargs: object) -> DynamoResponse:
        """Page embedding keys under a receipt prefix."""

    def batch_write_item(self, **kwargs: object) -> DynamoResponse:
        """Delete embedding items; may return UnprocessedItems."""


@runtime_checkable
class DynamoVectorLowLevelClient(Protocol):
    """SearchVectors + fetch-join BatchGet + GetItem for one vector."""

    def search_vectors(self, **kwargs: object) -> DynamoResponse:
        """Run a DynamoDB vector-index query."""

    def get_item(self, **kwargs: object) -> DynamoResponse:
        """Load one embedding item (vector retrieval)."""

    def batch_get_item(self, **kwargs: object) -> DynamoResponse:
        """Fetch-join neighbor metadata."""


@runtime_checkable
class ChromaQueryClient(Protocol):
    """Incumbent ChromaClient query/get surface used by the adapter."""

    def query(self, **kwargs: object) -> DynamoResponse:
        """Nearest-neighbor query against a named collection."""

    def get(self, **kwargs: object) -> DynamoResponse:
        """Exact-id lookup, typically with embeddings included."""


@runtime_checkable
class EmbeddingTableHandle(Protocol):
    """``DynamoClient``-shaped handle the dual-write path already holds."""

    table_name: str
    _client: DynamoBatchClient


class EmbeddingLine(Protocol):
    """Fields the write-request builder reads from a receipt line."""

    line_id: object
    text: str


class EmbeddingWord(Protocol):
    """Fields the write-request builder reads from a receipt word."""

    line_id: object
    word_id: object
    text: str


class EmbeddingSection(Protocol):
    """Fields used to denormalize ``section_type`` onto line embeddings."""

    line_ids: Sequence[object]
    section_type: object


class ReceiptPlaceLike(Protocol):
    """Optional ingest place: merchant_name / place_id may be missing."""

    merchant_name: object
    place_id: object


__all__ = [
    "ChromaQueryClient",
    "DynamoBatchClient",
    "DynamoItem",
    "DynamoQueryWriteClient",
    "DynamoResponse",
    "DynamoVectorLowLevelClient",
    "EmbeddingLine",
    "EmbeddingSection",
    "EmbeddingTableHandle",
    "EmbeddingWord",
    "ReceiptPlaceLike",
]
