from datetime import datetime
from typing import Literal

import pytest
from botocore.exceptions import ClientError

from receipt_dynamo import DynamoClient, ReceiptLabelAnalysis
from receipt_dynamo.data.shared_exceptions import (
    DynamoDBAccessError,
    DynamoDBError,
    DynamoDBServerError,
    DynamoDBThroughputError,
    DynamoDBValidationError,
    EntityAlreadyExistsError,
    EntityNotFoundError,
    EntityValidationError,
    OperationError,
)

pytestmark = [pytest.mark.integration, pytest.mark.unused_in_production]


def _client_error(error_code, operation, message=None):
    return ClientError(
        {
            "Error": {
                "Code": error_code,
                "Message": message or f"Mocked {error_code}",
            }
        },
        operation,
    )


def _patch_client_error(
    mocker, client, method, error_code, operation, message=None
):
    return mocker.patch.object(
        client._client,
        method,
        side_effect=_client_error(error_code, operation, message),
    )


def _expected_client_exception(
    error_code, conditional_exception=DynamoDBError
):
    return {
        "ConditionalCheckFailedException": conditional_exception,
        "ResourceNotFoundException": OperationError,
        "ProvisionedThroughputExceededException": DynamoDBThroughputError,
        "InternalServerError": DynamoDBServerError,
        "ValidationException": EntityValidationError,
        "AccessDeniedException": DynamoDBError,
    }.get(error_code, DynamoDBError)


@pytest.fixture
def sample_receipt_label_analysis():
    return ReceiptLabelAnalysis(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=1,
        labels=[
            {
                "label_type": "BUSINESS_NAME",
                "line_id": 10,
                "word_id": 5,
                "text": "Example Business",
                "reasoning": "This field appears to be the business name",
            }
        ],
        timestamp_added=datetime.now(),
        version="1.0",
        overall_reasoning="Overall reasoning for the label analysis",
    )


@pytest.mark.integration
def test_addReceiptLabelAnalysis_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_label_analysis: ReceiptLabelAnalysis,
):
    client = DynamoClient(dynamodb_table)

    client.add_receipt_label_analysis(sample_receipt_label_analysis)

    retrieved_analysis = client.get_receipt_label_analysis(
        sample_receipt_label_analysis.image_id,
        sample_receipt_label_analysis.receipt_id,
    )
    assert retrieved_analysis == sample_receipt_label_analysis


