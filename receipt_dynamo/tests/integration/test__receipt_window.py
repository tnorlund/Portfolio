# infra/lambda_layer/python/test/integration/test__receipt_window.py
from uuid import uuid4

import boto3
import pytest
from botocore.exceptions import ClientError

from receipt_dynamo import DynamoClient, ReceiptWindow


@pytest.fixture
def example_receipt_window():
    return ReceiptWindow(image_id=str(uuid4()),
        receipt_id=1,
        corner_name="TOP_LEFT",
        width=100,
        height=50,
        cdn_s3_bucket="some-bucket",  # <--- provide valid string
        cdn_s3_key="some-key-1",  # <--- provide valid string
        inner_corner_coordinates=(10, 20), )


@pytest.mark.integration
def test_addReceiptWindow_raises_value_error_for_none_receipt_window(dynamodb_table, ):
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
    with pytest.raises(ValueError, match="must be an instance of the ReceiptWindow class"):
        client.addReceiptWindow("not-a-receipt-window")


@pytest.mark.integration
def test_addReceiptWindow_raises_conditional_check_failed(dynamodb_table, example_receipt_window, mocker):
    """
    Checks that addReceiptWindow raises ValueError when
    a ConditionalCheckFailedException occurs (i.e., item already exists).
    """
    client = DynamoClient(dynamodb_table)

    # Patch the underlying put_item to raise a ClientError with
    # "ConditionalCheckFailedException"
    mock_put = mocker.patch.object(client._client,
        "put_item",
        side_effect=ClientError({"Error": {"Code": "ConditionalCheckFailedException",
                    "Message": "The conditional request failed", }},
            "PutItem", ), )

    with pytest.raises(ValueError, match="already exists"):
        client.addReceiptWindow(example_receipt_window)

    mock_put.assert_called_once()


@pytest.mark.integration
def test_addReceiptWindow_raises_resource_not_found(dynamodb_table, example_receipt_window, mocker):
    """
    Checks that addReceiptWindow raises ValueError when
    a ResourceNotFoundException occurs (i.e., table does not exist).
    """
    client = DynamoClient(dynamodb_table)

    # Patch the underlying put_item to raise a ClientError with
    # "ConditionalCheckFailedException"
    mock_put = mocker.patch.object(client._client,
        "put_item",
        side_effect=ClientError({"Error": {"Code": "ResourceNotFoundException",
                    "Message": "The table does not exist", }},
            "PutItem", ), )

    with pytest.raises(Exception, match="Could not add receipt window to DynamoDB"):
        client.addReceiptWindow(example_receipt_window)

    mock_put.assert_called_once()


@pytest.mark.integration
def test_addReceiptWindow_raises_provisioned_throughput(dynamodb_table, example_receipt_window, mocker):
    """
    Checks that addReceiptWindow raises an Exception with a message indicating
    that the provisioned throughput was exceeded.
    """
    client = DynamoClient(dynamodb_table)

    mock_put = mocker.patch.object(client._client,
        "put_item",
        side_effect=ClientError({"Error": {"Code": "ProvisionedThroughputExceededException",
                    "Message": "Provisioned throughput exceeded", }},
            "PutItem", ), )

    with pytest.raises(Exception, match="Provisioned throughput exceeded"):
        client.addReceiptWindow(example_receipt_window)

    mock_put.assert_called_once()


@pytest.mark.integration
def test_addReceiptWindow_raises_internal_server_error(dynamodb_table, example_receipt_window, mocker):
    """
    Checks that addReceiptWindow raises an Exception indicating
    an internal server error occurred.
    """
    client = DynamoClient(dynamodb_table)

    mock_put = mocker.patch.object(client._client,
        "put_item",
        side_effect=ClientError({"Error": {"Code": "InternalServerError",
                    "Message": "Internal server error", }},
            "PutItem", ), )

    with pytest.raises(Exception, match="Internal server error:"):
        client.addReceiptWindow(example_receipt_window)

    mock_put.assert_called_once()


