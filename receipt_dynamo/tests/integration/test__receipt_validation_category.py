import uuid
from typing import Any, Literal, Type

import pytest
from botocore.exceptions import ClientError
from pytest_mock import MockerFixture

from receipt_dynamo import ReceiptValidationCategory
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.data.shared_exceptions import (
    DynamoDBError,
    DynamoDBServerError,
    DynamoDBThroughputError,
    EntityAlreadyExistsError,
    EntityNotFoundError,
    EntityValidationError,
    OperationError,
)

# This entity is not used in production infrastructure
pytestmark = [pytest.mark.integration, pytest.mark.unused_in_production]

IMAGE_ID = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
TIMESTAMP = "2023-05-15T12:34:56.789Z"
UPDATED_TIMESTAMP = "2023-05-16T10:20:30.456Z"


def make_category(
    field_name: str = "total_amount",
    field_category: str = "amount",
    status: str = "valid",
    reasoning: str = "The total amount value matches the expected format",
    valid: int = 3,
    invalid: int = 0,
    metadata: dict[str, Any] | None = None,
    receipt_id: int = 1,
    image_id: str = IMAGE_ID,
    validation_timestamp: str = TIMESTAMP,
) -> ReceiptValidationCategory:
    return ReceiptValidationCategory(
        receipt_id=receipt_id,
        image_id=image_id,
        field_name=field_name,
        field_category=field_category,
        status=status,
        reasoning=reasoning,
        result_summary={"valid": valid, "invalid": invalid},
        validation_timestamp=validation_timestamp,
        metadata={"confidence": 0.95} if metadata is None else metadata,
    )


def make_numbered_categories(count: int) -> list[ReceiptValidationCategory]:
    return [
        make_category(
            field_name=f"field_{i}",
            field_category="test_category",
            reasoning=f"Test reasoning {i}",
            valid=i,
            metadata={"index": i},
        )
        for i in range(count)
    ]


def make_updated_category(
    category: ReceiptValidationCategory,
    *,
    status: str = "invalid",
    reasoning: str = "Updated reasoning after review",
    valid: int = 1,
    invalid: int = 2,
    metadata: dict[str, Any] | None = None,
) -> ReceiptValidationCategory:
    return make_category(
        receipt_id=category.receipt_id,
        image_id=category.image_id,
        field_name=category.field_name,
        field_category=category.field_category,
        status=status,
        reasoning=reasoning,
        valid=valid,
        invalid=invalid,
        validation_timestamp=UPDATED_TIMESTAMP,
        metadata=(
            {"confidence": 0.85, "updated": True}
            if metadata is None
            else metadata
        ),
    )


def assert_category_items(
    client: DynamoClient,
    table_name: str,
    categories: list[ReceiptValidationCategory],
    *,
    present: bool = True,
) -> None:
    for category in categories:
        response = client._client.get_item(
            TableName=table_name, Key=category.key
        )
        assert (
            "Item" in response
        ) is present, (
            f"Category {category.field_name} presence should be {present}."
        )


@pytest.fixture
def sample_receipt_validation_category() -> ReceiptValidationCategory:
    return make_category()


# -------------------------------------------------------------------
#                   PARAMETERIZED CLIENT ERROR TESTS
# -------------------------------------------------------------------

# Common error scenarios for all operations
ERROR_SCENARIOS = [
    (
        "ProvisionedThroughputExceededException",
        DynamoDBThroughputError,
        "Throughput exceeded",
    ),
    ("InternalServerError", DynamoDBServerError, "DynamoDB server error"),
    ("ValidationException", EntityValidationError, "Validation error"),
    ("AccessDeniedException", DynamoDBError, "DynamoDB error during"),
    (
        "ResourceNotFoundException",
        OperationError,
        "DynamoDB resource not found",
    ),
]

# Additional error for add operations
ADD_ERROR_SCENARIOS = [
    (
        "ConditionalCheckFailedException",
        EntityAlreadyExistsError,
        "already exists",
    ),
] + ERROR_SCENARIOS

# Additional error for update operations
UPDATE_ERROR_SCENARIOS = [
    (
        "ConditionalCheckFailedException",
        EntityNotFoundError,
        "does not exist",
    ),
] + ERROR_SCENARIOS