@pytest.mark.integration
def test_addReceiptLabelAnalysis_duplicate_raises(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_label_analysis: ReceiptLabelAnalysis,
):
    client = DynamoClient(dynamodb_table)
    client.add_receipt_label_analysis(sample_receipt_label_analysis)

    with pytest.raises(EntityAlreadyExistsError, match="already exists"):
        client.add_receipt_label_analysis(sample_receipt_label_analysis)


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (
            None,
            "ReceiptLabelAnalysis cannot be None",
        ),
        (
            "not-a-receipt-label-analysis",
            "ReceiptLabelAnalysis must be an instance of ReceiptLabelAnalysis",
        ),
    ],
)
def test_addReceiptLabelAnalysis_invalid_parameters(
    dynamodb_table,
    sample_receipt_label_analysis,
    mocker,
    invalid_input,
    expected_error,
):
    """
    Tests that addReceiptLabelAnalysis raises ValueError for invalid parameters:
    - When receipt label analysis is None
    - When receipt label analysis is not an instance of ReceiptLabelAnalysis
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(OperationError, match=expected_error):
        client.add_receipt_label_analysis(invalid_input)  # type: ignore


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_exception",
    [
        (
            "ConditionalCheckFailedException",
            "Item already exists",
            EntityAlreadyExistsError,
        ),
        (
            "ResourceNotFoundException",
            "Table not found",
            OperationError,
        ),
        (
            "ProvisionedThroughputExceededException",
            "Provisioned throughput exceeded",
            DynamoDBThroughputError,
        ),
        (
            "InternalServerError",
            "Internal server error",
            DynamoDBServerError,
        ),
        (
            "UnknownError",
            "Unknown error",
            DynamoDBError,
        ),
        (
            "ValidationException",
            "One or more parameters given were invalid",
            EntityValidationError,
        ),
        ("AccessDeniedException", "Access denied", DynamoDBError),
    ],
)
def test_addReceiptLabelAnalysis_client_errors(
    dynamodb_table,
    sample_receipt_label_analysis,
    mocker,
    error_code,
    error_message,
    expected_exception,
):
    """
    Tests that addReceiptLabelAnalysis handles various client errors appropriately:
    - ConditionalCheckFailedException when item already exists
    - ResourceNotFoundException when table not found
    - ProvisionedThroughputExceededException when throughput exceeded
    - InternalServerError for server-side errors
    - UnknownError for unexpected errors
    - ValidationException for invalid parameters
    - AccessDeniedException for access denied errors
    """
    client = DynamoClient(dynamodb_table)
    mock_put = _patch_client_error(
        mocker, client, "put_item", error_code, "PutItem", error_message
    )

    error_match = (
        "already exists"
        if error_code == "ConditionalCheckFailedException"
        else error_message
    )
    with pytest.raises(expected_exception, match=error_match):
        client.add_receipt_label_analysis(sample_receipt_label_analysis)
    mock_put.assert_called_once()


@pytest.mark.integration
def test_addReceiptLabelAnalyses_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_label_analysis: ReceiptLabelAnalysis,
):
    client = DynamoClient(dynamodb_table)
    analyses = [
        sample_receipt_label_analysis,
        ReceiptLabelAnalysis(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=2,
            labels=[
                {
                    "label_type": "ADDRESS",
                    "line_id": 20,
                    "word_id": 10,
                    "text": "123 Example St",
                    "reasoning": "This field appears to be the address",
                }
            ],
            timestamp_added=datetime.now(),
            version="1.0",
            overall_reasoning="Overall reasoning for the label analysis",
        ),
    ]

    client.add_receipt_label_analyses(analyses)

    for analysis in analyses:
        retrieved_analysis = client.get_receipt_label_analysis(
            analysis.image_id,
            analysis.receipt_id,
        )
        assert retrieved_analysis == analysis


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (
            None,
            "receipt_label_analyses cannot be None",
        ),
        (
            "not-a-list",
            "receipt_label_analyses must be a list",
        ),
        (
            [1, 2, 3],
            "All items in receipt_label_analyses must be instances of ReceiptLabelAnalysis",
        ),
    ],
)
def test_addReceiptLabelAnalyses_invalid_parameters(
    dynamodb_table,
    sample_receipt_label_analysis,
    mocker,
    invalid_input,
    expected_error,
):
    """
    Tests that addReceiptLabelAnalyses raises ValueError for invalid parameters:
    - When receipt label analyses is None
    - When receipt label analyses is not a list
    - When receipt label analyses contains non-ReceiptLabelAnalysis instances
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(OperationError, match=expected_error):
        client.add_receipt_label_analyses(invalid_input)  # type: ignore


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_exception",
    [
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
            "Unknown error",
            "Could not add receipt label analyses to DynamoDB",
        ),
    ],
)
def test_addReceiptLabelAnalyses_client_errors(
    dynamodb_table,
    sample_receipt_label_analysis,
    mocker,
    error_code,
    error_message,
    expected_exception,
):
    """
    Tests that addReceiptLabelAnalyses handles various client errors appropriately:
    - ProvisionedThroughputExceededException when throughput exceeded
    - InternalServerError for server-side errors
    - ValidationException for invalid parameters
    - AccessDeniedException for access denied errors
    - UnknownError for unexpected errors
    """
    client = DynamoClient(dynamodb_table)
    analyses = [sample_receipt_label_analysis]
    mock_batch_write = _patch_client_error(
        mocker,
        client,
        "batch_write_item",
        error_code,
        "BatchWriteItem",
        error_message,
    )

    exception_mapping = {
        "ProvisionedThroughputExceededException": DynamoDBThroughputError,
        "InternalServerError": DynamoDBServerError,
        "ValidationException": DynamoDBValidationError,
        "AccessDeniedException": DynamoDBAccessError,
        "UnknownError": DynamoDBError,
    }

    with pytest.raises(
        _expected_client_exception(error_code), match=error_message
    ):
        client.add_receipt_label_analyses(analyses)
    mock_batch_write.assert_called_once()


@pytest.mark.integration
def test_addReceiptLabelAnalyses_unprocessed_items(
    dynamodb_table,
    sample_receipt_label_analysis,
    mocker,
):
    """
    Tests that addReceiptLabelAnalyses handles unprocessed items correctly by retrying them.
    """
    client = DynamoClient(dynamodb_table)
    analyses = [sample_receipt_label_analysis]

    mock_batch_write = mocker.patch.object(
        client._client,
        "batch_write_item",
        side_effect=[
            {
                "UnprocessedItems": {
                    dynamodb_table: [
                        {
                            "PutRequest": {
                                "Item": sample_receipt_label_analysis.to_item()
                            }
                        }
                    ]
                }
            },
            {},  # Empty response on second call
        ],
    )

    client.add_receipt_label_analyses(analyses)

    assert mock_batch_write.call_count == 2


@pytest.mark.integration
def test_updateReceiptLabelAnalysis_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_label_analysis: ReceiptLabelAnalysis,
):
    client = DynamoClient(dynamodb_table)
    client.add_receipt_label_analysis(sample_receipt_label_analysis)

    updated_analysis = ReceiptLabelAnalysis(
        image_id=sample_receipt_label_analysis.image_id,
        receipt_id=sample_receipt_label_analysis.receipt_id,
        labels=[
            {
                "label_type": "BUSINESS_NAME",
                "line_id": 10,
                "word_id": 5,
                "text": "Updated Business",
                "reasoning": "Updated reasoning",
            }
        ],
        timestamp_added=sample_receipt_label_analysis.timestamp_added,
        version="1.0",
        overall_reasoning="Updated reasoning for the label analysis",
    )

    client.update_receipt_label_analysis(updated_analysis)

    retrieved_analysis = client.get_receipt_label_analysis(
        updated_analysis.image_id,
        updated_analysis.receipt_id,
    )
    assert retrieved_analysis == updated_analysis
    assert retrieved_analysis.labels[0]["text"] == "Updated Business"