@pytest.mark.integration
def test_addReceiptWindow_raises_unknown_exception(dynamodb_table, example_receipt_window, mocker):
    """
    Checks that addReceiptWindow raises a generic Exception when the DynamoDB
    put_item call returns a ClientError with an unknown error code.
    """
    client = DynamoClient(dynamodb_table)

    mock_put = mocker.patch.object(client._client,
        "put_item",
        side_effect=ClientError({"Error": {"Code": "SomeUnknownException",
                    "Message": "An unknown error occurred", }},
            "PutItem", ), )

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
    response = dynamo.get_item(TableName=dynamodb_table,
        Key={"PK": {"S": f"IMAGE#{example_receipt_window.image_id}"},
            "SK": {"S": f"RECEIPT#{example_receipt_window.receipt_id:05d}#RECEIPT_WINDOW#{example_receipt_window.corner_name}"}, }, )
    assert "Item" in response, "The receipt window item should be in DynamoDB"
    # Optionally compare the full item to what you'd expect from
    # example_receipt_window.to_item()
    expected = example_receipt_window.to_item()
    assert (response["Item"] == expected), "DynamoDB item does not match expected ReceiptWindow data"


# ------------------------------- addReceiptWindows Tests ----------------


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
def test_addReceiptWindows_raises_value_error_for_invalid_contents(dynamodb_table, example_receipt_window):
    """
    Checks that addReceiptWindows raises ValueError if the list contains
    non-ReceiptWindow objects.
    """
    client = DynamoClient(dynamodb_table)
    invalid_list = [example_receipt_window, "not-a-receipt-window"]
    with pytest.raises(ValueError, match="must be instances of the ReceiptWindow class"):
        client.addReceiptWindows(invalid_list)


@pytest.mark.integration
def test_addReceiptWindows_raises_resource_not_found(dynamodb_table, example_receipt_window, mocker):
    """
    Checks that addReceiptWindows raises ValueError when
    a ResourceNotFoundException occurs (i.e., table does not exist).
    """
    client = DynamoClient(dynamodb_table)

    mock_batch = mocker.patch.object(client._client,
        "batch_write_item",
        side_effect=ClientError({"Error": {"Code": "ResourceNotFoundException",
                    "Message": "The table does not exist", }},
            "BatchWriteItem", ), )

    with pytest.raises(Exception, match="Could not add receipt windows to DynamoDB"):
        client.addReceiptWindows([example_receipt_window])

    mock_batch.assert_called_once()


@pytest.mark.integration
def test_addReceiptWindows_raises_provisioned_throughput(dynamodb_table, example_receipt_window, mocker):
    """
    Checks that addReceiptWindows raises an Exception indicating
    provisioned throughput was exceeded if batch_write_item fails with that code.
    """
    client = DynamoClient(dynamodb_table)

    mock_batch = mocker.patch.object(client._client,
        "batch_write_item",
        side_effect=ClientError({"Error": {"Code": "ProvisionedThroughputExceededException",
                    "Message": "Provisioned throughput exceeded", }},
            "BatchWriteItem", ), )

    with pytest.raises(Exception, match="Provisioned throughput exceeded"):
        client.addReceiptWindows([example_receipt_window])

    mock_batch.assert_called_once()


@pytest.mark.integration
def test_addReceiptWindows_raises_validation_exception(dynamodb_table, example_receipt_window, mocker):
    """
    Checks that addReceiptWindows raises a ValueError if batch_write_item
    fails with ValidationException.
    """
    client = DynamoClient(dynamodb_table)

    mock_batch = mocker.patch.object(client._client,
        "batch_write_item",
        side_effect=ClientError({"Error": {"Code": "ValidationException",
                    "Message": "One or more parameter values were invalid", }},
            "BatchWriteItem", ), )

    with pytest.raises(ValueError, match="One or more parameters given were invalid"):
        client.addReceiptWindows([example_receipt_window])

    mock_batch.assert_called_once()


@pytest.mark.integration
def test_addReceiptWindows_raises_internal_server_error(dynamodb_table, example_receipt_window, mocker):
    """
    Checks that addReceiptWindows raises an Exception indicating an
    internal server error if batch_write_item fails with InternalServerError.
    """
    client = DynamoClient(dynamodb_table)

    mock_batch = mocker.patch.object(client._client,
        "batch_write_item",
        side_effect=ClientError({"Error": {"Code": "InternalServerError",
                    "Message": "Internal server error", }},
            "BatchWriteItem", ), )

    with pytest.raises(Exception, match="Internal server error:"):
        client.addReceiptWindows([example_receipt_window])

    mock_batch.assert_called_once()