# Additional error for delete operations
DELETE_ERROR_SCENARIOS = [
    (
        "ConditionalCheckFailedException",
        EntityNotFoundError,
        "receiptvalidationcategory not found during delete_receipt_validation_category",
    ),
] + ERROR_SCENARIOS

# -------------------------------------------------------------------
#                   PARAMETERIZED VALIDATION ERROR TESTS
# -------------------------------------------------------------------

ADD_VALIDATION_SCENARIOS = [
    (None, "category cannot be None"),
    (
        "not-a-validation-category",
        "category must be an instance of ReceiptValidationCategory",
    ),
]

UPDATE_VALIDATION_SCENARIOS = [
    (None, "category cannot be None"),
    (
        "not a ReceiptValidationCategory",
        "category must be an instance of ReceiptValidationCategory",
    ),
]


@pytest.mark.integration
def test_addReceiptValidationCategory_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_category: ReceiptValidationCategory,
):
    # Setup
    client = DynamoClient(table_name=dynamodb_table)

    # Execute
    client.add_receipt_validation_category(sample_receipt_validation_category)

    # Verify
    response = client._client.get_item(
        TableName=dynamodb_table,
        Key=sample_receipt_validation_category.key,
    )
    assert "Item" in response, "Item was not added to the table."


@pytest.mark.integration
def test_addReceiptValidationCategory_duplicate_raises(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_category: ReceiptValidationCategory,
):
    # Setup
    client = DynamoClient(table_name=dynamodb_table)

    # Add the category first time
    client.add_receipt_validation_category(sample_receipt_validation_category)

    # Attempt to add the same category again and expect an error
    with pytest.raises(
        EntityAlreadyExistsError,
        match="already exists",
    ):
        client.add_receipt_validation_category(
            sample_receipt_validation_category
        )

    # Clean up
    client._client.delete_item(
        TableName=dynamodb_table,
        Key=sample_receipt_validation_category.key,
    )


@pytest.mark.integration
@pytest.mark.parametrize("invalid_input,error_match", ADD_VALIDATION_SCENARIOS)
def test_addReceiptValidationCategory_invalid_parameters(
    dynamodb_table: Literal["MyMockedTable"],
    invalid_input: Any,
    error_match: str,
) -> None:
    client = DynamoClient(dynamodb_table)
    with pytest.raises(OperationError, match=error_match):
        client.add_receipt_validation_category(invalid_input)  # type: ignore


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,expected_exception,error_match", ADD_ERROR_SCENARIOS
)
def test_addReceiptValidationCategory_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_category: ReceiptValidationCategory,
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:
    client = DynamoClient(dynamodb_table)
    # pylint: disable=protected-access
    mock_put = mocker.patch.object(
        client._client,
        "put_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": error_code,
                    "Message": f"Mocked {error_code}",
                }
            },
            "PutItem",
        ),
    )
    with pytest.raises(expected_exception, match=error_match):
        client.add_receipt_validation_category(
            sample_receipt_validation_category
        )
    mock_put.assert_called_once()


@pytest.mark.integration
def test_addReceiptValidationCategories_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_category: ReceiptValidationCategory,
):
    # Setup
    client = DynamoClient(table_name=dynamodb_table)

    # Create a list of categories
    categories = [
        sample_receipt_validation_category,
        make_category(
            field_name="payment_method",
            field_category="payment",
            reasoning="The payment method is valid",
            valid=2,
            metadata={"confidence": 0.9},
        ),
        make_category(
            field_name="merchant_name",
            field_category="merchant",
            status="invalid",
            reasoning="The merchant name could not be validated",
            valid=0,
            invalid=1,
            metadata={"confidence": 0.7},
        ),
    ]

    # Execute
    client.add_receipt_validation_categories(categories)

    assert_category_items(client, dynamodb_table, categories)


@pytest.mark.integration
def test_addReceiptValidationCategories_with_large_batch(
    dynamodb_table, sample_receipt_validation_category
):
    # Setup
    client = DynamoClient(table_name=dynamodb_table)

    categories = make_numbered_categories(30)

    # Execute
    client.add_receipt_validation_categories(categories)

    assert_category_items(client, dynamodb_table, categories)


