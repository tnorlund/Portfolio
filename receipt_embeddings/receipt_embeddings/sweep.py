"""Retrying delete of native ``#EMBEDDING`` items for one receipt.

Merge and re-OCR both need to sweep existing embedding rows before a
rewrite (the engine writer skip-existing would otherwise keep a stale
vector). Unify on the re-OCR variant: 25-item BatchWrite chunks with
``UnprocessedItems`` retry. Raises if items remain unprocessed; merge
wraps the call in its contractual never-raise handler.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from receipt_embeddings.keys import EMBEDDING_SK_SUFFIX
from receipt_embeddings.protocols import DynamoQueryWriteClient
from receipt_embeddings.service_limits import MAX_BATCH_WRITE_ITEMS

_DEFAULT_MAX_RETRIES = 8


def delete_native_embedding_items(
    dynamodb_client: DynamoQueryWriteClient,
    table_name: str,
    image_id: str,
    receipt_id: int,
    *,
    max_retries: int = _DEFAULT_MAX_RETRIES,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Delete every ``#EMBEDDING`` item under one receipt prefix.

    Queries with ``begins_with(SK, RECEIPT#{n}#)``, keeps keys whose SK
    ends with ``#EMBEDDING``, then BatchWrite-deletes them in 25-item
    chunks. UnprocessedItems are retried with exponential backoff.
    Returns the number of matching keys found. Raises ``RuntimeError``
    if any deletes remain unprocessed after retries.
    """

    kwargs: dict[str, Any] = {
        "TableName": table_name,
        "KeyConditionExpression": "PK = :p AND begins_with(SK, :s)",
        "ExpressionAttributeValues": {
            ":p": {"S": f"IMAGE#{image_id}"},
            ":s": {"S": f"RECEIPT#{int(receipt_id):05d}#"},
        },
        "ProjectionExpression": "PK, SK",
    }
    keys: list[dict[str, Any]] = []
    while True:
        response = dynamodb_client.query(**kwargs)
        keys.extend(
            {"PK": item["PK"], "SK": item["SK"]}
            for item in response.get("Items", [])
            if item["SK"]["S"].endswith(EMBEDDING_SK_SUFFIX)
        )
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        kwargs["ExclusiveStartKey"] = last_key

    for start in range(0, len(keys), MAX_BATCH_WRITE_ITEMS):
        chunk = keys[start : start + MAX_BATCH_WRITE_ITEMS]
        pending = [{"DeleteRequest": {"Key": key}} for key in chunk]
        for attempt in range(max_retries):
            response = dynamodb_client.batch_write_item(
                RequestItems={table_name: pending}
            )
            pending = response.get("UnprocessedItems", {}).get(table_name, [])
            if not pending:
                break
            if attempt < max_retries - 1:
                sleep(0.2 * (2**attempt))
        if pending:
            raise RuntimeError(
                f"{len(pending)} stale embedding deletes unprocessed "
                "after retries"
            )
    return len(keys)


__all__ = ["delete_native_embedding_items"]
