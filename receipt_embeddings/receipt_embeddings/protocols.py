"""Duck-typed seams for the vector-search and embedding-write stack.

``VectorSearchClient`` lives in ``vector_client.py`` (the retrieval
surface) and the entity ducks the request builder reads live in
``formatting`` (``LineLike``/``WordLike``). This module names the
*other* ducks the migration surfaces pass around: low-level DynamoDB
clients, the incumbent Chroma query/get wrapper, the
``DynamoClient``-shaped table handle the write paths hold, the
section duck used to denormalize ``section_type``, and the
writer/report seam the write paths inject in tests.

Boto3 request/response payloads stay ``Any``-valued on purpose — their
shapes are the AWS wire format, not ours — but every public signature
now names the seam it actually needs instead of ``Any``.

This module stays a dependency LEAF (stdlib typing only) so images that
install ``receipt_embeddings --no-deps`` for one helper keep importing.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:  # leaf module: never import siblings at runtime
    from receipt_embeddings.writer import EmbeddingWriteRequest

#: One DynamoDB item in wire format (``{"attr": {"S": ...}, ...}``).
DynamoItem = Mapping[str, Mapping[str, Any]]
#: A boto3 DynamoDB response payload.
DynamoResponse = Mapping[str, Any]


@runtime_checkable
class DynamoBatchClient(Protocol):
    """Writer seam: strongly-consistent BatchGet + BatchWrite."""

    def batch_get_item(self, **kwargs: Any) -> DynamoResponse:
        """Return existing keys for skip-existing."""

    def batch_write_item(self, **kwargs: Any) -> DynamoResponse:
        """Put embedding items; may return UnprocessedItems."""


@runtime_checkable
class DynamoQueryWriteClient(Protocol):
    """Sweeper seam: prefix query + BatchWrite deletes."""

    def query(self, **kwargs: Any) -> DynamoResponse:
        """Page embedding keys under a receipt prefix."""

    def batch_write_item(self, **kwargs: Any) -> DynamoResponse:
        """Delete embedding items; may return UnprocessedItems."""


@runtime_checkable
class DynamoEmbeddingClient(
    DynamoBatchClient, DynamoQueryWriteClient, Protocol
):
    """The full low-level surface the native write paths use.

    ``write_native_embeddings`` hands one client to both the sweeper
    (query + batch_write) and the writer (batch_get + batch_write), so
    the table handle's ``_client`` satisfies both seams.
    """


@runtime_checkable
class DynamoVectorLowLevelClient(Protocol):
    """SearchVectors + fetch-join BatchGet + GetItem for one vector."""

    def search_vectors(self, **kwargs: Any) -> DynamoResponse:
        """Run a DynamoDB vector-index query."""

    def get_item(self, **kwargs: Any) -> DynamoResponse:
        """Load one embedding item (vector retrieval)."""

    def batch_get_item(self, **kwargs: Any) -> DynamoResponse:
        """Fetch-join neighbor metadata."""


@runtime_checkable
class ChromaQueryClient(Protocol):
    """Incumbent ChromaClient query/get surface used by the adapter."""

    def query(self, **kwargs: Any) -> Mapping[str, Any]:
        """Nearest-neighbor query against a named collection."""

    def get(self, **kwargs: Any) -> Mapping[str, Any]:
        """Exact-id lookup, typically with embeddings included."""


class EmbeddingTableHandle(Protocol):
    """``DynamoClient``-shaped handle the native write paths hold."""

    table_name: str
    _client: DynamoEmbeddingClient


class SectionLike(Protocol):
    """Fields used to denormalize ``section_type`` onto line embeddings."""

    line_ids: Sequence[int]
    section_type: str


class WriteReportLike(Protocol):
    """The report surface the write paths read off any writer."""

    @property
    def written(self) -> int:
        """Count of items actually written."""

    @property
    def skipped_existing_keys(self) -> Sequence[str]:
        """Canonical keys skipped because they already exist."""

    @property
    def failures(self) -> Sequence[Any]:
        """Per-item failures (shape owned by the writer)."""


class EmbeddingWriterLike(Protocol):
    """The writer seam the write paths inject in tests."""

    def write(
        self, requests: "Sequence[EmbeddingWriteRequest]"
    ) -> WriteReportLike:
        """Persist the missing requests and report the outcome."""


__all__ = [
    "ChromaQueryClient",
    "DynamoBatchClient",
    "DynamoEmbeddingClient",
    "DynamoItem",
    "DynamoQueryWriteClient",
    "DynamoResponse",
    "DynamoVectorLowLevelClient",
    "EmbeddingTableHandle",
    "EmbeddingWriterLike",
    "SectionLike",
    "WriteReportLike",
]
