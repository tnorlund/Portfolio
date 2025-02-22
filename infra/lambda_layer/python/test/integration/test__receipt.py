# infra/lambda_layer/python/test/integration/test__receipt.py

import pytest
import boto3
from botocore.exceptions import ClientError
from datetime import datetime
from typing import Literal
from uuid import uuid4

from dynamo import (
    DynamoClient,
    Receipt,
    ReceiptLine,
    ReceiptWord,
    ReceiptWordTag,
    ReceiptLetter,
    Image,
)

# -------------------------------------------------------------------
#                        FIXTURES
# -------------------------------------------------------------------

image_id = str(uuid4())


@pytest.fixture
def sample_receipt():
    """
    Provides a sample Receipt for testing.
    """
    return Receipt(
        receipt_id=1,
        image_id=image_id,
        width=10,
        height=20,
        timestamp_added=datetime.now().isoformat(),
        raw_s3_bucket="bucket",
        raw_s3_key="key",
        top_left={"x": 0, "y": 0},
        top_right={"x": 10, "y": 0},
        bottom_left={"x": 0, "y": 20},
        bottom_right={"x": 10, "y": 20},
        sha256="sha256",
    )


@pytest.fixture
def sample_receipt_word():
    return ReceiptWord(
        image_id=image_id,
        receipt_id=1,
        line_id=1,
        word_id=1,
        text="example-word",
        bounding_box={"x": 0, "y": 0, "width": 10, "height": 20},
        top_left={"x": 0, "y": 0},
        top_right={"x": 10, "y": 0},
        bottom_left={"x": 0, "y": 20},
        bottom_right={"x": 10, "y": 20},
        angle_degrees=0,
        angle_radians=0,
        confidence=1,
    )


@pytest.fixture
def sample_receipt_word_tag():
    return ReceiptWordTag(
        image_id=image_id,
        receipt_id=1,
        line_id=1,
        word_id=1,
        tag="SampleTag",
        timestamp_added=datetime.now().isoformat(),
    )


@pytest.fixture
def sample_receipt_letter():
    return ReceiptLetter(
        image_id=image_id,
        receipt_id=1,
        line_id=1,
        word_id=1,
        letter_id=1,
        text="X",
        bounding_box={"x": 0, "y": 0, "width": 5, "height": 10},
        top_left={"x": 0, "y": 0},
        top_right={"x": 5, "y": 0},
        bottom_left={"x": 0, "y": 10},
        bottom_right={"x": 5, "y": 10},
        angle_degrees=0,
        angle_radians=0,
        confidence=1,
    )


@pytest.fixture
def sample_image():
    return Image(
        image_id=image_id,
        width=640,
        height=480,
        timestamp_added=datetime.now().isoformat(),
        raw_s3_bucket="bucket",
        raw_s3_key="key",
    )


# -------------------------------------------------------------------
#                   addReceipt / addReceipts
# -------------------------------------------------------------------


@pytest.mark.integration
def test_addReceipt_success(dynamodb_table: Literal["MyMockedTable"], sample_receipt):
    """
    Tests the happy path of addReceipt.
    """
    client = DynamoClient(dynamodb_table)
    client.addReceipt(sample_receipt)

    # Verify the receipt in DynamoDB
    retrieved = client.getReceipt(sample_receipt.image_id, sample_receipt.receipt_id)
    assert retrieved == sample_receipt, "Stored and retrieved receipts should match."


@pytest.mark.integration
def test_addReceipt_raises_value_error(dynamodb_table, sample_receipt, mocker):
    """
    Tests that addReceipt raises ValueError when the receipt is None.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(
        ValueError, match="Receipt parameter is required and cannot be None."
    ):
        client.addReceipt(None)  # type: ignore


@pytest.mark.integration
def test_addReceipt_raises_value_error_receipt_not_instance(
    dynamodb_table, sample_receipt, mocker
):
    """
    Tests that addReceipt raises ValueError when the receipt is not an instance of Receipt.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(
        ValueError, match="receipt must be an instance of the Receipt class."
    ):
        client.addReceipt("not-a-receipt")  # type: ignore


@pytest.mark.integration
def test_addReceipt_raises_conditional_check_failed(
    dynamodb_table, sample_receipt, mocker
):
    """
    Simulate a receipt already existing, causing a ConditionalCheckFailedException.
    """
    client = DynamoClient(dynamodb_table)
    mock_put = mocker.patch.object(
        client._client,
        "put_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ConditionalCheckFailedException",
                    "Message": "Item already exists",
                }
            },
            "PutItem",
        ),
    )

    with pytest.raises(ValueError, match="already exists"):
        client.addReceipt(sample_receipt)
    mock_put.assert_called_once()


