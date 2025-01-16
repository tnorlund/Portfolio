from typing import Literal
from datetime import datetime
import pytest
import boto3
from dynamo import Receipt, Image, DynamoClient
import json

correct_receipt_params = {
    "id": 1,
    "image_id": 1,
    "width": 10,
    "height": 20,
    "timestamp_added": datetime.now().isoformat(),
    "raw_s3_bucket": "bucket",
    "raw_s3_key": "key",
    "top_left": {"x": 0, "y": 0},
    "top_right": {"x": 10, "y": 0},
    "bottom_left": {"x": 0, "y": 20},
    "bottom_right": {"x": 10, "y": 20},
    "sha256": "sha256",
}

correct_image_params = {
    "id": 1,
    "width": 10,
    "height": 20,
    "timestamp_added": datetime.now().isoformat(),
    "raw_s3_bucket": "bucket",
    "raw_s3_key": "key",
}


def test_addReceipt(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    dynamo_client = DynamoClient(dynamodb_table)
    receipt = Receipt(**correct_receipt_params)

    # Act
    dynamo_client.addReceipt(receipt)

    # Assert
    response_receipt = dynamo_client.getReceipt(receipt.image_id, receipt.id)
    assert response_receipt == receipt


def test_addReceipt_error_receipt_that_exists(dynamodb_table: str):
    # Arrange
    dynamo_client = DynamoClient(dynamodb_table)
    receipt = Receipt(**correct_receipt_params)

    # Act
    dynamo_client.addReceipt(receipt)
    with pytest.raises(ValueError):
        dynamo_client.addReceipt(receipt)


def test_addReceipts(dynamodb_table: str):
    # Arrange
    dynamo_client = DynamoClient(dynamodb_table)
    receipts = [
        Receipt(**correct_receipt_params),
        Receipt(**{**correct_receipt_params, "id": 2}),
    ]

    # Act
    dynamo_client.addReceipts(receipts)

    # Assert
    response_receipts = dynamo_client.listReceipts()
    assert receipts == response_receipts


def test_addReceipts_error_receipt_that_exists(dynamodb_table: str):
    # Arrange
    dynamo_client = DynamoClient(dynamodb_table)
    receipts = [
        Receipt(**correct_receipt_params),
        Receipt(**correct_receipt_params),
    ]

    # Act
    dynamo_client.addReceipt(receipts[0])
    with pytest.raises(ValueError):
        dynamo_client.addReceipts(receipts)


def test_updateReceipt(dynamodb_table: str):
    # Arrange
    dynamo_client = DynamoClient(dynamodb_table)
    receipt = Receipt(**correct_receipt_params)
    dynamo_client.addReceipt(receipt)
    receipt.s3_key = "new/path"

    # Act
    dynamo_client.updateReceipt(receipt)

    # Assert
    response_receipt = dynamo_client.getReceipt(receipt.image_id, receipt.id)
    assert response_receipt == receipt


def test_updateReceipt_error_receipt_not_exists(dynamodb_table: str):
    # Arrange
    dynamo_client = DynamoClient(dynamodb_table)
    receipt = Receipt(**correct_receipt_params)

    # Act
    with pytest.raises(ValueError):
        dynamo_client.updateReceipt(receipt)


def test_updateReceipts(dynamodb_table: str):
    # Arrange
    dynamo_client = DynamoClient(dynamodb_table)
    receipts = [
        Receipt(**correct_receipt_params),
        Receipt(**{**correct_receipt_params, "id": 2}),
    ]
    dynamo_client.addReceipts(receipts)
    for receipt in receipts:
        receipt.s3_key = "new/path"

    # Act
    dynamo_client.updateReceipts(receipts)

    # Assert
    assert dynamo_client.listReceipts() == receipts


def test_updateReceipts_error_receipt_not_exists(dynamodb_table: str):
    # Arrange
    dynamo_client = DynamoClient(dynamodb_table)
    receipts = [
        Receipt(**correct_receipt_params),
        Receipt(**correct_receipt_params),
    ]

    # Act
    with pytest.raises(ValueError):
        dynamo_client.updateReceipts(receipts)


def test_deleteReceipt(dynamodb_table: str):
    # Arrange
    dynamo_client = DynamoClient(dynamodb_table)
    receipt = Receipt(**correct_receipt_params)
    dynamo_client.addReceipt(receipt)

    # Act
    dynamo_client.deleteReceipt(receipt)

    # Assert
    response_receipts = dynamo_client.listReceipts()
    assert receipt not in response_receipts


def test_deleteReceipt_error_receipt_not_exists(dynamodb_table: str):
    # Arrange
    dynamo_client = DynamoClient(dynamodb_table)
    receipt = Receipt(**correct_receipt_params)

    # Act
    with pytest.raises(ValueError):
        dynamo_client.deleteReceipt(receipt)


def test_deleteReceipts(dynamodb_table: str):
    # Arrange
    dynamo_client = DynamoClient(dynamodb_table)
    receipts = [
        Receipt(**correct_receipt_params),
        Receipt(**{**correct_receipt_params, "id": 2}),
    ]
    dynamo_client.addReceipts(receipts)

    # Act
    dynamo_client.deleteReceipts(receipts)

    # Assert
    response_receipts = dynamo_client.listReceipts()
    assert not response_receipts


def test_deleteReceipts_error_receipt_not_exists(dynamodb_table: str):
    # Arrange
    dynamo_client = DynamoClient(dynamodb_table)
    receipts = [
        Receipt(**correct_receipt_params),
        Receipt(**correct_receipt_params),
    ]

    # Act
    with pytest.raises(ValueError):
        dynamo_client.deleteReceipts(receipts)


def test_deleteReceiptsFromImage(dynamodb_table: str):
    # Arrange
    dynamo_client = DynamoClient(dynamodb_table)
    receipt = Receipt(**correct_receipt_params)
    image = Image(**correct_image_params)
    receipt_different_image = Receipt(**{**correct_receipt_params, "image_id": 2})
    dynamo_client.addReceipts([receipt, receipt_different_image])
    dynamo_client.addImage(image)

    # Act
    dynamo_client.deleteReceiptsFromImage(receipt.image_id)

    # Assert
    assert dynamo_client.getImage(image.id) == image
    assert (
        dynamo_client.getReceipt(
            receipt_different_image.image_id, receipt_different_image.id
        )
        == receipt_different_image
    )
    with pytest.raises(ValueError):
        dynamo_client.getReceipt(receipt.image_id, receipt.id)


def test_deleteReceiptsFromImage_error_no_receipts_in_image(dynamodb_table: str):
    # Arrange
    dynamo_client = DynamoClient(dynamodb_table)
    image_id = 1

    # Act
    with pytest.raises(ValueError):
        dynamo_client.deleteReceiptsFromImage(image_id)


def test_getReceipt(dynamodb_table: str):
    # Arrange
    dynamo_client = DynamoClient(dynamodb_table)
    receipt = Receipt(**correct_receipt_params)
    dynamo_client.addReceipt(receipt)

    # Act
    retrieved_receipt = dynamo_client.getReceipt(receipt.image_id, receipt.id)

    # Assert
    assert retrieved_receipt == receipt


def test_getReceipt_error_receipt_not_exists(dynamodb_table: str):
    # Arrange
    dynamo_client = DynamoClient(dynamodb_table)
    receipt = Receipt(**correct_receipt_params)

    # Act
    with pytest.raises(ValueError):
        dynamo_client.getReceipt(receipt.image_id, receipt.id)


def test_getReceiptsFromImage(dynamodb_table: str):
    # Arrange
    dynamo_client = DynamoClient(dynamodb_table)
    receipt = Receipt(**correct_receipt_params)
    receipt_same_image = Receipt(**{**correct_receipt_params, "id": 2})
    receipt_different_image = Receipt(**{**correct_receipt_params, "image_id": 2})
    dynamo_client.addReceipts([receipt, receipt_same_image, receipt_different_image])

    # Act
    retrieved_receipts = dynamo_client.getReceiptsFromImage(receipt.image_id)

    # Assert
    assert receipt in retrieved_receipts
    assert receipt_same_image in retrieved_receipts
    assert receipt_different_image not in retrieved_receipts
