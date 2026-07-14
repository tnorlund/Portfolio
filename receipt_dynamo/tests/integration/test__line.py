# pylint: disable=too-many-lines,too-many-positional-arguments
"""
Comprehensive parameterized integration tests for line operations.
This file contains refactored tests using pytest.mark.parametrize to ensure
complete test coverage matching test__receipt_line.py standards.
"""

from math import radians
from typing import Any, Literal, Type
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError
from pytest_mock import MockerFixture

from receipt_dynamo import DynamoClient, Line
from receipt_dynamo.data.shared_exceptions import (
    DynamoDBError,
    DynamoDBServerError,
    DynamoDBThroughputError,
    EntityAlreadyExistsError,
    EntityNotFoundError,
    EntityValidationError,
    OperationError,
)

# -------------------------------------------------------------------
#           FIXTURES
# -------------------------------------------------------------------


@pytest.fixture(name="unique_image_id")
def _unique_image_id() -> str:
    """Generate a unique image ID for each test to avoid conflicts."""
    return str(uuid4())


def _make_line(
    image_id: str,
    line_id: int = 1,
    *,
    geometry: tuple[float, float, float, float] | None = None,
    angle_degrees: float | None = None,
    **overrides: Any,
) -> Line:
    """Build a valid line while keeping test-specific fields explicit."""
    values: dict[str, Any] = {
        "image_id": image_id,
        "line_id": line_id,
        "text": f"Line {line_id}",
        "bounding_box": {"x": 0, "y": 0, "width": 1, "height": 0.1},
        "top_left": {"x": 0, "y": 0},
        "top_right": {"x": 1, "y": 0},
        "bottom_left": {"x": 0, "y": 0.1},
        "bottom_right": {"x": 1, "y": 0.1},
        "angle_degrees": 0,
        "angle_radians": 0,
        "confidence": 0.95,
    }
    if geometry is not None:
        x, y, width, height = geometry
        values.update(
            {
                "bounding_box": {
                    "x": x,
                    "y": y,
                    "width": width,
                    "height": height,
                },
                "top_left": {"x": x, "y": y},
                "top_right": {"x": x + width, "y": y},
                "bottom_left": {"x": x, "y": y + height},
                "bottom_right": {"x": x + width, "y": y + height},
            }
        )
    if angle_degrees is not None:
        values["angle_degrees"] = angle_degrees
        values["angle_radians"] = radians(angle_degrees)
    values.update(overrides)
    return Line(**values)


@pytest.fixture(name="sample_line")
def _sample_line(unique_image_id: str) -> Line:
    """Returns a valid Line object with unique data."""
    return _make_line(
        unique_image_id,
        text="test_string",
        bounding_box={
            "x": 0.4454263367632384,
            "height": 0.022867568134581906,
            "width": 0.08690182470506236,
            "y": 0.9167082878750482,
        },
        top_right={"y": 0.9307722198001792, "x": 0.5323281614683008},
        top_left={"y": 0.9395758560096301, "x": 0.44837726658954413},
        bottom_right={"x": 0.529377231641995, "y": 0.9167082878750482},
        bottom_left={"x": 0.4454263367632384, "y": 0.9255119240844992},
        angle_degrees=-5.986527,
        angle_radians=-0.10448461,
        confidence=1,
    )


# -------------------------------------------------------------------
#           ERROR SCENARIO PARAMETERS
# -------------------------------------------------------------------

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

UPDATE_BATCH_ERROR_SCENARIOS = ERROR_SCENARIOS + [
    (
        "ConditionalCheckFailedException",
        EntityNotFoundError,
        "Cannot update lines",
    ),
]

DELETE_BATCH_ERROR_SCENARIOS = ERROR_SCENARIOS + [
    (
        "ConditionalCheckFailedException",
        EntityNotFoundError,
        "line not found during delete_lines",
    ),
]

# Fixed UUIDs for test consistency
FIXED_UUIDS = [
    "550e8400-e29b-41d4-a716-446655440001",
    "550e8400-e29b-41d4-a716-446655440002",
    "550e8400-e29b-41d4-a716-446655440003",
]


# -------------------------------------------------------------------
#           PARAMETERIZED CLIENT ERROR TESTS
# -------------------------------------------------------------------