@pytest.mark.integration
def test_addReceiptValidationCategories_with_unprocessed_items_retries(
    dynamodb_table, sample_receipt_validation_category, mocker
):
    # Setup
    client = DynamoClient(table_name=dynamodb_table)

    # Create a list of 2 categories
    categories = [
        sample_receipt_validation_category,
        make_category(
            field_name="payment_method",
            field_category="payment",
            reasoning="The payment method is valid",
            valid=2,
            metadata={"confidence": 0.9},
        ),
    ]

    # Mock the batch_write_item to simulate unprocessed items on first call
    mock_batch_write = mocker.patch.object(client._client, "batch_write_item")

    # Define the side effect function to return unprocessed items on first call and nothing on second call
    def batch_write_side_effect(*args, **kwargs):
        if mock_batch_write.call_count == 1:
            # First call: return one unprocessed item
            return {
                "UnprocessedItems": {
                    dynamodb_table: [
                        {"PutRequest": {"Item": categories[1].to_item()}}
                    ]
                }
            }
        else:
            # Second call: all processed successfully
            return {"UnprocessedItems": {}}

    mock_batch_write.side_effect = batch_write_side_effect

    # Execute
    client.add_receipt_validation_categories(categories)

    # Verify batch_write_item was called exactly twice
    assert (
        mock_batch_write.call_count == 2
    ), "batch_write_item should be called exactly twice"


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "categories cannot be None"),
        (
            "not-a-list",
            "categories must be a list",
        ),
        (
            ["not-a-validation-category"],
            "All items in categories must be instances of ReceiptValidationCategory",
        ),
    ],
)
def test_addReceiptValidationCategories_invalid_parameters(
    dynamodb_table,
    sample_receipt_validation_category,
    mocker,
    invalid_input,
    expected_error,
):
    # Setup
    client = DynamoClient(table_name=dynamodb_table)
    mock_batch_write = mocker.patch.object(client._client, "batch_write_item")

    # Execute and Assert
    with pytest.raises(OperationError, match=expected_error):
        client.add_receipt_validation_categories(invalid_input)

    # Verify that batch_write_item was not called
    mock_batch_write.assert_not_called()


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error_message",
    [
        (
            "ResourceNotFoundException",
            "Table not found",
            "Table not found",
        ),
        (
            "ProvisionedThroughputExceededException",
            "Throughput exceeded",
            "Throughput exceeded",
        ),
        (
            "InternalServerError",
            "Internal server error",
            "Internal server error",
        ),
        (
            "ValidationException",
            "One or more parameters given were invalid",
            "One or more parameters given were invalid",
        ),
        (
            "AccessDeniedException",
            "Access denied",
            "Access denied",
        ),
        (
            "UnknownError",
            "Unknown error occurred",
            "DynamoDB error during add_receipt_validation_categories",
        ),
    ],
)
def test_addReceiptValidationCategories_client_errors(
    dynamodb_table,
    sample_receipt_validation_category,
    mocker,
    error_code,
    error_message,
    expected_error_message,
):
    # Setup
    client = DynamoClient(table_name=dynamodb_table)
    mock_batch_write = mocker.patch.object(client._client, "batch_write_item")
    mock_batch_write.side_effect = ClientError(
        {"Error": {"Code": error_code, "Message": error_message}},
        "BatchWriteItem",
    )

    # Execute and Assert
    exception_type = {
        code: exception for code, exception, _ in ERROR_SCENARIOS
    }.get(error_code, DynamoDBError)
    with pytest.raises(exception_type, match=error_message):
        client.add_receipt_validation_categories(
            [sample_receipt_validation_category]
        )


