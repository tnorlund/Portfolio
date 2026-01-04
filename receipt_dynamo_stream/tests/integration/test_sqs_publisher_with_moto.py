from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, cast

import boto3
import pytest
from moto import mock_aws

from receipt_dynamo_stream import (
    ChromaDBCollection,
    FieldChange,
    StreamMessage,
    StreamRecordContext,
    publish_messages,
)


@pytest.fixture
def moto_sqs() -> Any:
    with mock_aws():
        yield boto3.client("sqs", region_name="us-east-1")


@pytest.fixture
def hybrid_queues(moto_sqs: Any) -> dict[str, str]:
    """Create hybrid queue architecture: Standard for writes, FIFO for deletes."""
    # Standard queues for INSERT/MODIFY (high throughput)
    lines_standard = moto_sqs.create_queue(
        QueueName="lines-standard",
    )["QueueUrl"]
    words_standard = moto_sqs.create_queue(
        QueueName="words-standard",
    )["QueueUrl"]

    # FIFO queues for REMOVE only (ordered deletes)
    lines_delete = moto_sqs.create_queue(
        QueueName="lines-delete.fifo",
        Attributes={
            "FifoQueue": "true",
            "ContentBasedDeduplication": "true",
        },
    )["QueueUrl"]
    words_delete = moto_sqs.create_queue(
        QueueName="words-delete.fifo",
        Attributes={
            "FifoQueue": "true",
            "ContentBasedDeduplication": "true",
        },
    )["QueueUrl"]

    # Set environment variables for new queue architecture
    os.environ["LINES_STANDARD_QUEUE_URL"] = lines_standard
    os.environ["WORDS_STANDARD_QUEUE_URL"] = words_standard
    os.environ["LINES_DELETE_QUEUE_URL"] = lines_delete
    os.environ["WORDS_DELETE_QUEUE_URL"] = words_delete

    return {
        "lines_standard": lines_standard,
        "words_standard": words_standard,
        "lines_delete": lines_delete,
        "words_delete": words_delete,
    }


def _sample_place_message() -> StreamMessage:
    """MODIFY event - should go to Standard queues."""
    return StreamMessage(
        entity_type="RECEIPT_PLACE",
        entity_data={"image_id": "img-1", "receipt_id": 1},
        changes={
            "merchant_name": FieldChange(old="Old", new="New"),
        },
        event_name="MODIFY",
        collections=(ChromaDBCollection.LINES, ChromaDBCollection.WORDS),
        context=StreamRecordContext(
            timestamp=datetime.now().isoformat(),
            record_id="event-1",
            aws_region="us-east-1",
        ),
    )


def _sample_place_remove() -> StreamMessage:
    """REMOVE event - should go to FIFO delete queues."""
    return StreamMessage(
        entity_type="RECEIPT_PLACE",
        entity_data={"image_id": "img-1", "receipt_id": 1},
        changes={},
        event_name="REMOVE",
        collections=(ChromaDBCollection.LINES, ChromaDBCollection.WORDS),
        context=StreamRecordContext(
            timestamp=datetime.now().isoformat(),
            record_id="event-1",
            aws_region="us-east-1",
        ),
    )


def _sample_word_label_remove() -> StreamMessage:
    """REMOVE event - should go to FIFO delete queue."""
    return StreamMessage(
        entity_type="RECEIPT_WORD_LABEL",
        entity_data={
            "image_id": "img-2",
            "receipt_id": 2,
            "line_id": 1,
            "word_id": 1,
            "label": "TOTAL",
        },
        changes={},
        event_name="REMOVE",
        collections=(ChromaDBCollection.WORDS,),
        context=StreamRecordContext(
            timestamp=datetime.now().isoformat(),
            record_id="event-2",
            aws_region="us-east-1",
        ),
    )


def _sample_compaction_run() -> StreamMessage:
    """INSERT event - should go to Standard queues."""
    return StreamMessage(
        entity_type="COMPACTION_RUN",
        entity_data={
            "run_id": "run-123",
            "image_id": "img-3",
            "receipt_id": 3,
            "delta_s3_prefix": "s3://bucket/prefix",
        },
        changes={},
        event_name="INSERT",
        collections=(ChromaDBCollection.LINES, ChromaDBCollection.WORDS),
        context=StreamRecordContext(
            timestamp=datetime.now().isoformat(),
            record_id="event-3",
            aws_region="us-east-1",
        ),
    )