@pytest.mark.integration
def test_addReceiptWindows_raises_unknown_exception(dynamodb_table, example_receipt_window, mocker):
    """
    Checks that addReceiptWindows raises a generic Exception if batch_write_item
    fails with an unrecognized error code.
    """
    client = DynamoClient(dynamodb_table)

    mock_batch = mocker.patch.object(client._client,
        "batch_write_item",
        side_effect=ClientError({"Error": {"Code": "SomethingUnknown",
                    "Message": "An unknown error occurred", }},
            "BatchWriteItem", ), )

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
        rw = ReceiptWindow(image_id=str(uuid4()),
            receipt_id=i,
            corner_name="BOTTOM_RIGHT",
            width=200,
            height=100,
            cdn_s3_bucket="some-bucket",
            cdn_s3_key=f"some-key-{i}",
            inner_corner_coordinates=(10, 20), )
        receipt_windows.append(rw)

    client.addReceiptWindows(receipt_windows)

    # Verify items were inserted. We'll do a spot-check of a few items.
    dynamo = boto3.client("dynamodb", region_name="us-east-1")
    for i in [0,
        1,
        28, ]:  # Check first, second, and last to ensure chunk boundary coverage
        rw = receipt_windows[i]
        response = dynamo.get_item(TableName=dynamodb_table,
            Key={"PK": {"S": f"IMAGE#{rw.image_id}"},
                "SK": {"S": f"RECEIPT#{rw.receipt_id:05d}#RECEIPT_WINDOW#{rw.corner_name}"}, }, )
        assert "Item" in response, f"Item {i} should exist in DynamoDB"
        assert (response["Item"] == rw.to_item()), f"Item {i} data mismatch in DynamoDB"


@pytest.mark.integration
def test_addReceiptWindows_unprocessed_items_retry_partial_mock(dynamodb_table, example_receipt_window, mocker):
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
    rw2 = ReceiptWindow(image_id=str(uuid4()),
        receipt_id=2,
        corner_name="TOP_RIGHT",
        width=300,
        height=150,
        cdn_s3_bucket="some-bucket",
        cdn_s3_key="some-key-2",
        inner_corner_coordinates=(10, 20), )

    # 1) Capture the *real* (unmocked) batch_write_item so we can still call it
    real_batch_write_item = client._client.batch_write_item

    # 2) We'll keep track of how many times we've invoked the function
    # so the first call can return an unprocessed item, the second can
    # succeed.
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
        real_batch_write_item(*args, **kwargs)

        if call_count["value"] == 1:
            # Pretend that rw2 was unprocessed on the first call
            return {"UnprocessedItems": {dynamodb_table: [{"PutRequest": {"Item": rw2.to_item()}}]}}
        else:
            # On the second call, no unprocessed items remain
            return {"UnprocessedItems": {}}

    # 3) Patch the client method with our custom side_effect
    mocker.patch.object(client._client,
        "batch_write_item",
        side_effect=custom_side_effect, )

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
        response = dynamo.get_item(TableName=dynamodb_table,
            Key={"PK": {"S": f"IMAGE#{rw.image_id}"},
                "SK": {"S": f"RECEIPT#{rw.receipt_id:05d}#RECEIPT_WINDOW#{rw.corner_name}"}, },
            ConsistentRead=True, )
        assert ("Item" in response), f"Receipt window {rw.receipt_id} should exist."
        assert (response["Item"] == rw.to_item()), "Item data mismatch in DynamoDB."


# ------------------------------- deleteReceiptWindows Tests -------------


@pytest.mark.integration
def test_deleteReceiptWindows_success(dynamodb_table, example_receipt_window):
    """
    Inserts a receipt window then deletes it,
    confirming that the item no longer exists.
    """
    client = DynamoClient(dynamodb_table)
    # First, add the receipt window so we can delete it.
    client.addReceiptWindow(example_receipt_window)

    # Confirm the item exists in DynamoDB.
    dynamo = boto3.client("dynamodb", region_name="us-east-1")
    response = dynamo.get_item(TableName=dynamodb_table,
        Key={"PK": {"S": f"IMAGE#{example_receipt_window.image_id}"},
            "SK": {"S": f"RECEIPT#{example_receipt_window.receipt_id:05d}#RECEIPT_WINDOW#{example_receipt_window.corner_name}"}, }, )
    assert "Item" in response, "Receipt window should exist before deletion"

    # Now delete the receipt window.
    client.deleteReceiptWindows([example_receipt_window])

    # Verify that the item no longer exists.
    response = dynamo.get_item(TableName=dynamodb_table,
        Key={"PK": {"S": f"IMAGE#{example_receipt_window.image_id}"},
            "SK": {"S": f"RECEIPT#{example_receipt_window.receipt_id:05d}#RECEIPT_WINDOW#{example_receipt_window.corner_name}"}, }, )
    assert "Item" not in response, "Receipt window should be deleted"


