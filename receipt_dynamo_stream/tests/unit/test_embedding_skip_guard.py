"""Regression tests: embedding-item stream records must be skipped.

The Round C backfill wrote 15k+ RECEIPT_LINE_EMBEDDING /
RECEIPT_WORD_EMBEDDING items; their stream records contain #LINE#/#WORD#
in the SK and previously misclassified as RECEIPT_LINE / RECEIPT_WORD,
failing entity parsing with "missing required keys" noise. SPEC §3.4a:
skip *_EMBEDDING records first thing, so embedding writes don't echo.
"""

from typing import Any

from receipt_dynamo.entities.receipt_embedding import (
    ReceiptLineEmbedding,
    ReceiptWordEmbedding,
)
from receipt_dynamo_stream import (
    build_messages_from_records,
    detect_entity_type,
    is_embedding_sk,
    parse_stream_record,
)

from .conftest import MockMetrics

_IMAGE_ID = "550e8400-e29b-41d4-a716-446655440000"
_VECTOR = [0.001] * 1536


def _line_embedding() -> ReceiptLineEmbedding:
    return ReceiptLineEmbedding(
        image_id=_IMAGE_ID,
        receipt_id=1,
        line_id=2,
        text="COFFEE 4.50",
        merchant_name="Cafe Nero",
        place_id="place123",
        row_line_ids=[2, 3],
        section_type="ITEMS",
        line_vector=list(_VECTOR),
        normalized_phone_10="5551234567",
    )


def _word_embedding() -> ReceiptWordEmbedding:
    return ReceiptWordEmbedding(
        image_id=_IMAGE_ID,
        receipt_id=1,
        line_id=2,
        word_id=3,
        text="COFFEE",
        merchant_name="Cafe Nero",
        label_status="pending",
        word_vector=list(_VECTOR),
    )


def _stream_record(
    item: dict[str, Any], event_name: str = "INSERT"
) -> dict[str, Any]:
    """Build a stream record exactly as DynamoDB emits for this item."""
    dynamodb: dict[str, Any] = {
        "Keys": {"PK": item["PK"], "SK": item["SK"]},
        "StreamViewType": "NEW_AND_OLD_IMAGES",
    }
    if event_name in {"INSERT", "MODIFY"}:
        dynamodb["NewImage"] = item
    if event_name in {"MODIFY", "REMOVE"}:
        dynamodb["OldImage"] = item
    return {
        "eventID": "test-event",
        "eventName": event_name,
        "awsRegion": "us-east-1",
        "dynamodb": dynamodb,
    }


def test_is_embedding_sk() -> None:
    assert is_embedding_sk("RECEIPT#00001#LINE#00002#EMBEDDING")
    assert is_embedding_sk("RECEIPT#00001#LINE#00002#WORD#00003#EMBEDDING")
    assert not is_embedding_sk("RECEIPT#00001#LINE#00002")
    assert not is_embedding_sk("RECEIPT#00001#LINE#00002#WORD#00003")


def test_detect_entity_type_rejects_embedding_sks() -> None:
    """Previously RECEIPT_LINE / RECEIPT_WORD (the audit defect)."""
    assert detect_entity_type("RECEIPT#00001#LINE#00002#EMBEDDING") is None
    assert (
        detect_entity_type("RECEIPT#00001#LINE#00002#WORD#00003#EMBEDDING")
        is None
    )


def test_detect_entity_type_still_matches_receipt_entities() -> None:
    """The guard must not overreach onto ordinary entity SKs."""
    assert detect_entity_type("RECEIPT#00001#LINE#00002") == "RECEIPT_LINE"
    assert (
        detect_entity_type("RECEIPT#00001#LINE#00002#WORD#00003")
        == "RECEIPT_WORD"
    )
    assert (
        detect_entity_type("RECEIPT#00001#LINE#00002#WORD#00003#LABEL#TOTAL")
        == "RECEIPT_WORD_LABEL"
    )


def test_parse_stream_record_skips_line_embedding_insert() -> None:
    metrics = MockMetrics()
    record = _stream_record(_line_embedding().to_item())

    assert parse_stream_record(record, metrics) is None

    assert ("EmbeddingStreamRecordSkipped", 1, None) in metrics.counts
    error_metrics = [count for count in metrics.counts if "Error" in count[0]]
    assert not error_metrics


def test_parse_stream_record_skips_word_embedding_modify() -> None:
    metrics = MockMetrics()
    record = _stream_record(_word_embedding().to_item(), "MODIFY")

    assert parse_stream_record(record, metrics) is None

    assert ("EmbeddingStreamRecordSkipped", 1, None) in metrics.counts
    error_metrics = [count for count in metrics.counts if "Error" in count[0]]
    assert not error_metrics


def test_build_messages_ignores_embedding_records() -> None:
    """Full processor path: embedding records produce zero SQS messages."""
    metrics = MockMetrics()
    records = [
        _stream_record(_line_embedding().to_item(), "INSERT"),
        _stream_record(_line_embedding().to_item(), "MODIFY"),
        _stream_record(_word_embedding().to_item(), "INSERT"),
        _stream_record(_word_embedding().to_item(), "REMOVE"),
    ]

    messages = build_messages_from_records(records, metrics)

    assert messages == []
    error_metrics = [count for count in metrics.counts if "Error" in count[0]]
    assert not error_metrics
