"""Serialize/parse cost of a 1536-dim SearchVectors query payload.

Moto does not implement SearchVectors. This measures the request JSON
the client actually sends (~40KB) and the parse path against a fake
low-level client. Live wall latency:

    scripts/similarity_harness/evaluate.py --backend dynamo \\
        --measure-wall-latency
"""

from __future__ import annotations

import json
import time
from typing import Any

import pytest
from receipt_embeddings.dynamo_client import DynamoVectorSearchClient
from receipt_embeddings.service_limits import (
    LINE_INDEX,
    search_vector_attribute_values,
)

from receipt_dynamo.entities import EMBEDDING_DIMENSIONS

pytestmark = pytest.mark.performance

# Documented band for a 1536-dim ``SearchVector`` AttributeValue list.
# Typical text-embedding-3-small values serialize to ~30–40KB of JSON;
# the join-path comment in dynamo_client.py calls the vector ~40KB.
_SEARCH_VECTOR_JSON_MIN = 20_000
_SEARCH_VECTOR_JSON_MAX = 60_000


class _FakeSearchClient:
    """SearchVectors + empty join so parse cost is isolated from I/O."""

    def __init__(self, results: list[dict[str, Any]]) -> None:
        self.results = results
        self.last_search_vector: list[dict[str, str]] | None = None
        self.search_calls = 0

    def search_vectors(self, **kwargs: Any) -> dict[str, Any]:
        self.search_calls += 1
        self.last_search_vector = kwargs["SearchVector"]
        return {"SearchResults": self.results}

    def batch_get_item(self, **_kwargs: Any) -> dict[str, Any]:
        return {"Responses": {}}

    def get_item(self, **_kwargs: Any) -> dict[str, Any]:
        return {}


def _varied_vector() -> list[float]:
    """Finite values with mixed magnitude, closer to real embeddings."""

    return [
        ((index % 97) - 48) / 500.0 for index in range(EMBEDDING_DIMENSIONS)
    ]


def _line_hit(line_id: int) -> dict[str, Any]:
    image_id = "2f1e7204-84f1-4ab3-9b05-7dc6edebc1b7"
    return {
        "Item": {
            "PK": {"S": f"IMAGE#{image_id}"},
            "SK": {"S": f"RECEIPT#00001#LINE#{line_id:05d}#EMBEDDING"},
            "TYPE": {"S": "RECEIPT_LINE_EMBEDDING"},
            "image_id": {"S": image_id},
            "receipt_id": {"N": "1"},
            "line_id": {"N": str(line_id)},
            "text": {"S": f"LINE {line_id}"},
            "merchant_name": {"S": "Fixture Mart"},
        },
        "Score": 0.12,
    }


def test_search_vector_json_is_about_forty_kilobytes() -> None:
    vector = _varied_vector()
    begun = time.perf_counter()
    values = search_vector_attribute_values(vector)
    payload = json.dumps(values, separators=(",", ":"))
    serialize_s = time.perf_counter() - begun
    nbytes = len(payload.encode("utf-8"))
    params = {
        "TableName": "ReceiptsTable-dc5be22",
        "IndexName": LINE_INDEX,
        "SearchVector": values,
        "TopK": 10,
    }
    request_nbytes = len(json.dumps(params, separators=(",", ":")).encode())

    assert len(values) == EMBEDDING_DIMENSIONS
    assert _SEARCH_VECTOR_JSON_MIN <= nbytes <= _SEARCH_VECTOR_JSON_MAX
    assert request_nbytes >= nbytes
    print(
        f"SearchVector JSON={nbytes}B (~{nbytes / 1024:.1f}KiB); "
        f"full request JSON={request_nbytes}B; "
        f"serialize={serialize_s * 1e6:.0f}µs"
    )


def test_search_parse_cost_against_fake_client() -> None:
    hits = [_line_hit(index) for index in range(1, 11)]
    fake = _FakeSearchClient(hits)
    client = DynamoVectorSearchClient(fake, "ReceiptsTable-dc5be22")
    vector = _varied_vector()

    # Warm the parser once so the timed loop is not import-dominated.
    warmed = client.search(vector, LINE_INDEX, top_k=10)
    assert len(warmed) == 10
    assert fake.last_search_vector is not None
    assert len(fake.last_search_vector) == EMBEDDING_DIMENSIONS

    rounds = 50
    begun = time.perf_counter()
    for _ in range(rounds):
        results = client.search(vector, LINE_INDEX, top_k=10)
        assert len(results) == 10
    elapsed = time.perf_counter() - begun
    per_call_us = elapsed / rounds * 1e6
    print(
        f"DynamoVectorSearchClient.search parse "
        f"(10 hits, no join I/O): {per_call_us:.0f}µs/call"
    )
    assert fake.search_calls == rounds + 1