@pytest.mark.integration
def test_addReceipt_raises_resource_not_found(dynamodb_table, sample_receipt, mocker):
    """
    Simulate a ResourceNotFoundException when adding a receipt.
    """
    client = DynamoClient(dynamodb_table)
    mock_put = mocker.patch.object(
        client._client,
        "put_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ResourceNotFoundException",
                    "Message": "Table not found",
                }
            },
            "PutItem",
        ),
    )

    with pytest.raises(Exception, match="Table not found"):
        # Note: In _receipt.py we do not specifically catch ResourceNotFoundException in addReceipt,
        # so it won't raise ValueError, it re-raises the original error if it's not ConditionalCheckFailedException.
        client.addReceipt(sample_receipt)
    mock_put.assert_called_once()


@pytest.mark.integration
def test_addReceipt_raises_provisioned_throughput_exceeded(
    dynamodb_table, sample_receipt, mocker
):
    """
    Simulate a ProvisionedThroughputExceededException when adding a receipt.
    """
    client = DynamoClient(dynamodb_table)
    mock_put = mocker.patch.object(
        client._client,
        "put_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ProvisionedThroughputExceededException",
                    "Message": "Provisioned throughput exceeded",
                }
            },
            "PutItem",
        ),
    )

    with pytest.raises(Exception, match="Provisioned throughput exceeded"):
        client.addReceipt(sample_receipt)
    mock_put.assert_called_once()


@pytest.mark.integration
def test_addReceipt_raises_internal_server_error(
    dynamodb_table, sample_receipt, mocker
):
    """
    Simulate an InternalServerError when adding a receipt.
    """
    client = DynamoClient(dynamodb_table)
    mock_put = mocker.patch.object(
        client._client,
        "put_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "InternalServerError",
                    "Message": "Internal server error",
                }
            },
            "PutItem",
        ),
    )

    with pytest.raises(Exception, match="Internal server error"):
        client.addReceipt(sample_receipt)


@pytest.mark.integration
def test_addReceipt_raises_unknown_error(dynamodb_table, sample_receipt, mocker):
    """
    Simulate an unknown exception from DynamoDB.
    """
    client = DynamoClient(dynamodb_table)
    mock_put = mocker.patch.object(
        client._client,
        "put_item",
        side_effect=ClientError(
            {"Error": {"Code": "UnknownError", "Message": "Something unexpected"}},
            "PutItem",
        ),
    )

    with pytest.raises(Exception, match="Something unexpected"):
        client.addReceipt(sample_receipt)
    mock_put.assert_called_once()


@pytest.mark.integration
def test_addReceipts_success(dynamodb_table, sample_receipt):
    """
    Tests the happy path of addReceipts (batch write).
    """
    client = DynamoClient(dynamodb_table)
    receipts = [sample_receipt]
    second_receipt = Receipt(
        **{
            **sample_receipt.__dict__,
            "receipt_id": sample_receipt.receipt_id + 1,
        }
    )
    receipts.append(second_receipt)

    client.addReceipts(receipts)

    stored, _ = client.listReceipts()
    assert len(stored) == 2
    assert sample_receipt in stored
    assert second_receipt in stored


@pytest.mark.integration
def test_addReceipts_raises_value_error_receipts_none(dynamodb_table, sample_receipt):
    """
    Tests that addReceipts raises ValueError when the receipts parameter is None.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(
        ValueError, match="Receipts parameter is required and cannot be None."
    ):
        client.addReceipts(None)  # type: ignore


@pytest.mark.integration
def test_addReceipts_raises_value_error_receipts_not_list(
    dynamodb_table, sample_receipt
):
    """
    Tests that addReceipts raises ValueError when the receipts parameter is not a list.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(
        ValueError, match="receipts must be a list of Receipt instances."
    ):
        client.addReceipts("not-a-list")  # type: ignore


