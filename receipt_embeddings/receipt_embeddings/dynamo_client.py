"""DynamoDB ``SearchVectors`` implementation of ``VectorSearchClient``."""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from receipt_dynamo.constants import CORE_LABEL_NAMES, ValidationStatus
from receipt_dynamo.entities.dynamodb_utils import parse_dynamodb_map

from receipt_embeddings.keys import (
    canonical_key_from_item,
    embedding_item_key,
    parse_canonical_key,
)
from receipt_embeddings.protocols import DynamoVectorLowLevelClient
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
# The label name itself comes from the requested SK, so it is not
# projected; the extra attributes let similar_labeled_words reuse this
# one join for evidence provenance instead of re-fetching the same keys
# (E3 review P2-4).
_WORD_LABEL_JOIN_ATTRIBUTES = (
    "PK",
    "SK",
    "validation_status",
    "reasoning",
    "label_proposed_by",
    "timestamp_added",
)


class DynamoVectorSearchClient:
    """Search and retrieve vectors from the two judge-provisioned indexes."""

    def __init__(
        self,
        dynamodb_client: DynamoVectorLowLevelClient,
        table_name: str,
        *,
        max_retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not table_name:
            raise ValueError("table_name must not be empty")
        if not callable(getattr(dynamodb_client, "search_vectors", None)):
            raise RuntimeError(
                "DynamoDB client lacks SearchVectors; "
                "boto3 >= 1.43.64 is required"
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
                key = canonical_key_from_item(item, index=physical)
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
        elif (
            physical == WORD_INDEX
            and filters
            and filters.get("label_status") == "validated"
        ):
            results = self._join_word_label_metadata(results)
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
            parsed = parse_canonical_key(result.key)
            if parsed is None or parsed.word_id is not None:
                continue
            keys_by_id[result.key] = embedding_item_key(
                parsed.image_id, parsed.receipt_id, parsed.line_id
            )
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
                            joined_key = canonical_key_from_item(
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
            # pylint: disable-next=broad-exception-caught
            except Exception:  # noqa: BLE001 - degrade to projection metadata
                # CONTRACTUAL: a join failure keeps SearchVectors hits
                # with projection metadata rather than discarding them.
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

    def _join_word_label_metadata(
        self, results: list[ScoredItem]
    ) -> list[ScoredItem]:
        """Hydrate Chroma-compatible validated-label arrays for words.

        The word index can filter on aggregate ``label_status`` but its
        immutable projection does not contain the label names used by the
        semantic proposer. Fetch the known core-label rows by exact key and
        surface the same ``valid_labels_array`` metadata shape as Chroma,
        plus the full per-row provenance as ``label_rows`` so
        similar_labeled_words reuses this single join instead of
        re-fetching the same keys (E3 review P2-4). Any failed or
        unprocessed join leaves both absent, which makes consumers abstain
        instead of voting on partial evidence.
        """

        self.last_join_read_units = None
        if not results:
            return results

        owner_by_token: dict[str, tuple[str, str]] = {}
        request_keys: list[dict[str, Any]] = []
        valid = {result.key: set() for result in results}
        rows_by_key: dict[str, list[dict[str, Any]]] = {}
        hydratable: set[str] = set()
        for result in results:
            parsed = parse_canonical_key(result.key)
            if parsed is None or parsed.word_id is None:
                continue
            pk = f"IMAGE#{parsed.image_id}"
            prefix = (
                f"RECEIPT#{parsed.receipt_id:05d}#"
                f"LINE#{parsed.line_id:05d}#"
                f"WORD#{parsed.word_id:05d}#LABEL#"
            )
            hydratable.add(result.key)
            for label in CORE_LABEL_NAMES:
                sk = f"{prefix}{label}"
                owner_by_token[f"{pk}|{sk}"] = (result.key, label)
                request_keys.append({"PK": {"S": pk}, "SK": {"S": sk}})

        names = {
            f"#w{position}": name
            for position, name in enumerate(_WORD_LABEL_JOIN_ATTRIBUTES)
        }
        consumed = 0.0
        try:
            for offset in range(0, len(request_keys), MAX_BATCH_GET_ITEMS):
                pending = request_keys[offset : offset + MAX_BATCH_GET_ITEMS]
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
                        pk = item.get("PK", {}).get("S", "")
                        sk = item.get("SK", {}).get("S", "")
                        owner = owner_by_token.get(f"{pk}|{sk}")
                        if owner is None:
                            continue
                        result_key, row_label = owner
                        parsed = parse_dynamodb_map(dict(item))
                        status = parsed.get("validation_status")
                        if status == ValidationStatus.VALID.value:
                            valid[result_key].add(row_label)
                        rows_by_key.setdefault(result_key, []).append(
                            {
                                "label": row_label,
                                "validation_status": status,
                                "reasoning": parsed.get("reasoning"),
                                "label_proposed_by": parsed.get(
                                    "label_proposed_by"
                                ),
                                "timestamp_added": parsed.get(
                                    "timestamp_added"
                                ),
                            }
                        )
                    pending = (
                        response.get("UnprocessedKeys", {})
                        .get(self.table_name, {})
                        .get("Keys", [])
                    )
                    if not pending:
                        break
                    if attempt < self._max_retries:
                        self._sleep(0.1 * (2**attempt))
                if pending:
                    return results
        # pylint: disable-next=broad-exception-caught
        except Exception:  # noqa: BLE001 - abstain on join failure
            # CONTRACTUAL: a failed label join leaves valid_labels_array
            # absent so consumers abstain instead of voting on partial
            # evidence.
            return results

        self.last_join_read_units = consumed
        joined: list[ScoredItem] = []
        for result in results:
            if result.key not in hydratable:
                joined.append(result)
                continue
            metadata = dict(result.metadata)
            metadata["valid_labels_array"] = sorted(valid[result.key]) or None
            metadata["label_rows"] = sorted(
                rows_by_key.get(result.key, []),
                key=lambda row: str(row["label"]),
            )
            joined.append(
                ScoredItem(
                    key=result.key,
                    distance=result.distance,
                    metadata=metadata,
                )
            )
        return joined

    def get_vector(self, key: str) -> list[float]:
        parsed = parse_canonical_key(key)
        if parsed is None:
            raise KeyError(f"invalid receipt vector key: {key}")
        if parsed.word_id is None:
            vector_attribute = INDEX_VECTOR_ATTRIBUTES[LINE_INDEX]
        else:
            vector_attribute = INDEX_VECTOR_ATTRIBUTES[WORD_INDEX]
        response = self._client.get_item(
            TableName=self.table_name,
            Key=embedding_item_key(
                parsed.image_id,
                parsed.receipt_id,
                parsed.line_id,
                parsed.word_id,
            ),
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
