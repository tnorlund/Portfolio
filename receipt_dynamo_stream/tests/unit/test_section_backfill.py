"""Unit tests for the one-off section backfill entry point."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from receipt_dynamo_stream.backfill import (
    build_section_backfill_messages,
    iter_section_receipts,
    publish_section_backfill,
)
from receipt_dynamo_stream.models import ChromaDBCollection

IMAGE_ID = "550e8400-e29b-41d4-a716-446655440000"


def _section(image_id: str, receipt_id: int):
    return SimpleNamespace(image_id=image_id, receipt_id=receipt_id)


def test_build_messages_match_stream_message_shape() -> None:
    messages = build_section_backfill_messages([(IMAGE_ID, 1), (IMAGE_ID, 2)])
    assert len(messages) == 2
    for msg, receipt_id in zip(messages, (1, 2)):
        assert msg.entity_type == "RECEIPT_SECTION"
        assert msg.collections == (ChromaDBCollection.LINES,)
        assert msg.event_name == "MODIFY"
        assert msg.entity_data["image_id"] == IMAGE_ID
        assert msg.entity_data["receipt_id"] == receipt_id
        assert msg.context.source == "section_backfill"
    # record ids must be unique so SQS batching/failure tracking works
    assert len({m.context.record_id for m in messages}) == 2


def test_iter_section_receipts_paginates_and_dedupes() -> None:
    dynamo = Mock()
    dynamo.list_receipt_sections.side_effect = [
        ([_section(IMAGE_ID, 1), _section(IMAGE_ID, 1)], {"k": "next"}),
        ([_section(IMAGE_ID, 2)], None),
    ]
    receipts = list(iter_section_receipts(dynamo))
    assert receipts == [(IMAGE_ID, 1), (IMAGE_ID, 2)]
    assert dynamo.list_receipt_sections.call_count == 2


def test_publish_with_explicit_receipts() -> None:
    with patch(
        "receipt_dynamo_stream.backfill.publish_messages", return_value=1
    ) as publish:
        sent = publish_section_backfill(receipts=[(IMAGE_ID, 1)])
    assert sent == 1
    (messages,) = publish.call_args.args[:1]
    assert len(messages) == 1
    assert messages[0].entity_data["receipt_id"] == 1


def test_publish_discovers_receipts_from_dynamo() -> None:
    dynamo = Mock()
    dynamo.list_receipt_sections.return_value = (
        [_section(IMAGE_ID, 3)],
        None,
    )
    with patch(
        "receipt_dynamo_stream.backfill.publish_messages", return_value=1
    ) as publish:
        sent = publish_section_backfill(dynamo_client=dynamo)
    assert sent == 1
    (messages,) = publish.call_args.args[:1]
    assert messages[0].entity_data["receipt_id"] == 3


def test_publish_requires_receipts_or_dynamo_client() -> None:
    with pytest.raises(ValueError):
        publish_section_backfill()


def test_publish_no_receipts_is_noop() -> None:
    with patch("receipt_dynamo_stream.backfill.publish_messages") as publish:
        sent = publish_section_backfill(receipts=[])
    assert sent == 0
    publish.assert_not_called()
