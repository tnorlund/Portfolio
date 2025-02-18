# infra/lambda_layer/python/test/integration/test__receipt_window.py
import pytest
import boto3
from uuid import uuid4
from botocore.exceptions import ClientError
from dynamo import DynamoClient, ReceiptWindow


@pytest.fixture
def example_receipt_window():
    return ReceiptWindow(
        image_id=str(uuid4()),
        receipt_id=1,
        corner_name="TOP_LEFT",
        width=100,
        height=50,
        cdn_s3_bucket="some-bucket",  # <--- provide valid string
        cdn_s3_key="some-key-1",      # <--- provide valid string
        inner_corner_coordinates=(10, 20),
    )


@pytest.mark.integration
def test_addReceiptWindow_raises_value_error_for_none_receipt_window(dynamodb_table):
    """
    Checks that addReceiptWindow raises ValueError when the 'receipt_window'
    argument is None.
    """
    client = DynamoClient(dynamodb_table)

    with pytest.raises(ValueError, match="ReceiptWindow parameter is required"):
        client.addReceiptWindow(None)


@pytest.mark.integration
def test_addReceiptWindow_raises_value_error_for_invalid_type(dynamodb_table):
    """
    Checks addReceiptWindow raises ValueError when 'receipt_window' is not
    an instance of ReceiptWindow.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(
        ValueError, match="must be an instance of the ReceiptWindow class"
    ):
        client.addReceiptWindow("not-a-receipt-window")


@pytest.mark.integration
def test_addReceiptWindow_raises_conditional_check_failed(
    dynamodb_table, example_receipt_window, mocker
):
    """
    Checks that addReceiptWindow raises ValueError when
    a ConditionalCheckFailedException occurs (i.e., item already exists).
    """
    client = DynamoClient(dynamodb_table)

    # Patch the underlying put_item to raise a ClientError with "ConditionalCheckFailedException"
    mock_put = mocker.patch.object(
        client._client,
        "put_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ConditionalCheckFailedException",
                    "Message": "The conditional request failed",
                }
            },
            "PutItem",
        ),
    )

    with pytest.raises(ValueError, match="already exists"):
        client.addReceiptWindow(example_receipt_window)

    mock_put.assert_called_once()


@pytest.mark.integration
def test_addReceiptWindow_raises_provisioned_throughput(
    dynamodb_table, example_receipt_window, mocker
):
    """
    Checks that addReceiptWindow raises an Exception with a message indicating
    that the provisioned throughput was exceeded.
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
        client.addReceiptWindow(example_receipt_window)

    mock_put.assert_called_once()


@pytest.mark.integration
def test_addReceiptWindow_raises_internal_server_error(
    dynamodb_table, example_receipt_window, mocker
):
    """
    Checks that addReceiptWindow raises an Exception indicating
    an internal server error occurred.
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

    with pytest.raises(Exception, match="Internal server error:"):
        client.addReceiptWindow(example_receipt_window)

    mock_put.assert_called_once()


@pytest.mark.integration
def test_addReceiptWindow_raises_unknown_exception(
    dynamodb_table, example_receipt_window, mocker
):
    """
    Checks that addReceiptWindow raises a generic Exception when the DynamoDB
    put_item call returns a ClientError with an unknown error code.
    """
    client = DynamoClient(dynamodb_table)

    mock_put = mocker.patch.object(
        client._client,
        "put_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "SomeUnknownException",
                    "Message": "An unknown error occurred",
                }
            },
            "PutItem",
        ),
    )

    with pytest.raises(Exception, match="Error adding receipt window:"):
        client.addReceiptWindow(example_receipt_window)

    mock_put.assert_called_once()


@pytest.mark.integration
def test_addReceiptWindow_success(dynamodb_table, example_receipt_window):
    """
    Checks that addReceiptWindow persists data successfully to DynamoDB.
    """
    client = DynamoClient(dynamodb_table)
    client.addReceiptWindow(example_receipt_window)

    # Perform a direct DynamoDB read to confirm the item exists.
    dynamo = boto3.client("dynamodb", region_name="us-east-1")
    # Construct the PK, SK, etc. the same way as in your "to_item()" method
    response = dynamo.get_item(
        TableName=dynamodb_table,
        Key={
            "PK": {"S": f"IMAGE#{example_receipt_window.image_id}"},
            "SK": {
                "S": f"RECEIPT#{example_receipt_window.receipt_id:05d}#RECEIPT_WINDOW#{example_receipt_window.corner_name}"
            },
        },
    )
    assert "Item" in response, "The receipt window item should be in DynamoDB"
    # Optionally compare the full item to what you'd expect from example_receipt_window.to_item()
    expected = example_receipt_window.to_item()
    assert (
        response["Item"] == expected
    ), "DynamoDB item does not match expected ReceiptWindow data"


# ------------------------------- addReceiptWindows Tests -------------------------------


@pytest.mark.integration
def test_addReceiptWindows_raises_value_error_for_none(dynamodb_table):
    """
    Checks that addReceiptWindows raises ValueError if the 'receipt_windows'
    argument is None.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match="receipt_windows parameter is required"):
        client.addReceiptWindows(None)