@pytest.mark.integration
def test_updateReceiptLabelAnalysis_nonexistent_raises(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_label_analysis: ReceiptLabelAnalysis,
):
    client = DynamoClient(dynamodb_table)

    with pytest.raises(EntityNotFoundError, match="does not exist"):
        client.update_receipt_label_analysis(sample_receipt_label_analysis)


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (
            None,
            "receipt_label_analysis cannot be None",
        ),
        (
            "not-a-receipt-label-analysis",
            "receipt_label_analysis must be an instance of ReceiptLabelAnalysis",
        ),
    ],
)
def test_updateReceiptLabelAnalysis_invalid_parameters(
    dynamodb_table,
    sample_receipt_label_analysis,
    mocker,
    invalid_input,
    expected_error,
):
    """
    Tests that updateReceiptLabelAnalysis raises ValueError for invalid parameters:
    - When receipt label analysis is None
    - When receipt label analysis is not an instance of ReceiptLabelAnalysis
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(OperationError, match=expected_error):
        client.update_receipt_label_analysis(invalid_input)  # type: ignore


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_exception",
    [
        (
            "ConditionalCheckFailedException",
            "Item does not exist",
            "does not exist",
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
            "Unknown error",
            "Could not update receipt label analysis in DynamoDB",
        ),
    ],
)
def test_updateReceiptLabelAnalysis_client_errors(
    dynamodb_table,
    sample_receipt_label_analysis,
    mocker,
    error_code,
    error_message,
    expected_exception,
):
    """
    Tests that updateReceiptLabelAnalysis handles various client errors appropriately:
    - ConditionalCheckFailedException when item doesn't exist
    - ProvisionedThroughputExceededException when throughput exceeded
    - InternalServerError for server-side errors
    - ValidationException for invalid parameters
    - AccessDeniedException for access denied errors
    - UnknownError for unexpected errors
    """
    client = DynamoClient(dynamodb_table)
    mock_put = _patch_client_error(
        mocker, client, "put_item", error_code, "PutItem", error_message
    )

    exception_mapping = {
        "ConditionalCheckFailedException": EntityNotFoundError,  # For update operations
        "ProvisionedThroughputExceededException": DynamoDBThroughputError,
        "InternalServerError": DynamoDBServerError,
        "ValidationException": DynamoDBValidationError,
        "AccessDeniedException": DynamoDBAccessError,
        "UnknownError": DynamoDBError,
    }

    exception_type = _expected_client_exception(
        error_code, EntityNotFoundError
    )
    error_match = (
        "does not exist"
        if error_code == "ConditionalCheckFailedException"
        else error_message
    )
    with pytest.raises(exception_type, match=error_match):
        client.update_receipt_label_analysis(sample_receipt_label_analysis)
    mock_put.assert_called_once()


@pytest.mark.integration
def test_updateReceiptLabelAnalyses_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_label_analysis: ReceiptLabelAnalysis,
):
    client = DynamoClient(dynamodb_table)

    analyses = [
        sample_receipt_label_analysis,
        ReceiptLabelAnalysis(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=2,
            labels=[
                {
                    "label_type": "ADDRESS",
                    "line_id": 20,
                    "word_id": 10,
                    "text": "123 Example St",
                    "reasoning": "This field appears to be the address",
                }
            ],
            timestamp_added=datetime.now(),
            version="1.0",
            overall_reasoning="Overall reasoning for the label analysis",
        ),
    ]

    client.add_receipt_label_analyses(analyses)

    updated_analyses = [
        ReceiptLabelAnalysis(
            image_id=analyses[0].image_id,
            receipt_id=analyses[0].receipt_id,
            labels=[
                {
                    "label_type": "BUSINESS_NAME",
                    "line_id": 10,
                    "word_id": 5,
                    "text": "Updated Business",
                    "reasoning": "Updated reasoning",
                }
            ],
            timestamp_added=analyses[0].timestamp_added,
            version="1.0",
            overall_reasoning="Updated reasoning for the label analysis",
        ),
        ReceiptLabelAnalysis(
            image_id=analyses[1].image_id,
            receipt_id=analyses[1].receipt_id,
            labels=[
                {
                    "label_type": "ADDRESS",
                    "line_id": 20,
                    "word_id": 10,
                    "text": "456 Updated St",
                    "reasoning": "Updated address reasoning",
                }
            ],
            timestamp_added=analyses[1].timestamp_added,
            version="1.0",
            overall_reasoning="Updated reasoning for the address analysis",
        ),
    ]

    client.update_receipt_label_analyses(updated_analyses)

    for analysis in updated_analyses:
        retrieved_analysis = client.get_receipt_label_analysis(
            analysis.image_id,
            analysis.receipt_id,
        )
        assert retrieved_analysis == analysis


@pytest.mark.integration
def test_updateReceiptLabelAnalyses_nonexistent_raises(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_label_analysis: ReceiptLabelAnalysis,
):
    client = DynamoClient(dynamodb_table)
    analyses = [sample_receipt_label_analysis]

    with pytest.raises(DynamoDBError, match="Transaction cancelled"):
        client.update_receipt_label_analyses(analyses)


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (
            None,
            "receipt_label_analyses cannot be None",
        ),
        (
            "not-a-list",
            "receipt_label_analyses must be a list",
        ),
        (
            [1, 2, 3],
            "All items in receipt_label_analyses must be instances of ReceiptLabelAnalysis",
        ),
    ],
)
def test_updateReceiptLabelAnalyses_invalid_parameters(
    dynamodb_table,
    sample_receipt_label_analysis,
    mocker,
    invalid_input,
    expected_error,
):
    """
    Tests that updateReceiptLabelAnalyses raises ValueError for invalid parameters:
    - When receipt label analyses is None
    - When receipt label analyses is not a list
    - When receipt label analyses contains non-ReceiptLabelAnalysis instances
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(OperationError, match=expected_error):
        client.update_receipt_label_analyses(invalid_input)  # type: ignore


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error,expected_exception",
    [
        (
            "ConditionalCheckFailedException",
            "One or more items do not exist",
            "not found",
            EntityNotFoundError,
        ),
        (
            "ProvisionedThroughputExceededException",
            "Provisioned throughput exceeded",
            "Provisioned throughput exceeded",
            DynamoDBThroughputError,
        ),
        (
            "InternalServerError",
            "Internal server error",
            "Internal server error",
            DynamoDBServerError,
        ),
        (
            "ValidationException",
            "One or more parameters given were invalid",
            "One or more parameters given were invalid",
            EntityValidationError,
        ),
        (
            "AccessDeniedException",
            "Access denied",
            "Access denied",
            DynamoDBError,
        ),
        (
            "UnknownError",
            "Unknown error",
            "Unknown error",
            DynamoDBError,
        ),
    ],
)
def test_updateReceiptLabelAnalyses_client_errors(
    dynamodb_table,
    sample_receipt_label_analysis,
    mocker,
    error_code,
    error_message,
    expected_error,
    expected_exception,
):
    """
    Tests that updateReceiptLabelAnalyses handles various client errors appropriately.
    """
    client = DynamoClient(dynamodb_table)
    analyses = [sample_receipt_label_analysis]
    mock_transact = _patch_client_error(
        mocker,
        client,
        "transact_write_items",
        error_code,
        "TransactWriteItems",
        error_message,
    )

    with pytest.raises(expected_exception, match=expected_error):
        client.update_receipt_label_analyses(analyses)
    mock_transact.assert_called_once()


