"""VectorSearchClient adapters used by capture / evaluate.

Chroma and Dynamo live outside ``receipt_embeddings`` so that package has
zero ``chromadb`` imports (Round B gate) and Round A never creates vector
indexes.
"""

from __future__ import annotations

import os
from typing import Any, Mapping, Sequence

from scripts.similarity_harness.metrics import estimate_request_units

from receipt_embeddings.testing import FakeVectorIndex
from receipt_embeddings.vector_client import (
    DISTANCE_ATOL,
    INDEX_LINES,
    INDEX_WORDS,
    ScoredItem,
    as_float_array,
    normalize_index_name,
)


def _chroma_rows(result: Mapping[str, Any], field: str) -> list[Any]:
    rows = result.get(field) or [[]]
    if not rows:
        return []
    return list(rows[0] or [])


class ReplayVectorClient:
    """Return captured neighbors for query vectors in a golden fixture.

    Used for ``evaluate.py --backend chroma`` self-parity when Chroma Cloud
    credentials are absent: scoring the captured answers against themselves
    yields recall/agreement of 1.0. Live Chroma uses :class:`ChromaVectorClient`.
    """

    def __init__(self, queries: Sequence[Mapping[str, Any]]) -> None:
        self._entries: list[tuple[Any, str, list[ScoredItem]]] = []
        self.last_request_units: float | None = 0.0
        for query in queries:
            vector = as_float_array(query["query_vector"])
            neighbors = [
                ScoredItem(
                    key=str(item["key"]),
                    score=float(item["distance"]),
                    metadata=dict(item.get("metadata") or {}),
                )
                for item in query.get("neighbors") or []
            ]
            self._entries.append(
                (vector, normalize_index_name(query["index"]), neighbors)
            )
        self._vectors: dict[str, tuple[float, ...]] = {}
        for query in queries:
            key = str(query.get("query_key") or "")
            if key:
                self._vectors[key] = tuple(
                    float(x) for x in query["query_vector"]
                )

    def search(
        self,
        vector: Sequence[float],
        index: str,
        top_k: int,
        filters: Mapping[str, Any] | None = None,
    ) -> list[ScoredItem]:
        del filters
        canonical = normalize_index_name(index)
        query = as_float_array(vector)
        for stored, stored_index, neighbors in self._entries:
            if stored_index != canonical:
                continue
            if stored.size != query.size:
                continue
            if float(max(abs(stored - query))) <= DISTANCE_ATOL:
                self.last_request_units = 0.0
                return neighbors[:top_k]
        self.last_request_units = 0.0
        return []

    def get_vector(self, key: str) -> Sequence[float] | None:
        return self._vectors.get(key)


class ChromaVectorClient:
    """Read-only ``VectorSearchClient`` over ``receipt_chroma.ChromaClient``."""

    def __init__(self, chroma_client: Any) -> None:
        self._chroma = chroma_client
        self.last_request_units: float | None = 0.0

    def search(
        self,
        vector: Sequence[float],
        index: str,
        top_k: int,
        filters: Mapping[str, Any] | None = None,
    ) -> list[ScoredItem]:
        collection = normalize_index_name(index)
        where = dict(filters) if filters else None
        result = self._chroma.query(
            collection_name=collection,
            query_embeddings=[list(vector)],
            n_results=top_k,
            where=where,
            include=["metadatas", "distances"],
        )
        ids = _chroma_rows(result, "ids")
        distances = _chroma_rows(result, "distances")
        metadatas = _chroma_rows(result, "metadatas")
        items: list[ScoredItem] = []
        for key, distance, metadata in zip(
            ids, distances, metadatas, strict=False
        ):
            items.append(
                ScoredItem(
                    key=str(key),
                    score=float(distance),
                    metadata=dict(metadata or {}),
                )
            )
        self.last_request_units = 0.0
        return items[:top_k]

    def get_vector(self, key: str) -> Sequence[float] | None:
        for collection in (INDEX_LINES, INDEX_WORDS):
            try:
                result = self._chroma.get(
                    collection_name=collection,
                    ids=[key],
                    include=["embeddings"],
                )
            except Exception:  # noqa: BLE001 — collection may not exist
                continue
            embeddings = result.get("embeddings") or []
            if embeddings and embeddings[0] is not None:
                return [float(x) for x in embeddings[0]]
        return None