@pytest.mark.integration
def test_addReceipts_raises_value_error_receipts_not_list_of_receipts(
    dynamodb_table, sample_receipt
):
    """
    Tests that addReceipts raises ValueError when the receipts parameter is not a list of Receipt instances.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(
        ValueError, match="All receipts must be instances of the Receipt class."
    ):
        client.addReceipts([sample_receipt, "not-a-receipt"])  # type: ignore


@pytest.mark.integration
def test_addReceipts_unprocessed_items_retry(dynamodb_table, sample_receipt, mocker):
    """
    Partial mocking to simulate unprocessed items. The second call should succeed.
    """
    client = DynamoClient(dynamodb_table)
    receipts = [sample_receipt]
    second_receipt = Receipt(
        **{**sample_receipt.__dict__, "receipt_id": sample_receipt.receipt_id + 1}
    )
    receipts.append(second_receipt)

    real_batch_write_item = client._client.batch_write_item
    call_count = {"value": 0}

    def custom_side_effect(*args, **kwargs):
        call_count["value"] += 1
        response = real_batch_write_item(*args, **kwargs)
        # On first call, pretend second receipt was unprocessed
        if call_count["value"] == 1:
            return {
                "UnprocessedItems": {
                    client.table_name: [
                        {"PutRequest": {"Item": second_receipt.to_item()}}
                    ]
                }
            }
        else:
            return {"UnprocessedItems": {}}

    mocker.patch.object(
        client._client, "batch_write_item", side_effect=custom_side_effect
    )
    client.addReceipts(receipts)

    assert call_count["value"] == 2, "Should have retried once."

    stored, _ = client.listReceipts()
    assert len(stored) == 2
    assert sample_receipt in stored
    assert second_receipt in stored


@pytest.mark.integration
def test_addReceipts_raises_clienterror_provisioned_throughput_exceeded(
    dynamodb_table, sample_receipt, mocker
):
    """
    Tests that addReceipts raises an Exception when the ProvisionedThroughputExceededException error is raised.
    """
    client = DynamoClient(dynamodb_table)
    mock_batch = mocker.patch.object(
        client._client,
        "batch_write_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ProvisionedThroughputExceededException",
                    "Message": "Provisioned throughput exceeded",
                }
            },
            "BatchWriteItem",
        ),
    )
    with pytest.raises(Exception, match="Provisioned throughput exceeded"):
        client.addReceipts([sample_receipt])
    mock_batch.assert_called_once()


@pytest.mark.integration
def test_addReceipts_raises_clienterror_internal_server_error(
    dynamodb_table, sample_receipt, mocker
):
    """
    Tests that addReceipts raises an Exception when the InternalServerError error is raised.
    """
    client = DynamoClient(dynamodb_table)
    mock_batch = mocker.patch.object(
        client._client,
        "batch_write_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "InternalServerError",
                    "Message": "Internal server error",
                }
            },
            "BatchWriteItem",
        ),
    )
    with pytest.raises(Exception, match="Internal server error"):
        client.addReceipts([sample_receipt])
    mock_batch.assert_called_once()


@pytest.mark.integration
def test_addReceipts_raises_clienterror_validation_exception(
    dynamodb_table, sample_receipt, mocker
):
    """
    Tests that addReceipts raises an Exception when the ValidationException error is raised.
    """
    client = DynamoClient(dynamodb_table)
    mock_batch = mocker.patch.object(
        client._client,
        "batch_write_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ValidationException",
                    "Message": "One or more parameters given were invalid",
                }
            },
            "BatchWriteItem",
        ),
    )
    with pytest.raises(Exception, match="One or more parameters given were invalid"):
        client.addReceipts([sample_receipt])
    mock_batch.assert_called_once()


@pytest.mark.integration
def test_addReceipts_raises_clienterror_access_denied(
    dynamodb_table, sample_receipt, mocker
):
    """
    Tests that addReceipts raises an Exception when the ValidationException error is raised.
    """
    client = DynamoClient(dynamodb_table)
    mock_batch = mocker.patch.object(
        client._client,
        "batch_write_item",
        side_effect=ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "Access denied"}},
            "BatchWriteItem",
        ),
    )
    with pytest.raises(Exception, match="Access denied"):
        client.addReceipts([sample_receipt])
    mock_batch.assert_called_once()


@pytest.mark.integration
def test_addReceipts_raises_clienterror(dynamodb_table, sample_receipt, mocker):
    """
    Simulates a client error like ResourceNotFound or ProvisionedThroughputExceeded, etc.
    """
    client = DynamoClient(dynamodb_table)

    mock_batch = mocker.patch.object(
        client._client,
        "batch_write_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ResourceNotFoundException",
                    "Message": "No table found",
                }
            },
            "BatchWriteItem",
        ),
    )
    with pytest.raises(Exception, match="Error adding receipts: "):
        client.addReceipts([sample_receipt])

    mock_batch.assert_called_once()


# -------------------------------------------------------------------
#                  updateReceipt / updateReceipts
# -------------------------------------------------------------------


@pytest.mark.integration
def test_updateReceipt_success(dynamodb_table, sample_receipt):
    """
    Tests happy path for updateReceipt.
    """
    client = DynamoClient(dynamodb_table)
    client.addReceipt(sample_receipt)

    # Modify something
    sample_receipt.raw_s3_key = "new/path"
    client.updateReceipt(sample_receipt)

    updated = client.getReceipt(sample_receipt.image_id, sample_receipt.receipt_id)
    assert updated.raw_s3_key == "new/path"


@pytest.mark.integration
def test_updateReceipt_raises_value_error_receipt_none(dynamodb_table, sample_receipt):
    """
    Tests that updateReceipt raises ValueError when the receipt parameter is None.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match="Receipt parameter is required and cannot be None."):
        client.updateReceipt(None)  # type: ignore