@pytest.mark.integration
def test_updateReceiptValidationCategory_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_category: ReceiptValidationCategory,
):
    # Setup
    client = DynamoClient(table_name=dynamodb_table)

    # First, add the category
    client.add_receipt_validation_category(sample_receipt_validation_category)

    updated_category = make_updated_category(
        sample_receipt_validation_category
    )

    # Execute
    client.update_receipt_validation_category(updated_category)

    # Verify
    response = client._client.get_item(
        TableName=dynamodb_table,
        Key=updated_category.key,
    )
    assert "Item" in response, "Item was not found in the table."

    # Verify specific attributes were updated
    item = response["Item"]
    assert "status" in item, "Status field is missing"
    assert item["status"]["S"] == "invalid", "Status was not updated correctly"
    assert "reasoning" in item, "Reasoning field is missing"
    assert (
        item["reasoning"]["S"] == "Updated reasoning after review"
    ), "Reasoning was not updated correctly"


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "category cannot be None"),
        (
            "not a ReceiptValidationCategory",
            "category must be an instance of ReceiptValidationCategory",
        ),
    ],
)
def test_updateReceiptValidationCategory_invalid_parameters(
    dynamodb_table,
    sample_receipt_validation_category,
    mocker,
    invalid_input,
    expected_error,
):
    # Setup
    client = DynamoClient(table_name=dynamodb_table)
    mock_put_item = mocker.patch.object(client._client, "put_item")

    # Execute and Assert
    with pytest.raises(OperationError, match=expected_error):
        client.update_receipt_validation_category(invalid_input)

    # Verify that put_item was not called
    mock_put_item.assert_not_called()


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error",
    [
        (
            "ConditionalCheckFailedException",
            "Item does not exist",
            "receiptvalidationcategory not found during",
        ),
        (
            "ProvisionedThroughputExceededException",
            "Provisioned throughput exceeded",
            "Provisioned throughput exceeded",
        ),
        (
            "InternalServerError",
            "Internal server error",
            "Internal server error",
        ),
        (
            "ResourceNotFoundException",
            "Table not found",
            "Table not found",
        ),
        (
            "ValidationException",
            "One or more parameters given were invalid",
            "One or more parameters given were invalid",
        ),
        (
            "AccessDeniedException",
            "Access denied",
            "Access denied",
        ),
        (
            "UnknownError",
            "Unknown error occurred",
            "DynamoDB error during update_receipt_validation_category",
        ),
    ],
)
def test_updateReceiptValidationCategory_client_errors(
    dynamodb_table,
    sample_receipt_validation_category,
    mocker,
    error_code,
    error_message,
    expected_error,
):
    # Setup
    client = DynamoClient(table_name=dynamodb_table)

    # Mock the DynamoDB client to raise the specified error
    mock_put_item = mocker.patch.object(client._client, "put_item")
    mock_put_item.side_effect = ClientError(
        {"Error": {"Code": error_code, "Message": error_message}}, "PutItem"
    )

    # Execute and Assert
    expected_exception = {
        code: exception for code, exception, _ in UPDATE_ERROR_SCENARIOS
    }.get(error_code, DynamoDBError)
    error_match = (
        "does not exist"
        if error_code == "ConditionalCheckFailedException"
        else error_message
    )
    with pytest.raises(expected_exception, match=error_match):
        client.update_receipt_validation_category(
            sample_receipt_validation_category
        )


@pytest.mark.integration
def test_updateReceiptValidationCategories_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_category: ReceiptValidationCategory,
):
    # Setup
    client = DynamoClient(table_name=dynamodb_table)

    # Create multiple categories
    categories = [
        sample_receipt_validation_category,
        make_category(
            field_name="payment_method",
            field_category="payment",
            reasoning="The payment method is valid",
            valid=2,
            metadata={"confidence": 0.9},
        ),
    ]

    # Add all categories first
    for category in categories:
        client.add_receipt_validation_category(category)

    updated_categories = [
        make_updated_category(
            categories[0], reasoning="Updated reasoning for first category"
        ),
        make_updated_category(
            categories[1],
            reasoning="Updated reasoning for second category",
            valid=0,
            invalid=3,
            metadata={"confidence": 0.75, "updated": True},
        ),
    ]

    # Execute
    client.update_receipt_validation_categories(updated_categories)

    # Verify
    for category in updated_categories:
        response = client._client.get_item(
            TableName=dynamodb_table,
            Key=category.key,
        )
        assert (
            "Item" in response
        ), f"Category {category.field_name} was not found in the table."
        item = response["Item"]
        assert (
            item["status"]["S"] == "invalid"
        ), f"Category {category.field_name} status was not updated correctly"