@pytest.mark.integration
def test_addReceiptWindows_raises_value_error_for_non_list(dynamodb_table):
    """
    Checks that addReceiptWindows raises ValueError if 'receipt_windows' is
    not actually a list.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match="receipt_windows must be provided as a list"):
        client.addReceiptWindows("not-a-list")


@pytest.mark.integration
def test_addReceiptWindows_raises_value_error_for_invalid_contents(
    dynamodb_table, example_receipt_window
):
    """
    Checks that addReceiptWindows raises ValueError if the list contains
    non-ReceiptWindow objects.
    """
    client = DynamoClient(dynamodb_table)
    invalid_list = [example_receipt_window, "not-a-receipt-window"]
    with pytest.raises(
        ValueError, match="must be instances of the ReceiptWindow class"
    ):
        client.addReceiptWindows(invalid_list)


@pytest.mark.integration
def test_addReceiptWindows_raises_provisioned_throughput(
    dynamodb_table, example_receipt_window, mocker
):
    """
    Checks that addReceiptWindows raises an Exception indicating
    provisioned throughput was exceeded if batch_write_item fails with that code.
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
        client.addReceiptWindows([example_receipt_window])

    mock_batch.assert_called_once()


@pytest.mark.integration
def test_addReceiptWindows_raises_validation_exception(
    dynamodb_table, example_receipt_window, mocker
):
    """
    Checks that addReceiptWindows raises a ValueError if batch_write_item
    fails with ValidationException.
    """
    client = DynamoClient(dynamodb_table)

    mock_batch = mocker.patch.object(
        client._client,
        "batch_write_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ValidationException",
                    "Message": "One or more parameter values were invalid",
                }
            },
            "BatchWriteItem",
        ),
    )

    with pytest.raises(ValueError, match="One or more parameters given were invalid"):
        client.addReceiptWindows([example_receipt_window])

    mock_batch.assert_called_once()