def _get_messages(sqs: Any, queue_url: str) -> list[dict[str, Any]]:
    resp: dict[str, Any] = sqs.receive_message(
        QueueUrl=queue_url,
        MaxNumberOfMessages=10,
        MessageAttributeNames=["All"],
    )
    return cast(list[dict[str, Any]], resp.get("Messages", []))


def test_modify_routed_to_standard_queues(
    moto_sqs: Any, hybrid_queues: dict[str, str]
) -> None:
    """MODIFY events go to Standard queues, not FIFO."""
    msg = _sample_place_message()
    sent = publish_messages([msg])
    assert sent == 2

    # Should be in Standard queues
    lines_msgs = _get_messages(moto_sqs, hybrid_queues["lines_standard"])
    words_msgs = _get_messages(moto_sqs, hybrid_queues["words_standard"])
    assert len(lines_msgs) == 1
    assert len(words_msgs) == 1

    # Should NOT be in FIFO queues
    lines_delete_msgs = _get_messages(moto_sqs, hybrid_queues["lines_delete"])
    words_delete_msgs = _get_messages(moto_sqs, hybrid_queues["words_delete"])
    assert len(lines_delete_msgs) == 0
    assert len(words_delete_msgs) == 0

    body = json.loads(lines_msgs[0]["Body"])
    assert body["entity_type"] == "RECEIPT_PLACE"
    assert body["event_name"] == "MODIFY"
    assert body["entity_data"]["receipt_id"] == 1

    attrs = lines_msgs[0].get("MessageAttributes", {})
    assert attrs["collection"]["StringValue"] == "lines"


def test_remove_routed_to_fifo_queues(
    moto_sqs: Any, hybrid_queues: dict[str, str]
) -> None:
    """REMOVE events go to FIFO queues, not Standard."""
    msg = _sample_place_remove()
    sent = publish_messages([msg])
    assert sent == 2

    # Should be in FIFO queues
    lines_delete_msgs = _get_messages(moto_sqs, hybrid_queues["lines_delete"])
    words_delete_msgs = _get_messages(moto_sqs, hybrid_queues["words_delete"])
    assert len(lines_delete_msgs) == 1
    assert len(words_delete_msgs) == 1

    # Should NOT be in Standard queues
    lines_msgs = _get_messages(moto_sqs, hybrid_queues["lines_standard"])
    words_msgs = _get_messages(moto_sqs, hybrid_queues["words_standard"])
    assert len(lines_msgs) == 0
    assert len(words_msgs) == 0

    body = json.loads(lines_delete_msgs[0]["Body"])
    assert body["entity_type"] == "RECEIPT_PLACE"
    assert body["event_name"] == "REMOVE"


def test_word_label_remove_only_words_fifo_queue(
    moto_sqs: Any, hybrid_queues: dict[str, str]
) -> None:
    """RECEIPT_WORD_LABEL REMOVE goes to words FIFO queue only."""
    msg = _sample_word_label_remove()
    sent = publish_messages([msg])
    assert sent == 1

    # Should be in words FIFO queue only
    words_delete_msgs = _get_messages(moto_sqs, hybrid_queues["words_delete"])
    assert len(words_delete_msgs) == 1

    # Should NOT be in any other queue
    lines_msgs = _get_messages(moto_sqs, hybrid_queues["lines_standard"])
    words_msgs = _get_messages(moto_sqs, hybrid_queues["words_standard"])
    lines_delete_msgs = _get_messages(moto_sqs, hybrid_queues["lines_delete"])
    assert len(lines_msgs) == 0
    assert len(words_msgs) == 0
    assert len(lines_delete_msgs) == 0

    body = json.loads(words_delete_msgs[0]["Body"])
    assert body["event_name"] == "REMOVE"
    assert body["entity_type"] == "RECEIPT_WORD_LABEL"


