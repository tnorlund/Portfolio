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
    publish_messages,
)


@pytest.fixture
def moto_sqs() -> Any:
    with mock_aws():
        yield boto3.client("sqs", region_name="us-east-1")


@pytest.fixture
def fifo_queues(moto_sqs: Any) -> dict[str, str]:
    lines_queue = moto_sqs.create_queue(
        QueueName="lines.fifo",
        Attributes={
            "FifoQueue": "true",
            "ContentBasedDeduplication": "true",
        },
    )["QueueUrl"]
    words_queue = moto_sqs.create_queue(
        QueueName="words.fifo",
        Attributes={
            "FifoQueue": "true",
            "ContentBasedDeduplication": "true",
        },
    )["QueueUrl"]

    os.environ["LINES_QUEUE_URL"] = lines_queue
    os.environ["WORDS_QUEUE_URL"] = words_queue
    return {"lines": lines_queue, "words": words_queue}


def _sample_metadata_message() -> StreamMessage:
    return StreamMessage(
        entity_type="RECEIPT_METADATA",
        entity_data={"image_id": "img-1", "receipt_id": 1},
        changes={
            "merchant_name": FieldChange(old="Old", new="New"),
        },
        event_name="MODIFY",
        collections=(ChromaDBCollection.LINES, ChromaDBCollection.WORDS),
        timestamp=datetime.now().isoformat(),
        stream_record_id="event-1",
        aws_region="us-east-1",
    )


def _sample_word_label_remove() -> StreamMessage:
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
        timestamp=datetime.now().isoformat(),
        stream_record_id="event-2",
        aws_region="us-east-1",
    )


def _sample_compaction_run() -> StreamMessage:
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
        timestamp=datetime.now().isoformat(),
        stream_record_id="event-3",
        aws_region="us-east-1",
    )


def _get_messages(sqs: Any, queue_url: str) -> list[dict[str, Any]]:
    resp: dict[str, Any] = sqs.receive_message(
        QueueUrl=queue_url,
        MaxNumberOfMessages=10,
        MessageAttributeNames=["All"],
    )
    return cast(list[dict[str, Any]], resp.get("Messages", []))


def test_metadata_routed_to_both_queues(
    moto_sqs: Any, fifo_queues: dict[str, str]
) -> None:
    msg = _sample_metadata_message()
    sent = publish_messages([msg])
    assert sent == 2

    lines_msgs = _get_messages(moto_sqs, fifo_queues["lines"])
    words_msgs = _get_messages(moto_sqs, fifo_queues["words"])

    assert len(lines_msgs) == 1
    assert len(words_msgs) == 1

    body = json.loads(lines_msgs[0]["Body"])
    assert body["entity_type"] == "RECEIPT_METADATA"
    assert body["entity_data"]["receipt_id"] == 1
    assert "merchant_name" in body["changes"]

    attrs = lines_msgs[0].get("MessageAttributes", {})
    assert attrs["collection"]["StringValue"] == "lines"


def test_word_label_remove_only_words_queue(
    moto_sqs: Any, fifo_queues: dict[str, str]
) -> None:
    msg = _sample_word_label_remove()
    sent = publish_messages([msg])
    assert sent == 1

    lines_msgs = _get_messages(moto_sqs, fifo_queues["lines"])
    words_msgs = _get_messages(moto_sqs, fifo_queues["words"])

    assert len(lines_msgs) == 0
    assert len(words_msgs) == 1
    body = json.loads(words_msgs[0]["Body"])
    assert body["event_name"] == "REMOVE"
    assert body["entity_type"] == "RECEIPT_WORD_LABEL"


def test_compaction_run_sends_one_per_collection(
    moto_sqs: Any, fifo_queues: dict[str, str]
) -> None:
    msg = _sample_compaction_run()
    sent = publish_messages([msg])
    assert sent == 2

    lines_msgs = _get_messages(moto_sqs, fifo_queues["lines"])
    words_msgs = _get_messages(moto_sqs, fifo_queues["words"])

    assert len(lines_msgs) == 1
    assert len(words_msgs) == 1
    body = json.loads(lines_msgs[0]["Body"])
    assert body["entity_type"] == "COMPACTION_RUN"
    assert body["entity_data"]["run_id"] == "run-123"


def test_batches_above_ten_messages(
    moto_sqs: Any, fifo_queues: dict[str, str]
) -> None:
    msgs = [
        StreamMessage(
            entity_type="RECEIPT_METADATA",
            entity_data={"image_id": f"img-{i}", "receipt_id": i},
            changes={"merchant_name": FieldChange(old="A", new="B")},
            event_name="MODIFY",
            collections=(ChromaDBCollection.LINES, ChromaDBCollection.WORDS),
            timestamp=datetime.now().isoformat(),
            stream_record_id=f"event-{i}",
            aws_region="us-east-1",
        )
        for i in range(15)
    ]

    sent = publish_messages(msgs)
    # 15 records, two queues each => 30
    assert sent == 30

    lines_msgs = _get_messages(moto_sqs, fifo_queues["lines"])
    words_msgs = _get_messages(moto_sqs, fifo_queues["words"])
    assert len(lines_msgs) == 10  # moto default receive max
    assert len(words_msgs) == 10


def test_missing_queue_env_returns_zero(moto_sqs: Any) -> None:
    os.environ.pop("LINES_QUEUE_URL", None)
    os.environ.pop("WORDS_QUEUE_URL", None)
    msg = _sample_metadata_message()
    sent = publish_messages([msg])
    assert sent == 0
