"""Discover and claim a bounded page of receipt entities to embed.

The same package backs the line and word discovery Lambdas. ``ENTITY_TYPE``
selects the entity.  Discovery claims rows before returning them to Step
Functions so overlapping submit executions cannot both discover the same
``NONE`` rows.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError
from receipt_dynamo.constants import EmbeddingStatus
from receipt_dynamo.entities import ReceiptLine, ReceiptWord

logger = logging.getLogger()
logger.setLevel(logging.INFO)

DEFAULT_MAX_BATCHES = 100


def _entity_type() -> str:
    entity_type = os.environ.get("ENTITY_TYPE", "lines").lower()
    if entity_type not in {"lines", "words"}:
        raise ValueError("ENTITY_TYPE must be 'lines' or 'words'")
    return entity_type


def _discover_batches(
    table_name: str, entity_type: str, max_batches: int
) -> tuple[list[list], bool]:
    """Query only enough of the status GSI for a bounded receipt page.

    Keeping a receipt together prevents a visual line row from being split
    across concurrent OpenAI batches and keeps word/line context consistent.
    The GSI sort key groups entities by image and receipt, so reading one extra
    receipt is enough to determine whether another submit pass is needed.
    """
    ddb = boto3.client("dynamodb")
    converter = (
        ReceiptLine.from_item if entity_type == "lines" else ReceiptWord.from_item
    )
    prefix = "LINE#" if entity_type == "lines" else "WORD#"
    batches: list[list] = []
    current_key: tuple[str, int] | None = None
    current_batch: list = []
    last_evaluated_key = None

    while True:
        query = {
            "TableName": table_name,
            "IndexName": "GSI1",
            "KeyConditionExpression": (
                "GSI1PK = :status AND begins_with(GSI1SK, :prefix)"
            ),
            "ExpressionAttributeValues": {
                ":status": {"S": "EMBEDDING_STATUS#NONE"},
                ":prefix": {"S": prefix},
            },
            "Limit": 500,
        }
        if last_evaluated_key:
            query["ExclusiveStartKey"] = last_evaluated_key
        response = ddb.query(**query)

        for item in response.get("Items", []):
            entity = converter(item)
            key = (entity.image_id, entity.receipt_id)
            if current_key is None:
                current_key = key
            elif key != current_key:
                if current_batch:
                    current_batch.sort(
                        key=lambda value: (
                            value.line_id,
                            getattr(value, "word_id", -1),
                        )
                    )
                    batches.append(current_batch)
                    if len(batches) > max_batches:
                        return batches[:max_batches], True
                current_key = key
                current_batch = []
            if not entity.is_noise:
                current_batch.append(entity)

        last_evaluated_key = response.get("LastEvaluatedKey")
        if not last_evaluated_key:
            break

    if current_batch:
        current_batch.sort(
            key=lambda value: (value.line_id, getattr(value, "word_id", -1))
        )
        batches.append(current_batch)
    return batches[:max_batches], len(batches) > max_batches


def _serialize_and_upload(
    batches: list[list],
    bucket: str,
    entity_type: str,
    submission_namespace: str,
) -> list[dict[str, Any]]:
    """Write receipt batches to temporary NDJSON files and upload them."""
    s3 = boto3.client("s3")
    uploaded: list[dict[str, Any]] = []

    for batch in batches:
        image_id = batch[0].image_id
        receipt_id = batch[0].receipt_id
        rows = [json.dumps(entity.to_dict(), sort_keys=True) for entity in batch]
        content = "\n".join(rows) + "\n"
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
        filename = f"{entity_type}-{image_id}-{receipt_id}-{content_hash}.ndjson"
        path = Path("/tmp") / filename
        try:
            with path.open("w", encoding="utf-8") as stream:
                stream.write(content)
            key = f"{entity_type}_embeddings/{submission_namespace}/{filename}"
            s3.upload_file(str(path), bucket, key)
            uploaded.append(
                {
                    "s3_key": key,
                    "s3_bucket": bucket,
                    "image_id": image_id,
                    "receipt_id": receipt_id,
                }
            )
        finally:
            path.unlink(missing_ok=True)

    return uploaded


def _release_entities(table_name: str, entities: list) -> None:
    """Best-effort rollback for a partially claimed receipt."""
    ddb = boto3.client("dynamodb")
    for offset in range(0, len(entities), 25):
        chunk = entities[offset : offset + 25]
        ddb.transact_write_items(
            TransactItems=[
                {
                    "Update": {
                        "TableName": table_name,
                        "Key": entity.key,
                        "UpdateExpression": (
                            "SET embedding_status = :none, GSI1PK = :none_gsi"
                        ),
                        "ConditionExpression": "embedding_status = :pending",
                        "ExpressionAttributeValues": {
                            ":pending": {"S": EmbeddingStatus.PENDING.value},
                            ":none": {"S": EmbeddingStatus.NONE.value},
                            ":none_gsi": {"S": "EMBEDDING_STATUS#NONE"},
                        },
                    }
                }
                for entity in chunk
            ]
        )


def _claim_receipt(table_name: str, entities: list) -> bool:
    """Atomically claim a receipt in bounded transactions.

    Very large receipts need multiple transactions. If a later chunk loses a
    conditional race, the earlier chunks are released before this receipt is
    skipped, so discovery never intentionally returns a partial receipt.
    """
    ddb = boto3.client("dynamodb")
    claimed: list = []
    try:
        for offset in range(0, len(entities), 25):
            chunk = entities[offset : offset + 25]
            ddb.transact_write_items(
                TransactItems=[
                    {
                        "Update": {
                            "TableName": table_name,
                            "Key": entity.key,
                            "UpdateExpression": (
                                "SET embedding_status = :pending, GSI1PK = :pending_gsi"
                            ),
                            "ConditionExpression": "embedding_status = :none",
                            "ExpressionAttributeValues": {
                                ":none": {"S": EmbeddingStatus.NONE.value},
                                ":pending": {"S": EmbeddingStatus.PENDING.value},
                                ":pending_gsi": {"S": "EMBEDDING_STATUS#PENDING"},
                            },
                        }
                    }
                    for entity in chunk
                ]
            )
            claimed.extend(chunk)
    except ClientError as exc:
        if claimed:
            _release_entities(table_name, claimed)
        cancellation_reasons = exc.response.get("CancellationReasons", [])
        lost_race = exc.response.get("Error", {}).get("Code") == (
            "TransactionCanceledException"
        ) and any(
            reason.get("Code") == "ConditionalCheckFailed"
            for reason in cancellation_reasons
        )
        if lost_race:
            return False
        raise
    return True


def _claim_batches(table_name: str, batches: list[list]) -> list[list]:
    """Move discovered receipts to ``PENDING`` before submission.

    Conditional collisions are expected when scheduled and manual workflows
    overlap. The winner submits the receipt; the loser skips it.
    """
    claimed = []
    for batch in batches:
        if _claim_receipt(table_name, batch):
            claimed.append(batch)
    return claimed


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Discover, upload, and claim at most ``max_batches`` receipts."""
    bucket = os.environ.get("S3_BUCKET")
    table_name = os.environ.get("DYNAMODB_TABLE_NAME")
    if not bucket:
        raise ValueError("S3_BUCKET environment variable not set")
    if not table_name:
        raise ValueError("DYNAMODB_TABLE_NAME environment variable not set")

    entity_type = _entity_type()
    raw_namespace = str(event.get("submission_namespace") or "incremental")
    submission_namespace = "".join(
        character
        for character in raw_namespace.lower()
        if character.isalnum() or character in {"-", "_", "."}
    )[:64]
    if not submission_namespace:
        raise ValueError("submission_namespace must contain a safe character")
    try:
        max_batches = int(
            event.get("max_batches")
            or os.environ.get("MAX_BATCHES_PER_RUN", DEFAULT_MAX_BATCHES)
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("max_batches must be a positive integer") from exc
    if max_batches <= 0:
        raise ValueError("max_batches must be a positive integer")

    selected, has_more = _discover_batches(table_name, entity_type, max_batches)

    if not selected:
        return {
            "entity_type": entity_type,
            "submission_namespace": submission_namespace,
            "batches": [],
            "claimed_items": 0,
            "remaining_batches": 0,
            "has_more": False,
        }

    # Upload first: a crash leaves only a harmless deterministic S3 object,
    # whereas claiming first could strand PENDING rows before Step Functions
    # receives their manifest. Overlapping runs write identical object bytes.
    uploaded_candidates = _serialize_and_upload(
        selected,
        bucket,
        entity_type,
        submission_namespace,
    )
    claimed_batches = _claim_batches(table_name, selected)
    claimed_receipts = {
        (batch[0].image_id, batch[0].receipt_id) for batch in claimed_batches
    }
    uploaded = [
        item
        for item in uploaded_candidates
        if (item["image_id"], item["receipt_id"]) in claimed_receipts
    ]
    claimed = sum(len(batch) for batch in claimed_batches)
    logger.info(
        "Prepared %d %s receipt batches (%d items claimed, more=%s)",
        len(uploaded),
        entity_type,
        claimed,
        has_more,
    )
    return {
        "entity_type": entity_type,
        "submission_namespace": submission_namespace,
        "batches": uploaded,
        "claimed_items": claimed,
        "batch_count": len(uploaded),
        "remaining_batches": None if has_more else 0,
        "has_more": has_more,
    }
