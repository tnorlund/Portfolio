"""DynamoDB ``SearchVectors`` implementation of ``VectorSearchClient``."""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from receipt_dynamo.entities.dynamodb_utils import parse_dynamodb_map

from receipt_embeddings.service_limits import (
    EMBEDDING_DIMENSIONS,
    INDEX_VECTOR_ATTRIBUTES,
    LINE_INDEX,
    MAX_BATCH_GET_ITEMS,
    VECTOR_SEARCH_USD_PER_GB,
    WORD_INDEX,
    build_search_filter,
    normalize_vector,
    physical_index_name,
    search_vector_attribute_values,
    validate_top_k,
)
from receipt_embeddings.vector_client import FilterValue, ScoredItem

DEFAULT_TABLE_NAME = "ReceiptsTable-dc5be22"
DEFAULT_REGION = "us-east-1"
_CANONICAL_KEY = re.compile(
    r"^IMAGE#(?P<image_id>[^#]+)#RECEIPT#(?P<receipt_id>[0-9]+)#"
    r"LINE#(?P<line_id>[0-9]+)(?:#WORD#(?P<word_id>[0-9]+))?$"
)

# Fetch-join ruling (spec §3.2/§3.3 amendment): the line index's projection
# omits fields the resolver's phone/address tiers need, so line retrieval is
# SearchVectors -> strongly consistent BatchGetItem of the neighbor items ->
# full metadata. These are the base-item attributes fetched for that join
# (everything metadata-bearing; never the vector, which would multiply the
# response size ~40KB per neighbor for nothing).
_LINE_JOIN_ATTRIBUTES = (
    "image_id",
    "receipt_id",
    "line_id",
    "text",
    "merchant_name",
    "place_id",
    "row_line_ids",
    "section_type",
    "normalized_phone_10",
    "normalized_full_address",
)


def _canonical_key(item: Mapping[str, Any], *, index: str) -> str:
    image_id = str(item["image_id"])
    receipt_id = int(item["receipt_id"])
    line_id = int(item["line_id"])
    prefix = f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}"
    if index == WORD_INDEX:
        return f"{prefix}#WORD#{int(item['word_id']):05d}"
    return prefix