LINE_CLIENT_ERROR_CASES = (
    [
        ("add_line", "put_item", "PutItem", False, "entity", *case)
        for case in ERROR_SCENARIOS
    ]
    + [
        ("update_line", "put_item", "PutItem", True, "entity", *case)
        for case in ERROR_SCENARIOS
    ]
    + [
        ("delete_line", "delete_item", "DeleteItem", True, "key", *case)
        for case in ERROR_SCENARIOS
    ]
    + [
        ("get_line", "get_item", "GetItem", False, "key", *case)
        for case in ERROR_SCENARIOS
    ]
    + [
        (
            method_name,
            "transact_write_items",
            "TransactWriteItems",
            False,
            "batch",
            *case,
        )
        for method_name, scenarios in (
            ("add_lines", ERROR_SCENARIOS),
            ("update_lines", UPDATE_BATCH_ERROR_SCENARIOS),
            ("delete_lines", DELETE_BATCH_ERROR_SCENARIOS),
        )
        for case in scenarios
    ]
)


def _line_args(sample_line: Line, arg_source: str) -> tuple[Any, ...]:
    """Return DynamoClient method args for a line case."""
    return {
        "entity": (sample_line,),
        "key": (sample_line.image_id, sample_line.line_id),
        "batch": ([sample_line],),
    }[arg_source]


@pytest.mark.integration
@pytest.mark.parametrize(
    "method_name,api_method,operation_name,pre_add,arg_source,"
    "error_code,expected_exception,error_match",
    LINE_CLIENT_ERROR_CASES,
)
# pylint: disable=too-many-arguments,protected-access
def test_line_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    sample_line: Line,
    mocker: MockerFixture,
    method_name: str,
    api_method: str,
    operation_name: str,
    pre_add: bool,
    arg_source: str,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:
    """Tests error handling for line operations."""
    client = DynamoClient(dynamodb_table)
    if pre_add:
        client.add_line(sample_line)

    mock_api = mocker.patch.object(
        client._client,
        api_method,
        side_effect=ClientError(
            {"Error": {"Code": error_code}}, operation_name
        ),
    )

    with pytest.raises(expected_exception, match=error_match):
        getattr(client, method_name)(*_line_args(sample_line, arg_source))

    mock_api.assert_called_once()


# -------------------------------------------------------------------
#           PARAMETERIZED VALIDATION TESTS
# -------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize(
    "method_name,invalid_input,error_match",
    [
        ("add_line", None, "line cannot be None"),
        (
            "add_line",
            "not-a-line",
            "line must be an instance of Line",
        ),
        ("update_line", None, "line cannot be None"),
        (
            "update_line",
            "not-a-line",
            "line must be an instance of Line",
        ),
    ],
)
def test_single_line_validation(
    dynamodb_table: Literal["MyMockedTable"],
    method_name: str,
    invalid_input: Any,
    error_match: str,
) -> None:
    """Tests validation for single line operations."""
    client = DynamoClient(dynamodb_table)
    method = getattr(client, method_name)

    with pytest.raises(OperationError, match=error_match):
        method(invalid_input)


@pytest.mark.integration
@pytest.mark.parametrize(
    "method_name,invalid_input,error_match",
    [
        ("add_lines", None, "lines cannot be None"),
        ("add_lines", "not-a-list", "lines must be a list"),
        ("update_lines", None, "lines cannot be None"),
        ("update_lines", "not-a-list", "lines must be a list"),
        ("delete_lines", None, "lines cannot be None"),
        ("delete_lines", "not-a-list", "lines must be a list"),
    ],
)
def test_batch_line_validation_basic(
    dynamodb_table: Literal["MyMockedTable"],
    method_name: str,
    invalid_input: Any,
    error_match: str,
) -> None:
    """Tests basic validation for batch line operations."""
    client = DynamoClient(dynamodb_table)
    method = getattr(client, method_name)

    with pytest.raises(OperationError, match=error_match):
        method(invalid_input)


@pytest.mark.integration
@pytest.mark.parametrize(
    "method_name",
    [
        "add_lines",
        "update_lines",
        "delete_lines",
    ],
)
# pylint: disable=redefined-outer-name
def test_batch_line_validation_mixed_types(
    dynamodb_table: Literal["MyMockedTable"],
    sample_line: Line,
    method_name: str,
) -> None:
    """Tests validation for batch operations with mixed types."""
    client = DynamoClient(dynamodb_table)
    method = getattr(client, method_name)

    mixed_list = [sample_line, "not-a-line", 123]

    with pytest.raises(
        (EntityValidationError, OperationError), match=r"lines.*must be.*Line"
    ):
        method(mixed_list)


