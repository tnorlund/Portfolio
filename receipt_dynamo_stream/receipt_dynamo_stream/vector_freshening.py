"""Inline vector-attribute freshening for embedding items (SPEC §3.4a).

Replaces the Chroma metadata appliers: when a receipt's place, a word's
label, or a section changes, refresh the denormalized attributes stored
on the receipt's embedding items with targeted, idempotent UpdateItems —
no new queues or Lambdas.

Behavior per entity type:

- ``RECEIPT_PLACE``: refresh ``merchant_name`` / ``place_id`` on every
  line-embedding item of the receipt. The ``normalized_phone_10`` /
  ``normalized_full_address`` fetch-join anchors are deliberately left
  alone: the backfill derives them from the row words' ``extracted_data``
  (``enrich_row_metadata_with_anchors``), not from place fields, so a
  place change never invalidates them.
- ``RECEIPT_WORD_LABEL``: recompute ``label_status`` for the word from
  its *current* label set (any terminal VALID/INVALID verdict ->
  ``validated``, else any PENDING -> ``pending``, else ``none`` — same
  rule as the backfill) and write it to the word's embedding item.
- ``RECEIPT_SECTION``: write ``section_type`` onto the line-embedding
  items of the section's lines; lines dropped from the section (or the
  whole section on REMOVE) are cleared to ``""``. Embeddings exist only
  at each visual row's primary line, so conditional-check misses on
  non-primary lines are expected and counted, not errors.

All updates use ``attribute_exists(PK)`` so a missing embedding item is
skipped, never created (the embedding may simply not be written yet).
Every failure mode degrades gracefully — throttles and errors are
counted and logged, and the stream handler is never crashed.
"""

# pylint: disable=import-error
# import-error: receipt_dynamo is a monorepo sibling installed at runtime

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from receipt_dynamo.entities.receipt_place import ReceiptPlace
from receipt_dynamo.entities.receipt_section import ReceiptSection
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel
from receipt_dynamo.word_label_status import aggregate_word_label_status
from receipt_dynamo_stream.models import ParsedStreamRecord
from receipt_dynamo_stream.parsing import parse_stream_record
from receipt_dynamo_stream.stream_types import (
    DynamoDBStreamRecord,
    MetricsRecorder,
)

logger = logging.getLogger(__name__)

TABLE_ENV_VAR = "DYNAMO_TABLE_NAME"

# Bounded per-record work: cap the update fan-out of a single stream
# record (a receipt has well under this many visual rows or section
# lines; hitting the cap is reported, not fatal).
MAX_UPDATES_PER_RECORD = 500

_THROTTLE_ERROR_CODES = frozenset(
    {
        "ProvisionedThroughputExceededException",
        "ThrottlingException",
        "RequestLimitExceeded",
    }
)

_LINE_EMBEDDING_TYPE = "RECEIPT_LINE_EMBEDDING"


@dataclass
class FresheningStats:
    """Counters for one ``apply_vector_freshening`` invocation."""

    records_freshened: int = 0
    updates_applied: int = 0
    missing_embeddings: int = 0
    throttled: int = 0
    errors: int = 0
    truncated_records: int = 0

    def to_metrics(self) -> dict[str, float]:
        """Convert to a metrics dict for EMF-style batch logging."""
        return {
            "VectorFresheningRecords": self.records_freshened,
            "VectorFresheningUpdates": self.updates_applied,
            "VectorFresheningMissing": self.missing_embeddings,
            "VectorFresheningThrottled": self.throttled,
            "VectorFresheningErrors": self.errors,
            "VectorFresheningTruncated": self.truncated_records,
        }


@dataclass
class _Context:
    """Shared state for one freshening pass."""

    client: Any
    table_name: str
    stats: FresheningStats
    metrics: Optional[MetricsRecorder] = None

    def count(
        self,
        name: str,
        value: int = 1,
        dimensions: Optional[Mapping[str, str]] = None,
    ) -> None:
        if self.metrics:
            self.metrics.count(name, value, dimensions)


