# pylint: disable=redefined-outer-name
# pylint: disable=unused-argument
# pylint: disable=too-many-lines
"""Tests for ReceiptValidationResult entity."""

from copy import deepcopy
from datetime import datetime

import pytest

from receipt_dynamo import (
    ReceiptValidationResult,
    item_to_receipt_validation_result,
)


@pytest.fixture
def example_validation_result():
    """Create a sample ReceiptValidationResult for testing"""
    return ReceiptValidationResult(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        field_name="total_amount",
        result_index=0,
        type="error",
        message="Total amount does not match sum of items",
        reasoning=(
            "The total ($45.99) does not equal the sum of line items ($42.99)"
        ),
        field="price",
        expected_value="42.99",
        actual_value="45.99",
        validation_timestamp="2023-05-15T10:30:00",
        metadata={
            "source_info": {"model": "validation-v1"},
            "confidence": 0.92,
        },
    )


@pytest.mark.unit
def test_validation_result_init_valid(example_validation_result):
    """Test initialization with valid parameters"""
    assert example_validation_result.receipt_id == 1
    assert (
        example_validation_result.image_id
        == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    )
    assert example_validation_result.field_name == "total_amount"
    assert example_validation_result.result_index == 0
    assert example_validation_result.type == "error"
    assert (
        example_validation_result.message
        == "Total amount does not match sum of items"
    )
    assert (
        example_validation_result.reasoning
        == "The total ($45.99) does not equal the sum of line items ($42.99)"
    )
    assert example_validation_result.field == "price"
    assert example_validation_result.expected_value == "42.99"
    assert example_validation_result.actual_value == "45.99"
    assert (
        example_validation_result.validation_timestamp == "2023-05-15T10:30:00"
    )
    assert (
        example_validation_result.metadata["source_info"]["model"]
        == "validation-v1"
    )
    assert example_validation_result.metadata["confidence"] == 0.92


@pytest.mark.unit
def test_validation_result_init_minimal():
    """Test initialization with minimal required parameters"""
    result = ReceiptValidationResult(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        field_name="total_amount",
        result_index=0,
        type="warning",
        message="Possible discrepancy detected",
        reasoning="The total amount seems higher than expected",
        validation_timestamp="2023-05-15T10:30:00",
    )

    assert result.receipt_id == 1
    assert result.image_id == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert result.field_name == "total_amount"
    assert result.result_index == 0
    assert result.type == "warning"
    assert result.message == "Possible discrepancy detected"
    assert result.reasoning == "The total amount seems higher than expected"
    assert result.field is None
    assert result.expected_value is None
    assert result.actual_value is None
    assert result.validation_timestamp == "2023-05-15T10:30:00"
    assert isinstance(result.metadata, dict)
    assert len(result.metadata) == 0


@pytest.mark.unit
def test_validation_result_init_datetime_timestamp():
    """Test initialization with a datetime object for validation_timestamp"""
    timestamp = datetime(2023, 5, 15, 10, 30, 0)
    result = ReceiptValidationResult(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        field_name="total_amount",
        result_index=0,
        type="warning",
        message="Possible discrepancy detected",
        reasoning="The total amount seems higher than expected",
        validation_timestamp=timestamp,
    )

    assert result.validation_timestamp == "2023-05-15T10:30:00"


@pytest.mark.unit
def test_validation_result_init_invalid_receipt_id():
    """Test initialization with invalid receipt_id"""
    with pytest.raises(ValueError, match="receipt_id must be an integer"):
        ReceiptValidationResult(
            receipt_id="1",
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            field_name="total_amount",
            result_index=0,
            type="error",
            message="Total amount does not match",
            reasoning="The total does not equal the sum",
            validation_timestamp="2023-05-15T10:30:00",
        )

    with pytest.raises(ValueError, match="receipt_id must be positive"):
        ReceiptValidationResult(
            receipt_id=-1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            field_name="total_amount",
            result_index=0,
            type="error",
            message="Total amount does not match",
            reasoning="The total does not equal the sum",
            validation_timestamp="2023-05-15T10:30:00",
        )