@pytest.mark.integration
@pytest.mark.parametrize(
    "image_id,line_id,expected_error,error_match",
    [
        (None, 1, EntityValidationError, "image_id cannot be None"),
        ("not-a-uuid", 1, OperationError, "uuid must be a valid UUIDv4"),
        (
            FIXED_UUIDS[0],
            None,
            OperationError,
            "unsupported format string",
        ),
        (
            FIXED_UUIDS[0],
            "not-an-int",
            OperationError,
            "Unknown format code",
        ),
        (
            FIXED_UUIDS[0],
            -1,
            EntityNotFoundError,
            "not found",
        ),
    ],
)
# pylint: disable=too-many-arguments
def test_get_line_parameter_validation(
    dynamodb_table: Literal["MyMockedTable"],
    image_id: Any,
    line_id: Any,
    expected_error: Type[Exception],
    error_match: str,
) -> None:
    """Tests parameter validation for get_line."""
    client = DynamoClient(dynamodb_table)

    with pytest.raises(expected_error, match=error_match):
        client.get_line(image_id, line_id)


@pytest.mark.integration
@pytest.mark.parametrize(
    "image_id,line_id,expected_error,error_match",
    [
        (None, 1, EntityValidationError, "image_id cannot be None"),
        ("not-a-uuid", 1, OperationError, "uuid must be a valid UUIDv4"),
        (
            FIXED_UUIDS[0],
            None,
            OperationError,
            "line_id must be an integer",
        ),
        (
            FIXED_UUIDS[0],
            "not-an-int",
            OperationError,
            "line_id must be an integer",
        ),
        (
            FIXED_UUIDS[0],
            -1,
            OperationError,
            "line_id must be positive",
        ),
    ],
)
# pylint: disable=too-many-arguments
def test_delete_line_parameter_validation(
    dynamodb_table: Literal["MyMockedTable"],
    image_id: Any,
    line_id: Any,
    expected_error: Type[Exception],
    error_match: str,
) -> None:
    """Tests parameter validation for delete_line."""
    client = DynamoClient(dynamodb_table)

    with pytest.raises(expected_error, match=error_match):
        client.delete_line(image_id, line_id)


# -------------------------------------------------------------------
#           LIST OPERATIONS VALIDATION TESTS
# -------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize(
    "limit,error_match",
    [
        ("not-an-int", "unsupported operand type"),
        (-1, "Invalid value for parameter Limit"),  # -1 is invalid
        (0, None),  # 0 is valid for line operations
    ],
)
def test_list_lines_invalid_limit(
    dynamodb_table: Literal["MyMockedTable"],
    limit: Any,
    error_match: str,
) -> None:
    """Tests list_lines with invalid limit parameter."""
    client = DynamoClient(dynamodb_table)

    if error_match:
        with pytest.raises(OperationError, match=error_match):
            client.list_lines(limit=limit)
    else:
        # Should not raise for limit=0 or -1
        lines, _ = client.list_lines(limit=limit)
        assert isinstance(lines, list)


@pytest.mark.integration
def test_list_lines_invalid_last_evaluated_key(
    dynamodb_table: Literal["MyMockedTable"],
) -> None:
    """Tests list_lines with invalid last_evaluated_key."""
    client = DynamoClient(dynamodb_table)

    with pytest.raises(OperationError, match="Parameter validation failed"):
        client.list_lines(last_evaluated_key="not-a-dict")


@pytest.mark.integration
@pytest.mark.parametrize(
    "image_id,should_pass",
    [
        (None, True),  # None is actually allowed
        ("not-a-uuid", True),  # Non-UUID is also allowed
    ],
)
def test_list_lines_from_image_validation(
    dynamodb_table: Literal["MyMockedTable"],
    image_id: Any,
    should_pass: bool,
) -> None:
    """Tests validation for list_lines_from_image."""
    client = DynamoClient(dynamodb_table)

    if should_pass:
        # Should not raise, just return empty list
        lines = client.list_lines_from_image(image_id)
        assert lines == []


# -------------------------------------------------------------------
#           CONDITIONAL CHECK FAILED TESTS
# -------------------------------------------------------------------


@pytest.mark.integration
# pylint: disable=redefined-outer-name
def test_add_line_conditional_check_failed(
    dynamodb_table: Literal["MyMockedTable"],
    sample_line: Line,
    mocker: MockerFixture,
) -> None:
    """Tests ConditionalCheckFailedException handling for add."""
    client = DynamoClient(dynamodb_table)

    # pylint: disable=protected-access
    mock_put = mocker.patch.object(
        client._client,
        "put_item",
        side_effect=ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem"
        ),
    )

    with pytest.raises(EntityAlreadyExistsError, match="already exists"):
        client.add_line(sample_line)

    mock_put.assert_called_once()


