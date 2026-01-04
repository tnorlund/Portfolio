from datetime import datetime
from typing import Any, Iterator, cast

import boto3
import pytest
from moto import mock_aws
from receipt_dynamo.entities.receipt_place import ReceiptPlace
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel

from receipt_dynamo_stream import (
    get_chromadb_relevant_changes,
    parse_stream_record,
)


@pytest.fixture
def dynamodb_with_stream() -> Iterator[tuple[Any, Any, str]]:
    """
    Spin up a DynamoDB table with streams enabled for integration tests.
    """
    with mock_aws():
        dynamodb = boto3.client("dynamodb", region_name="us-east-1")
        streams = boto3.client("dynamodbstreams", region_name="us-east-1")
        table_name = "ReceiptData"

        dynamodb.create_table(
            TableName=table_name,
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
            StreamSpecification={
                "StreamEnabled": True,
                "StreamViewType": "NEW_AND_OLD_IMAGES",
            },
        )

        dynamodb.get_waiter("table_exists").wait(TableName=table_name)
        yield dynamodb, streams, table_name


def _get_stream_records(
    dynamodb_client: Any, streams_client: Any, table_name: str
) -> list[dict[str, object]]:
    stream_arn = dynamodb_client.describe_table(TableName=table_name)["Table"][
        "LatestStreamArn"
    ]
    stream_desc = streams_client.describe_stream(StreamArn=stream_arn)[
        "StreamDescription"
    ]
    shard_id = stream_desc["Shards"][0]["ShardId"]
    iterator = streams_client.get_shard_iterator(
        StreamArn=stream_arn,
        ShardId=shard_id,
        ShardIteratorType="TRIM_HORIZON",
    )["ShardIterator"]
    records = streams_client.get_records(ShardIterator=iterator)["Records"]
    return cast(list[dict[str, object]], records)


def _place_entity(merchant_name: str = "Cafe Nero") -> ReceiptPlace:
    return ReceiptPlace(
        image_id="550e8400-e29b-41d4-a716-446655440000",
        receipt_id=1,
        place_id="place123",
        merchant_name=merchant_name,
        formatted_address="123 Main St",
        phone_number="555-123-4567",
        matched_fields=["name"],
        validated_by="INFERENCE",
        timestamp=datetime.fromisoformat("2024-01-01T00:00:00"),
    )


def _word_label_entity(label: str = "TOTAL") -> ReceiptWordLabel:
    return ReceiptWordLabel(
        image_id="550e8400-e29b-41d4-a716-446655440000",
        receipt_id=1,
        line_id=1,
        word_id=1,
        label=label,
        reasoning="initial",
        timestamp_added=datetime.fromisoformat("2024-01-01T00:00:00"),
        validation_status="NONE",
    )


def test_parse_stream_record_for_place_insert_and_modify(
    dynamodb_with_stream: tuple[Any, Any, str],
) -> None:
    dynamodb, streams, table_name = dynamodb_with_stream

    place = _place_entity()
    dynamodb.put_item(TableName=table_name, Item=place.to_item())

    records = _get_stream_records(dynamodb, streams, table_name)
    insert_record = next(
        rec for rec in records if rec["eventName"] == "INSERT"
    )

    parsed_insert = parse_stream_record(insert_record)
    assert parsed_insert is not None
    assert parsed_insert.entity_type == "RECEIPT_PLACE"
    assert parsed_insert.new_entity is not None
    assert isinstance(parsed_insert.new_entity, ReceiptPlace)
    assert parsed_insert.new_entity.merchant_name == "Cafe Nero"
    assert parsed_insert.old_entity is None

    dynamodb.update_item(
        TableName=table_name,
        Key={"PK": place.key["PK"], "SK": place.key["SK"]},
        UpdateExpression="SET merchant_name = :m",
        ExpressionAttributeValues={":m": {"S": "New Merchant"}},
        ReturnValues="ALL_NEW",
    )

    updated_records = _get_stream_records(dynamodb, streams, table_name)
    modify_record = next(
        rec for rec in updated_records if rec["eventName"] == "MODIFY"
    )

    parsed_modify = parse_stream_record(modify_record)
    assert parsed_modify is not None
    assert parsed_modify.entity_type == "RECEIPT_PLACE"
    assert parsed_modify.old_entity is not None
    assert parsed_modify.new_entity is not None
    assert isinstance(parsed_modify.old_entity, ReceiptPlace)
    assert isinstance(parsed_modify.new_entity, ReceiptPlace)
    changes = get_chromadb_relevant_changes(
        parsed_modify.entity_type,
        parsed_modify.old_entity,
        parsed_modify.new_entity,
    )
    assert "merchant_name" in changes
    assert changes["merchant_name"].new == "New Merchant"


def test_parse_word_label_remove_event(
    dynamodb_with_stream: tuple[Any, Any, str],
) -> None:
    dynamodb, streams, table_name = dynamodb_with_stream

    word_label = _word_label_entity()
    dynamodb.put_item(TableName=table_name, Item=word_label.to_item())
    dynamodb.delete_item(
        TableName=table_name,
        Key={"PK": word_label.key["PK"], "SK": word_label.key["SK"]},
    )

    records = _get_stream_records(dynamodb, streams, table_name)
    remove_record = next(
        rec for rec in records if rec["eventName"] == "REMOVE"
    )

    parsed = parse_stream_record(remove_record)
    assert parsed is not None
    assert parsed.entity_type == "RECEIPT_WORD_LABEL"
    assert parsed.old_entity is not None
    assert parsed.new_entity is None