@pytest.mark.integration
def test_updateReceiptLabelAnalyses_chunking(
    dynamodb_table,
    sample_receipt_label_analysis,
    mocker,
):
    """
    Tests that updateReceiptLabelAnalyses handles chunking correctly when more than 25 items.
    DynamoDB transactions are limited to 25 items at a time.
    """
    client = DynamoClient(dynamodb_table)

    analyses = []
    for i in range(1, 31):
        analysis = ReceiptLabelAnalysis(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=i,
            labels=[
                {
                    "label_type": f"LABEL_{i}",
                    "line_id": 10,
                    "word_id": 5,
                    "text": f"Text {i}",
                    "reasoning": f"Reasoning {i}",
                }
            ],
            timestamp_added=datetime.now(),
            version="1.0",
            overall_reasoning=f"Overall reasoning {i}",
        )
        analyses.append(analysis)

    mock_transact = mocker.patch.object(
        client._client, "transact_write_items", return_value={}
    )

    client.update_receipt_label_analyses(analyses)

    assert mock_transact.call_count == 2

    first_call_args = mock_transact.call_args_list[0][1]
    assert len(first_call_args["TransactItems"]) == 25

    second_call_args = mock_transact.call_args_list[1][1]
    assert len(second_call_args["TransactItems"]) == 5


@pytest.mark.integration
def test_deleteReceiptLabelAnalysis_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_label_analysis: ReceiptLabelAnalysis,
):
    client = DynamoClient(dynamodb_table)
    client.add_receipt_label_analysis(sample_receipt_label_analysis)

    client.delete_receipt_label_analysis(sample_receipt_label_analysis)

    with pytest.raises(
        EntityNotFoundError, match="(does not exist|not found)"
    ):
        client.get_receipt_label_analysis(
            sample_receipt_label_analysis.image_id,
            sample_receipt_label_analysis.receipt_id,
        )


