"""One-off section backfill through the stream pipeline.

Receipts embedded before ReceiptSection rows existed (or before the
section stream path deployed) have line rows in ChromaDB without
``section_label`` metadata even though DynamoDB now holds their
sections. This module publishes synthetic RECEIPT_SECTION messages
through the SAME SQS -> enhanced-compaction path the DynamoDB stream
uses, so the backfill:

- serializes with concurrent delta ingestion under the compactor's
  lock (no direct Chroma writer is introduced), and
- reuses the consumer's recompute-from-current-DynamoDB-state logic,
  making re-runs idempotent (rows already correct are not rewritten).

Usage (one-off, from an environment with LINES_QUEUE_URL set to the
lines compaction queue):

    from receipt_dynamo.data.dynamo_client import DynamoClient
    from receipt_dynamo_stream.backfill import publish_section_backfill

    sent = publish_section_backfill(
        dynamo_client=DynamoClient(table_name)
    )

or, for a known subset of receipts:

    sent = publish_section_backfill(
        receipts=[(image_id, receipt_id), ...]
    )
"""

# pylint: disable=import-error
# import-error: receipt_dynamo is a monorepo sibling installed at runtime

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Iterable, Iterator, Optional
from uuid import uuid4

from receipt_dynamo_stream.models import (
    ChromaDBCollection,
    StreamMessage,
    StreamRecordContext,
)
from receipt_dynamo_stream.sqs_publisher import publish_messages
from receipt_dynamo_stream.stream_types import MetricsRecorder

logger = logging.getLogger(__name__)

_BACKFILL_SOURCE = "section_backfill"


def iter_section_receipts(dynamo_client: Any) -> Iterator[tuple[str, int]]:
    """Yield unique (image_id, receipt_id) pairs that have sections.

    Paginates every ReceiptSection row via ``list_receipt_sections`` and
    de-duplicates, so each receipt is recomputed exactly once.
    """
    seen: set[tuple[str, int]] = set()
    last_evaluated_key: Optional[dict] = None
    while True:
        sections, last_evaluated_key = dynamo_client.list_receipt_sections(
            last_evaluated_key=last_evaluated_key
        )
        for section in sections:
            key = (section.image_id, section.receipt_id)
            if key not in seen:
                seen.add(key)
                yield key
        if not last_evaluated_key:
            break


def build_section_backfill_messages(
    receipts: Iterable[tuple[str, int]],
) -> list[StreamMessage]:
    """Build one RECEIPT_SECTION recompute message per receipt.

    The consumer ignores the event image entirely (it recomputes from
    the receipt's current sections in DynamoDB), so the message only
    needs (image_id, receipt_id) — identical in shape to stream-emitted
    section messages, just tagged with a backfill source.
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    messages: list[StreamMessage] = []
    for image_id, receipt_id in receipts:
        messages.append(
            StreamMessage(
                entity_type="RECEIPT_SECTION",
                entity_data={
                    "entity_type": "RECEIPT_SECTION",
                    "image_id": image_id,
                    "receipt_id": receipt_id,
                },
                changes={},
                event_name="MODIFY",
                collections=(ChromaDBCollection.LINES,),
                context=StreamRecordContext(
                    source=_BACKFILL_SOURCE,
                    timestamp=timestamp,
                    record_id=f"{_BACKFILL_SOURCE}-{uuid4()}",
                    aws_region="backfill",
                ),
            )
        )
    return messages


def publish_section_backfill(
    receipts: Optional[Iterable[tuple[str, int]]] = None,
    dynamo_client: Any = None,
    metrics: Optional[MetricsRecorder] = None,
) -> int:
    """Queue section recomputes for receipts via the lines SQS queue.

    Args:
        receipts: Explicit (image_id, receipt_id) pairs to recompute.
            If omitted, every receipt owning at least one ReceiptSection
            row is discovered via ``dynamo_client``.
        dynamo_client: Required when ``receipts`` is omitted.
        metrics: Optional metrics recorder.

    Returns:
        Number of SQS messages successfully published.
    """
    if receipts is None:
        if dynamo_client is None:
            raise ValueError(
                "dynamo_client is required when receipts is not provided"
            )
        receipts = iter_section_receipts(dynamo_client)

    messages = build_section_backfill_messages(receipts)
    if not messages:
        logger.info("Section backfill found no receipts to publish")
        return 0

    sent = publish_messages(messages, metrics)
    logger.info(
        "Section backfill published %s/%s messages", sent, len(messages)
    )
    return sent


__all__ = [
    "build_section_backfill_messages",
    "iter_section_receipts",
    "publish_section_backfill",
]