def apply_vector_freshening(
    records: Iterable[DynamoDBStreamRecord],
    metrics: Optional[MetricsRecorder] = None,
    *,
    dynamo_client: Any = None,
    table_name: Optional[str] = None,
) -> FresheningStats:
    """Freshen embedding-item attributes for a batch of stream records.

    Safe to call alongside the existing message-building legs; it never
    raises. When ``DYNAMO_TABLE_NAME`` is unset (and no ``table_name``
    is passed) the leg is inert and returns zeroed stats, so wiring it
    into a handler without the env var is a no-op rather than a crash.
    """
    stats = FresheningStats()
    table = table_name or os.environ.get(TABLE_ENV_VAR, "")
    if not table:
        logger.warning(
            "Vector freshening disabled: %s not configured", TABLE_ENV_VAR
        )
        if metrics:
            metrics.count("VectorFresheningNotConfigured", 1)
        return stats

    ctx = _Context(
        client=dynamo_client or boto3.client("dynamodb"),
        table_name=table,
        stats=stats,
        metrics=metrics,
    )

    for record in records:
        # pylint: disable-next=broad-exception-caught
        try:
            _freshen_record(record, ctx)
        except Exception:  # graceful degradation: never crash the handler
            logger.exception(
                "Vector freshening failed for stream record",
                extra={"event_id": record.get("eventID")},
            )
            stats.errors += 1
            ctx.count("VectorFresheningRecordError", 1)

    return stats


def _freshen_record(record: DynamoDBStreamRecord, ctx: _Context) -> None:
    if record.get("eventName") not in {"INSERT", "MODIFY", "REMOVE"}:
        return

    parsed = parse_stream_record(record, ctx.metrics)
    if not parsed:
        return

    event_name = str(record.get("eventName"))
    if parsed.entity_type == "RECEIPT_PLACE":
        _freshen_place(parsed, event_name, ctx)
    elif parsed.entity_type == "RECEIPT_WORD_LABEL":
        _freshen_word_label(parsed, event_name, ctx)
    elif parsed.entity_type == "RECEIPT_SECTION":
        _freshen_section(parsed, event_name, ctx)


def _freshen_place(
    parsed: ParsedStreamRecord, event_name: str, ctx: _Context
) -> None:
    """Refresh merchant_name/place_id on the receipt's line embeddings."""
    new = parsed.new_entity
    if event_name == "REMOVE" or not isinstance(new, ReceiptPlace):
        # Place REMOVE keeps the last-known merchant on embeddings; the
        # replacement place record refreshes them when it lands.
        return

    old = parsed.old_entity
    if (
        isinstance(old, ReceiptPlace)
        and old.merchant_name == new.merchant_name
        and old.place_id == new.place_id
    ):
        return

    line_embedding_sks = _list_line_embedding_sks(
        parsed.pk, int(new.receipt_id), ctx
    )
    if line_embedding_sks is None:
        return

    values = {
        ":m": {"S": str(new.merchant_name or "")},
        ":p": {"S": str(new.place_id or "")},
    }
    for sk in line_embedding_sks:
        _update_embedding_item(
            parsed.pk,
            sk,
            "SET merchant_name = :m, place_id = :p",
            values,
            "RECEIPT_PLACE",
            ctx,
        )
    ctx.stats.records_freshened += 1