def test_insert_routed_to_standard_queues(
    moto_sqs: Any, hybrid_queues: dict[str, str]
) -> None:
    """INSERT events (like COMPACTION_RUN) go to Standard queues."""
    msg = _sample_compaction_run()
    sent = publish_messages([msg])
    assert sent == 2

    # Should be in Standard queues
    lines_msgs = _get_messages(moto_sqs, hybrid_queues["lines_standard"])
    words_msgs = _get_messages(moto_sqs, hybrid_queues["words_standard"])
    assert len(lines_msgs) == 1
    assert len(words_msgs) == 1

    # Should NOT be in FIFO queues
    lines_delete_msgs = _get_messages(moto_sqs, hybrid_queues["lines_delete"])
    words_delete_msgs = _get_messages(moto_sqs, hybrid_queues["words_delete"])
    assert len(lines_delete_msgs) == 0
    assert len(words_delete_msgs) == 0

    body = json.loads(lines_msgs[0]["Body"])
    assert body["entity_type"] == "COMPACTION_RUN"
    assert body["entity_data"]["run_id"] == "run-123"


def test_batches_above_ten_messages(
    moto_sqs: Any, hybrid_queues: dict[str, str]
) -> None:
    """Large batches are split correctly across Standard queues."""
    msgs = [
        StreamMessage(
            entity_type="RECEIPT_PLACE",
            entity_data={"image_id": f"img-{i}", "receipt_id": i},
            changes={"merchant_name": FieldChange(old="A", new="B")},
            event_name="MODIFY",
            collections=(ChromaDBCollection.LINES, ChromaDBCollection.WORDS),
            context=StreamRecordContext(
                timestamp=datetime.now().isoformat(),
                record_id=f"event-{i}",
                aws_region="us-east-1",
            ),
        )
        for i in range(15)
    ]

    sent = publish_messages(msgs)
    # 15 records, two queues each => 30
    assert sent == 30

    lines_msgs = _get_messages(moto_sqs, hybrid_queues["lines_standard"])
    words_msgs = _get_messages(moto_sqs, hybrid_queues["words_standard"])
    assert len(lines_msgs) == 10  # moto default receive max
    assert len(words_msgs) == 10


def test_mixed_events_route_correctly(
    moto_sqs: Any, hybrid_queues: dict[str, str]
) -> None:
    """Mixed INSERT/MODIFY/REMOVE events route to correct queues."""
    msgs = [
        _sample_compaction_run(),  # INSERT -> Standard
        _sample_place_message(),  # MODIFY -> Standard
        _sample_place_remove(),  # REMOVE -> FIFO
        _sample_word_label_remove(),  # REMOVE -> FIFO (words only)
    ]

    sent = publish_messages(msgs)
    # INSERT: 2, MODIFY: 2, place REMOVE: 2, word_label REMOVE: 1 = 7
    assert sent == 7

    # Standard queues should have INSERT and MODIFY
    lines_standard = _get_messages(moto_sqs, hybrid_queues["lines_standard"])
    words_standard = _get_messages(moto_sqs, hybrid_queues["words_standard"])
    assert len(lines_standard) == 2  # 1 INSERT + 1 MODIFY
    assert len(words_standard) == 2  # 1 INSERT + 1 MODIFY

    # FIFO queues should have REMOVE only
    lines_delete = _get_messages(moto_sqs, hybrid_queues["lines_delete"])
    words_delete = _get_messages(moto_sqs, hybrid_queues["words_delete"])
    assert len(lines_delete) == 1  # 1 place REMOVE
    assert len(words_delete) == 2  # 1 place REMOVE + 1 word_label REMOVE


def test_missing_queue_env_returns_zero(moto_sqs: Any) -> None:
    """Missing queue URLs cause zero messages to be sent."""
    os.environ.pop("LINES_STANDARD_QUEUE_URL", None)
    os.environ.pop("WORDS_STANDARD_QUEUE_URL", None)
    os.environ.pop("LINES_DELETE_QUEUE_URL", None)
    os.environ.pop("WORDS_DELETE_QUEUE_URL", None)
    msg = _sample_place_message()
    sent = publish_messages([msg])
    assert sent == 0