@pytest.mark.integration
# pylint: disable=redefined-outer-name
def test_update_line_conditional_check_failed(
    dynamodb_table: Literal["MyMockedTable"],
    sample_line: Line,
    mocker: MockerFixture,
) -> None:
    """Tests ConditionalCheckFailedException handling for update."""
    client = DynamoClient(dynamodb_table)

    # pylint: disable=protected-access
    mock_put = mocker.patch.object(
        client._client,
        "put_item",
        side_effect=ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem"
        ),
    )

    with pytest.raises(
        EntityNotFoundError, match="(does not exist|not found)"
    ):
        client.update_line(sample_line)

    mock_put.assert_called_once()


@pytest.mark.integration
# pylint: disable=redefined-outer-name
def test_delete_line_conditional_check_failed(
    dynamodb_table: Literal["MyMockedTable"],
    sample_line: Line,
    mocker: MockerFixture,
) -> None:
    """Tests ConditionalCheckFailedException handling for delete."""
    client = DynamoClient(dynamodb_table)

    # pylint: disable=protected-access
    mock_delete = mocker.patch.object(
        client._client,
        "delete_item",
        side_effect=ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException"}},
            "DeleteItem",
        ),
    )

    with pytest.raises(
        EntityNotFoundError, match="(does not exist|not found)"
    ):
        client.delete_line(sample_line.image_id, sample_line.line_id)

    mock_delete.assert_called_once()


# -------------------------------------------------------------------
#           SUCCESS PATH TESTS
# -------------------------------------------------------------------


@pytest.mark.integration
# pylint: disable=redefined-outer-name
def test_add_line_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_line: Line,
) -> None:
    """Tests successful add_line operation."""
    client = DynamoClient(dynamodb_table)

    # Act
    client.add_line(sample_line)

    # Assert
    retrieved = client.get_line(sample_line.image_id, sample_line.line_id)
    assert retrieved == sample_line


@pytest.mark.integration
# pylint: disable=redefined-outer-name
def test_add_line_duplicate_raises(
    dynamodb_table: Literal["MyMockedTable"],
    sample_line: Line,
) -> None:
    """Tests that adding duplicate line raises error."""
    client = DynamoClient(dynamodb_table)
    client.add_line(sample_line)

    with pytest.raises(EntityAlreadyExistsError, match="already exists"):
        client.add_line(sample_line)


@pytest.mark.integration
# pylint: disable=redefined-outer-name
def test_update_line_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_line: Line,
) -> None:
    """Tests successful update_line operation."""
    client = DynamoClient(dynamodb_table)
    client.add_line(sample_line)

    # Modify some fields
    sample_line.text = "Updated line text"
    sample_line.confidence = 0.99

    # Update
    client.update_line(sample_line)

    # Verify
    retrieved = client.get_line(sample_line.image_id, sample_line.line_id)
    assert retrieved.text == "Updated line text"
    assert retrieved.confidence == 0.99


@pytest.mark.integration
# pylint: disable=redefined-outer-name
def test_update_line_not_found(
    dynamodb_table: Literal["MyMockedTable"],
    sample_line: Line,
) -> None:
    """Tests update_line when line doesn't exist."""
    client = DynamoClient(dynamodb_table)

    with pytest.raises(
        EntityNotFoundError, match="(does not exist|not found)"
    ):
        client.update_line(sample_line)


@pytest.mark.integration
# pylint: disable=redefined-outer-name
def test_delete_line_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_line: Line,
) -> None:
    """Tests successful delete_line operation."""
    client = DynamoClient(dynamodb_table)
    client.add_line(sample_line)

    # Delete
    client.delete_line(sample_line.image_id, sample_line.line_id)

    # Verify - Line's get_line raises EntityNotFoundError when not found
    with pytest.raises(
        EntityNotFoundError, match="(does not exist|not found)"
    ):
        client.get_line(sample_line.image_id, sample_line.line_id)


@pytest.mark.integration
# pylint: disable=redefined-outer-name
def test_delete_line_not_found(
    dynamodb_table: Literal["MyMockedTable"],
    unique_image_id: str,
) -> None:
    """Tests delete_line when line doesn't exist."""
    client = DynamoClient(dynamodb_table)

    # Line's delete_line raises EntityNotFoundError for non-existent items
    with pytest.raises(
        EntityNotFoundError, match="(does not exist|not found)"
    ):
        client.delete_line(unique_image_id, 999)


