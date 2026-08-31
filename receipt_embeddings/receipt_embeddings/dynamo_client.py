"""``VectorSearchClient`` over the DynamoDB SearchVectors API.

The client is intentionally plain boto3 (no receipt_dynamo import): the
search path deserializes projected attributes straight off the wire, so
the module works in any environment carrying only boto3 >= 1.43.64.

Keys follow the Round A fixture convention
``IMAGE#{image_id}#RECEIPT#{r:05d}#LINE#{l:05d}[#WORD#{w:05d}]`` — the
stored item's PK + SK minus the trailing ``#EMBEDDING`` suffix.
"""

from __future__ import annotations

import os
import time
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from receipt_embeddings.dynamo_quotas import (
    LINE_VECTOR_ATTRIBUTE,
    MIN_BOTO3_VERSION,
    WORD_VECTOR_ATTRIBUTE,
    ensure_equality_only_filters,
    ensure_top_k_within_search_quota,
    resolve_dynamo_index_name,
)
from receipt_embeddings.vector_client import (
    FilterValue,
    ScoredItem,
    Vector,
)

DEFAULT_TABLE_NAME = "ReceiptsTable-dc5be22"
DEFAULT_REGION = "us-east-1"
_EMBEDDING_SK_SUFFIX = "#EMBEDDING"
# Item attributes that are key/vector plumbing, not consumer metadata.
_NON_METADATA_ATTRIBUTES = frozenset(
    {"PK", "SK", "TYPE", LINE_VECTOR_ATTRIBUTE, WORD_VECTOR_ATTRIBUTE}
)


def _number_string(value: float) -> str:
    """Serialize one vector component as a positional Number string."""

    return format(Decimal(repr(float(value))), "f")


def _parse_key(key: str) -> tuple[str, str]:
    """Split a protocol key into the embedding item's (PK, SK)."""

    parts = key.split("#")
    valid = (
        len(parts) in (6, 8)
        and parts[0] == "IMAGE"
        and parts[2] == "RECEIPT"
        and parts[4] == "LINE"
        and (len(parts) == 6 or parts[6] == "WORD")
    )
    if not valid:
        raise KeyError(f"unknown vector key: {key}")
    primary_key = f"IMAGE#{parts[1]}"
    sort_key = "#".join(parts[2:]) + _EMBEDDING_SK_SUFFIX
    return primary_key, sort_key


def _deserialize(value: Mapping[str, Any]) -> object:
    """Deserialize one AttributeValue into fixture-shaped plain Python."""

    if "S" in value:
        return value["S"]
    if "N" in value:
        number = float(value["N"])
        return int(number) if number.is_integer() else number
    if "BOOL" in value:
        return bool(value["BOOL"])
    if "NULL" in value:
        return None
    if "L" in value:
        return [_deserialize(item) for item in value["L"]]
    if "M" in value:
        return {key: _deserialize(item) for key, item in value["M"].items()}
    if "SS" in value:
        return list(value["SS"])
    if "NS" in value:
        return [float(item) for item in value["NS"]]
    raise ValueError(f"unsupported attribute value: {sorted(value)}")