class DynamoVectorClient:
    """Read-only SearchVectors adapter. Never creates or updates indexes.

    Round A only needs the client shape so ``evaluate.py --backend dynamo``
    exists. It refuses to call ``UpdateTable`` / ``CreateTable``. Without
    AWS credentials or a boto3 that exposes ``search_vectors``, ``search``
    raises ``RuntimeError``.
    """

    # Physical index names from SPEC §3.2. Not created in this round.
    PHYSICAL_INDEX = {
        INDEX_LINES: "lines-vectors",
        INDEX_WORDS: "words-vectors",
    }

    def __init__(
        self,
        *,
        table_name: str | None = None,
        client: Any | None = None,
    ) -> None:
        self.table_name = table_name or os.environ.get(
            "DYNAMODB_TABLE_NAME", "ReceiptsTable-dc5be22"
        )
        self._client = client
        self.last_request_units: float | None = None

    def _boto(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "boto3 is required for --backend dynamo"
            ) from exc
        self._client = boto3.client("dynamodb")
        return self._client

    def search(
        self,
        vector: Sequence[float],
        index: str,
        top_k: int,
        filters: Mapping[str, Any] | None = None,
    ) -> list[ScoredItem]:
        boto = self._boto()
        if not hasattr(boto, "search_vectors"):
            raise RuntimeError(
                "boto3 client has no search_vectors (need boto3>=1.43.64). "
                "Round A does not create DynamoDB vector indexes."
            )
        canonical = normalize_index_name(index)
        kwargs: dict[str, Any] = {
            "TableName": self.table_name,
            "IndexName": self.PHYSICAL_INDEX[canonical],
            "SearchVector": [float(x) for x in vector],
            "TopK": top_k,
            "ReturnConsumedCapacity": "TOTAL",
        }
        if filters:
            names = {}
            values = {}
            clauses = []
            for offset, (key, value) in enumerate(filters.items()):
                name_token = f"#f{offset}"
                value_token = f":f{offset}"
                names[name_token] = key
                values[value_token] = value
                clauses.append(f"{name_token} = {value_token}")
            kwargs["SearchConditionExpression"] = " AND ".join(clauses)
            kwargs["ExpressionAttributeNames"] = names
            kwargs["ExpressionAttributeValues"] = values
        response = boto.search_vectors(**kwargs)
        consumed = (response.get("ConsumedCapacity") or {}).get(
            "ReadRequestUnits"
        )
        self.last_request_units = estimate_request_units(top_k, consumed)
        items: list[ScoredItem] = []
        for row in response.get("Items") or response.get("Results") or []:
            key = str(row.get("key") or row.get("Id") or row.get("SK") or "")
            score = float(row.get("Distance", row.get("Score", 0.0)))
            metadata = dict(row.get("Metadata") or row.get("metadata") or {})
            items.append(ScoredItem(key=key, score=score, metadata=metadata))
        return items[:top_k]

    def get_vector(self, key: str) -> Sequence[float] | None:
        boto = self._boto()
        # Embedding items live under the RECEIPT# SK prefix (SPEC §3.1).
        # Round A does not write them; this is a point-read only.
        if not hasattr(boto, "get_item"):
            return None
        # key is IMAGE#{id}#RECEIPT#… — split into PK / SK at the receipt.
        parts = key.split("#")
        if len(parts) < 4:
            return None
        pk = f"IMAGE#{parts[1]}"
        sk = "#".join(parts[2:]) + "#EMBEDDING"
        response = boto.get_item(
            TableName=self.table_name,
            Key={"PK": {"S": pk}, "SK": {"S": sk}},
        )
        item = response.get("Item") or {}
        vector_attr = item.get("vector") or {}
        numbers = vector_attr.get("L") or []
        if not numbers:
            return None
        return [float(entry.get("N", 0.0)) for entry in numbers]


def fake_from_corpus(corpus: Mapping[str, Any]) -> FakeVectorIndex:
    index = FakeVectorIndex()
    for item in corpus.get("items") or []:
        index.upsert(
            key=str(item["key"]),
            vector=item["vector"],
            index=str(item["index"]),
            metadata=item.get("metadata") or {},
        )
    return index