@pytest.mark.integration
def test_deleteReceiptWindows_unprocessed_items_retry(dynamodb_table, example_receipt_window, mocker):
    """
    Simulates a scenario where the first call to batch_write_item for deletion returns an
    unprocessed item (forcing a retry) and the second call returns an empty unprocessed dict.
    Verifies that the deletion method retries and that the item is ultimately deleted.
    """
    client = DynamoClient(dynamodb_table)
    # Add the receipt window so there is something to delete.
    client.addReceiptWindow(example_receipt_window)

    # Capture the real batch_write_item to allow real deletion on retry.
    real_batch_write_item = client._client.batch_write_item
    call_count = {"value": 0}

    def custom_side_effect(*args, **kwargs):
        call_count["value"] += 1
        # Call the real method so that deletion actually occurs.
        real_batch_write_item(*args, **kwargs)
        if call_count["value"] == 1:
            # Simulate that the deletion for this item was not processed on the
            # first call.
            return {"UnprocessedItems": {client.table_name: [{"DeleteRequest": {"Key": example_receipt_window.key()}}]}}
        else:
            # On the second call, everything is processed.
            return {"UnprocessedItems": {}}

    mocker.patch.object(client._client, "batch_write_item", side_effect=custom_side_effect)

    client.deleteReceiptWindows([example_receipt_window])

    # Ensure our side effect was invoked twice.
    assert (call_count["value"] == 2), "batch_write_item should be called twice due to retry."

    # Verify that the item is no longer in DynamoDB.
    dynamo = boto3.client("dynamodb", region_name="us-east-1")
    response = dynamo.get_item(TableName=dynamodb_table,
        Key={"PK": {"S": f"IMAGE#{example_receipt_window.image_id}"},
            "SK": {"S": f"RECEIPT#{example_receipt_window.receipt_id:05d}#RECEIPT_WINDOW#{example_receipt_window.corner_name}"}, }, )
    assert "Item" not in response, "Receipt window should be deleted."


@pytest.mark.integration
def test_deleteReceiptWindows_raises_value_error_for_none(dynamodb_table):
    """
    Verifies that deleteReceiptWindows raises a ValueError when the input is None.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError,
        match="receipt_windows parameter is required and cannot be None.", ):
        client.deleteReceiptWindows(None)


@pytest.mark.integration
def test_deleteReceiptWindows_raises_value_error_for_non_list(dynamodb_table, example_receipt_window):
    """
    Verifies that deleteReceiptWindows raises a ValueError when the input is not a list.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match="receipt_windows must be provided as a list."):
        client.deleteReceiptWindows("not-a-list")