@pytest.mark.unit
def test_validation_result_init_invalid_uuid():
    """Test initialization with invalid image_id (UUID)"""
    with pytest.raises(ValueError, match="uuid must be a string"):
        ReceiptValidationResult(
            receipt_id=1,
            image_id=123,
            field_name="total_amount",
            result_index=0,
            type="error",
            message="Total amount does not match",
            reasoning="The total does not equal the sum",
            validation_timestamp="2023-05-15T10:30:00",
        )

    with pytest.raises(ValueError, match="uuid must be a valid UUID"):
        ReceiptValidationResult(
            receipt_id=1,
            image_id="not-a-valid-uuid",
            field_name="total_amount",
            result_index=0,
            type="error",
            message="Total amount does not match",
            reasoning="The total does not equal the sum",
            validation_timestamp="2023-05-15T10:30:00",
        )


@pytest.mark.unit
def test_validation_result_init_invalid_field_name():
    """Test initialization with invalid field_name"""
    with pytest.raises(ValueError, match="field_name must be a string"):
        ReceiptValidationResult(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            field_name=123,
            result_index=0,
            type="error",
            message="Total amount does not match",
            reasoning="The total does not equal the sum",
            validation_timestamp="2023-05-15T10:30:00",
        )

    with pytest.raises(ValueError, match="field_name must not be empty"):
        ReceiptValidationResult(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            field_name="",
            result_index=0,
            type="error",
            message="Total amount does not match",
            reasoning="The total does not equal the sum",
            validation_timestamp="2023-05-15T10:30:00",
        )


@pytest.mark.unit
def test_validation_result_init_invalid_result_index():
    """Test initialization with invalid result_index"""
    with pytest.raises(ValueError, match="result_index must be an integer"):
        ReceiptValidationResult(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            field_name="total_amount",
            result_index="0",
            type="error",
            message="Total amount does not match",
            reasoning="The total does not equal the sum",
            validation_timestamp="2023-05-15T10:30:00",
        )

    with pytest.raises(ValueError, match="result_index must be positive"):
        ReceiptValidationResult(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            field_name="total_amount",
            result_index=-1,
            type="error",
            message="Total amount does not match",
            reasoning="The total does not equal the sum",
            validation_timestamp="2023-05-15T10:30:00",
        )


@pytest.mark.unit
def test_validation_result_init_invalid_type():
    """Test initialization with invalid type"""
    with pytest.raises(ValueError, match="type must be a string"):
        ReceiptValidationResult(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            field_name="total_amount",
            result_index=0,
            type=123,
            message="Total amount does not match",
            reasoning="The total does not equal the sum",
            validation_timestamp="2023-05-15T10:30:00",
        )

    with pytest.raises(ValueError, match="type must not be empty"):
        ReceiptValidationResult(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            field_name="total_amount",
            result_index=0,
            type="",
            message="Total amount does not match",
            reasoning="The total does not equal the sum",
            validation_timestamp="2023-05-15T10:30:00",
        )


@pytest.mark.unit
def test_validation_result_init_invalid_message():
    """Test initialization with invalid message"""
    with pytest.raises(ValueError, match="message must be a string"):
        ReceiptValidationResult(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            field_name="total_amount",
            result_index=0,
            type="error",
            message=123,
            reasoning="The total does not equal the sum",
            validation_timestamp="2023-05-15T10:30:00",
        )

    with pytest.raises(ValueError, match="message must not be empty"):
        ReceiptValidationResult(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            field_name="total_amount",
            result_index=0,
            type="error",
            message="",
            reasoning="The total does not equal the sum",
            validation_timestamp="2023-05-15T10:30:00",
        )


@pytest.mark.unit
def test_validation_result_init_invalid_reasoning():
    """Test initialization with invalid reasoning"""
    with pytest.raises(ValueError, match="reasoning must be a string"):
        ReceiptValidationResult(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            field_name="total_amount",
            result_index=0,
            type="error",
            message="Total amount does not match",
            reasoning=123,
            validation_timestamp="2023-05-15T10:30:00",
        )

    with pytest.raises(ValueError, match="reasoning must not be empty"):
        ReceiptValidationResult(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            field_name="total_amount",
            result_index=0,
            type="error",
            message="Total amount does not match",
            reasoning="",
            validation_timestamp="2023-05-15T10:30:00",
        )