@pytest.mark.integration
# pylint: disable=redefined-outer-name
def test_get_line_not_found(
    dynamodb_table: Literal["MyMockedTable"],
    unique_image_id: str,
) -> None:
    """Tests get_line when line doesn't exist."""
    client = DynamoClient(dynamodb_table)

    # Line's get_line raises EntityNotFoundError when not found
    with pytest.raises(
        EntityNotFoundError, match="(does not exist|not found)"
    ):
        client.get_line(unique_image_id, 999)


# -------------------------------------------------------------------
#           BATCH OPERATIONS SUCCESS TESTS
# -------------------------------------------------------------------


@pytest.mark.integration
# pylint: disable=redefined-outer-name
def test_add_lines_success(
    dynamodb_table: Literal["MyMockedTable"],
    unique_image_id: str,
) -> None:
    """Tests successful batch add operation."""
    client = DynamoClient(dynamodb_table)

    lines = [
        _make_line(
            unique_image_id,
            i,
            text=f"Line {i}",
            confidence=0.95 + i * 0.01,
        )
        for i in range(1, 4)
    ]

    client.add_lines(lines)

    # Verify all were added
    for line in lines:
        retrieved = client.get_line(line.image_id, line.line_id)
        assert retrieved == line


@pytest.mark.integration
# pylint: disable=redefined-outer-name
def test_add_lines_empty_list(
    dynamodb_table: Literal["MyMockedTable"],
) -> None:
    """Tests add_lines with empty list."""
    client = DynamoClient(dynamodb_table)

    # Empty input is a no-op and must not call DynamoDB.
    client.add_lines([])


@pytest.mark.integration
# pylint: disable=redefined-outer-name
def test_update_lines_success(
    dynamodb_table: Literal["MyMockedTable"],
    unique_image_id: str,
) -> None:
    """Tests successful batch update operation."""
    client = DynamoClient(dynamodb_table)

    # First add lines
    lines = [
        _make_line(
            unique_image_id,
            i,
            text=f"Original line {i}",
        )
        for i in range(1, 4)
    ]
    client.add_lines(lines)

    # Update them
    for line in lines:
        line.text = f"Updated line {line.line_id}"
        line.confidence = 0.99

    client.update_lines(lines)

    # Verify updates
    for line in lines:
        retrieved = client.get_line(line.image_id, line.line_id)
        assert retrieved.text == f"Updated line {line.line_id}"
        assert retrieved.confidence == 0.99


@pytest.mark.integration
# pylint: disable=redefined-outer-name
def test_delete_lines_success(
    dynamodb_table: Literal["MyMockedTable"],
    unique_image_id: str,
) -> None:
    """Tests successful batch delete operation."""
    client = DynamoClient(dynamodb_table)

    # First add lines
    lines = [
        _make_line(
            unique_image_id,
            i,
            text=f"Line to delete {i}",
        )
        for i in range(1, 4)
    ]
    client.add_lines(lines)

    # Delete them
    client.delete_lines(lines)

    # Verify deletion
    for line in lines:
        with pytest.raises(
            EntityNotFoundError, match="(does not exist|not found)"
        ):
            client.get_line(line.image_id, line.line_id)


# -------------------------------------------------------------------
#           LIST OPERATIONS TESTS
# -------------------------------------------------------------------


@pytest.mark.integration
# pylint: disable=redefined-outer-name
def test_list_lines_with_pagination(
    dynamodb_table: Literal["MyMockedTable"],
    unique_image_id: str,
) -> None:
    """Tests list_lines with pagination."""
    client = DynamoClient(dynamodb_table)

    # Add multiple lines
    for i in range(1, 6):
        line = _make_line(
            unique_image_id,
            i,
            text=f"Line {i}",
        )
        client.add_line(line)

    # List with limit
    first_page, last_key = client.list_lines(limit=3)
    assert len(first_page) == 3
    assert last_key is not None

    # Get next page
    second_page, last_key = client.list_lines(
        limit=3, last_evaluated_key=last_key
    )
    assert len(second_page) >= 2

    # Ensure no duplicates between pages
    first_ids = {(l.image_id, l.line_id) for l in first_page}
    second_ids = {(l.image_id, l.line_id) for l in second_page}
    assert first_ids.isdisjoint(second_ids)