@pytest.mark.integration
def test_addReceiptWindows_raises_internal_server_error(
    dynamodb_table, example_receipt_window, mocker
):
    """
    Checks that addReceiptWindows raises an Exception indicating an
    internal server error if batch_write_item fails with InternalServerError.
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

    with pytest.raises(Exception, match="Internal server error:"):
        client.addReceiptWindows([example_receipt_window])

    mock_batch.assert_called_once()


@pytest.mark.integration
def test_addReceiptWindows_raises_unknown_exception(
    dynamodb_table, example_receipt_window, mocker
):
    """
    Checks that addReceiptWindows raises a generic Exception if batch_write_item
    fails with an unrecognized error code.
    """
    client = DynamoClient(dynamodb_table)

    mock_batch = mocker.patch.object(
        client._client,
        "batch_write_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "SomethingUnknown",
                    "Message": "An unknown error occurred",
                }
            },
            "BatchWriteItem",
        ),
    )

    with pytest.raises(Exception, match="Error adding receipt windows:"):
        client.addReceiptWindows([example_receipt_window])

    mock_batch.assert_called_once()


@pytest.mark.integration
def test_addReceiptWindows_success_inserts_data(dynamodb_table, example_receipt_window):
    """
    Checks that addReceiptWindows correctly persists multiple items to DynamoDB.
    """
    client = DynamoClient(dynamodb_table)

    # Create several receipt windows to verify multiple inserts.
    # Also tests chunking if we create more than 25.
    receipt_windows = []
    for i in range(1, 30):
        rw = ReceiptWindow(
            image_id=str(uuid4()),
            receipt_id=i,
            corner_name="BOTTOM_RIGHT",
            width=200,
            height=100,
            cdn_s3_bucket="some-bucket",
            cdn_s3_key=f"some-key-{i}",
            inner_corner_coordinates=(10, 20),
        )
        receipt_windows.append(rw)

    client.addReceiptWindows(receipt_windows)

    # Verify items were inserted. We'll do a spot-check of a few items.
    dynamo = boto3.client("dynamodb", region_name="us-east-1")
    for i in [
        0,
        1,
        28,
    ]:  # Check first, second, and last to ensure chunk boundary coverage
        rw = receipt_windows[i]
        response = dynamo.get_item(
            TableName=dynamodb_table,
            Key={
                "PK": {"S": f"IMAGE#{rw.image_id}"},
                "SK": {"S": f"RECEIPT#{rw.receipt_id:05d}#RECEIPT_WINDOW#{rw.corner_name}"},
            },
        )
        assert "Item" in response, f"Item {i} should exist in DynamoDB"
        assert response["Item"] == rw.to_item(), f"Item {i} data mismatch in DynamoDB"


@pytest.mark.integration
def test_addReceiptWindows_unprocessed_items_retry_partial_mock(
    dynamodb_table, example_receipt_window, mocker
):
    """
    Demonstrates partial mocking to simulate unprocessed items
    *while still performing real writes* to DynamoDB.
    The first call to batch_write_item returns 'rw2' as unprocessed;
    the second call has no unprocessed items.
    This way we confirm that both items truly end up in DynamoDB.
    """
    client = DynamoClient(dynamodb_table)

    # We'll try with 2 items so it's clear if unprocessed was re-sent.
    rw1 = example_receipt_window
    rw2 = ReceiptWindow(
        image_id=str(uuid4()),
        receipt_id=2,
        corner_name="TOP_RIGHT",
        width=300,
        height=150,
        cdn_s3_bucket="some-bucket",
        cdn_s3_key="some-key-2",
        inner_corner_coordinates=(10, 20),
    )

    # 1) Capture the *real* (unmocked) batch_write_item so we can still call it
    real_batch_write_item = client._client.batch_write_item

    # 2) We'll keep track of how many times we've invoked the function
    #    so the first call can return an unprocessed item, the second can succeed.
    call_count = {"value": 0}  # Use a dict or mutable object for closure usage

    def custom_side_effect(*args, **kwargs):
        """
        1. Call the original batch_write_item => real writes happen in DynamoDB
        2. On the *first* call, we simulate that rw2 was unprocessed
           by returning a custom 'UnprocessedItems' dict.
        3. On subsequent calls, we return empty 'UnprocessedItems' 
           so the retry logic sees that everything was processed.
        """
        call_count["value"] += 1
        response = real_batch_write_item(*args, **kwargs)

        if call_count["value"] == 1:
            # Pretend that rw2 was unprocessed on the first call
            return {
                "UnprocessedItems": {
                    dynamodb_table: [{"PutRequest": {"Item": rw2.to_item()}}]
                }
            }
        else:
            # On the second call, no unprocessed items remain
            return {"UnprocessedItems": {}}

    # 3) Patch the client method with our custom side_effect
    mocker.patch.object(
        client._client,
        "batch_write_item",
        side_effect=custom_side_effect,
    )

    # 4) Now do the real call: the first batch_write_item call will
    #    store rw1 in the table and "simulate" that rw2 was not processed.
    #    The second call (retry) will store rw2.
    client.addReceiptWindows([rw1, rw2])

    # 5) Verify that batch_write_item was called *twice*
    #    => once initially, once for the unprocessed retry.
    assert call_count["value"] == 2, "Should call batch_write_item twice."

    # 6) Confirm both items truly exist in the real table
    dynamo = boto3.client("dynamodb", region_name="us-east-1")
    for rw in [rw1, rw2]:
        response = dynamo.get_item(
            TableName=dynamodb_table,
            Key={
                "PK": {"S": f"IMAGE#{rw.image_id}"},
                "SK": {"S": f"RECEIPT#{rw.receipt_id:05d}#RECEIPT_WINDOW#{rw.corner_name}"},
            },
            ConsistentRead=True,
        )
        assert "Item" in response, f"Receipt window {rw.receipt_id} should exist."
        assert response["Item"] == rw.to_item(), "Item data mismatch in DynamoDB."