@pytest.mark.unit
def test_validation_result_init_invalid_field():
    """Test initialization with invalid field"""
    with pytest.raises(ValueError, match="field must be a string or None"):
        ReceiptValidationResult(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            field_name="total_amount",
            result_index=0,
            type="error",
            message="Total amount does not match",
            reasoning="The total does not equal the sum",
            field=123,
            validation_timestamp="2023-05-15T10:30:00",
        )


@pytest.mark.unit
def test_validation_result_init_invalid_expected_value():
    """Test initialization with invalid expected_value"""
    with pytest.raises(
        ValueError, match="expected_value must be a string or None"
    ):
        ReceiptValidationResult(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            field_name="total_amount",
            result_index=0,
            type="error",
            message="Total amount does not match",
            reasoning="The total does not equal the sum",
            expected_value=123,
            validation_timestamp="2023-05-15T10:30:00",
        )


@pytest.mark.unit
def test_validation_result_init_invalid_actual_value():
    """Test initialization with invalid actual_value"""
    with pytest.raises(
        ValueError, match="actual_value must be a string or None"
    ):
        ReceiptValidationResult(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            field_name="total_amount",
            result_index=0,
            type="error",
            message="Total amount does not match",
            reasoning="The total does not equal the sum",
            actual_value=123,
            validation_timestamp="2023-05-15T10:30:00",
        )


@pytest.mark.unit
def test_validation_result_init_invalid_validation_timestamp():
    """Test initialization with invalid validation_timestamp"""
    with pytest.raises(
        ValueError,
        match="validation_timestamp must be a datetime, string, or None",
    ):
        ReceiptValidationResult(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            field_name="total_amount",
            result_index=0,
            type="error",
            message="Total amount does not match",
            reasoning="The total does not equal the sum",
            validation_timestamp=123,
        )


@pytest.mark.unit
def test_validation_result_init_invalid_metadata():
    """Test initialization with invalid metadata"""
    with pytest.raises(
        ValueError, match="metadata must be a dictionary or None"
    ):
        ReceiptValidationResult(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            field_name="total_amount",
            result_index=0,
            type="error",
            message="Total amount does not match",
            reasoning="The total does not equal the sum",
            validation_timestamp="2023-05-15T10:30:00",
            metadata="not a dict",
        )


@pytest.mark.unit
def test_key(example_validation_result):
    """Test the key property"""
    assert example_validation_result.key == {
        "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {
            "S": (
                "RECEIPT#00001#ANALYSIS#VALIDATION#CATEGORY#"
                "total_amount#RESULT#0"
            )
        },
    }


@pytest.mark.unit
def test_gsi1_key(example_validation_result):
    """Test the gsi1_key property"""
    assert example_validation_result.gsi1_key == {
        "GSI1PK": {"S": "ANALYSIS_TYPE"},
        "GSI1SK": {
            "S": (
                "VALIDATION#2023-05-15T10:30:00#CATEGORY#"
                "total_amount#RESULT"
            )
        },
    }


@pytest.mark.unit
def test_gsi3_key(example_validation_result):
    """Test the gsi3_key property"""
    assert example_validation_result.gsi3_key == {
        "GSI3PK": {"S": "RESULT_TYPE#error"},
        "GSI3SK": {
            "S": (
                "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3#RECEIPT#00001#"
                "CATEGORY#total_amount"
            )
        },
    }


