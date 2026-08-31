"""DynamoDB ``SearchVectors`` implementation of ``VectorSearchClient``."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from typing import Any

from receipt_dynamo.entities.dynamodb_utils import parse_dynamodb_map

from receipt_embeddings.service_limits import (
    EMBEDDING_DIMENSIONS,
    INDEX_VECTOR_ATTRIBUTES,
    LINE_INDEX,
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

    def __init__(self, dynamodb_client: Any, table_name: str) -> None:
        if not table_name:
            raise ValueError("table_name must not be empty")
        if not callable(getattr(dynamodb_client, "search_vectors", None)):
            raise RuntimeError(
                "DynamoDB client lacks SearchVectors; boto3 >= 1.43.64 is required"
            )
        self._client = dynamodb_client
        self.table_name = table_name
        self.last_request_bytes: int | None = None
        self.last_request_units: None = None

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
        return results[:top_k]

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
        }


def create_client_from_env() -> DynamoVectorSearchClient:
    return DynamoVectorSearchClient.from_env()


__all__ = [
    "DEFAULT_REGION",
    "DEFAULT_TABLE_NAME",
    "DynamoVectorSearchClient",
    "create_client_from_env",
]
