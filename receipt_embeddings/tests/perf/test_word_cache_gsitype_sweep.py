"""Word-cache GSITYPE sweep on a synthetic 10k-item moto table.

Mirrors ``infra/routes/word_similarity_cache_generator/lambdas/index.py``
``_fetch_lines_from_dynamo``: one GSITYPE query, projection skips the
1536-dim vectors, case-insensitive MILK match. No behavior change.

Parallel Scan would not help this access pattern: Query against a single
GSITYPE partition (``TYPE = RECEIPT_LINE_EMBEDDING``) cannot be split
with ``Segment``/``TotalSegments`` — those apply to Scan. A table Scan
with a TYPE filter would read every entity class on the mixed receipts
table and is strictly worse. Sharding TYPE (for example
``RECEIPT_LINE_EMBEDDING#<n>``) would be a live A/B, not a code change
here; run that with ``scripts/similarity_harness/evaluate.py
--backend dynamo --measure-wall-latency``.
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from receipt_embeddings.keys import parse_embedding_pk_sk

pytestmark = pytest.mark.performance

TABLE = "ReceiptsTable-dc5be22"
TARGET_WORD = "MILK"
LINE_TYPE = "RECEIPT_LINE_EMBEDDING"
WORD_TYPE = "RECEIPT_WORD_EMBEDDING"
ITEM_COUNT = 10_000
MAX_BATCH_WRITE = 25

# Same kwargs as the word-cache generator (projection skips vectors).
_QUERY: dict[str, object] = {
    "IndexName": "GSITYPE",
    "KeyConditionExpression": "#t = :t",
    "ExpressionAttributeNames": {"#t": "TYPE", "#x": "text"},
    "ExpressionAttributeValues": {":t": {"S": LINE_TYPE}},
    "ProjectionExpression": "PK, SK, #x, merchant_name, row_line_ids",
}


def _line_item(line_id: int, text: str) -> dict[str, Any]:
    image_id = f"{line_id:08x}-0000-4000-8000-000000000000"
    return {
        "PK": {"S": f"IMAGE#{image_id}"},
        "SK": {"S": f"RECEIPT#00001#LINE#{line_id:05d}#EMBEDDING"},
        "TYPE": {"S": LINE_TYPE},
        "text": {"S": text},
        "merchant_name": {"S": "Fixture Mart"},
        "row_line_ids": {"L": [{"N": str(line_id)}]},
    }


def _word_item(word_id: int) -> dict[str, Any]:
    image_id = f"{word_id:08x}-1111-4000-8000-000000000000"
    return {
        "PK": {"S": f"IMAGE#{image_id}"},
        "SK": {
            "S": (f"RECEIPT#00001#LINE#00001#WORD#{word_id:05d}#EMBEDDING")
        },
        "TYPE": {"S": WORD_TYPE},
        "text": {"S": "MILK"},
        "merchant_name": {"S": "Fixture Mart"},
    }


def _seed_table(client: Any) -> int:
    """Put 10k line rows plus a handful of word rows. Return milk hits."""

    milk_expected = 0
    pending: list[dict[str, Any]] = []

    def flush() -> None:
        if not pending:
            return
        client.batch_write_item(
            RequestItems={
                TABLE: [{"PutRequest": {"Item": item}} for item in pending]
            }
        )
        pending.clear()

    for line_id in range(ITEM_COUNT):
        residue = line_id % 20
        if residue == 0:
            text = "ORGANIC MILK 1GAL"
            milk_expected += 1
        elif residue == 1:
            # Case-insensitive on purpose (Chroma $contains was not).
            text = "Milk Shake"
            milk_expected += 1
        else:
            text = "BREAD LOAF"
        pending.append(_line_item(line_id, text))
        if len(pending) == MAX_BATCH_WRITE:
            flush()
    for word_id in range(10):
        pending.append(_word_item(word_id))
        if len(pending) == MAX_BATCH_WRITE:
            flush()
    flush()
    return milk_expected


def _fetch_milk_rows(
    client: Any,
) -> tuple[list[tuple[str, dict[str, Any]]], int]:
    rows: list[tuple[str, dict[str, Any]]] = []
    kwargs: dict[str, Any] = {"TableName": TABLE, **_QUERY}
    pages = 0
    while True:
        response = client.query(**kwargs)
        pages += 1
        for item in response.get("Items", []):
            text = item.get("text", {}).get("S", "")
            if TARGET_WORD not in text.upper():
                continue
            parsed = parse_embedding_pk_sk(item["PK"]["S"], item["SK"]["S"])
            if parsed is None or parsed.word_id is not None:
                continue
            rows.append((parsed.canonical(), {"text": text}))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        kwargs["ExclusiveStartKey"] = last_key
    return rows, pages


def test_gsitype_paginated_sweep_of_ten_thousand_line_embeddings(
    gsitype_client: Any,
) -> None:
    expected_milk = _seed_table(gsitype_client)
    begun = time.perf_counter()
    rows, pages = _fetch_milk_rows(gsitype_client)
    elapsed = time.perf_counter() - begun

    texts = {payload["text"] for _, payload in rows}
    assert len(rows) == expected_milk
    assert expected_milk == ITEM_COUNT // 10
    assert "ORGANIC MILK 1GAL" in texts
    assert "Milk Shake" in texts
    assert pages >= 1
    print(
        f"GSITYPE sweep: {ITEM_COUNT} line items, {len(rows)} milk rows, "
        f"{pages} pages, {elapsed:.2f}s query (seed excluded)"
    )