@pytest.mark.integration
def test_updateReceiptValidationCategories_with_large_batch(
    dynamodb_table, sample_receipt_validation_category
):
    # Setup
    client = DynamoClient(table_name=dynamodb_table)

    categories = make_numbered_categories(30)
    for category in categories:
        client.add_receipt_validation_category(category)

    updated_categories = [
        make_updated_category(
            category,
            status="updated",
            reasoning=f"Updated reasoning {i}",
            valid=0,
            invalid=i,
            metadata={"index": i, "updated": True},
        )
        for i, category in enumerate(categories)
    ]

    # Execute
    client.update_receipt_validation_categories(updated_categories)

    # Verify
    for category in updated_categories:
        response = client._client.get_item(
            TableName=dynamodb_table,
            Key=category.key,
        )
        assert (
            "Item" in response
        ), f"Category {category.field_name} was not found in the table."
        item = response["Item"]
        assert (
            item["status"]["S"] == "updated"
        ), f"Category {category.field_name} status was not updated correctly"


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "categories cannot be None"),
        (
            "not-a-list",
            "categories must be a list",
        ),
        (
            [123, "not-a-validation-category"],
            "All items in categories must be instances of ReceiptValidationCategory",
        ),
    ],
)
def test_updateReceiptValidationCategories_invalid_inputs(
    dynamodb_table,
    sample_receipt_validation_category,
    mocker,
    invalid_input,
    expected_error,
):
    # Setup
    client = DynamoClient(table_name=dynamodb_table)
    mock_transact_write = mocker.patch.object(
        client._client, "transact_write_items"
    )

    # Execute and Assert
    with pytest.raises(OperationError, match=expected_error):
        client.update_receipt_validation_categories(invalid_input)

    # Verify that transact_write_items was not called
    mock_transact_write.assert_not_called()


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,expected_exception,error_match", ERROR_SCENARIOS
)
def test_updateReceiptValidationCategories_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_category: ReceiptValidationCategory,
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:
    # Setup
    client = DynamoClient(table_name=dynamodb_table)
    mock_transact_write = mocker.patch.object(
        client._client, "transact_write_items"
    )

    mock_transact_write.side_effect = ClientError(
        {
            "Error": {
                "Code": error_code,
                "Message": f"Mocked {error_code}",
            }
        },
        "TransactWriteItems",
    )

    # Execute and Assert
    with pytest.raises(expected_exception, match=error_match):
        client.update_receipt_validation_categories(
            [sample_receipt_validation_category]
        )
    mock_transact_write.assert_called_once()


@pytest.mark.integration
def test_deleteReceiptValidationCategory_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_category: ReceiptValidationCategory,
):
    # Setup
    client = DynamoClient(table_name=dynamodb_table)

    # Add the category first
    client.add_receipt_validation_category(sample_receipt_validation_category)

    # Verify it was added
    response = client._client.get_item(
        TableName=dynamodb_table,
        Key=sample_receipt_validation_category.key,
    )
    assert "Item" in response, "Item was not added to the table."

    # Execute
    client.delete_receipt_validation_category(
        sample_receipt_validation_category
    )

    # Verify it was deleted
    response = client._client.get_item(
        TableName=dynamodb_table,
        Key=sample_receipt_validation_category.key,
    )
    assert "Item" not in response, "Item was not deleted from the table."


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "category cannot be None"),
        (
            "not-a-validation-category",
            "category must be an instance of ReceiptValidationCategory",
        ),
    ],
)
def test_deleteReceiptValidationCategory_invalid_parameters(
    dynamodb_table,
    sample_receipt_validation_category,
    mocker,
    invalid_input,
    expected_error,
):
    # Setup
    client = DynamoClient(table_name=dynamodb_table)
    mock_delete_item = mocker.patch.object(client._client, "delete_item")

    # Execute and Assert
    with pytest.raises(OperationError, match=expected_error):
        client.delete_receipt_validation_category(invalid_input)

    # Verify that delete_item was not called
    mock_delete_item.assert_not_called()


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error",
    [
        (
            "ConditionalCheckFailedException",
            "Item does not exist",
            "receiptvalidationcategory not found during",
        ),
        (
            "ResourceNotFoundException",
            "Table not found",
            "Table not found",
        ),
        (
            "ProvisionedThroughputExceededException",
            "Provisioned throughput exceeded",
            "Provisioned throughput exceeded",
        ),
        (
            "InternalServerError",
            "Internal server error",
            "Internal server error",
        ),
        (
            "ValidationException",
            "One or more parameters given were invalid",
            "One or more parameters given were invalid",
        ),
        ("AccessDeniedException", "Access denied", "Access denied"),
        (
            "UnknownError",
            "Unknown error occurred",
            "DynamoDB error during delete_receipt_validation_category",
        ),
    ],
)
def test_deleteReceiptValidationCategory_client_errors(
    dynamodb_table,
    sample_receipt_validation_category,
    mocker,
    error_code,
    error_message,
    expected_error,
):
    # Setup
    client = DynamoClient(table_name=dynamodb_table)

    # Mock the DynamoDB client to raise the specified error
    mock_delete_item = mocker.patch.object(client._client, "delete_item")
    mock_delete_item.side_effect = ClientError(
        {"Error": {"Code": error_code, "Message": error_message}}, "DeleteItem"
    )

    # Execute and Assert
    expected_exception = {
        code: exception for code, exception, _ in DELETE_ERROR_SCENARIOS
    }.get(error_code, DynamoDBError)
    error_match = (
        "does not exist"
        if error_code == "ConditionalCheckFailedException"
        else error_message
    )
    with pytest.raises(expected_exception, match=error_match):
        client.delete_receipt_validation_category(
            sample_receipt_validation_category
        )


