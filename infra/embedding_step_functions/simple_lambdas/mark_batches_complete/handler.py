"""Finalize embedding state after canonical snapshot promotion.

The pollers stage deltas but deliberately leave receipt entities ``PENDING``.
This handler runs only after final compaction succeeds, marks the exact staged
items ``SUCCESS``, and then closes completed BatchSummaries.  Any failure is
raised so Step Functions retries or catches it; returning an HTTP-style 500
would incorrectly count as a successful Task.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from typing import Any

import boto3
from receipt_dynamo.constants import BatchStatus, EmbeddingStatus
from receipt_dynamo.data.dynamo_client import DynamoClient

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def _load_poll_results(event: dict[str, Any]) -> list[dict[str, Any]]:
    poll_results = event.get("poll_results") or []
    key = event.get("poll_results_s3_key")
    bucket = event.get("poll_results_s3_bucket")
    if poll_results or not (key and bucket):
        return poll_results

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as stream:
        path = stream.name
    try:
        boto3.client("s3").download_file(bucket, key, path)
        with open(path, "r", encoding="utf-8") as stream:
            loaded = json.load(stream)
        if not isinstance(loaded, list):
            raise ValueError("poll results must be a JSON list")
        return loaded
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _items_to_finalize(
    poll_results: list[dict[str, Any]], collection: str
) -> list[dict[str, Any]]:
    items: dict[tuple, dict[str, Any]] = {}
    for result in poll_results:
        if not isinstance(result, dict):
            continue
        if result.get("collection") != collection or not result.get("delta_key"):
            continue
        if result.get("action") not in {"process_results", "process_partial"}:
            continue
        for item in result.get("embedded_items") or []:
            if not isinstance(item, dict):
                continue
            key = (
                item.get("image_id"),
                item.get("receipt_id"),
                item.get("line_id"),
                item.get("word_id"),
            )
            if all(value is not None for value in key[:3]):
                items[key] = item
    return list(items.values())


def _finalize_lines(dynamo: DynamoClient, items: list[dict[str, Any]]) -> int:
    requested: dict[tuple[str, int], set[int]] = {}
    for item in items:
        requested.setdefault((item["image_id"], int(item["receipt_id"])), set()).add(
            int(item["line_id"])
        )

    changed = []
    for (image_id, receipt_id), line_ids in requested.items():
        for line in dynamo.list_receipt_lines_from_receipt(image_id, receipt_id):
            if (
                line.line_id in line_ids
                and line.embedding_status != EmbeddingStatus.SUCCESS.value
            ):
                line.embedding_status = EmbeddingStatus.SUCCESS.value
                changed.append(line)
    for offset in range(0, len(changed), 25):
        dynamo.update_receipt_lines(changed[offset : offset + 25])
    return len(changed)


def _finalize_words(dynamo: DynamoClient, items: list[dict[str, Any]]) -> int:
    requested: dict[tuple[str, int], set[tuple[int, int]]] = {}
    for item in items:
        requested.setdefault((item["image_id"], int(item["receipt_id"])), set()).add(
            (int(item["line_id"]), int(item["word_id"]))
        )

    changed = []
    for (image_id, receipt_id), word_ids in requested.items():
        for word in dynamo.list_receipt_words_from_receipt(image_id, receipt_id):
            if (
                word.line_id,
                word.word_id,
            ) in word_ids and word.embedding_status != EmbeddingStatus.SUCCESS.value:
                word.embedding_status = EmbeddingStatus.SUCCESS.value
                changed.append(word)
    for offset in range(0, len(changed), 25):
        dynamo.update_receipt_words(changed[offset : offset + 25])
    return len(changed)


def _completed_batch_ids(
    poll_results: list[dict[str, Any]],
) -> list[str]:
    return sorted(
        {
            result["batch_id"]
            for result in poll_results
            if isinstance(result, dict)
            and result.get("batch_id")
            and str(result.get("batch_status", "")).lower() == "completed"
            and result.get("action") == "process_results"
        }
    )


def _complete_summaries(dynamo: DynamoClient, batch_ids: list[str]) -> int:
    if not batch_ids:
        return 0
    summaries = dynamo.get_batch_summaries_by_batch_ids(batch_ids)
    found = {summary.batch_id for summary in summaries}
    missing = sorted(set(batch_ids) - found)
    if missing:
        raise RuntimeError(f"Batch summaries missing: {missing[:10]}")

    for summary in summaries:
        summary.status = BatchStatus.COMPLETED.value
    for offset in range(0, len(summaries), 25):
        dynamo.update_batch_summaries(summaries[offset : offset + 25])
    return len(summaries)


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Finalize exact items and BatchSummaries after compaction."""
    table_name = os.environ.get("DYNAMODB_TABLE_NAME")
    if not table_name:
        raise ValueError("DYNAMODB_TABLE_NAME environment variable not set")
    dynamo = DynamoClient(table_name)
    poll_results = _load_poll_results(event)
    if not poll_results:
        return {
            "lines_marked": 0,
            "words_marked": 0,
            "batches_marked": 0,
            "batch_ids": [],
        }

    lines_marked = _finalize_lines(dynamo, _items_to_finalize(poll_results, "lines"))
    words_marked = _finalize_words(dynamo, _items_to_finalize(poll_results, "words"))
    batch_ids = _completed_batch_ids(poll_results)
    batches_marked = _complete_summaries(dynamo, batch_ids)
    logger.info(
        "Finalized canonical embedding state: %d lines, %d words, %d batches",
        lines_marked,
        words_marked,
        batches_marked,
    )
    return {
        "lines_marked": lines_marked,
        "words_marked": words_marked,
        "batches_marked": batches_marked,
        "batch_ids": batch_ids,
    }
