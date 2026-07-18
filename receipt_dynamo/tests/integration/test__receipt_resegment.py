"""Integration tests for receipt ID reservation and atomic activation."""

from datetime import datetime, timezone
from uuid import uuid4

import boto3
import pytest

from receipt_dynamo import DynamoClient, Receipt, ReceiptLetter, ReceiptWord
from receipt_dynamo.data.shared_exceptions import (
    DynamoDBError,
    EntityNotFoundError,
)


def _receipt(image_id: str, receipt_id: int) -> Receipt:
    return Receipt(
        image_id=image_id,
        receipt_id=receipt_id,
        width=100,
        height=200,
        timestamp_added=datetime.now(timezone.utc),
        raw_s3_bucket="raw-bucket",
        raw_s3_key=f"receipts/{image_id}/{receipt_id}.png",
        top_left={"x": 0.0, "y": 1.0},
        top_right={"x": 1.0, "y": 1.0},
        bottom_left={"x": 0.0, "y": 0.0},
        bottom_right={"x": 1.0, "y": 0.0},
    )


@pytest.mark.integration
def test_reservations_are_hidden_and_commit_is_atomic(dynamodb_table):
    image_id = str(uuid4())
    operation_id = str(uuid4())
    client = DynamoClient(dynamodb_table)
    source = _receipt(image_id, 1)
    outputs = [_receipt(image_id, 2), _receipt(image_id, 3)]
    client.add_receipt(source)

    client.reserve_receipt_ids(image_id, [2, 3], operation_id)
    client.reserve_receipt_ids(image_id, [2, 3], operation_id)
    with pytest.raises(DynamoDBError):
        client.reserve_receipt_ids(image_id, [2, 3], str(uuid4()))
    assert [
        receipt.receipt_id
        for receipt in client.get_receipts_from_image(image_id)
    ] == [1]

    client.commit_receipt_resegmentation(source, outputs, operation_id)
    assert sorted(
        receipt.receipt_id
        for receipt in client.get_receipts_from_image(image_id)
    ) == [2, 3]
    with pytest.raises(EntityNotFoundError):
        client.get_receipt(image_id, 1)


@pytest.mark.integration
def test_release_removes_staged_children_and_reservation(dynamodb_table):
    image_id = str(uuid4())
    operation_id = str(uuid4())
    client = DynamoClient(dynamodb_table)
    client.reserve_receipt_ids(image_id, [2], operation_id)
    client.add_receipt_word(
        ReceiptWord(
            image_id=image_id,
            receipt_id=2,
            line_id=1,
            word_id=1,
            text="staged",
            bounding_box={"x": 0.1, "y": 0.1, "width": 0.1, "height": 0.1},
            top_left={"x": 0.1, "y": 0.2},
            top_right={"x": 0.2, "y": 0.2},
            bottom_left={"x": 0.1, "y": 0.1},
            bottom_right={"x": 0.2, "y": 0.1},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=1.0,
        )
    )

    client.release_receipt_id_reservations(image_id, [2], operation_id)

    raw_client = boto3.client("dynamodb", region_name="us-east-1")
    response = raw_client.query(
        TableName=dynamodb_table,
        KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
        ExpressionAttributeValues={
            ":pk": {"S": f"IMAGE#{image_id}"},
            ":prefix": {"S": "RECEIPT#00002"},
        },
    )
    assert response["Items"] == []


@pytest.mark.integration
def test_release_is_idempotent_but_never_deletes_receipts(dynamodb_table):
    image_id = str(uuid4())
    operation_id = str(uuid4())
    client = DynamoClient(dynamodb_table)

    # Releasing reservations that never existed (or were already released)
    # must succeed so a crashed apply can always be retried.
    client.release_receipt_id_reservations(image_id, [2, 3], operation_id)
    client.reserve_receipt_ids(image_id, [2], operation_id)
    client.release_receipt_id_reservations(image_id, [2], operation_id)
    client.release_receipt_id_reservations(image_id, [2], operation_id)

    # A committed receipt occupying the same key must never be deleted,
    # and the guarded parent delete must fail BEFORE any child rows are
    # touched (a rollback racing a landed commit must not destroy data).
    client.add_receipt(_receipt(image_id, 2))
    client.add_receipt_word(
        ReceiptWord(
            image_id=image_id,
            receipt_id=2,
            line_id=1,
            word_id=1,
            text="committed",
            bounding_box={"x": 0.1, "y": 0.1, "width": 0.1, "height": 0.1},
            top_left={"x": 0.1, "y": 0.2},
            top_right={"x": 0.2, "y": 0.2},
            bottom_left={"x": 0.1, "y": 0.1},
            bottom_right={"x": 0.2, "y": 0.1},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=1.0,
        )
    )
    with pytest.raises(DynamoDBError):
        client.release_receipt_id_reservations(image_id, [2], operation_id)
    assert [
        receipt.receipt_id
        for receipt in client.get_receipts_from_image(image_id)
    ] == [2]
    assert len(client.get_receipt_details(image_id, 2).words) == 1


@pytest.mark.integration
def test_bulk_receipt_letter_snapshot_filters_one_receipt(dynamodb_table):
    image_id = str(uuid4())
    client = DynamoClient(dynamodb_table)
    geometry = {
        "bounding_box": {"x": 0.1, "y": 0.1, "width": 0.05, "height": 0.1},
        "top_left": {"x": 0.1, "y": 0.2},
        "top_right": {"x": 0.15, "y": 0.2},
        "bottom_left": {"x": 0.1, "y": 0.1},
        "bottom_right": {"x": 0.15, "y": 0.1},
        "angle_degrees": 0.0,
        "angle_radians": 0.0,
        "confidence": 1.0,
    }
    letters = [
        ReceiptLetter(
            image_id=image_id,
            receipt_id=receipt_id,
            line_id=1,
            word_id=1,
            letter_id=1,
            text=text,
            **geometry,
        )
        for receipt_id, text in ((1, "A"), (2, "B"))
    ]
    client.add_receipt_letters(letters)

    result = client.list_receipt_letters_from_receipt(image_id, 1)

    assert [(letter.receipt_id, letter.text) for letter in result] == [
        (1, "A")
    ]