def _freshen_word_label(
    parsed: ParsedStreamRecord, event_name: str, ctx: _Context
) -> None:
    """Recompute and write label_status on the word's embedding item."""
    entity = parsed.new_entity or parsed.old_entity
    if not isinstance(entity, ReceiptWordLabel):
        return

    old = parsed.old_entity
    new = parsed.new_entity
    if (
        event_name == "MODIFY"
        and isinstance(old, ReceiptWordLabel)
        and isinstance(new, ReceiptWordLabel)
        and old.validation_status == new.validation_status
    ):
        return

    label_status = _compute_word_label_status(parsed.pk, entity, ctx)
    if label_status is None:
        return

    embedding_sk = (
        f"RECEIPT#{int(entity.receipt_id):05d}#"
        f"LINE#{int(entity.line_id):05d}#"
        f"WORD#{int(entity.word_id):05d}#EMBEDDING"
    )
    _update_embedding_item(
        parsed.pk,
        embedding_sk,
        "SET label_status = :s",
        {":s": {"S": label_status}},
        "RECEIPT_WORD_LABEL",
        ctx,
    )
    ctx.stats.records_freshened += 1


def _freshen_section(
    parsed: ParsedStreamRecord, event_name: str, ctx: _Context
) -> None:
    """Write section_type onto the section lines' embedding items.

    Unlike the backfill (which blanks ``section_type`` for a visual row
    whose lines span multiple sections), this leg writes per primary
    line — deterministic and idempotent; ambiguous multi-section rows
    converge to the section owning their primary line.
    """
    old = (
        parsed.old_entity
        if isinstance(parsed.old_entity, ReceiptSection)
        else None
    )
    new = (
        parsed.new_entity
        if isinstance(parsed.new_entity, ReceiptSection)
        else None
    )

    entity = new or old
    if entity is None:
        return

    if (
        event_name == "MODIFY"
        and old is not None
        and new is not None
        and str(old.section_type) == str(new.section_type)
        and list(old.line_ids) == list(new.line_ids)
    ):
        return

    # line_id -> section_type value to write ("" clears membership).
    targets: dict[int, str] = {}
    if event_name == "REMOVE" or new is None:
        if old is not None:
            targets = {int(lid): "" for lid in old.line_ids}
    else:
        targets = {int(lid): str(new.section_type) for lid in new.line_ids}
        if old is not None:
            new_ids = {int(lid) for lid in new.line_ids}
            for lid in old.line_ids:
                if int(lid) not in new_ids:
                    targets[int(lid)] = ""

    if len(targets) > MAX_UPDATES_PER_RECORD:
        logger.warning(
            "Section freshening truncated",
            extra={
                "pk": parsed.pk,
                "sk": parsed.sk,
                "line_count": len(targets),
            },
        )
        ctx.stats.truncated_records += 1
        ctx.count("VectorFresheningTruncated", 1)

    receipt_id = int(entity.receipt_id)
    for line_id, section_value in list(targets.items())[
        :MAX_UPDATES_PER_RECORD
    ]:
        embedding_sk = f"RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#EMBEDDING"
        _update_embedding_item(
            parsed.pk,
            embedding_sk,
            "SET section_type = :s",
            {":s": {"S": section_value}},
            "RECEIPT_SECTION",
            ctx,
        )
    ctx.stats.records_freshened += 1


def _list_line_embedding_sks(
    pk: str, receipt_id: int, ctx: _Context
) -> Optional[list[str]]:
    """Enumerate the receipt's line-embedding SKs (None on failure)."""
    sks: list[str] = []
    kwargs: dict[str, Any] = {
        "TableName": ctx.table_name,
        "KeyConditionExpression": "PK = :pk AND begins_with(SK, :sk)",
        "FilterExpression": "#t = :t",
        "ExpressionAttributeNames": {"#t": "TYPE"},
        "ExpressionAttributeValues": {
            ":pk": {"S": pk},
            ":sk": {"S": f"RECEIPT#{receipt_id:05d}#LINE#"},
            ":t": {"S": _LINE_EMBEDDING_TYPE},
        },
        "ProjectionExpression": "SK",
    }
    while True:
        try:
            response = ctx.client.query(**kwargs)
        except (ClientError, BotoCoreError) as exc:
            _record_client_failure(exc, "RECEIPT_PLACE", "query", ctx)
            return None
        for item in response.get("Items", []):
            sk = item.get("SK", {}).get("S")
            if sk:
                sks.append(sk)
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            return sks
        if len(sks) >= MAX_UPDATES_PER_RECORD:
            logger.warning(
                "Place freshening truncated",
                extra={"pk": pk, "receipt_id": receipt_id},
            )
            ctx.stats.truncated_records += 1
            ctx.count("VectorFresheningTruncated", 1)
            return sks[:MAX_UPDATES_PER_RECORD]
        kwargs["ExclusiveStartKey"] = last_key