@pytest.mark.integration
def test_deleteReceiptLabelAnalysis_nonexistent_raises(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_label_analysis: ReceiptLabelAnalysis,
):
    client = DynamoClient(dynamodb_table)

    with pytest.raises(EntityNotFoundError, match="does not exist"):
        client.delete_receipt_label_analysis(sample_receipt_label_analysis)


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (
            None,
            "receipt_label_analysis cannot be None",
        ),
        (
            "not-a-receipt-label-analysis",
            "receipt_label_analysis must be an instance of ReceiptLabelAnalysis",
        ),
    ],
)
def test_deleteReceiptLabelAnalysis_invalid_parameters(
    dynamodb_table,
    sample_receipt_label_analysis,
    mocker,
    invalid_input,
    expected_error,
):
    """
    Tests that deleteReceiptLabelAnalysis raises ValueError for invalid parameters:
    - When receipt label analysis is None
    - When receipt label analysis is not an instance of ReceiptLabelAnalysis
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(OperationError, match=expected_error):
        client.delete_receipt_label_analysis(invalid_input)  # type: ignore


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_exception",
    [
        (
            "ConditionalCheckFailedException",
            "Item does not exist",
            "does not exist",
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
            "Unknown error",
            "Could not delete receipt label analysis from DynamoDB",
        ),
    ],
)
def test_deleteReceiptLabelAnalysis_client_errors(
    dynamodb_table,
    sample_receipt_label_analysis,
    mocker,
    error_code,
    error_message,
    expected_exception,
):
    """
    Tests that deleteReceiptLabelAnalysis handles various client errors appropriately:
    - ConditionalCheckFailedException when item doesn't exist
    - ProvisionedThroughputExceededException when throughput exceeded
    - InternalServerError for server-side errors
    - ValidationException for invalid parameters
    - AccessDeniedException for access denied errors
    - UnknownError for unexpected errors
    """
    client = DynamoClient(dynamodb_table)
    mock_delete = _patch_client_error(
        mocker, client, "delete_item", error_code, "DeleteItem", error_message
    )

    exception_mapping = {
        "ConditionalCheckFailedException": EntityNotFoundError,  # For delete operations
        "ProvisionedThroughputExceededException": DynamoDBThroughputError,
        "InternalServerError": DynamoDBServerError,
        "ValidationException": DynamoDBValidationError,
        "AccessDeniedException": DynamoDBAccessError,
        "UnknownError": DynamoDBError,
    }

    exception_type = _expected_client_exception(
        error_code, EntityNotFoundError
    )
    error_match = (
        "does not exist"
        if error_code == "ConditionalCheckFailedException"
        else error_message
    )
    with pytest.raises(exception_type, match=error_match):
        client.delete_receipt_label_analysis(sample_receipt_label_analysis)
    mock_delete.assert_called_once()


@pytest.mark.integration
def test_deleteReceiptLabelAnalyses_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_label_analysis: ReceiptLabelAnalysis,
):
    client = DynamoClient(dynamodb_table)

    analyses = [
        sample_receipt_label_analysis,
        ReceiptLabelAnalysis(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=2,
            labels=[
                {
                    "label_type": "ADDRESS",
                    "line_id": 20,
                    "word_id": 10,
                    "text": "123 Example St",
                    "reasoning": "This field appears to be the address",
                }
            ],
            timestamp_added=datetime.now(),
            version="1.0",
            overall_reasoning="Overall reasoning for the label analysis",
        ),
    ]

    client.add_receipt_label_analyses(analyses)

    client.delete_receipt_label_analyses(analyses)

    for analysis in analyses:
        with pytest.raises(
            EntityNotFoundError, match="(does not exist|not found)"
        ):
            client.get_receipt_label_analysis(
                analysis.image_id,
                analysis.receipt_id,
            )


@pytest.mark.integration
def test_deleteReceiptLabelAnalyses_nonexistent_raises(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_label_analysis: ReceiptLabelAnalysis,
):
    client = DynamoClient(dynamodb_table)
    analyses = [sample_receipt_label_analysis]

    with pytest.raises(DynamoDBError, match="Transaction cancelled"):
        client.delete_receipt_label_analyses(analyses)


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (
            None,
            "receipt_label_analyses cannot be None",
        ),
        (
            "not-a-list",
            "receipt_label_analyses must be a list",
        ),
        (
            [1, 2, 3],
            "All items in receipt_label_analyses must be instances of ReceiptLabelAnalysis",
        ),
    ],
)
def test_deleteReceiptLabelAnalyses_invalid_parameters(
    dynamodb_table,
    sample_receipt_label_analysis,
    mocker,
    invalid_input,
    expected_error,
):
    """
    Tests that deleteReceiptLabelAnalyses raises ValueError for invalid parameters:
    - When receipt label analyses is None
    - When receipt label analyses is not a list
    - When receipt label analyses contains non-ReceiptLabelAnalysis instances
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(OperationError, match=expected_error):
        client.delete_receipt_label_analyses(invalid_input)  # type: ignore


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_exception",
    [
        (
            "ConditionalCheckFailedException",
            "One or more items do not exist",
            "One or more receipt label analyses do not exist",
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
            "Unknown error",
            "Could not delete receipt label analyses from DynamoDB",
        ),
    ],
)
def test_deleteReceiptLabelAnalyses_client_errors(
    dynamodb_table,
    sample_receipt_label_analysis,
    mocker,
    error_code,
    error_message,
    expected_exception,
):
    """
    Tests that deleteReceiptLabelAnalyses handles various client errors appropriately.
    """
    client = DynamoClient(dynamodb_table)
    analyses = [sample_receipt_label_analysis]
    mock_transact = _patch_client_error(
        mocker,
        client,
        "transact_write_items",
        error_code,
        "TransactWriteItems",
        error_message,
    )

    exception_mapping = {
        "ConditionalCheckFailedException": EntityNotFoundError,  # For batch operations
        "ProvisionedThroughputExceededException": DynamoDBThroughputError,
        "InternalServerError": DynamoDBServerError,
        "ValidationException": DynamoDBValidationError,
        "AccessDeniedException": DynamoDBAccessError,
        "UnknownError": DynamoDBError,
    }

    exception_type = _expected_client_exception(
        error_code, EntityNotFoundError
    )
    error_match = (
        "not found"
        if error_code == "ConditionalCheckFailedException"
        else error_message
    )
    with pytest.raises(exception_type, match=error_match):
        client.delete_receipt_label_analyses(analyses)
    mock_transact.assert_called_once()


