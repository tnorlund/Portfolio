from typing import Literal

import pytest

from receipt_dynamo import DynamoClient, ReceiptLine
from receipt_dynamo.data.shared_exceptions import EntityAlreadyExistsError


@pytest.fixture
def sample_receipt_line():
    """Returns a valid ReceiptLine object with placeholder data."""
    return ReceiptLine(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=10,
        text="Sample receipt line",
        bounding_box={"x": 0.1, "y": 0.2, "width": 0.4, "height": 0.05},
        top_left={"x": 0.1, "y": 0.2},
        top_right={"x": 0.5, "y": 0.2},
        bottom_left={"x": 0.1, "y": 0.25},
        bottom_right={"x": 0.5, "y": 0.25},
        angle_degrees=5.0,
        angle_radians=0.0872665,
        confidence=0.98,
    )


@pytest.mark.integration
def test_add_receipt_line(
    dynamodb_table: Literal["MyMockedTable"], sample_receipt_line: ReceiptLine
):
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Act
    client.add_receipt_line(sample_receipt_line)

    # Assert
    retrieved_line = client.get_receipt_line(
        sample_receipt_line.receipt_id,
        sample_receipt_line.image_id,
        sample_receipt_line.line_id,
    )
    assert retrieved_line == sample_receipt_line


@pytest.mark.integration
def test_add_receipt_line_duplicate_raises(
    dynamodb_table: Literal["MyMockedTable"], sample_receipt_line: ReceiptLine
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    client.add_receipt_line(sample_receipt_line)

    # Act & Assert
    with pytest.raises(EntityAlreadyExistsError, match="already exists"):
        client.add_receipt_line(sample_receipt_line)


@pytest.mark.integration
def test_update_receipt_line(
    dynamodb_table: Literal["MyMockedTable"], sample_receipt_line: ReceiptLine
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    client.add_receipt_line(sample_receipt_line)

    # Modify some field
    updated_text = "Updated line text"
    sample_receipt_line.text = updated_text

    # Act
    client.update_receipt_line(sample_receipt_line)

    # Assert
    retrieved_line = client.get_receipt_line(
        sample_receipt_line.receipt_id,
        sample_receipt_line.image_id,
        sample_receipt_line.line_id,
    )
    assert retrieved_line.text == updated_text


@pytest.mark.integration
def test_delete_receipt_line(
    dynamodb_table: Literal["MyMockedTable"], sample_receipt_line: ReceiptLine
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    client.add_receipt_line(sample_receipt_line)

    # Act
    client.delete_receipt_line(
        sample_receipt_line.receipt_id,
        sample_receipt_line.image_id,
        sample_receipt_line.line_id,
    )

    # Assert
    with pytest.raises(ValueError, match="not found"):
        client.get_receipt_line(
            sample_receipt_line.receipt_id,
            sample_receipt_line.image_id,
            sample_receipt_line.line_id,
        )


@pytest.mark.integration
def test_receipt_line_list(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    lines = [
        ReceiptLine(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=i,
            text=f"Line {i}",
            bounding_box={"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0},
            top_left={"x": 0, "y": 0},
            top_right={"x": 1, "y": 0},
            bottom_left={"x": 0, "y": 1},
            bottom_right={"x": 1, "y": 1},
            angle_degrees=0,
            angle_radians=0,
            confidence=1.0,
        )
        for i in range(1, 4)
    ]
    for ln in lines:
        client.add_receipt_line(ln)

    # Act
    returned_lines, _ = client.list_receipt_lines()

    # Assert
    # Might return lines for multiple receipts/images if your table is reused,
    # so filter by ID or compare lengths if you only have these lines in the
    # table
    for ln in lines:
        assert ln in returned_lines


@pytest.mark.integration
def test_receipt_line_list_from_receipt(
    dynamodb_table: Literal["MyMockedTable"],
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    # Lines for receipt_id=1, image_id=1
    lines_same_receipt = [
        ReceiptLine(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=i,
            text=f"Line {i}",
            bounding_box={"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0},
            top_left={"x": 0, "y": 0},
            top_right={"x": 1, "y": 0},
            bottom_left={"x": 0, "y": 1},
            bottom_right={"x": 1, "y": 1},
            angle_degrees=0,
            angle_radians=0,
            confidence=1.0,
        )
        for i in range(1, 3)
    ]
    # A line for a different receipt
    another_line = ReceiptLine(
        receipt_id=2,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=10,
        text="Different",
        bounding_box={"x": 0.2, "y": 0.2, "width": 0.1, "height": 0.1},
        top_left={"x": 0.2, "y": 0.2},
        top_right={"x": 0.3, "y": 0.2},
        bottom_left={"x": 0.2, "y": 0.3},
        bottom_right={"x": 0.3, "y": 0.3},
        angle_degrees=10,
        angle_radians=0.17453,
        confidence=0.99,
    )
    for ln in lines_same_receipt + [another_line]:
        client.add_receipt_line(ln)

    # Act
    found_lines = client.list_receipt_lines_from_receipt(
        receipt_id=1, image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    )

    # Assert
    assert len(found_lines) == 2
    for ln in lines_same_receipt:
        assert ln in found_lines
    assert another_line not in found_lines
