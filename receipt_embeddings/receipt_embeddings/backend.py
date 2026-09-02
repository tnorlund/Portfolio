"""Lazy ``VECTOR_BACKEND`` selection shared by vector-search consumers.

Promoted from ``receipt_upload.vector_search`` (card E2) so consumers that
cannot depend on receipt_upload — receipt_agent's QA tools and both MCP
servers — share the one selector instead of duplicating it.
``receipt_upload.vector_search`` re-exports this function unchanged.
"""

from __future__ import annotations

import logging
import os

from receipt_embeddings.chroma_client import ChromaVectorSearchClient
from receipt_embeddings.dynamo_client import (
    DEFAULT_REGION,
    DynamoVectorSearchClient,
)
from receipt_embeddings.protocols import (
    ChromaQueryClient,
    DynamoVectorLowLevelClient,
)
from receipt_embeddings.vector_client import VectorSearchClient

logger = logging.getLogger(__name__)


def vector_search_client(
    chroma_client: VectorSearchClient | ChromaQueryClient | None,
    *,
    vector_client: VectorSearchClient | None = None,
    dynamodb_client: DynamoVectorLowLevelClient | None = None,
    table_name: str | None = None,
) -> VectorSearchClient:
    """Return the configured vector backend, defaulting to DynamoDB.

    ``vector_client`` is an explicit injection seam for tests and callers
    that already own a backend. For the ``dynamodb`` backend, callers that
    hold a configured Dynamo connection thread it through ``table_name``
    (and optionally ``dynamodb_client``, a low-level boto3 client) so the
    search targets the SAME table as the rest of their session; the
    ``from_env`` fallback — which can resolve to the hard-coded dev
    default — is a documented last resort and logs the table it chose
    (E3 review P1-3). Backend construction stays lazy so the default
    Chroma path never initializes an AWS client.
    """

    if vector_client is not None:
        return vector_client
    if isinstance(chroma_client, VectorSearchClient):
        return chroma_client

    backend = os.environ.get("VECTOR_BACKEND", "dynamodb").strip().lower()
    if backend == "dynamodb":
        if table_name:
            if dynamodb_client is None:
                import boto3

                region = os.environ.get(
                    "AWS_REGION",
                    os.environ.get("AWS_DEFAULT_REGION", DEFAULT_REGION),
                )
                dynamodb_client = boto3.client("dynamodb", region_name=region)
            return DynamoVectorSearchClient(dynamodb_client, table_name)
        client = DynamoVectorSearchClient.from_env()
        logger.warning(
            "VECTOR_BACKEND=dynamodb with no caller-provided table; "
            "falling back to environment configuration (table %s)",
            getattr(client, "table_name", "<unknown>"),
        )
        return client
    if backend == "chroma":
        if chroma_client is None:
            # Same exception type/message the adapter's own guard raises,
            # so the widened Optional parameter changes no behavior.
            raise TypeError("chroma_client must provide query()")
        return ChromaVectorSearchClient(chroma_client)
    raise ValueError("VECTOR_BACKEND must be either 'chroma' or 'dynamodb'")


__all__ = ["vector_search_client"]