@pytest.mark.integration
def test_deleteReceiptLabelAnalyses_chunking(
    dynamodb_table,
    sample_receipt_label_analysis,
    mocker,
):
    """
    Tests that deleteReceiptLabelAnalyses handles chunking correctly when more than 25 items.
    DynamoDB transactions are limited to 25 items at a time.
    """
    client = DynamoClient(dynamodb_table)

    analyses = []
    for i in range(1, 31):
        analysis = ReceiptLabelAnalysis(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=i,
            labels=[
                {
                    "label_type": f"LABEL_{i}",
                    "line_id": 10,
                    "word_id": 5,
                    "text": f"Text {i}",
                    "reasoning": f"Reasoning {i}",
                }
            ],
            timestamp_added=datetime.now(),
            version="1.0",
            overall_reasoning=f"Overall reasoning {i}",
        )
        analyses.append(analysis)

    mock_transact = mocker.patch.object(
        client._client, "transact_write_items", return_value={}
    )

    client.delete_receipt_label_analyses(analyses)

    assert mock_transact.call_count == 2

    first_call_args = mock_transact.call_args_list[0][1]
    assert len(first_call_args["TransactItems"]) == 25

    second_call_args = mock_transact.call_args_list[1][1]
    assert len(second_call_args["TransactItems"]) == 5


@pytest.mark.integration
def test_getReceiptLabelAnalysis_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_label_analysis: ReceiptLabelAnalysis,
):
    client = DynamoClient(dynamodb_table)
    client.add_receipt_label_analysis(sample_receipt_label_analysis)

    retrieved_analysis = client.get_receipt_label_analysis(
        sample_receipt_label_analysis.image_id,
        sample_receipt_label_analysis.receipt_id,
    )

    assert retrieved_analysis == sample_receipt_label_analysis


@pytest.mark.integration
def test_getReceiptLabelAnalysis_nonexistent_raises(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_label_analysis: ReceiptLabelAnalysis,
):
    client = DynamoClient(dynamodb_table)

    with pytest.raises(
        EntityNotFoundError, match="(does not exist|not found)"
    ):
        client.get_receipt_label_analysis(
            sample_receipt_label_analysis.image_id,
            sample_receipt_label_analysis.receipt_id,
        )


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_params,expected_error",
    [
        (
            (None, 1),
            "image_id cannot be None",
        ),
        (
            ("3f52804b-2fad-4e00-92c8-b593da3a8ed3", None),
            "receipt_id cannot be None",
        ),
        (
            ("invalid-uuid", 1),
            "uuid must be a valid UUIDv4",
        ),
        (
            ("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 0),
            "receipt_id must be a positive integer",
        ),
    ],
)
def test_getReceiptLabelAnalysis_invalid_parameters(
    dynamodb_table,
    sample_receipt_label_analysis,
    mocker,
    invalid_params,
    expected_error,
):
    """
    Tests that getReceiptLabelAnalysis raises ValueError for invalid parameters:
    - When image ID is None
    - When receipt ID is None
    - When image ID is an invalid UUID
    - When receipt ID is not a positive integer
    """
    client = DynamoClient(dynamodb_table)
    exception_type = (
        OperationError
        if invalid_params[0] == "invalid-uuid"
        else EntityValidationError
    )
    with pytest.raises(exception_type, match=expected_error):
        client.get_receipt_label_analysis(*invalid_params)  # type: ignore


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_exception",
    [
        (
            "ProvisionedThroughputExceededException",
            "Provisioned throughput exceeded",
            "Provisioned throughput exceeded",
        ),
        (
            "ValidationException",
            "One or more parameters given were invalid",
            "One or more parameters given were invalid",
        ),
        (
            "InternalServerError",
            "Internal server error",
            "Internal server error",
        ),
        ("AccessDeniedException", "Access denied", "Access denied"),
        (
            "UnknownError",
            "Unknown error",
            "Could not get receipt label analysis",
        ),
    ],
)
def test_getReceiptLabelAnalysis_client_errors(
    dynamodb_table,
    sample_receipt_label_analysis,
    mocker,
    error_code,
    error_message,
    expected_exception,
):
    """
    Tests that getReceiptLabelAnalysis handles various client errors appropriately:
    - ProvisionedThroughputExceededException when throughput exceeded
    - ValidationException for invalid parameters
    - InternalServerError for server-side errors
    - AccessDeniedException for access denied errors
    - UnknownError for unexpected errors
    """
    client = DynamoClient(dynamodb_table)
    mock_get = _patch_client_error(
        mocker, client, "query", error_code, "Query", error_message
    )

    exception_mapping = {
        "ProvisionedThroughputExceededException": DynamoDBThroughputError,
        "ValidationException": DynamoDBValidationError,
        "InternalServerError": DynamoDBServerError,
        "AccessDeniedException": DynamoDBAccessError,
        "UnknownError": DynamoDBError,
    }

    with pytest.raises(
        _expected_client_exception(error_code), match=error_message
    ):
        client.get_receipt_label_analysis(
            sample_receipt_label_analysis.image_id,
            sample_receipt_label_analysis.receipt_id,
        )
    mock_get.assert_called_once()


@pytest.mark.integration
def test_listReceiptLabelAnalyses_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_label_analysis: ReceiptLabelAnalysis,
):
    client = DynamoClient(dynamodb_table)
    client.add_receipt_label_analysis(sample_receipt_label_analysis)

    analyses, last_evaluated_key = client.list_receipt_label_analyses()

    assert len(analyses) == 1
    assert analyses[0] == sample_receipt_label_analysis
    assert last_evaluated_key is None


