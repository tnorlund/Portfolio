"""DynamoDB SearchVectors backend — not implemented in Round A.

Round A must not create vector indexes (5-index budget; judge-scripted)
and must not write the receipts table. The client exists so
``evaluate.py --backend dynamo`` is a stable flag for later rounds.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from receipt_embeddings.vector_client import ScoredItem


class DynamoVectorSearchClient:
    """Stub :class:`VectorSearchClient`. Raises on every call."""

    def search(
        self,
        vector: Sequence[float],
        index: str,
        top_k: int,
        filters: Mapping[str, Any] | None = None,
    ) -> list[ScoredItem]:
        raise NotImplementedError(
            "DynamoDB SearchVectors ships in Round C/D. Round A does "
            "not create vector indexes or write the receipts table."
        )

    def get_vector(self, key: str) -> Sequence[float]:
        raise NotImplementedError(
            "DynamoDB GetItem for embedding items ships in Round C."
        )