@pytest.mark.integration
def test_deleteReceiptValidationCategories_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_category: ReceiptValidationCategory,
):
    # Setup
    client = DynamoClient(table_name=dynamodb_table)

    # Create a list of categories
    categories = [
        sample_receipt_validation_category,
        make_category(
            field_name="payment_method",
            field_category="payment",
            reasoning="The payment method is valid",
            valid=2,
            metadata={"confidence": 0.9},
        ),
        make_category(
            field_name="merchant_name",
            field_category="merchant",
            status="invalid",
            reasoning="The merchant name could not be validated",
            valid=0,
            invalid=1,
            metadata={"confidence": 0.7},
        ),
    ]

    # Add all categories first
    for category in categories:
        client.add_receipt_validation_category(category)

    # Execute
    client.delete_receipt_validation_categories(categories)

    # Verify
    for category in categories:
        response = client._client.get_item(
            TableName=dynamodb_table,
            Key=category.key,
        )
        assert (
            "Item" not in response
        ), f"Category {category.field_name} was not deleted from the table."


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "categories cannot be None"),
        (
            "not-a-list",
            "categories must be a list",
        ),
        (
            [123, "not-a-validation-category"],
            "All items in categories must be instances of ReceiptValidationCategory",
        ),
    ],
)
def test_deleteReceiptValidationCategories_invalid_parameters(
    dynamodb_table,
    sample_receipt_validation_category,
    mocker,
    invalid_input,
    expected_error,
):
    # Setup
    client = DynamoClient(table_name=dynamodb_table)
    mock_batch_write = mocker.patch.object(client._client, "batch_write_item")

    # Execute and Assert
    with pytest.raises(OperationError, match=expected_error):
        client.delete_receipt_validation_categories(invalid_input)

    # Verify that batch_write_item was not called
    mock_batch_write.assert_not_called()