@pytest.mark.integration
def test_listReceiptLabelAnalyses_with_limit(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_label_analysis: ReceiptLabelAnalysis,
):
    client = DynamoClient(dynamodb_table)

    analyses = []
    for i in range(1, 4):
        analysis = ReceiptLabelAnalysis(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=i,
            labels=[
                {
                    "label_type": f"LABEL_{i}",
                    "line_id": 10,
                    "word_id": 5,
                    "text": f"Text {i}",
                    "reasoning": f"Reasoning {i}",
                }
            ],
            timestamp_added=datetime.now(),
            version="1.0",
            overall_reasoning=f"Overall reasoning {i}",
        )
        analyses.append(analysis)
        client.add_receipt_label_analysis(analysis)

    result_analyses, last_evaluated_key = client.list_receipt_label_analyses(
        limit=2
    )

    assert len(result_analyses) == 2
    assert last_evaluated_key is not None  # Should have more results


@pytest.mark.integration
def test_listReceiptLabelAnalyses_with_last_evaluated_key(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_label_analysis: ReceiptLabelAnalysis,
    mocker,
):
    client = DynamoClient(dynamodb_table)

    mock_query = mocker.patch.object(
        client._client,
        "query",
        autospec=True,  # Use autospec to better track calls
    )

    mock_query.return_value = {
        "Items": [sample_receipt_label_analysis.to_item()],
        "LastEvaluatedKey": {
            "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
            "SK": {"S": "RECEIPT#1#ANALYSIS#LABELS"},
            "TYPE": {"S": "RECEIPT_LABEL_ANALYSIS"},
        },
    }

    analyses, last_evaluated_key = client.list_receipt_label_analyses(
        limit=1
    )  # Add a limit to ensure it exits the loop

    assert len(analyses) == 1
    assert last_evaluated_key == {
        "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": "RECEIPT#00001#ANALYSIS#LABELS"},
        "TYPE": {"S": "RECEIPT_LABEL_ANALYSIS"},
    }
    assert mock_query.call_count == 1


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input",
    [
        {"limit": "not-an-int"},
        {"limit": -1},
        {"last_evaluated_key": "not-a-dict"},
        {"last_evaluated_key": {"PK": "not-a-dict", "SK": {"S": "value"}}},
    ],
)
def test_listReceiptLabelAnalyses_invalid_parameters(
    dynamodb_table,
    invalid_input,
):
    """Invalid query arguments fail through the public operation contract."""
    client = DynamoClient(dynamodb_table)

    with pytest.raises(
        OperationError,
        match="Unexpected error during list_receipt_label_analyses",
    ):
        client.list_receipt_label_analyses(**invalid_input)


@pytest.mark.integration
@pytest.mark.parametrize(
    "accepted_input",
    [
        {"limit": 0},
        {"last_evaluated_key": {}},
    ],
)
def test_listReceiptLabelAnalyses_accepted_edge_parameters(
    dynamodb_table,
    accepted_input,
):
    """Zero limit and an empty start key retain their no-filter semantics."""
    client = DynamoClient(dynamodb_table)

    analyses, last_evaluated_key = client.list_receipt_label_analyses(
        **accepted_input
    )

    assert analyses == []
    assert last_evaluated_key is None


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_exception",
    [
        (
            "ResourceNotFoundException",
            "Table not found",
            OperationError,
        ),
        (
            "ProvisionedThroughputExceededException",
            "Provisioned throughput exceeded",
            DynamoDBThroughputError,
        ),
        (
            "ValidationException",
            "One or more parameters given were invalid",
            EntityValidationError,
        ),
        (
            "InternalServerError",
            "Internal server error",
            DynamoDBServerError,
        ),
        ("AccessDeniedException", "Access denied", DynamoDBError),
        (
            "UnknownError",
            "Unknown error",
            DynamoDBError,
        ),
    ],
)
def test_listReceiptLabelAnalyses_client_errors(
    dynamodb_table,
    mocker,
    error_code,
    error_message,
    expected_exception,
):
    """
    Tests that listReceiptLabelAnalyses handles various client errors appropriately.
    """
    client = DynamoClient(dynamodb_table)
    mock_query = _patch_client_error(
        mocker, client, "query", error_code, "Query", error_message
    )

    with pytest.raises(expected_exception, match=error_message):
        client.list_receipt_label_analyses()
    mock_query.assert_called_once()


@pytest.mark.integration
def test_getReceiptLabelAnalysesByImage_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_label_analysis: ReceiptLabelAnalysis,
):
    client = DynamoClient(dynamodb_table)
    client.add_receipt_label_analysis(sample_receipt_label_analysis)

    analyses = client.list_receipt_label_analyses_for_image(
        sample_receipt_label_analysis.image_id
    )

    assert len(analyses) == 1
    assert analyses[0] == sample_receipt_label_analysis