@pytest.mark.unit
def test_to_item(example_validation_result):
    """Test the to_item method"""
    item = example_validation_result.to_item()

    # Check that the basic keys are present
    assert item["PK"] == {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"}
    assert item["SK"] == {
        "S": (
            "RECEIPT#00001#ANALYSIS#VALIDATION#CATEGORY#"
            "total_amount#RESULT#0"
        )
    }
    assert item["GSI1PK"] == {"S": "ANALYSIS_TYPE"}
    assert item["GSI1SK"] == {
        "S": ("VALIDATION#2023-05-15T10:30:00#CATEGORY#" "total_amount#RESULT")
    }
    assert item["GSI3PK"] == {"S": "RESULT_TYPE#error"}
    assert item["GSI3SK"] == {
        "S": (
            "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3#RECEIPT#00001#"
            "CATEGORY#total_amount"
        )
    }

    # Check that the required fields are present
    assert item["type"] == {"S": "error"}
    assert item["message"] == {"S": "Total amount does not match sum of items"}
    assert item["reasoning"] == {
        "S": (
            "The total ($45.99) does not equal the sum of "
            "line items ($42.99)"
        )
    }
    assert item["validation_timestamp"] == {"S": "2023-05-15T10:30:00"}

    # Check that the optional fields are present
    assert item["field"] == {"S": "price"}
    assert item["expected_value"] == {"S": "42.99"}
    assert item["actual_value"] == {"S": "45.99"}
    assert item["metadata"] == {
        "M": {
            "source_info": {"M": {"model": {"S": "validation-v1"}}},
            "confidence": {"N": "0.92"},
        }
    }


@pytest.mark.unit
def test_to_item_with_minimal_fields():
    """Test the to_item method with minimal fields"""
    result = ReceiptValidationResult(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        field_name="total_amount",
        result_index=0,
        type="warning",
        message="Possible discrepancy detected",
        reasoning="The total amount seems higher than expected",
        validation_timestamp="2023-05-15T10:30:00",
    )

    item = result.to_item()

    # Check that the optional fields are not present
    assert "field" not in item
    assert "expected_value" not in item
    assert "actual_value" not in item
    assert item["metadata"] == {"M": {}}


@pytest.mark.unit
def test_from_item(example_validation_result):
    """Test the from_item method"""
    # Convert to item and back
    item = example_validation_result.to_item()
    result = ReceiptValidationResult.from_item(item)

    # Check that all fields match
    assert result.receipt_id == example_validation_result.receipt_id
    assert result.image_id == example_validation_result.image_id
    assert result.field_name == example_validation_result.field_name
    assert result.result_index == example_validation_result.result_index
    assert result.type == example_validation_result.type
    assert result.message == example_validation_result.message
    assert result.reasoning == example_validation_result.reasoning
    assert result.field == example_validation_result.field
    assert result.expected_value == example_validation_result.expected_value
    assert result.actual_value == example_validation_result.actual_value
    assert (
        result.validation_timestamp
        == example_validation_result.validation_timestamp
    )
    assert result.metadata == example_validation_result.metadata


@pytest.mark.unit
def test_eq(example_validation_result):
    """Test equality comparison"""
    # Same attributes should be equal
    result1 = example_validation_result
    result2 = deepcopy(example_validation_result)
    assert result1 == result2

    # Different receipt_id should not be equal
    result2.receipt_id = 2
    assert result1 != result2

    # Different image_id should not be equal
    result2.receipt_id = 1
    result2.image_id = "4f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert result1 != result2

    # Different field_name should not be equal
    result2.image_id = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    result2.field_name = "subtotal"
    assert result1 != result2

    # Different result_index should not be equal
    result2.field_name = "total_amount"
    result2.result_index = 1
    assert result1 != result2

    # Different type should not be equal
    result2.result_index = 0
    result2.type = "warning"
    assert result1 != result2

    # Non-ReceiptValidationResult should not be equal
    assert result1 != "not a ReceiptValidationResult"


@pytest.mark.unit
def test_repr(example_validation_result):
    """Test string representation"""
    repr_str = repr(example_validation_result)
    assert "ReceiptValidationResult" in repr_str
    assert "receipt_id=1" in repr_str
    assert "image_id=" in repr_str
    assert "3f52804b-2fad-4e00-92c8-b593da3a8ed3" in repr_str
    assert "field_name=total_amount" in repr_str
    assert "result_index=0" in repr_str
    assert "type=error" in repr_str


@pytest.mark.unit
def test_item_to_receipt_validation_result(example_validation_result):
    """Test the item_to_receipt_validation_result function"""
    # Convert to item using to_item
    item = example_validation_result.to_item()

    # Use the conversion function
    result = item_to_receipt_validation_result(item)

    # Check that the result matches the original
    assert result.receipt_id == example_validation_result.receipt_id
    assert result.image_id == example_validation_result.image_id
    assert result.field_name == example_validation_result.field_name
    assert result.result_index == example_validation_result.result_index
    assert result.type == example_validation_result.type
    assert result.message == example_validation_result.message
    assert result.reasoning == example_validation_result.reasoning
    assert result.field == example_validation_result.field
    assert result.expected_value == example_validation_result.expected_value
    assert result.actual_value == example_validation_result.actual_value
    assert (
        result.validation_timestamp
        == example_validation_result.validation_timestamp
    )
    assert result.metadata == example_validation_result.metadata
