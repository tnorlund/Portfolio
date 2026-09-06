from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, cast

import boto3
import pytest
from moto import mock_aws

from receipt_dynamo_stream import (
    FieldChange,
    StreamMessage,
    StreamRecordContext,
    TargetQueue,
    publish_messages,
)
from receipt_dynamo_stream.exceptions import QueueConfigurationError


@pytest.fixture
def moto_sqs() -> Any:
    _original_region = os.environ.get("AWS_DEFAULT_REGION")
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
    with mock_aws():
        yield boto3.client("sqs", region_name="us-east-1")
    if _original_region is None:
        os.environ.pop("AWS_DEFAULT_REGION", None)
    else:
        os.environ["AWS_DEFAULT_REGION"] = _original_region


@pytest.fixture
def standard_queues(moto_sqs: Any) -> dict[str, str]:
    """Create Standard SQS queues for high-throughput testing."""
    summary_queue = moto_sqs.create_queue(
        QueueName="summary-queue",
    )["QueueUrl"]
    line_item_queue = moto_sqs.create_queue(
        QueueName="line-item-queue",
    )["QueueUrl"]

    os.environ["RECEIPT_SUMMARY_QUEUE_URL"] = summary_queue
    os.environ["LINE_ITEM_QUEUE_URL"] = line_item_queue
    return {"summary": summary_queue, "line_items": line_item_queue}


def _sample_place_message() -> StreamMessage:
    return StreamMessage(
        entity_type="RECEIPT_PLACE",
        entity_data={"image_id": "img-1", "receipt_id": 1},
        changes={
            "merchant_name": FieldChange(old="Old", new="New"),
        },
        event_name="MODIFY",
        collections=(TargetQueue.RECEIPT_SUMMARY,),
        context=StreamRecordContext(
            timestamp=datetime.now().isoformat(),
            record_id="event-1",
            aws_region="us-east-1",
        ),
    )


def _sample_items_section_message() -> StreamMessage:
    return StreamMessage(
        entity_type="RECEIPT_SECTION",
        entity_data={
            "image_id": "img-2",
            "receipt_id": 2,
            "section_type": "ITEMS",
        },
        changes={},
        event_name="REMOVE",
        collections=(TargetQueue.LINE_ITEMS,),
        context=StreamRecordContext(
            timestamp=datetime.now().isoformat(),
            record_id="event-2",
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


def test_place_routed_to_summary_queue(
    moto_sqs: Any, standard_queues: dict[str, str]
) -> None:
    msg = _sample_place_message()
    sent = publish_messages([msg])
    assert sent == 1

    summary_msgs = _get_messages(moto_sqs, standard_queues["summary"])
    line_item_msgs = _get_messages(moto_sqs, standard_queues["line_items"])

    assert len(summary_msgs) == 1
    assert len(line_item_msgs) == 0

    body = json.loads(summary_msgs[0]["Body"])
    assert body["entity_type"] == "RECEIPT_PLACE"
    assert body["entity_data"]["receipt_id"] == 1
    assert "merchant_name" in body["changes"]

    attrs = summary_msgs[0].get("MessageAttributes", {})
    assert attrs["collection"]["StringValue"] == "receipt_summary"


def test_items_section_remove_only_line_item_queue(
    moto_sqs: Any, standard_queues: dict[str, str]
) -> None:
    msg = _sample_items_section_message()
    sent = publish_messages([msg])
    assert sent == 1

    summary_msgs = _get_messages(moto_sqs, standard_queues["summary"])
    line_item_msgs = _get_messages(moto_sqs, standard_queues["line_items"])

    assert len(summary_msgs) == 0
    assert len(line_item_msgs) == 1
    body = json.loads(line_item_msgs[0]["Body"])
    assert body["event_name"] == "REMOVE"
    assert body["entity_type"] == "RECEIPT_SECTION"


def test_batches_above_ten_messages(
    moto_sqs: Any, standard_queues: dict[str, str]
) -> None:
    msgs = [
        StreamMessage(
            entity_type="RECEIPT_SECTION",
            entity_data={"image_id": f"img-{i}", "receipt_id": i},
            changes={"line_ids": FieldChange(old=[1], new=[1, 2])},
            event_name="MODIFY",
            collections=(TargetQueue.RECEIPT_SUMMARY, TargetQueue.LINE_ITEMS),
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

    summary_msgs = _get_messages(moto_sqs, standard_queues["summary"])
    line_item_msgs = _get_messages(moto_sqs, standard_queues["line_items"])
    assert len(summary_msgs) == 10  # moto default receive max
    assert len(line_item_msgs) == 10


def test_missing_queue_env_raises(moto_sqs: Any) -> None:
    """The summary/line-item legs keep their fail-loud contract."""
    os.environ.pop("RECEIPT_SUMMARY_QUEUE_URL", None)
    msg = _sample_place_message()
    with pytest.raises(QueueConfigurationError) as caught:
        publish_messages([msg])
    assert caught.value.environment_variable == "RECEIPT_SUMMARY_QUEUE_URL"