def _compute_word_label_status(
    pk: str, label: ReceiptWordLabel, ctx: _Context
) -> Optional[str]:
    """Aggregate the word's current labels into a label_status value.

    Same rule as the backfill: any terminal human verdict (VALID or
    INVALID) -> validated, else any PENDING -> pending, else none.
    INVALID-only words must stay in the validated population or the
    word index's ``label_status = validated`` filter would drop exactly
    the counterexamples similar_labeled_words needs for
    ``evidence_against`` (E3 review P1-2). Returns None if the label
    query fails.
    """
    prefix = (
        f"RECEIPT#{int(label.receipt_id):05d}#"
        f"LINE#{int(label.line_id):05d}#"
        f"WORD#{int(label.word_id):05d}#LABEL#"
    )
    statuses: list[str] = []
    kwargs: dict[str, Any] = {
        "TableName": ctx.table_name,
        "KeyConditionExpression": "PK = :pk AND begins_with(SK, :sk)",
        "ExpressionAttributeValues": {
            ":pk": {"S": pk},
            ":sk": {"S": prefix},
        },
        "ProjectionExpression": "validation_status",
    }
    while True:
        try:
            response = ctx.client.query(**kwargs)
        except (ClientError, BotoCoreError) as exc:
            _record_client_failure(exc, "RECEIPT_WORD_LABEL", "query", ctx)
            return None
        for item in response.get("Items", []):
            status = item.get("validation_status", {}).get("S")
            if status:
                statuses.append(str(status))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        kwargs["ExclusiveStartKey"] = last_key

    return aggregate_word_label_status(statuses)


def _update_embedding_item(
    pk: str,
    sk: str,
    update_expression: str,
    values: dict[str, Any],
    entity_type: str,
    ctx: _Context,
) -> None:
    """Idempotent conditional update; missing embedding items are skipped."""
    try:
        ctx.client.update_item(
            TableName=ctx.table_name,
            Key={"PK": {"S": pk}, "SK": {"S": sk}},
            UpdateExpression=update_expression,
            ConditionExpression="attribute_exists(PK)",
            ExpressionAttributeValues=values,
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code == "ConditionalCheckFailedException":
            # Embedding not written yet (or a non-primary section line):
            # skip, never create.
            ctx.stats.missing_embeddings += 1
            ctx.count(
                "VectorFresheningMissing", 1, {"entity_type": entity_type}
            )
            return
        _record_client_failure(exc, entity_type, "update", ctx)
    except BotoCoreError as exc:
        _record_client_failure(exc, entity_type, "update", ctx)
    else:
        ctx.stats.updates_applied += 1
        ctx.count("VectorFresheningUpdates", 1, {"entity_type": entity_type})


def _record_client_failure(
    exc: Exception, entity_type: str, operation: str, ctx: _Context
) -> None:
    code = ""
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "")
    if code in _THROTTLE_ERROR_CODES:
        logger.warning(
            "Vector freshening throttled; skipping",
            extra={"entity_type": entity_type, "operation": operation},
        )
        ctx.stats.throttled += 1
        ctx.count("VectorFresheningThrottled", 1, {"entity_type": entity_type})
        return
    logger.exception(
        "Vector freshening %s failed",
        operation,
        extra={"entity_type": entity_type},
    )
    ctx.stats.errors += 1
    ctx.count("VectorFresheningError", 1, {"entity_type": entity_type})


__all__ = [
    "FresheningStats",
    "MAX_UPDATES_PER_RECORD",
    "TABLE_ENV_VAR",
    "apply_vector_freshening",
]