@pytest.mark.integration
def test_listReceiptValidationCategoriesForReceipt_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_category: ReceiptValidationCategory,
    mocker,
):
    # Setup
    client = DynamoClient(table_name=dynamodb_table)
    receipt_id = sample_receipt_validation_category.receipt_id
    image_id = sample_receipt_validation_category.image_id

    # Mock the query response
    mock_query = mocker.patch.object(client._client, "query")

    # Create sample response
    mock_query.return_value = {
        "Items": [
            sample_receipt_validation_category.to_item(),
            {
                "PK": {"S": f"IMAGE#{image_id}"},
                "SK": {
                    "S": f"RECEIPT#{receipt_id}#ANALYSIS#VALIDATION#CATEGORY#payment_method"
                },
                "GSI1PK": {"S": "ANALYSIS_TYPE"},
                "GSI1SK": {
                    "S": "VALIDATION#2023-05-15T13:14:15.678Z#CATEGORY#payment_method"
                },
                "field_category": {"S": "payment"},
                "status": {"S": "valid"},
                "reasoning": {"S": "The payment method is valid"},
                "result_summary": {
                    "M": {"valid": {"N": "2"}, "invalid": {"N": "0"}}
                },
                "validation_timestamp": {"S": "2023-05-15T13:14:15.678Z"},
                "metadata": {"M": {"confidence": {"N": "0.9"}}},
            },
        ]
    }

    # Execute
    results, last_evaluated_key = (
        client.list_receipt_validation_categories_for_receipt(
            receipt_id=receipt_id,
            image_id=image_id,
        )
    )

    # Verify
    assert len(results) == 2, "Should return 2 categories"
    assert all(
        isinstance(result, ReceiptValidationCategory) for result in results
    ), "All results should be ReceiptValidationCategory instances"
    assert (
        results[0].field_name == sample_receipt_validation_category.field_name
    ), "First result should match sample"
    assert (
        results[1].field_name == "payment_method"
    ), "Second result should match mock data"

    # Verify the query parameters
    mock_query.assert_called_once()
    _, kwargs = mock_query.call_args
    assert kwargs["TableName"] == dynamodb_table
    assert (
        kwargs["KeyConditionExpression"]
        == "#pk = :pk AND begins_with(#sk, :sk_prefix)"
    )
    assert kwargs["ExpressionAttributeNames"] == {
        "#pk": "PK",
        "#sk": "SK",
    }
    assert kwargs["ExpressionAttributeValues"] == {
        ":pk": {"S": f"IMAGE#{image_id}"},
        ":sk_prefix": {
            "S": (f"RECEIPT#{receipt_id:05d}#" "ANALYSIS#VALIDATION#CATEGORY#")
        },
    }


@pytest.mark.integration
def test_listReceiptValidationCategoriesForReceipt_with_pagination(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_category: ReceiptValidationCategory,
    mocker,
):
    # Setup
    client = DynamoClient(table_name=dynamodb_table)
    receipt_id = sample_receipt_validation_category.receipt_id
    image_id = sample_receipt_validation_category.image_id

    # Mock the query response
    mock_query = mocker.patch.object(client._client, "query")

    # Create sample responses with pagination
    last_evaluated_key = {
        "PK": {"S": f"IMAGE#{image_id}"},
        "SK": {
            "S": f"RECEIPT#{receipt_id}#ANALYSIS#VALIDATION#CATEGORY#some-field"
        },
    }
    mock_query.side_effect = [
        {
            "Items": [sample_receipt_validation_category.to_item()],
            "LastEvaluatedKey": last_evaluated_key,
        },
        {
            "Items": [
                {
                    "PK": {"S": f"IMAGE#{image_id}"},
                    "SK": {
                        "S": f"RECEIPT#{receipt_id}#ANALYSIS#VALIDATION#CATEGORY#payment_method"
                    },
                    "GSI1PK": {"S": "ANALYSIS_TYPE"},
                    "GSI1SK": {
                        "S": "VALIDATION#2023-05-15T13:14:15.678Z#CATEGORY#payment_method"
                    },
                    "field_category": {"S": "payment"},
                    "status": {"S": "valid"},
                    "reasoning": {"S": "The payment method is valid"},
                    "result_summary": {
                        "M": {"valid": {"N": "2"}, "invalid": {"N": "0"}}
                    },
                    "validation_timestamp": {"S": "2023-05-15T13:14:15.678Z"},
                    "metadata": {"M": {"confidence": {"N": "0.9"}}},
                }
            ],
        },
    ]

    # Execute with pagination
    results, _ = client.list_receipt_validation_categories_for_receipt(
        receipt_id=receipt_id,
        image_id=image_id,
    )

    # Verify
    assert len(results) == 2, "Should return 2 categories in total"
    assert all(
        isinstance(result, ReceiptValidationCategory) for result in results
    ), "All results should be ReceiptValidationCategory instances"
    assert (
        results[0].field_name == sample_receipt_validation_category.field_name
    ), "First result should match sample"
    assert (
        results[1].field_name == "payment_method"
    ), "Second result should match mock data"

    # Verify the query parameters for pagination
    assert (
        mock_query.call_count == 2
    ), "Query should be called twice for pagination"
    _, second_call_kwargs = mock_query.call_args_list[1]
    assert (
        "ExclusiveStartKey" in second_call_kwargs
    ), "Second call should include LastEvaluatedKey"
    assert (
        second_call_kwargs["ExclusiveStartKey"] == last_evaluated_key
    ), "Should use the correct LastEvaluatedKey"