@pytest.mark.integration
def test_deleteReceiptWindows_raises_value_error_for_invalid_contents(dynamodb_table, example_receipt_window):
    """
    Verifies that deleteReceiptWindows raises a ValueError when the list contains non-ReceiptWindow objects.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError,
        match="All items in the receipt_windows list must be instances of the ReceiptWindow class.", ):
        client.deleteReceiptWindows([example_receipt_window, "invalid"])


@pytest.mark.integration
def test_deleteReceiptWindows_raises_resource_not_found(dynamodb_table, example_receipt_window, mocker):
    """
    Simulates a ResourceNotFoundException when deleting receipt windows.
    """
    client = DynamoClient(dynamodb_table)
    mock_batch = mocker.patch.object(client._client,
        "batch_write_item",
        side_effect=ClientError({"Error": {"Code": "ResourceNotFoundException",
                    "Message": "Table not found", }},
            "BatchWriteItem", ), )
    with pytest.raises(Exception, match="Could not delete receipt windows from DynamoDB"):
        client.deleteReceiptWindows([example_receipt_window])
    mock_batch.assert_called_once()


@pytest.mark.integration
def test_deleteReceiptWindows_raises_provisioned_throughput(dynamodb_table, example_receipt_window, mocker):
    """
    Simulates a ProvisionedThroughputExceededException during deletion.
    """
    client = DynamoClient(dynamodb_table)
    mock_batch = mocker.patch.object(client._client,
        "batch_write_item",
        side_effect=ClientError({"Error": {"Code": "ProvisionedThroughputExceededException",
                    "Message": "Throughput exceeded", }},
            "BatchWriteItem", ), )
    with pytest.raises(Exception, match="Provisioned throughput exceeded"):
        client.deleteReceiptWindows([example_receipt_window])
    mock_batch.assert_called_once()


@pytest.mark.integration
def test_deleteReceiptWindows_raises_validation_exception(dynamodb_table, example_receipt_window, mocker):
    """
    Simulates a ValidationException during deletion.
    """
    client = DynamoClient(dynamodb_table)
    mock_batch = mocker.patch.object(client._client,
        "batch_write_item",
        side_effect=ClientError({"Error": {"Code": "ValidationException",
                    "Message": "Invalid parameter", }},
            "BatchWriteItem", ), )
    with pytest.raises(ValueError, match="One or more parameters given were invalid"):
        client.deleteReceiptWindows([example_receipt_window])
    mock_batch.assert_called_once()


@pytest.mark.integration
def test_deleteReceiptWindows_raises_internal_server_error(dynamodb_table, example_receipt_window, mocker):
    """
    Simulates an InternalServerError during deletion.
    """
    client = DynamoClient(dynamodb_table)
    mock_batch = mocker.patch.object(client._client,
        "batch_write_item",
        side_effect=ClientError({"Error": {"Code": "InternalServerError",
                    "Message": "Internal error", }},
            "BatchWriteItem", ), )
    with pytest.raises(Exception, match="Internal server error"):
        client.deleteReceiptWindows([example_receipt_window])
    mock_batch.assert_called_once()


@pytest.mark.integration
def test_deleteReceiptWindows_raises_unknown_exception(dynamodb_table, example_receipt_window, mocker):
    """
    Simulates an unknown exception during deletion.
    """
    client = DynamoClient(dynamodb_table)
    mock_batch = mocker.patch.object(client._client,
        "batch_write_item",
        side_effect=ClientError({"Error": {"Code": "UnknownError", "Message": "An unknown error"}},
            "BatchWriteItem", ), )
    with pytest.raises(Exception, match="Error deleting receipt windows"):
        client.deleteReceiptWindows([example_receipt_window])
    mock_batch.assert_called_once()


# ------------------------------- listReceiptWindows Tests ---------------


@pytest.mark.integration
def test_listReceiptWindows_success_no_pagination(dynamodb_table, example_receipt_window, mocker):
    """
    Tests listReceiptWindows with a single-page response (no pagination).
    """
    client = DynamoClient(dynamodb_table)
    fake_response = {"Items": [example_receipt_window.to_item()]}
    mock_query = mocker.patch.object(client._client, "query", return_value=fake_response)
    result, last_evaluated_key = client.listReceiptWindows(limit=10)
    assert len(result) == 1, "Should return one receipt window"
    assert last_evaluated_key is None, "LastEvaluatedKey should be None"
    mock_query.assert_called_once()


@pytest.mark.integration
def test_listReceiptWindows_success_paginated(dynamodb_table, example_receipt_window, mocker):
    """
    Tests listReceiptWindows with pagination where two query calls are needed.
    """
    client = DynamoClient(dynamodb_table)
    first_response = {"Items": [example_receipt_window.to_item()],
        "LastEvaluatedKey": {"dummy": "key"}, }
    second_response = {"Items": [example_receipt_window.to_item()]}
    mock_query = mocker.patch.object(client._client, "query", side_effect=[first_response, second_response])
    result, last_evaluated_key = client.listReceiptWindows()
    assert (len(result) == 2), "Should return two receipt windows from paginated responses"
    assert (last_evaluated_key is None), "LastEvaluatedKey should be None after complete pagination"
    assert (mock_query.call_count == 2), "Should have called query twice for pagination"


@pytest.mark.integration
def test_listReceiptWindows_with_LEK(dynamodb_table, example_receipt_window, mocker):
    """
    Tests listReceiptWindows when a starting lastEvaluatedKey is provided.
    The patched query returns a fake response containing a LastEvaluatedKey.
    Verifies that the method uses the provided LEK and then returns the LEK
    from the response.
    """
    client = DynamoClient(dynamodb_table)

    # Define a starting LEK to simulate continuing a paginated query.
    starting_LEK = {"PK": {"S": "IMAGE#start"}, "SK": {"S": "DUMMY_START"}}

    # Define the fake LEK returned by the query.
    fake_LEK = {"PK": {"S": "IMAGE#end"}, "SK": {"S": "DUMMY_END"}}

    # Create a fake response that includes the fake LEK.
    fake_response = {"Items": [example_receipt_window.to_item()],
        "LastEvaluatedKey": fake_LEK, }

    # Patch the query method so it returns our fake response.
    mock_query = mocker.patch.object(client._client, "query", return_value=fake_response)

    # Call listReceiptWindows with a limit and the starting LEK.
    result, last_evaluated_key = client.listReceiptWindows(limit=10, lastEvaluatedKey=starting_LEK)

    # Verify that query was called once.
    mock_query.assert_called_once()

    # Ensure the query call used the provided starting LEK.
    _, called_kwargs = mock_query.call_args
    assert called_kwargs.get("ExclusiveStartKey") == starting_LEK

    # Check that the returned LEK is the one from the fake response.
    assert (last_evaluated_key == fake_LEK), "Expected the returned LEK to match the fake LEK from the query response."

    # Verify that the returned list contains the expected item.
    assert len(result) == 1, "Expected one receipt window to be returned."


@pytest.mark.integration
def test_listReceiptWindows_invalid_limit(dynamodb_table):
    """
    Verifies that listReceiptWindows raises a ValueError when 'limit' is not an integer or None.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match="limit must be an integer or None."):
        client.listReceiptWindows(limit="abc")