@pytest.mark.integration
def test_updateReceipt_raises_value_error_receipt_not_instance(
    dynamodb_table, sample_receipt
):
    """
    Tests that updateReceipt raises ValueError when the receipt parameter is not an instance of Receipt.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(
        ValueError, match="receipt must be an instance of the Receipt class."
    ):
        client.updateReceipt("not-a-receipt")  # type: ignore

@pytest.mark.integration
def test_updateReceipt_raises_conditional_check_failed(
    dynamodb_table, sample_receipt, mocker
):
    """
    Simulate updating a receipt that does not exist.
    """
    client = DynamoClient(dynamodb_table)
    mock_put = mocker.patch.object(
        client._client,
        "put_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ConditionalCheckFailedException",
                    "Message": "Item does not exist",
                }
            },
            "PutItem",
        ),
    )

    with pytest.raises(ValueError, match="does not exist"):
        client.updateReceipt(sample_receipt)
    mock_put.assert_called_once()


@pytest.mark.integration
def test_updateReceipt_raises_clienterror_provisioned_throughput_exceeded(
    dynamodb_table, sample_receipt, mocker
):
    """
    Tests that updateReceipt raises an Exception when the ProvisionedThroughputExceededException error is raised.
    """
    client = DynamoClient(dynamodb_table)
    mock_put = mocker.patch.object(
        client._client,
        "put_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ProvisionedThroughputExceededException",
                    "Message": "Provisioned throughput exceeded",
                }
            },
            "PutItem",
        ),
    )
    with pytest.raises(Exception, match="Provisioned throughput exceeded"):
        client.updateReceipt(sample_receipt)
    mock_put.assert_called_once()


@pytest.mark.integration
def test_updateReceipt_raises_clienterror_internal_server_error(
    dynamodb_table, sample_receipt, mocker
):
    """
    Tests that updateReceipt raises an Exception when the InternalServerError error is raised.
    """
    client = DynamoClient(dynamodb_table)
    mock_put = mocker.patch.object(
        client._client,
        "put_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "InternalServerError",
                    "Message": "Internal server error",
                }
            },
            "PutItem",
        ),
    )
    with pytest.raises(Exception, match="Internal server error"):
        client.updateReceipt(sample_receipt)
    mock_put.assert_called_once()


@pytest.mark.integration
def test_updateReceipt_raises_clienterror_validation_exception(
    dynamodb_table, sample_receipt, mocker
):
    """
    Tests that updateReceipt raises an Exception when the ValidationException error is raised.
    """
    client = DynamoClient(dynamodb_table)
    mock_put = mocker.patch.object(
        client._client,
        "put_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ValidationException",
                    "Message": "One or more parameters given were invalid",
                }
            },
            "PutItem",
        ),
    )
    with pytest.raises(Exception, match="One or more parameters given were invalid"):
        client.updateReceipt(sample_receipt)
    mock_put.assert_called_once()


@pytest.mark.integration
def test_updateReceipt_raises_clienterror_access_denied(
    dynamodb_table, sample_receipt, mocker
):
    """
    Tests that updateReceipt raises an Exception when the AccessDeniedException error is raised.
    """
    client = DynamoClient(dynamodb_table)
    mock_put = mocker.patch.object(
        client._client,
        "put_item",
        side_effect=ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "Access denied"}},
            "PutItem",
        ),
    )
    with pytest.raises(Exception, match="Access denied"):
        client.updateReceipt(sample_receipt)
    mock_put.assert_called_once()


@pytest.mark.integration
def test_updateReceipt_raises_clienterror(dynamodb_table, sample_receipt, mocker):
    """
    Simulates a client error like ResourceNotFound or ProvisionedThroughputExceeded, etc.
    """
    client = DynamoClient(dynamodb_table)

    mock_put = mocker.patch.object(
        client._client,
        "put_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ResourceNotFoundException",
                    "Message": "No table found",
                }
            },
            "PutItem",
        ),
    )
    with pytest.raises(Exception, match="Error updating receipt: "):
        client.updateReceipt(sample_receipt)

    mock_put.assert_called_once()


@pytest.mark.integration
def test_updateReceipts_success(dynamodb_table, sample_receipt):
    """
    Tests happy path of updateReceipts (batch write).
    """
    client = DynamoClient(dynamodb_table)
    r1 = sample_receipt
    r2 = Receipt(
        **{**sample_receipt.__dict__, "receipt_id": sample_receipt.receipt_id + 1}
    )
    client.addReceipts([r1, r2])

    # Now update them
    r1.raw_s3_key = "updated/path/1"
    r2.raw_s3_key = "updated/path/2"
    client.updateReceipts([r1, r2])

    stored, _ = client.listReceipts()
    assert len(stored) == 2
    # Confirm the updated s3_keys
    for item in stored:
        if item.receipt_id == r1.receipt_id:
            assert item.raw_s3_key == "updated/path/1"
        else:
            assert item.raw_s3_key == "updated/path/2"


@pytest.mark.integration
def test_updateReceipts_raises_value_error_receipts_none(dynamodb_table, sample_receipt):
    """
    Tests that updateReceipts raises ValueError when the receipts parameter is None.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match="Receipts parameter is required and cannot be None."):
        client.updateReceipts(None)  # type: ignore