class DynamoVectorSearchClient:
    """Search and retrieve vectors from the two judge-provisioned indexes."""

    def __init__(
        self,
        dynamodb_client: Any,
        table_name: str,
        *,
        max_retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not table_name:
            raise ValueError("table_name must not be empty")
        if not callable(getattr(dynamodb_client, "search_vectors", None)):
            raise RuntimeError(
                "DynamoDB client lacks SearchVectors; boto3 >= 1.43.64 is required"
            )
        self._client = dynamodb_client
        self.table_name = table_name
        self._max_retries = max_retries
        self._sleep = sleep
        self.last_request_bytes: int | None = None
        self.last_request_units: None = None
        self.last_join_read_units: float | None = None

    @classmethod
    def from_env(cls) -> "DynamoVectorSearchClient":
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover - packaging guard
            raise RuntimeError(
                "boto3 is required for Dynamo vector search"
            ) from exc
        table_name = os.environ.get("DYNAMODB_TABLE_NAME", DEFAULT_TABLE_NAME)
        region = os.environ.get(
            "AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", DEFAULT_REGION)
        )
        return cls(boto3.client("dynamodb", region_name=region), table_name)

    def search(
        self,
        vector: Sequence[float],
        index: str,
        top_k: int,
        filters: Mapping[str, FilterValue] | None = None,
    ) -> list[ScoredItem]:
        validate_top_k(top_k)
        physical = physical_index_name(index)
        params: dict[str, Any] = {
            "TableName": self.table_name,
            "IndexName": physical,
            "SearchVector": search_vector_attribute_values(vector),
            "TopK": top_k,
            "ReturnConsumedCapacity": "TOTAL",
        }
        params.update(build_search_filter(physical, filters))
        response = self._client.search_vectors(**params)
        capacity = response.get("ConsumedCapacity") or {}
        request_bytes = capacity.get("VectorSearchRequestBytes")
        self.last_request_bytes = (
            int(request_bytes) if request_bytes is not None else None
        )

        results: list[ScoredItem] = []
        for raw_result in response.get("SearchResults", []):
            raw_item = raw_result.get("Item")
            score = raw_result.get("Score")
            if not isinstance(raw_item, Mapping) or score is None:
                continue
            try:
                item = parse_dynamodb_map(dict(raw_item))
                key = _canonical_key(item, index=physical)
                metadata = {
                    name: value
                    for name, value in item.items()
                    if name
                    not in {
                        "PK",
                        "SK",
                        "TYPE",
                        "line_vector",
                        "word_vector",
                    }
                }
                results.append(
                    ScoredItem(
                        key=key,
                        distance=float(score),
                        metadata=metadata,
                    )
                )
            except (KeyError, TypeError, ValueError):
                # One malformed projection must not discard healthy neighbors.
                continue
        results = results[:top_k]
        if physical == LINE_INDEX:
            results = self._join_line_metadata(results)
        return results

    def _join_line_metadata(
        self, results: list[ScoredItem]
    ) -> list[ScoredItem]:
        """Fetch-join full neighbor metadata for line-index results.

        SearchVectors returns only the index projection, which omits the
        resolver's ``normalized_phone_10`` / ``normalized_full_address``
        fields. Read the neighbor base items with strongly consistent
        BatchGetItem and replace each result's metadata with the full set.
        A neighbor whose item cannot be fetched (deleted since indexing, or
        unprocessed after bounded retries) keeps its projection metadata —
        a degraded neighbor never discards a healthy search hit.
        """
        self.last_join_read_units = None
        if not results:
            return results
        keys_by_id: dict[str, dict[str, Any]] = {}
        for result in results:
            match = _CANONICAL_KEY.fullmatch(result.key)
            if match is None or match.group("word_id") is not None:
                continue
            values = match.groupdict()
            keys_by_id[result.key] = {
                "PK": {"S": f"IMAGE#{values['image_id']}"},
                "SK": {
                    "S": (
                        f"RECEIPT#{int(values['receipt_id']):05d}#"
                        f"LINE#{int(values['line_id']):05d}#EMBEDDING"
                    )
                },
            }
        if not keys_by_id:
            return results
        names = {
            f"#j{position}": name
            for position, name in enumerate(_LINE_JOIN_ATTRIBUTES)
        }
        fetched: dict[str, dict[str, Any]] = {}
        consumed = 0.0
        all_keys = list(keys_by_id.values())
        for offset in range(0, len(all_keys), MAX_BATCH_GET_ITEMS):
            pending = all_keys[offset : offset + MAX_BATCH_GET_ITEMS]
            try:
                for attempt in range(self._max_retries + 1):
                    response = self._client.batch_get_item(
                        RequestItems={
                            self.table_name: {
                                "Keys": pending,
                                "ProjectionExpression": ", ".join(names),
                                "ExpressionAttributeNames": names,
                                "ConsistentRead": True,
                            }
                        },
                        ReturnConsumedCapacity="TOTAL",
                    )
                    for capacity in response.get("ConsumedCapacity") or []:
                        consumed += float(capacity.get("CapacityUnits") or 0)
                    for item in response.get("Responses", {}).get(
                        self.table_name, []
                    ):
                        parsed = parse_dynamodb_map(dict(item))
                        try:
                            joined_key = _canonical_key(
                                parsed, index=LINE_INDEX
                            )
                        except (KeyError, TypeError, ValueError):
                            continue
                        fetched[joined_key] = parsed
                    pending = (
                        response.get("UnprocessedKeys", {})
                        .get(self.table_name, {})
                        .get("Keys", [])
                    )
                    if not pending:
                        break
                    if attempt < self._max_retries:
                        self._sleep(0.1 * (2**attempt))
            except Exception:  # noqa: BLE001 - degrade to projection metadata
                continue
        self.last_join_read_units = consumed
        joined: list[ScoredItem] = []
        for result in results:
            item = fetched.get(result.key)
            if item is None:
                joined.append(result)
                continue
            joined.append(
                ScoredItem(
                    key=result.key,
                    distance=result.distance,
                    metadata=dict(item),
                )
            )
        return joined

    def get_vector(self, key: str) -> list[float]:
        match = _CANONICAL_KEY.fullmatch(key)
        if match is None:
            raise KeyError(f"invalid receipt vector key: {key}")
        values = match.groupdict()
        word_id = values["word_id"]
        sk = (
            f"RECEIPT#{int(values['receipt_id']):05d}#"
            f"LINE#{int(values['line_id']):05d}"
        )
        if word_id is None:
            vector_attribute = INDEX_VECTOR_ATTRIBUTES[LINE_INDEX]
        else:
            sk += f"#WORD#{int(word_id):05d}"
            vector_attribute = INDEX_VECTOR_ATTRIBUTES[WORD_INDEX]
        sk += "#EMBEDDING"
        response = self._client.get_item(
            TableName=self.table_name,
            Key={
                "PK": {"S": f"IMAGE#{values['image_id']}"},
                "SK": {"S": sk},
            },
            ProjectionExpression="#vector",
            ExpressionAttributeNames={"#vector": vector_attribute},
            ConsistentRead=True,
        )
        item = response.get("Item")
        if not item or vector_attribute not in item:
            raise KeyError(f"unknown vector key: {key}")
        vector = parse_dynamodb_map(item).get(vector_attribute)
        try:
            return normalize_vector(vector, dimensions=EMBEDDING_DIMENSIONS)
        except (TypeError, ValueError) as exc:
            raise KeyError(f"stored vector is invalid for key: {key}") from exc

    def get_last_search_metrics(self) -> dict[str, float | None]:
        estimated_cost = (
            self.last_request_bytes / 1_000_000_000 * VECTOR_SEARCH_USD_PER_GB
            if self.last_request_bytes is not None
            else None
        )
        return {
            "request_bytes": self.last_request_bytes,
            "estimated_usd": estimated_cost,
            "join_read_units": self.last_join_read_units,
            # SearchVectors itself bills request bytes, never read units;
            # the fetch-join's BatchGetItem units are the only read units
            # this client consumes, so they ARE its request_units — which
            # also lets the harness report read_request_units_per_query.
            "request_units": self.last_join_read_units,
        }


def create_client_from_env() -> DynamoVectorSearchClient:
    return DynamoVectorSearchClient.from_env()


__all__ = [
    "DEFAULT_REGION",
    "DEFAULT_TABLE_NAME",
    "DynamoVectorSearchClient",
    "create_client_from_env",
]