@pytest.mark.integration
def test_listReceiptWindows_invalid_lastEvaluatedKey(dynamodb_table):
    """
    Verifies that listReceiptWindows raises a ValueError when 'lastEvaluatedKey' is not a dict or None.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match="lastEvaluatedKey must be a dictionary or None."):
        client.listReceiptWindows(lastEvaluatedKey="not-a-dict")


@pytest.mark.integration
def test_listReceiptWindows_raises_resource_not_found(dynamodb_table, mocker):
    """
    Simulates a ResourceNotFoundException during listing.
    """
    client = DynamoClient(dynamodb_table)
    mock_query = mocker.patch.object(client._client,
        "query",
        side_effect=ClientError({"Error": {"Code": "ResourceNotFoundException",
                    "Message": "Table not found", }},
            "Query", ), )
    with pytest.raises(Exception, match="Could not list receipt windows from DynamoDB"):
        client.listReceiptWindows(limit=10)
    mock_query.assert_called_once()


@pytest.mark.integration
def test_listReceiptWindows_raises_provisioned_throughput(dynamodb_table, mocker):
    """
    Simulates a ProvisionedThroughputExceededException during listing.
    """
    client = DynamoClient(dynamodb_table)
    mock_query = mocker.patch.object(client._client,
        "query",
        side_effect=ClientError({"Error": {"Code": "ProvisionedThroughputExceededException",
                    "Message": "Throughput exceeded", }},
            "Query", ), )
    with pytest.raises(Exception, match="Provisioned throughput exceeded"):
        client.listReceiptWindows(limit=10)
    mock_query.assert_called_once()


@pytest.mark.integration
def test_listReceiptWindows_raises_validation_exception(dynamodb_table, mocker):
    """
    Simulates a ValidationException during listing.
    """
    client = DynamoClient(dynamodb_table)
    mock_query = mocker.patch.object(client._client,
        "query",
        side_effect=ClientError({"Error": {"Code": "ValidationException",
                    "Message": "Invalid parameter", }},
            "Query", ), )
    with pytest.raises(ValueError, match="One or more parameters given were invalid"):
        client.listReceiptWindows(limit=10)
    mock_query.assert_called_once()


@pytest.mark.integration
def test_listReceiptWindows_raises_internal_server_error(dynamodb_table, mocker):
    """
    Simulates an InternalServerError during listing.
    """
    client = DynamoClient(dynamodb_table)
    mock_query = mocker.patch.object(client._client,
        "query",
        side_effect=ClientError({"Error": {"Code": "InternalServerError",
                    "Message": "Internal error", }},
            "Query", ), )
    with pytest.raises(Exception, match="Internal server error"):
        client.listReceiptWindows(limit=10)
    mock_query.assert_called_once()


@pytest.mark.integration
def test_listReceiptWindows_raises_unknown_exception(dynamodb_table, mocker):
    """
    Simulates an unknown error during listing.
    """
    client = DynamoClient(dynamodb_table)
    mock_query = mocker.patch.object(client._client,
        "query",
        side_effect=ClientError({"Error": {"Code": "UnknownError", "Message": "Unknown error"}},
            "Query", ), )
    with pytest.raises(Exception, match="Error listing receipt windows"):
        client.listReceiptWindows(limit=10)
    mock_query.assert_called_once()