@pytest.mark.integration
def test_updateReceipts_raises_value_error_receipts_not_list(
    dynamodb_table, sample_receipt
):
    """
    Tests that updateReceipts raises ValueError when the receipts parameter is not a list.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(
        ValueError, match="receipts must be a list of Receipt instances."
    ):
        client.updateReceipts("not-a-list")  # type: ignore


@pytest.mark.integration
def test_updateReceipts_raises_value_error_receipts_not_list_of_receipts(
    dynamodb_table, sample_receipt
):
    """
    Tests that updateReceipts raises ValueError when the receipts parameter is not a list of Receipt instances.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(
        ValueError, match="All receipts must be instances of the Receipt class."
    ):
        client.updateReceipts([sample_receipt, "not-a-receipt"])  # type: ignore


@pytest.mark.integration
def test_updateReceipts_raises_clienterror_conditional_check_failed(
    dynamodb_table, sample_receipt, mocker
):
    """
    Tests that updateReceipts raises an Exception when the ConditionalCheckFailedException error is raised.
    """
    client = DynamoClient(dynamodb_table)
    mock_batch = mocker.patch.object(
        client._client,
        "transact_write_items",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ConditionalCheckFailedException",
                    "Message": "One or more receipts do not exist",
                }
            },
            "TransactWriteItems",
        ),
    )
    with pytest.raises(ValueError, match="One or more receipts do not exist"):
        client.updateReceipts([sample_receipt])
    mock_batch.assert_called_once()


@pytest.mark.integration
def test_updateReceipts_raises_clienterror_provisioned_throughput_exceeded(
    dynamodb_table, sample_receipt, mocker
):
    """
    Tests that updateReceipts raises an Exception when the ProvisionedThroughputExceededException error is raised.
    """
    client = DynamoClient(dynamodb_table)
    mock_batch = mocker.patch.object(
        client._client,
        "transact_write_items",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ProvisionedThroughputExceededException",
                    "Message": "Provisioned throughput exceeded",
                }
            },
            "TransactWriteItems",
        ),
    )
    with pytest.raises(Exception, match="Provisioned throughput exceeded"):
        client.updateReceipts([sample_receipt])
    mock_batch.assert_called_once()


@pytest.mark.integration
def test_updateReceipts_raises_clienterror_internal_server_error(
    dynamodb_table, sample_receipt, mocker
):
    """
    Tests that updateReceipts raises an Exception when the InternalServerError error is raised.
    """
    client = DynamoClient(dynamodb_table)
    mock_batch = mocker.patch.object(
        client._client,
        "transact_write_items",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "InternalServerError",
                    "Message": "Internal server error",
                }
            },
            "TransactWriteItems",
        ),
    )
    with pytest.raises(Exception, match="Internal server error"):
        client.updateReceipts([sample_receipt])
    mock_batch.assert_called_once() 


@pytest.mark.integration
def test_updateReceipts_raises_clienterror_validation_exception(
    dynamodb_table, sample_receipt, mocker
):
    """
    Tests that updateReceipts raises an Exception when the ValidationException error is raised.
    """
    client = DynamoClient(dynamodb_table)
    mock_batch = mocker.patch.object(
        client._client,
        "transact_write_items",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ValidationException",
                    "Message": "One or more parameters given were invalid",
                }
            },
            "TransactWriteItems",
        ),
    )
    with pytest.raises(Exception, match="One or more parameters given were invalid"):
        client.updateReceipts([sample_receipt])
    mock_batch.assert_called_once() 


@pytest.mark.integration
def test_updateReceipts_raises_clienterror_access_denied(
    dynamodb_table, sample_receipt, mocker
):
    """
    Tests that updateReceipts raises an Exception when the AccessDeniedException error is raised.
    """
    client = DynamoClient(dynamodb_table)
    mock_batch = mocker.patch.object(
        client._client,
        "transact_write_items",
        side_effect=ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "Access denied"}},
            "TransactWriteItems",
        ),
    )
    with pytest.raises(Exception, match="Access denied"):
        client.updateReceipts([sample_receipt])
    mock_batch.assert_called_once()

@pytest.mark.integration
def test_updateReceipts_raises_client_error(dynamodb_table, sample_receipt, mocker):
    """
    Simulate a client error in batch_write_item, e.g. ResourceNotFound.
    """
    client = DynamoClient(dynamodb_table)
    mock_batch = mocker.patch.object(
        client._client,
        "transact_write_items",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ResourceNotFoundException",
                    "Message": "No table found",
                }
            },
            "BatchWriteItem",
        ),
    )

    with pytest.raises(ValueError, match="Error updating receipts"):
        client.updateReceipts([sample_receipt])

    mock_batch.assert_called_once()


# -------------------------------------------------------------------
#                  deleteReceipt / deleteReceipts
# -------------------------------------------------------------------


@pytest.mark.integration
def test_deleteReceipt_success(dynamodb_table, sample_receipt):
    """
    Tests happy path for deleteReceipt.
    """
    client = DynamoClient(dynamodb_table)
    client.addReceipt(sample_receipt)

    client.deleteReceipt(sample_receipt)
    receipts, _ = client.listReceipts()
    assert sample_receipt not in receipts, "Receipt should be deleted."


