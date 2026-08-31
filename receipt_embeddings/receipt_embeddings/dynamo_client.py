"""Live ``VectorSearchClient`` over DynamoDB ``SearchVectors``.

Never creates, updates, or deletes vector indexes. Dev table only.
"""

from __future__ import annotations

import os
import time
from collections.abc import Mapping, Sequence
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from receipt_embeddings.indexes import (
    DEV_TABLE_NAME,
    EMBEDDING_DIMENSION,
    LINE_INDEX,
    LINE_VECTOR_ATTR,
    PROD_TABLE_NAME,
    WORD_VECTOR_ATTR,
    encode_search_vector,
    physical_index_name,
    validate_search_args,
)
from receipt_embeddings.vector_client import (
    FilterValue,
    ScoredItem,
)

_THROTTLE_CODES = {
    "ThrottlingException",
    "RequestLimitExceeded",
    "ProvisionedThroughputExceededException",
}
_MAX_RETRIES = 3


def _decode_attr(value: Any) -> Any:
    if not isinstance(value, dict) or len(value) != 1:
        return value
    ((kind, payload),) = value.items()
    if kind == "S":
        return payload
    if kind == "N":
        number = float(payload)
        if number.is_integer():
            return int(number)
        return number
    if kind == "L":
        return [_decode_attr(entry) for entry in payload]
    if kind == "NULL":
        return None
    if kind == "BOOL":
        return payload
    return value


def _harness_key(item: Mapping[str, Any]) -> str:
    pk = _decode_attr(item.get("PK", {}))
    sk = _decode_attr(item.get("SK", {}))
    if not isinstance(pk, str) or not isinstance(sk, str):
        raise KeyError("SearchResult item missing PK/SK")
    if sk.endswith("#EMBEDDING"):
        sk = sk[: -len("#EMBEDDING")]
    return f"{pk}#{sk}"


def _metadata_from_item(item: Mapping[str, Any]) -> dict[str, object]:
    keys = (
        "text",
        "merchant_name",
        "place_id",
        "image_id",
        "receipt_id",
        "line_id",
        "word_id",
        "row_line_ids",
        "section_type",
        "label_status",
        "primary_label",
    )
    metadata: dict[str, object] = {}
    for key in keys:
        if key in item:
            metadata[key] = _decode_attr(item[key])
    if "image_id" not in metadata:
        pk = _decode_attr(item.get("PK", {}))
        if isinstance(pk, str) and pk.startswith("IMAGE#"):
            metadata["image_id"] = pk.split("#", 1)[1]
    sk = _decode_attr(item.get("SK", {}))
    if isinstance(sk, str):
        parts = sk.split("#")
        if "receipt_id" not in metadata and len(parts) >= 2:
            metadata["receipt_id"] = int(parts[1])
        if "line_id" not in metadata and "LINE" in parts:
            metadata["line_id"] = int(parts[parts.index("LINE") + 1])
        if "word_id" not in metadata and "WORD" in parts:
            metadata["word_id"] = int(parts[parts.index("WORD") + 1])
    return metadata


def _vector_from_item(item: Mapping[str, Any]) -> list[float] | None:
    for attr in (LINE_VECTOR_ATTR, WORD_VECTOR_ATTR):
        if attr in item:
            decoded = _decode_attr(item[attr])
            if isinstance(decoded, list):
                return [float(value) for value in decoded]
    return None