@pytest.mark.integration
def test_getReceiptLabelAnalysesByImage_with_limit(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_label_analysis: ReceiptLabelAnalysis,
):
    client = DynamoClient(dynamodb_table)

    analyses = []
    for i in range(1, 4):
        analysis = ReceiptLabelAnalysis(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=i,
            labels=[
                {
                    "label_type": f"LABEL_{i}",
                    "line_id": 10,
                    "word_id": 5,
                    "text": f"Text {i}",
                    "reasoning": f"Reasoning {i}",
                }
            ],
            timestamp_added=datetime.now(),
            version="1.0",
            overall_reasoning=f"Overall reasoning {i}",
        )
        analyses.append(analysis)
        client.add_receipt_label_analysis(analysis)

    result_analyses, last_evaluated_key = (
        client.get_receipt_label_analyses_by_image(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3", limit=2
        )
    )

    assert len(result_analyses) == 2
    assert last_evaluated_key is not None  # Should have more results


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (
            {"image_id": None},
            "image_id must be a string",
        ),
        (
            {"image_id": "invalid-uuid"},
            "uuid must be a valid UUIDv4",
        ),
        (
            {
                "image_id": "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
                "limit": "not-an-int",
            },
            "Limit must be an integer",
        ),
        (
            {"image_id": "3f52804b-2fad-4e00-92c8-b593da3a8ed3", "limit": 0},
            "Limit must be greater than 0",
        ),
        (
            {"image_id": "3f52804b-2fad-4e00-92c8-b593da3a8ed3", "limit": -1},
            "Limit must be greater than 0",
        ),
        (
            {
                "image_id": "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
                "last_evaluated_key": "not-a-dict",
            },
            "last_evaluated_key must be a dictionary",
        ),
        (
            {
                "image_id": "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
                "last_evaluated_key": {},
            },
            "LastEvaluatedKey must contain keys: \\{'SK', 'PK'\\}|LastEvaluatedKey must contain keys: \\{'PK', 'SK'\\}",
        ),
        (
            {
                "image_id": "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
                "last_evaluated_key": {
                    "PK": "not-a-dict",
                    "SK": {"S": "value"},
                },
            },
            "LastEvaluatedKey\\[PK\\] must be a dict containing a key 'S'",
        ),
    ],
)
def test_getReceiptLabelAnalysesByImage_invalid_parameters(
    dynamodb_table,
    sample_receipt_label_analysis,
    mocker,
    invalid_input,
    expected_error,
):
    """
    Tests that getReceiptLabelAnalysesByImage raises ValueError for invalid parameters.
    """
    client = DynamoClient(dynamodb_table)

    kwargs = {}
    if "image_id" in invalid_input:
        image_id = invalid_input["image_id"]
    else:
        image_id = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"

    if "limit" in invalid_input:
        kwargs["limit"] = invalid_input["limit"]

    if "last_evaluated_key" in invalid_input:
        kwargs["last_evaluated_key"] = invalid_input["last_evaluated_key"]

    exception_type = (
        OperationError
        if invalid_input.get("image_id") == "invalid-uuid"
        else EntityValidationError
    )
    with pytest.raises(exception_type, match=expected_error):
        client.get_receipt_label_analyses_by_image(image_id, **kwargs)  # type: ignore


@pytest.mark.integration
def test_getReceiptLabelAnalysesByReceipt_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_label_analysis: ReceiptLabelAnalysis,
):
    client = DynamoClient(dynamodb_table)
    client.add_receipt_label_analysis(sample_receipt_label_analysis)

    analyses, last_evaluated_key = (
        client.get_receipt_label_analyses_by_receipt(
            sample_receipt_label_analysis.image_id,
            sample_receipt_label_analysis.receipt_id,
        )
    )

    assert len(analyses) == 1
    assert analyses[0] == sample_receipt_label_analysis
    assert last_evaluated_key is None


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (
            {"image_id": None, "receipt_id": 1},
            "image_id must be a string",
        ),
        (
            {"image_id": "invalid-uuid", "receipt_id": 1},
            "uuid must be a valid UUIDv4",
        ),
        (
            {
                "image_id": "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
                "receipt_id": None,
            },
            "receipt_id must be a positive integer",
        ),
        (
            {
                "image_id": "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
                "receipt_id": 0,
            },
            "receipt_id must be a positive integer",
        ),
        (
            {
                "image_id": "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
                "receipt_id": 1,
                "limit": "not-an-int",
            },
            "Limit must be an integer",
        ),
        (
            {
                "image_id": "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
                "receipt_id": 1,
                "limit": 0,
            },
            "Limit must be greater than 0",
        ),
        (
            {
                "image_id": "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
                "receipt_id": 1,
                "last_evaluated_key": "not-a-dict",
            },
            "last_evaluated_key must be a dictionary",
        ),
    ],
)
def test_getReceiptLabelAnalysesByReceipt_invalid_parameters(
    dynamodb_table,
    sample_receipt_label_analysis,
    mocker,
    invalid_input,
    expected_error,
):
    """
    Tests that getReceiptLabelAnalysesByReceipt raises ValueError for invalid parameters.
    """
    client = DynamoClient(dynamodb_table)

    kwargs = {}
    if "image_id" in invalid_input:
        image_id = invalid_input["image_id"]
    else:
        image_id = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"

    if "receipt_id" in invalid_input:
        receipt_id = invalid_input["receipt_id"]
    else:
        receipt_id = 1

    if "limit" in invalid_input:
        kwargs["limit"] = invalid_input["limit"]

    if "last_evaluated_key" in invalid_input:
        kwargs["last_evaluated_key"] = invalid_input["last_evaluated_key"]

    exception_type = (
        OperationError
        if invalid_input.get("image_id") == "invalid-uuid"
        else EntityValidationError
    )
    with pytest.raises(exception_type, match=expected_error):
        client.get_receipt_label_analyses_by_receipt(image_id, receipt_id, **kwargs)  # type: ignore