@pytest.mark.integration
def test_deleteReceipt_raises_conditional_check_failed(
    dynamodb_table, sample_receipt, mocker
):
    """
    Simulate trying to delete a non-existent receipt.
    """
    client = DynamoClient(dynamodb_table)
    mock_delete = mocker.patch.object(
        client._client,
        "delete_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ConditionalCheckFailedException",
                    "Message": "Item does not exist",
                }
            },
            "DeleteItem",
        ),
    )

    with pytest.raises(ValueError, match="does not exist"):
        client.deleteReceipt(sample_receipt)
    mock_delete.assert_called_once()


@pytest.mark.integration
def test_deleteReceipts_success(dynamodb_table, sample_receipt):
    """
    Tests happy path for deleteReceipts.
    """
    client = DynamoClient(dynamodb_table)
    r1 = sample_receipt
    r2 = Receipt(
        **{**sample_receipt.__dict__, "receipt_id": sample_receipt.receipt_id + 1}
    )
    client.addReceipts([r1, r2])

    client.deleteReceipts([r1, r2])
    receipts, _ = client.listReceipts()
    assert not receipts, "All receipts should be deleted."


@pytest.mark.integration
def test_deleteReceipts_unprocessed_items_retry(dynamodb_table, sample_receipt, mocker):
    """
    Partial mocking to simulate unprocessed items on deletion.
    """
    client = DynamoClient(dynamodb_table)
    r2 = Receipt(
        **{**sample_receipt.__dict__, "receipt_id": sample_receipt.receipt_id + 1}
    )
    client.addReceipts([sample_receipt, r2])

    real_batch_delete = client._client.batch_write_item
    call_count = {"value": 0}

    def custom_side_effect(*args, **kwargs):
        call_count["value"] += 1
        response = real_batch_delete(*args, **kwargs)
        if call_count["value"] == 1:
            # Simulate that r2 was unprocessed
            return {
                "UnprocessedItems": {
                    client.table_name: [{"DeleteRequest": {"Key": r2.key()}}]
                }
            }
        else:
            return {"UnprocessedItems": {}}

    mocker.patch.object(
        client._client, "batch_write_item", side_effect=custom_side_effect
    )

    client.deleteReceipts([sample_receipt, r2])
    assert call_count["value"] == 2, "Should retry once for unprocessed items."

    receipts, _ = client.listReceipts()
    assert not receipts, "All receipts should be deleted after retry."


@pytest.mark.integration
def test_deleteReceipts_raises_client_error(dynamodb_table, sample_receipt, mocker):
    """
    Simulate any error (ResourceNotFound, ProvisionedThroughputExceeded, etc.) in batch_delete.
    """
    client = DynamoClient(dynamodb_table)

    mock_batch = mocker.patch.object(
        client._client,
        "batch_write_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ResourceNotFoundException",
                    "Message": "No table found",
                }
            },
            "BatchWriteItem",
        ),
    )

    with pytest.raises(ValueError, match="Error deleting receipts"):
        client.deleteReceipts([sample_receipt])

    mock_batch.assert_called_once()


# -------------------------------------------------------------------
#               deleteReceiptsFromImage
# -------------------------------------------------------------------


@pytest.mark.integration
def test_deleteReceiptsFromImage_success(dynamodb_table, sample_receipt, sample_image):
    """
    Tests deleting all receipts from a given image.
    """
    client = DynamoClient(dynamodb_table)
    client.addImage(sample_image)  # if you have addImage method
    sample_receipt.image_id = sample_image.image_id
    client.addReceipt(sample_receipt)

    client.deleteReceiptsFromImage(sample_image.image_id)

    with pytest.raises(ValueError):
        client.getReceipt(sample_image.image_id, sample_receipt.receipt_id)


@pytest.mark.integration
def test_deleteReceiptsFromImage_no_receipts(dynamodb_table, sample_image):
    """
    Tests deleteReceiptsFromImage raises an error if no receipts exist.
    """
    client = DynamoClient(dynamodb_table)
    client.addImage(sample_image)

    with pytest.raises(ValueError, match="No receipts found"):
        client.deleteReceiptsFromImage(sample_image.image_id)


# -------------------------------------------------------------------
#               getReceipt / getReceiptDetails
# -------------------------------------------------------------------


@pytest.mark.integration
def test_getReceipt_success(dynamodb_table, sample_receipt):
    """
    Tests retrieving a single receipt.
    """
    client = DynamoClient(dynamodb_table)
    client.addReceipt(sample_receipt)
    retrieved = client.getReceipt(sample_receipt.image_id, sample_receipt.receipt_id)
    assert retrieved == sample_receipt