@pytest.mark.integration
def test_list_lines_empty(
    dynamodb_table: Literal["MyMockedTable"],
) -> None:
    """Tests list_lines when no lines exist."""
    client = DynamoClient(dynamodb_table)

    lines, last_key = client.list_lines()

    assert len(lines) == 0
    assert last_key is None


@pytest.mark.integration
# pylint: disable=redefined-outer-name
def test_list_lines_from_image_success(
    dynamodb_table: Literal["MyMockedTable"],
    unique_image_id: str,
) -> None:
    """Tests list_lines_from_image filtering."""
    client = DynamoClient(dynamodb_table)

    # Add lines for specific image
    for i in range(1, 3):
        line = _make_line(
            unique_image_id,
            i,
            text=f"Image Line {i}",
        )
        client.add_line(line)

    # Add line for different image
    other_image_id = str(uuid4())
    other_line = _make_line(
        other_image_id,
        10,
        text="Other Image Line",
        confidence=0.99,
    )
    client.add_line(other_line)

    # Query for specific image only
    image_lines = client.list_lines_from_image(unique_image_id)

    # Verify filtering
    assert len(image_lines) == 2
    assert all(l.image_id == unique_image_id for l in image_lines)
    assert other_line not in image_lines


# -------------------------------------------------------------------
#           SPECIAL CASES
# -------------------------------------------------------------------


@pytest.mark.integration
# pylint: disable=redefined-outer-name
def test_line_with_unicode_text(
    dynamodb_table: Literal["MyMockedTable"],
    unique_image_id: str,
) -> None:
    """Tests line with unicode characters in text."""
    client = DynamoClient(dynamodb_table)
    unicode_line = _make_line(
        unique_image_id,
        text="测试文本 Test émojis: 🎉🚀",
    )

    client.add_line(unicode_line)
    result = client.get_line(unicode_line.image_id, unicode_line.line_id)
    assert result == unicode_line


@pytest.mark.integration
# pylint: disable=redefined-outer-name
def test_line_with_extreme_coordinates(
    dynamodb_table: Literal["MyMockedTable"],
    unique_image_id: str,
) -> None:
    """Tests line with extreme coordinate values."""
    client = DynamoClient(dynamodb_table)
    extreme_line = _make_line(
        unique_image_id,
        text="Extreme coordinates",
        bounding_box={
            "x": 0.00001,
            "y": 0.99999,
            "width": 0.00001,
            "height": 0.00001,
        },
        top_left={"x": 0.00001, "y": 0.99999},
        top_right={"x": 0.00002, "y": 0.99999},
        bottom_left={"x": 0.00001, "y": 1.0},
        bottom_right={"x": 0.00002, "y": 1.0},
        angle_degrees=359.999,
        angle_radians=6.28318,
        confidence=0.95,  # Changed from 0.00001 to valid range
    )

    client.add_line(extreme_line)
    result = client.get_line(extreme_line.image_id, extreme_line.line_id)
    assert result == extreme_line


@pytest.mark.integration
# pylint: disable=redefined-outer-name
def test_large_batch_operations(
    dynamodb_table: Literal["MyMockedTable"],
    unique_image_id: str,
) -> None:
    """Tests batch operations with maximum batch size."""
    client = DynamoClient(dynamodb_table)

    # Create 100 lines (DynamoDB batch limit is 25, so this tests chunking)
    lines = [
        _make_line(
            unique_image_id,
            i,
            text=f"Batch line {i}",
            geometry=(0, i * 0.01, 1, 0.01),
            angle_degrees=i % 360,
            confidence=0.9 + (i % 10) * 0.01,
        )
        for i in range(1, 101)
    ]

    # Add in batch
    client.add_lines(lines)

    # Verify a sample
    for i in [1, 25, 50, 75, 100]:
        result = client.get_line(unique_image_id, i)
        expected = lines[i - 1]
        assert result.text == expected.text
        assert result.bounding_box == expected.bounding_box
        assert result.top_left == expected.top_left
        assert result.bottom_right == expected.bottom_right
        assert result.angle_degrees == expected.angle_degrees
        assert result.angle_radians == expected.angle_radians

    # Update all
    for line in lines:
        line.text = f"Updated batch line {line.line_id}"
    client.update_lines(lines)

    # Verify updates
    result = client.get_line(unique_image_id, 50)
    assert result.text == "Updated batch line 50"

    # Delete all
    client.delete_lines(lines)

    # Verify deletion
    with pytest.raises(
        EntityNotFoundError, match="(does not exist|not found)"
    ):
        client.get_line(unique_image_id, 50)