@pytest.mark.integration
def test_listReceiptValidationCategoriesForReceipt_empty_results(
    dynamodb_table: Literal["MyMockedTable"],
    mocker,
):
    # Setup
    client = DynamoClient(table_name=dynamodb_table)
    receipt_id = 1
    image_id = IMAGE_ID

    # Mock the query response to return no items
    mock_query = mocker.patch.object(client._client, "query")
    mock_query.return_value = {"Items": []}

    # Execute
    results, last_evaluated_key = (
        client.list_receipt_validation_categories_for_receipt(
            receipt_id=receipt_id,
            image_id=image_id,
        )
    )

    # Verify
    assert len(results) == 0, "Should return empty list"
    assert last_evaluated_key is None, "Last evaluated key should be None"


@pytest.mark.integration
@pytest.mark.parametrize(
    "receipt_id,image_id,expected_error",
    [
        (
            None,
            IMAGE_ID,
            "receipt_id must be an integer, got NoneType",
        ),
        (1, None, "image_id must be a string, got NoneType"),
        (1, "", "uuid must be a valid UUIDv4"),
    ],
)
def test_listReceiptValidationCategoriesForReceipt_invalid_parameters(
    dynamodb_table: Literal["MyMockedTable"],
    receipt_id,
    image_id,
    expected_error,
):
    # Setup
    client = DynamoClient(table_name=dynamodb_table)

    # Execute and Assert
    with pytest.raises(EntityValidationError, match=expected_error):
        client.list_receipt_validation_categories_for_receipt(
            receipt_id=receipt_id,
            image_id=image_id,
        )


@pytest.mark.integration
def test_listReceiptValidationCategoriesForReceipt_with_invalid_limit(
    dynamodb_table: Literal["MyMockedTable"],
):
    # Setup
    client = DynamoClient(table_name=dynamodb_table)
    receipt_id = 1
    image_id = IMAGE_ID

    # Execute and Assert
    with pytest.raises(
        EntityValidationError, match="limit must be an integer or None"
    ):
        client.list_receipt_validation_categories_for_receipt(
            receipt_id=receipt_id, image_id=image_id, limit="not-an-integer"  # type: ignore[arg-type]
        )

    with pytest.raises(
        EntityValidationError,
        match="last_evaluated_key must be a dictionary or None",
    ):
        client.list_receipt_validation_categories_for_receipt(
            receipt_id=receipt_id,
            image_id=image_id,
            last_evaluated_key="not-a-dict",  # type: ignore[arg-type]
        )


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error",
    [
        (
            "ResourceNotFoundException",
            "Table not found",
            "Table not found",
        ),
        (
            "ProvisionedThroughputExceededException",
            "Throughput exceeded",
            "Throughput exceeded",
        ),
        (
            "InternalServerError",
            "Internal server error",
            "Internal server error",
        ),
        (
            "ValidationException",
            "One or more parameters given were invalid",
            "One or more parameters given were invalid",
        ),
        (
            "AccessDeniedException",
            "Access denied",
            "Access denied",
        ),
        (
            "UnknownError",
            "Unknown error occurred",
            "DynamoDB error during list_receipt_validation_categories_for_receipt",
        ),
    ],
)
def test_listReceiptValidationCategoriesForReceipt_client_errors(
    dynamodb_table,
    mocker,
    error_code,
    error_message,
    expected_error,
):
    # Setup
    client = DynamoClient(table_name=dynamodb_table)
    receipt_id = 1
    image_id = IMAGE_ID

    # Mock the query to raise an error
    mock_query = mocker.patch.object(client._client, "query")
    mock_query.side_effect = ClientError(
        {"Error": {"Code": error_code, "Message": error_message}}, "Query"
    )

    # Execute and Assert
    exception_type = {
        code: exception for code, exception, _ in ERROR_SCENARIOS
    }.get(error_code, DynamoDBError)
    with pytest.raises(exception_type, match=error_message):
        client.list_receipt_validation_categories_for_receipt(
            receipt_id=receipt_id,
            image_id=image_id,
        )