class DynamoVectorSearchClient:
    """Read-only SearchVectors + GetItem adapter. Dev table only."""

    def __init__(
        self,
        *,
        table_name: str = DEV_TABLE_NAME,
        client: Any | None = None,
    ) -> None:
        if table_name == PROD_TABLE_NAME:
            raise RuntimeError("refusing to use the production table")
        if table_name != DEV_TABLE_NAME:
            raise RuntimeError(
                f"refusing DynamoDB table {table_name!r}; "
                f"only {DEV_TABLE_NAME!r} is allowed"
            )
        self.table_name = table_name
        self._client = client
        self.last_latency_ms = 0.0
        self.last_request_units = 0.0
        self.last_request_bytes = 0.0

    @classmethod
    def from_env(cls) -> "DynamoVectorSearchClient":
        table = os.environ.get("DYNAMODB_TABLE_NAME", DEV_TABLE_NAME)
        return cls(table_name=table)

    def _boto(self) -> Any:
        if self._client is not None:
            return self._client
        import boto3

        self._client = boto3.client("dynamodb")
        if not hasattr(self._client, "search_vectors"):
            raise RuntimeError(
                "boto3 client has no search_vectors (need boto3>=1.43.64)"
            )
        return self._client

    def search(
        self,
        vector: Sequence[float],
        index: str,
        top_k: int,
        filters: Mapping[str, FilterValue] | None = None,
    ) -> list[ScoredItem]:
        query = [float(value) for value in vector]
        validate_search_args(
            top_k=top_k, filters=filters, dimension=len(query)
        )
        physical = physical_index_name(index)
        kwargs: dict[str, Any] = {
            "TableName": self.table_name,
            "IndexName": physical,
            "SearchVector": encode_search_vector(query),
            "TopK": top_k,
            "ReturnConsumedCapacity": "TOTAL",
        }
        if filters:
            clauses = []
            values: dict[str, Any] = {}
            names: dict[str, str] = {}
            for offset, (key, expected) in enumerate(filters.items()):
                name_token = f"#f{offset}"
                value_token = f":f{offset}"
                names[name_token] = key
                if isinstance(expected, bool):
                    values[value_token] = {"BOOL": expected}
                elif isinstance(expected, int) and not isinstance(
                    expected, bool
                ):
                    values[value_token] = {"N": str(expected)}
                elif isinstance(expected, float):
                    values[value_token] = {"N": repr(expected)}
                else:
                    values[value_token] = {"S": str(expected)}
                clauses.append(f"{name_token} = {value_token}")
            kwargs["SearchConditionExpression"] = " AND ".join(clauses)
            kwargs["ExpressionAttributeNames"] = names
            kwargs["ExpressionAttributeValues"] = values

        started = time.perf_counter()
        response = self._search_with_retry(kwargs)
        self.last_latency_ms = (time.perf_counter() - started) * 1000.0
        consumed = response.get("ConsumedCapacity") or {}
        request_bytes = float(consumed.get("VectorSearchRequestBytes") or 0.0)
        self.last_request_bytes = request_bytes
        self.last_request_units = request_bytes
        return self._parse_results(response)

    def _search_with_retry(self, kwargs: Mapping[str, Any]) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                return self._boto().search_vectors(**kwargs)
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if code in _THROTTLE_CODES and attempt < _MAX_RETRIES - 1:
                    time.sleep(0.2 * (2**attempt))
                    last_error = exc
                    continue
                if code in _THROTTLE_CODES:
                    # Graceful degradation: empty neighbors, do not crash.
                    return {"SearchResults": [], "ConsumedCapacity": {}}
                raise
            except BotoCoreError as exc:
                last_error = exc
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(0.2 * (2**attempt))
                    continue
                raise
        if last_error:
            raise last_error
        return {"SearchResults": []}

    def _parse_results(self, response: Mapping[str, Any]) -> list[ScoredItem]:
        scored: list[ScoredItem] = []
        for row in response.get("SearchResults") or []:
            item = row.get("Item") or {}
            score = row.get("Score", row.get("Distance", 0.0))
            if isinstance(score, dict) and "N" in score:
                score = float(score["N"])
            try:
                key = _harness_key(item)
            except KeyError:
                continue
            scored.append(
                ScoredItem(
                    key=key,
                    distance=float(score),
                    metadata=_metadata_from_item(item),
                )
            )
        return scored

    def get_vector(self, key: str) -> list[float]:
        pk, sk = _key_to_pk_sk(key)
        response = self._boto().get_item(
            TableName=self.table_name,
            Key={"PK": {"S": pk}, "SK": {"S": sk}},
        )
        item = response.get("Item")
        if not item:
            raise KeyError(f"unknown vector key: {key}")
        vector = _vector_from_item(item)
        if vector is None:
            raise KeyError(f"missing vector attribute for {key}")
        return vector


def _key_to_pk_sk(key: str) -> tuple[str, str]:
    parts = key.split("#")
    if len(parts) < 4 or parts[0] != "IMAGE" or parts[2] != "RECEIPT":
        raise KeyError(f"unrecognized vector key: {key}")
    pk = f"IMAGE#{parts[1]}"
    remainder = "#".join(parts[2:])
    if not remainder.endswith("#EMBEDDING"):
        remainder = f"{remainder}#EMBEDDING"
    return pk, remainder


def create_client_from_env() -> DynamoVectorSearchClient:
    return DynamoVectorSearchClient.from_env()


__all__ = [
    "DynamoVectorSearchClient",
    "create_client_from_env",
    "encode_search_vector",
]
