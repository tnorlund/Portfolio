"""Control and reconcile the one-time embedding backfill.

This Lambda owns the durable v1 run marker and lease.  It also repairs the
legacy status model: retryable/orphaned items return to ``NONE`` and noise is
made explicit as ``NOISE``.  A first successful initialization resets every
eligible entity exactly once; later executions resume without resetting
already committed ``SUCCESS`` rows.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Iterable

import boto3
from botocore.exceptions import ClientError
from receipt_dynamo.constants import BatchStatus, BatchType, EmbeddingStatus
from receipt_dynamo.data.dynamo_client import DynamoClient

logger = logging.getLogger()
logger.setLevel(logging.INFO)

BACKFILL_VERSION = "v1"
CONTROL_PK = "SYSTEM#EMBEDDING_BACKFILL"
LOCK_SK = f"LOCK#{BACKFILL_VERSION}"
STATE_SK = f"STATE#{BACKFILL_VERSION}"
LEASE_SECONDS = 3 * 24 * 60 * 60
ACTIVE_BATCH_STATUSES = (
    BatchStatus.PENDING,
    BatchStatus.VALIDATING,
    BatchStatus.IN_PROGRESS,
    BatchStatus.FINALIZING,
    BatchStatus.CANCELING,
)
ENTITY_STATUSES = tuple(EmbeddingStatus)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _table_name() -> str:
    table_name = os.environ.get("DYNAMODB_TABLE_NAME")
    if not table_name:
        raise ValueError("DYNAMODB_TABLE_NAME environment variable not set")
    return table_name


def _ddb():
    return boto3.client("dynamodb")


def _owner(event: dict[str, Any]) -> str:
    owner = event.get("owner")
    if not isinstance(owner, str) or not owner:
        raise ValueError("owner is required")
    return owner


def _acquire(event: dict[str, Any]) -> dict[str, Any]:
    owner = _owner(event)
    now = int(time.time())
    lease_expires_at = now + LEASE_SECONDS
    try:
        _ddb().put_item(
            TableName=_table_name(),
            Item={
                "PK": {"S": CONTROL_PK},
                "SK": {"S": LOCK_SK},
                "TYPE": {"S": "EMBEDDING_BACKFILL_LOCK"},
                "owner": {"S": owner},
                "lease_expires_at": {"N": str(lease_expires_at)},
                "updated_at": {"S": _now_iso()},
            },
            ConditionExpression=(
                "attribute_not_exists(PK) OR lease_expires_at < :now OR #owner = :owner"
            ),
            ExpressionAttributeNames={"#owner": "owner"},
            ExpressionAttributeValues={
                ":now": {"N": str(now)},
                ":owner": {"S": owner},
            },
        )
        return {
            "acquired": True,
            "owner": owner,
            "lease_expires_at": lease_expires_at,
            "version": BACKFILL_VERSION,
        }
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != (
            "ConditionalCheckFailedException"
        ):
            raise
        return {
            "acquired": False,
            "owner": owner,
            "version": BACKFILL_VERSION,
        }


def _release(event: dict[str, Any]) -> dict[str, Any]:
    owner = _owner(event)
    try:
        _ddb().delete_item(
            TableName=_table_name(),
            Key={"PK": {"S": CONTROL_PK}, "SK": {"S": LOCK_SK}},
            ConditionExpression="#owner = :owner",
            ExpressionAttributeNames={"#owner": "owner"},
            ExpressionAttributeValues={":owner": {"S": owner}},
        )
        return {"released": True, "owner": owner}
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != (
            "ConditionalCheckFailedException"
        ):
            raise
        return {"released": False, "owner": owner}


def _state_phase() -> str:
    response = _ddb().get_item(
        TableName=_table_name(),
        Key={"PK": {"S": CONTROL_PK}, "SK": {"S": STATE_SK}},
        ConsistentRead=True,
    )
    return response.get("Item", {}).get("phase", {}).get("S", "NEW")


def _write_phase(phase: str, **counts: int) -> None:
    item = {
        "PK": {"S": CONTROL_PK},
        "SK": {"S": STATE_SK},
        "TYPE": {"S": "EMBEDDING_BACKFILL_STATE"},
        "version": {"S": BACKFILL_VERSION},
        "phase": {"S": phase},
        "updated_at": {"S": _now_iso()},
    }
    for name, value in counts.items():
        item[name] = {"N": str(value)}
    _ddb().put_item(TableName=_table_name(), Item=item)


def _list_batches(dynamo: DynamoClient, batch_type: BatchType) -> list:
    """Return every provider-live batch, deduplicated across status queries."""
    batches: dict[str, Any] = {}
    for status in ACTIVE_BATCH_STATUSES:
        page, last_key = dynamo.get_batch_summaries_by_status(
            status=status,
            batch_type=batch_type,
            limit=100,
            last_evaluated_key=None,
        )
        for batch in page:
            batches[batch.batch_id] = batch
        while last_key:
            page, last_key = dynamo.get_batch_summaries_by_status(
                status=status,
                batch_type=batch_type,
                limit=100,
                last_evaluated_key=last_key,
            )
            for batch in page:
                batches[batch.batch_id] = batch
    return list(batches.values())


def _list_entities(dynamo: DynamoClient, entity_type: str) -> list:
    by_key: dict[tuple, Any] = {}
    list_for_status = (
        dynamo.list_receipt_lines_by_embedding_status
        if entity_type == "lines"
        else dynamo.list_receipt_words_by_embedding_status
    )
    for status in ENTITY_STATUSES:
        for entity in list_for_status(status):
            key = (
                entity.image_id,
                entity.receipt_id,
                entity.line_id,
                getattr(entity, "word_id", None),
            )
            by_key[key] = entity
    return list(by_key.values())


def _update_entities(
    dynamo: DynamoClient, entity_type: str, entities: Iterable
) -> int:
    pending = list(entities)
    update = (
        dynamo.update_receipt_lines
        if entity_type == "lines"
        else dynamo.update_receipt_words
    )
    for offset in range(0, len(pending), 25):
        update(pending[offset : offset + 25])
    return len(pending)


def _active_receipts(batches: list) -> set[tuple[str, int]]:
    return {
        (image_id, receipt_id)
        for batch in batches
        for image_id, receipt_id in batch.receipt_refs
    }


def _reconcile_entities(
    dynamo: DynamoClient,
    entity_type: str,
    entities: list,
    active_receipts: set[tuple[str, int]],
) -> dict[str, int]:
    changed = []
    released = 0
    noise_marked = 0
    for entity in entities:
        target = None
        if (
            entity.is_noise
            and entity.embedding_status != EmbeddingStatus.NOISE.value
        ):
            target = EmbeddingStatus.NOISE.value
            noise_marked += 1
        elif (
            not entity.is_noise
            and entity.embedding_status
            in {EmbeddingStatus.PENDING.value, EmbeddingStatus.FAILED.value}
            and (entity.image_id, entity.receipt_id) not in active_receipts
        ):
            target = EmbeddingStatus.NONE.value
            released += 1
        elif (
            not entity.is_noise
            and entity.embedding_status == EmbeddingStatus.NOISE.value
        ):
            target = EmbeddingStatus.NONE.value
            released += 1

        if target and entity.embedding_status != target:
            entity.embedding_status = target
            changed.append(entity)

    _update_entities(dynamo, entity_type, changed)
    return {
        "updated": len(changed),
        "released": released,
        "noise_marked": noise_marked,
    }


def _counts(entities: list) -> dict[str, int]:
    counts = {status.value: 0 for status in ENTITY_STATUSES}
    for entity in entities:
        status = getattr(
            entity.embedding_status, "value", entity.embedding_status
        )
        counts[str(status)] = counts.get(str(status), 0) + 1
    counts["ELIGIBLE"] = sum(not entity.is_noise for entity in entities)
    return counts


def _inspect(_event: dict[str, Any]) -> dict[str, Any]:
    dynamo = DynamoClient(_table_name())
    line_batches = _list_batches(dynamo, BatchType.LINE_EMBEDDING)
    word_batches = _list_batches(dynamo, BatchType.WORD_EMBEDDING)
    lines = _list_entities(dynamo, "lines")
    words = _list_entities(dynamo, "words")

    line_reconcile = _reconcile_entities(
        dynamo, "lines", lines, _active_receipts(line_batches)
    )
    word_reconcile = _reconcile_entities(
        dynamo, "words", words, _active_receipts(word_batches)
    )
    line_counts = _counts(lines)
    word_counts = _counts(words)
    active_total = len(line_batches) + len(word_batches)
    unembedded = (
        line_counts[EmbeddingStatus.NONE.value]
        + line_counts[EmbeddingStatus.FAILED.value]
        + word_counts[EmbeddingStatus.NONE.value]
        + word_counts[EmbeddingStatus.FAILED.value]
    )
    pending = (
        line_counts[EmbeddingStatus.PENDING.value]
        + word_counts[EmbeddingStatus.PENDING.value]
    )

    return {
        "version": BACKFILL_VERSION,
        "backfill_phase": _state_phase(),
        "counts": {"lines": line_counts, "words": word_counts},
        "reconciled": {"lines": line_reconcile, "words": word_reconcile},
        "active_batches": {
            "lines": len(line_batches),
            "words": len(word_batches),
            "total": active_total,
        },
        "unembedded": unembedded,
        "pending": pending,
        "work_remaining": unembedded + pending + active_total,
    }


def _initialize(_event: dict[str, Any]) -> dict[str, Any]:
    """Reset all eligible rows once, guarded by the durable v1 marker."""
    phase = _state_phase()
    if phase in {"READY", "RUNNING", "COMPLETED"}:
        return {
            "initialized": False,
            "phase": phase,
            "version": BACKFILL_VERSION,
        }

    dynamo = DynamoClient(_table_name())
    active = _list_batches(dynamo, BatchType.LINE_EMBEDDING) + _list_batches(
        dynamo, BatchType.WORD_EMBEDDING
    )
    if active:
        raise RuntimeError(
            "Cannot initialize while embedding batches are active"
        )

    _write_phase("INITIALIZING")
    totals: dict[str, int] = {}
    for entity_type in ("lines", "words"):
        entities = _list_entities(dynamo, entity_type)
        changed = []
        for entity in entities:
            target = (
                EmbeddingStatus.NOISE.value
                if entity.is_noise
                else EmbeddingStatus.NONE.value
            )
            if entity.embedding_status != target:
                entity.embedding_status = target
                changed.append(entity)
        totals[f"{entity_type}_reset"] = _update_entities(
            dynamo, entity_type, changed
        )

    _write_phase("READY", **totals)
    return {
        "initialized": True,
        "phase": "READY",
        "version": BACKFILL_VERSION,
        **totals,
    }


def _complete(event: dict[str, Any]) -> dict[str, Any]:
    counts = event.get("counts", {})
    line_success = int(counts.get("lines", {}).get("SUCCESS", 0))
    word_success = int(counts.get("words", {}).get("SUCCESS", 0))
    _write_phase(
        "COMPLETED",
        lines_success=line_success,
        words_success=word_success,
    )
    return {
        "completed": True,
        "version": BACKFILL_VERSION,
        "lines_success": line_success,
        "words_success": word_success,
    }


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Dispatch backfill control actions."""
    action = event.get("action", "inspect")
    handlers = {
        "acquire": _acquire,
        "release": _release,
        "inspect": _inspect,
        "initialize": _initialize,
        "complete": _complete,
    }
    try:
        handler = handlers[action]
    except KeyError as exc:
        raise ValueError(f"Unsupported backfill action: {action}") from exc
    result = handler(event)
    logger.info("Backfill control action %s completed: %s", action, result)
    return result