class DynamoVectorSearchClient:
    """Query the receipts table's vector indexes through SearchVectors.

    Scores come back as cosine distance (both indexes are COSINE), the
    same quantity Chroma returns, so consumers need no conversion.
    ``last_latency_ms`` / ``last_request_units`` expose per-call
    telemetry for the evaluation harness.
    """

    def __init__(
        self,
        table_name: str = DEFAULT_TABLE_NAME,
        *,
        client: Any | None = None,
        region: str = DEFAULT_REGION,
    ) -> None:
        if client is None:
            import boto3
            from botocore.config import Config

            client = boto3.client(
                "dynamodb",
                region_name=region,
                config=Config(
                    retries={"max_attempts": 10, "mode": "adaptive"}
                ),
            )
        if not hasattr(client, "search_vectors"):
            import botocore

            raise RuntimeError(
                "this boto3/botocore does not support SearchVectors; "
                f"boto3 >= {MIN_BOTO3_VERSION} is required "
                f"(found botocore {botocore.__version__})"
            )
        self._client = client
        self.table_name = table_name
        self.last_latency_ms = 0.0
        self.last_request_units = 0.0

    @classmethod
    def from_env(cls) -> "DynamoVectorSearchClient":
        """Build a client from DYNAMODB_TABLE_NAME / AWS_REGION."""

        return cls(
            table_name=os.environ.get(
                "DYNAMODB_TABLE_NAME", DEFAULT_TABLE_NAME
            ),
            region=os.environ.get("AWS_REGION", DEFAULT_REGION),
        )

    def search(
        self,
        vector: Vector,
        index: str,
        top_k: int,
        filters: Mapping[str, FilterValue] | None = None,
    ) -> list[ScoredItem]:
        """Return up to ``top_k`` nearest items, closest first."""

        index_name = resolve_dynamo_index_name(index)
        ensure_top_k_within_search_quota(top_k)
        validated_filters = ensure_equality_only_filters(filters)

        request: dict[str, Any] = {
            "TableName": self.table_name,
            "IndexName": index_name,
            "SearchVector": [{"N": _number_string(value)} for value in vector],
            "TopK": top_k,
            "ReturnConsumedCapacity": "TOTAL",
        }
        if validated_filters:
            names: dict[str, str] = {}
            values: dict[str, Any] = {}
            clauses: list[str] = []
            for position, (key, value) in enumerate(
                sorted(validated_filters.items())
            ):
                names[f"#f{position}"] = key
                values[f":v{position}"] = self._serialize_filter_value(value)
                clauses.append(f"#f{position} = :v{position}")
            request["SearchConditionExpression"] = " AND ".join(clauses)
            request["ExpressionAttributeNames"] = names
            request["ExpressionAttributeValues"] = values

        started = time.perf_counter()
        response = self._client.search_vectors(**request)
        self.last_latency_ms = (time.perf_counter() - started) * 1000.0

        consumed = response.get("ConsumedCapacity") or {}
        self.last_request_units = float(
            consumed.get("VectorSearchRequestBytes") or 0.0
        )

        results = []
        for result in response.get("SearchResults", []):
            item = result.get("Item") or {}
            key = self._item_key(item)
            metadata = {
                name: _deserialize(value)
                for name, value in item.items()
                if name not in _NON_METADATA_ATTRIBUTES
            }
            results.append(
                ScoredItem(
                    key=key,
                    distance=float(result["Score"]),
                    metadata=metadata,
                )
            )
        # The service returns results ranked by score; the key tie-break
        # mirrors FakeVectorIndex so equal-distance orderings are stable.
        results.sort(key=lambda item: (item.distance, item.key))
        return results

    def get_vector(self, key: str) -> list[float]:
        """Fetch one stored vector by protocol key.

        Raises ``KeyError`` (matching the fake and the fixture replay)
        when the item or its vector attribute is absent.
        """

        primary_key, sort_key = _parse_key(key)
        vector_attribute = (
            WORD_VECTOR_ATTRIBUTE
            if "#WORD#" in sort_key
            else LINE_VECTOR_ATTRIBUTE
        )
        response = self._client.get_item(
            TableName=self.table_name,
            Key={"PK": {"S": primary_key}, "SK": {"S": sort_key}},
            ProjectionExpression="#v",
            ExpressionAttributeNames={"#v": vector_attribute},
        )
        item = response.get("Item")
        if not item or vector_attribute not in item:
            raise KeyError(f"unknown vector key: {key}")
        return [float(value["N"]) for value in item[vector_attribute]["L"]]

    @staticmethod
    def _serialize_filter_value(value: FilterValue) -> dict[str, Any]:
        if isinstance(value, bool):
            return {"BOOL": value}
        if isinstance(value, str):
            return {"S": value}
        return {"N": _number_string(float(value))}

    @staticmethod
    def _item_key(item: Mapping[str, Any]) -> str:
        primary_key = item["PK"]["S"]
        sort_key = item["SK"]["S"]
        if sort_key.endswith(_EMBEDDING_SK_SUFFIX):
            sort_key = sort_key[: -len(_EMBEDDING_SK_SUFFIX)]
        return f"{primary_key}#{sort_key}"


def create_client_from_env() -> DynamoVectorSearchClient:
    """Factory used by ``evaluate.py --backend dynamo``."""

    return DynamoVectorSearchClient.from_env()


__all__ = [
    "DEFAULT_REGION",
    "DEFAULT_TABLE_NAME",
    "DynamoVectorSearchClient",
    "create_client_from_env",
]