@pytest.mark.integration
def test_getReceipt_not_found(dynamodb_table, sample_receipt):
    """
    Tests getReceipt raises ValueError if not found.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match="does not exist"):
        client.getReceipt(sample_receipt.image_id, sample_receipt.receipt_id)


@pytest.mark.integration
def test_getReceiptDetails_success(
    dynamodb_table,
    sample_receipt,
    sample_receipt_word,
    sample_receipt_word_tag,
    sample_receipt_letter,
):
    """
    Tests retrieving a receipt with lines, words, letters, tags, etc.
    (Adjust the below method calls if you have separate add methods for lines, letters, etc.)
    """
    client = DynamoClient(dynamodb_table)
    client.addReceipt(sample_receipt)
    client.addReceiptWords([sample_receipt_word])
    client.addReceiptWordTags([sample_receipt_word_tag])
    client.addReceiptLetters([sample_receipt_letter])

    (
        r,
        lines,
        words,
        letters,
        tags,
        validations,
        initial_taggings,
    ) = client.getReceiptDetails(sample_receipt.image_id, sample_receipt.receipt_id)

    assert r == sample_receipt
    assert len(words) == 1 and words[0] == sample_receipt_word
    assert len(tags) == 1 and tags[0] == sample_receipt_word_tag
    assert len(letters) == 1 and letters[0] == sample_receipt_letter
    assert lines == [], "No lines were added in this test, so expect an empty list."
    assert validations == []
    assert initial_taggings == []


# -------------------------------------------------------------------
#                  listReceipts
# -------------------------------------------------------------------


@pytest.mark.integration
def test_listReceipts_no_limit(dynamodb_table, sample_receipt):
    """
    Tests listing all receipts without a limit.
    """
    client = DynamoClient(dynamodb_table)
    client.addReceipt(sample_receipt)
    r2 = Receipt(
        **{**sample_receipt.__dict__, "receipt_id": sample_receipt.receipt_id + 1}
    )
    client.addReceipt(r2)

    receipts, lek = client.listReceipts()
    assert len(receipts) == 2
    assert sample_receipt in receipts
    assert r2 in receipts
    assert lek is None, "Should have no pagination key if all items are fetched."


@pytest.mark.integration
def test_listReceipts_with_pagination(dynamodb_table, sample_receipt, mocker):
    """
    Tests listing receipts in multiple pages with a limit.
    We'll patch query to simulate a second page.
    """
    client = DynamoClient(dynamodb_table)

    # Fake responses
    first_page = {
        "Items": [sample_receipt.to_item()],
        "LastEvaluatedKey": {"dummy": "key"},
    }
    second_page = {"Items": [sample_receipt.to_item()]}

    mock_query = mocker.patch.object(
        client._client, "query", side_effect=[first_page, second_page]
    )

    receipts, lek = client.listReceipts(limit=10)
    assert len(receipts) == 2  # 1 from first page, 1 from second page
    assert lek is None
    assert mock_query.call_count == 2


@pytest.mark.integration
def test_listReceipts_with_starting_LEK(dynamodb_table, sample_receipt, mocker):
    """
    Tests that listReceipts uses a provided LastEvaluatedKey and, when paginating,
    correctly updates the ExclusiveStartKey and ultimately returns None when pagination ends.
    """
    client = DynamoClient(dynamodb_table)
    start_lek = {"PK": {"S": "IMAGE#start"}, "SK": {"S": "DUMMY_START"}}
    end_lek = {"PK": {"S": "IMAGE#end"}, "SK": {"S": "DUMMY_END"}}

    # First page returns one item plus a LEK indicating more pages.
    first_page = {
        "Items": [sample_receipt.to_item()],
        "LastEvaluatedKey": end_lek,
    }
    # Second page returns no items and no LEK, indicating end of pagination.
    second_page = {
        "Items": [],
    }

    # Patch the query method so that it returns first_page on the first call and second_page on the second.
    mock_query = mocker.patch.object(
        client._client, "query", side_effect=[first_page, second_page]
    )

    # Call listReceipts with limit=5 and starting LEK.
    receipts, returned_lek = client.listReceipts(limit=5, lastEvaluatedKey=start_lek)

    # Expect two calls: one for the first page, one for the second.
    assert mock_query.call_count == 2

    # Verify that the first call used the provided start_lek.
    first_call_kwargs = mock_query.call_args_list[0][1]
    assert first_call_kwargs.get("ExclusiveStartKey") == start_lek

    # Verify that the second call used the LEK returned from the first page.
    second_call_kwargs = mock_query.call_args_list[1][1]
    assert second_call_kwargs.get("ExclusiveStartKey") == end_lek

    # Since the second page returns no items, pagination ends and returned LEK should be None.
    assert returned_lek is None

    # Finally, only the one item from the first page should be returned.
    assert len(receipts) == 1


@pytest.mark.integration
def test_listReceipts_limit_trim(mocker, dynamodb_table, sample_receipt):
    """
    Tests that listReceipts stops paginating once the accumulated receipts reach the limit,
    trims any extra items, and returns the LastEvaluatedKey from the final query response.
    """
    client = DynamoClient(dynamodb_table)
    limit = 3

    # Create a fake response that returns 4 items (more than the limit)
    fake_items = [sample_receipt.to_item() for _ in range(4)]
    fake_response = {
        "Items": fake_items,
        "LastEvaluatedKey": {"PK": {"S": "dummy"}, "SK": {"S": "dummy"}},
    }

    # Patch the query method to return this fake response
    mock_query = mocker.patch.object(
        client._client, "query", return_value=fake_response
    )

    # Call listReceipts with a limit of 3
    receipts, lek = client.listReceipts(limit=limit)

    # The function should trim the items to exactly the limit
    assert len(receipts) == limit, "Should return exactly the limit number of items"

    # The returned LEK should be taken from the fake_response
    assert lek == fake_response.get(
        "LastEvaluatedKey"
    ), "LEK should match that of the fake response"

    # Ensure that the query was called only once, since the first page returned enough items.
    mock_query.assert_called_once()


@pytest.mark.integration
def test_listReceipts_invalid_limit(dynamodb_table):
    """
    listReceipts should raise a ValueError if limit is not int or is <= 0.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match="Limit must be an integer"):
        client.listReceipts(limit="not-an-int")
    with pytest.raises(ValueError, match="Limit must be greater than 0"):
        client.listReceipts(limit=0)


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_lek",
    [
        "not-a-dict",  # not a dict at all
        {"PK": {"S": "IMAGE#start"}},  # missing SK key
        {"SK": {"S": "DUMMY_START"}},  # missing PK key
        {
            "PK": "not-a-dict",
            "SK": {"S": "DUMMY_START"},
        },  # PK value not in proper format
        {
            "PK": {"S": "IMAGE#start"},
            "SK": "not-a-dict",
        },  # SK value not in proper format
    ],
)
def test_listReceipts_invalid_lastEvaluatedKey(dynamodb_table, invalid_lek):
    """
    Verifies that listReceipts raises a ValueError when lastEvaluatedKey is invalid.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match="LastEvaluatedKey"):
        client.listReceipts(lastEvaluatedKey=invalid_lek)


@pytest.mark.integration
def test_listReceipts_raises_resource_not_found(dynamodb_table, sample_receipt, mocker):
    """
    Simulates ResourceNotFound while listing receipts.
    """
    client = DynamoClient(dynamodb_table)
    mock_query = mocker.patch.object(
        client._client,
        "query",
        side_effect=ClientError(
            {"Error": {"Code": "ResourceNotFoundException"}},
            "Query",
        ),
    )
    with pytest.raises(Exception, match="Could not list receipts from the database"):
        client.listReceipts()
    mock_query.assert_called_once()


@pytest.mark.integration
def test_listReceipts_raises_throughput(dynamodb_table, sample_receipt, mocker):
    """
    Simulates ProvisionedThroughputExceededException while listing receipts.
    """
    client = DynamoClient(dynamodb_table)
    mock_query = mocker.patch.object(
        client._client,
        "query",
        side_effect=ClientError(
            {"Error": {"Code": "ProvisionedThroughputExceededException"}},
            "Query",
        ),
    )
    with pytest.raises(Exception, match="Provisioned throughput exceeded"):
        client.listReceipts()
    mock_query.assert_called_once()


@pytest.mark.integration
def test_listReceipts_raises_validation_exception(
    dynamodb_table, sample_receipt, mocker
):
    """
    Simulates ValidationException while listing receipts.
    """
    client = DynamoClient(dynamodb_table)
    mock_query = mocker.patch.object(
        client._client,
        "query",
        side_effect=ClientError(
            {"Error": {"Code": "ValidationException"}},
            "Query",
        ),
    )
    with pytest.raises(Exception, match="One or more parameters given were invalid"):
        client.listReceipts()
    mock_query.assert_called_once()


@pytest.mark.integration
def test_listReceipts_raises_internal_server_error(
    dynamodb_table, sample_receipt, mocker
):
    """
    Simulates InternalServerError while listing receipts.
    """
    client = DynamoClient(dynamodb_table)
    mock_query = mocker.patch.object(
        client._client,
        "query",
        side_effect=ClientError(
            {"Error": {"Code": "InternalServerError"}},
            "Query",
        ),
    )
    with pytest.raises(Exception, match="Internal server error"):
        client.listReceipts()
    mock_query.assert_called_once()


@pytest.mark.integration
def test_listReceipts_raises_unknown_error(dynamodb_table, sample_receipt, mocker):
    """
    Simulates an unknown error while listing receipts.
    """
    client = DynamoClient(dynamodb_table)
    mock_query = mocker.patch.object(
        client._client,
        "query",
        side_effect=ClientError(
            {"Error": {"Code": "SomethingUnknown"}},
            "Query",
        ),
    )
    with pytest.raises(Exception, match="Could not list receipts from the database"):
        client.listReceipts()
    mock_query.assert_called_once()
