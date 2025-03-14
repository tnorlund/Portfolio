import pytest
from copy import deepcopy
from datetime import datetime
from typing import Dict, Any, Optional

from receipt_dynamo import (
    ReceiptValidationCategory,
    itemToReceiptValidationCategory,
)


@pytest.fixture
def example_validation_category():
    """Create a sample ReceiptValidationCategory for testing"""
    return ReceiptValidationCategory(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        field_name="payment_info",
        field_category="creditcard",
        status="valid",
        reasoning="All payment information validated successfully",
        result_summary={
            "total": 3,
            "valid": 3,
            "invalid": 0,
            "warnings": 0,
        },
        validation_timestamp="2023-05-15T10:30:00",
        metadata={
            "source_info": {"model": "validation-v1"},
            "confidence": 0.95,
        },
    )


@pytest.mark.unit
def test_validation_category_init_valid(example_validation_category):
    """Test initialization with valid parameters"""
    assert example_validation_category.receipt_id == 1
    assert example_validation_category.image_id == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert example_validation_category.field_name == "payment_info"
    assert example_validation_category.field_category == "creditcard"
    assert example_validation_category.status == "valid"
    assert example_validation_category.reasoning == "All payment information validated successfully"
    assert example_validation_category.result_summary["total"] == 3
    assert example_validation_category.result_summary["valid"] == 3
    assert example_validation_category.result_summary["invalid"] == 0
    assert example_validation_category.result_summary["warnings"] == 0
    assert example_validation_category.validation_timestamp == "2023-05-15T10:30:00"
    assert example_validation_category.metadata["source_info"]["model"] == "validation-v1"
    assert example_validation_category.metadata["confidence"] == 0.95


@pytest.mark.unit
def test_validation_category_init_minimal():
    """Test initialization with minimal parameters"""
    category = ReceiptValidationCategory(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        field_name="payment_info",
        field_category="creditcard",
        status="valid",
        reasoning="All payment information validated successfully",
        result_summary={"total": 3},
        validation_timestamp="2023-05-15T10:30:00",  # Timestamp required
        metadata={},  # Empty dict for metadata
    )
    
    assert category.receipt_id == 1
    assert category.image_id == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert category.field_name == "payment_info"
    assert category.field_category == "creditcard"
    assert category.status == "valid"
    assert category.reasoning == "All payment information validated successfully"
    assert category.result_summary["total"] == 3
    assert category.validation_timestamp == "2023-05-15T10:30:00"
    
    # Check that metadata defaults to an empty dict
    assert isinstance(category.metadata, dict)
    assert len(category.metadata) == 0


@pytest.mark.unit
def test_validation_category_init_invalid_receipt_id():
    """Test initialization with invalid receipt_id"""
    with pytest.raises(ValueError, match="receipt_id must be an integer"):
        ReceiptValidationCategory(
            receipt_id="1",
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            field_name="payment_info",
            field_category="creditcard",
            status="valid",
            reasoning="All payment information validated successfully",
            result_summary={"total": 3},
            validation_timestamp="2023-05-15T10:30:00",
        )

    with pytest.raises(ValueError, match="receipt_id must be positive"):
        ReceiptValidationCategory(
            receipt_id=-1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            field_name="payment_info",
            field_category="creditcard",
            status="valid",
            reasoning="All payment information validated successfully",
            result_summary={"total": 3},
            validation_timestamp="2023-05-15T10:30:00",
        )


@pytest.mark.unit
def test_validation_category_init_invalid_uuid():
    """Test initialization with invalid image_id (UUID)"""
    with pytest.raises(ValueError, match="uuid must be a string"):
        ReceiptValidationCategory(
            receipt_id=1,
            image_id=123,
            field_name="payment_info",
            field_category="creditcard",
            status="valid",
            reasoning="All payment information validated successfully",
            result_summary={"total": 3},
            validation_timestamp="2023-05-15T10:30:00",
        )

    with pytest.raises(ValueError, match="uuid must be a valid UUID"):
        ReceiptValidationCategory(
            receipt_id=1,
            image_id="not-a-valid-uuid",
            field_name="payment_info",
            field_category="creditcard",
            status="valid",
            reasoning="All payment information validated successfully",
            result_summary={"total": 3},
            validation_timestamp="2023-05-15T10:30:00",
        )


@pytest.mark.unit
def test_validation_category_init_invalid_field_name():
    """Test initialization with invalid field_name"""
    with pytest.raises(ValueError, match="field_name must be a string"):
        ReceiptValidationCategory(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            field_name=123,
            field_category="creditcard",
            status="valid",
            reasoning="All payment information validated successfully",
            result_summary={"total": 3},
            validation_timestamp="2023-05-15T10:30:00",
        )

    with pytest.raises(ValueError, match="field_name must not be empty"):
        ReceiptValidationCategory(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            field_name="",
            field_category="creditcard",
            status="valid",
            reasoning="All payment information validated successfully",
            result_summary={"total": 3},
            validation_timestamp="2023-05-15T10:30:00",
        )


@pytest.mark.unit
def test_validation_category_init_invalid_field_category():
    """Test initialization with invalid field_category"""
    with pytest.raises(ValueError, match="field_category must be a string"):
        ReceiptValidationCategory(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            field_name="payment_info",
            field_category=123,
            status="valid",
            reasoning="All payment information validated successfully",
            result_summary={"total": 3},
            validation_timestamp="2023-05-15T10:30:00",
        )

    with pytest.raises(ValueError, match="field_category must not be empty"):
        ReceiptValidationCategory(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            field_name="payment_info",
            field_category="",
            status="valid",
            reasoning="All payment information validated successfully",
            result_summary={"total": 3},
            validation_timestamp="2023-05-15T10:30:00",
        )


@pytest.mark.unit
def test_validation_category_init_invalid_status():
    """Test initialization with invalid status"""
    with pytest.raises(ValueError, match="status must be a string"):
        ReceiptValidationCategory(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            field_name="payment_info",
            field_category="creditcard",
            status=123,
            reasoning="All payment information validated successfully",
            result_summary={"total": 3},
            validation_timestamp="2023-05-15T10:30:00",
        )

    with pytest.raises(ValueError, match="status must not be empty"):
        ReceiptValidationCategory(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            field_name="payment_info",
            field_category="creditcard",
            status="",
            reasoning="All payment information validated successfully",
            result_summary={"total": 3},
            validation_timestamp="2023-05-15T10:30:00",
        )


@pytest.mark.unit
def test_validation_category_init_invalid_reasoning():
    """Test initialization with invalid reasoning"""
    with pytest.raises(ValueError, match="reasoning must be a string"):
        ReceiptValidationCategory(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            field_name="payment_info",
            field_category="creditcard",
            status="valid",
            reasoning=123,
            result_summary={"total": 3},
            validation_timestamp="2023-05-15T10:30:00",
        )

    with pytest.raises(ValueError, match="reasoning must not be empty"):
        ReceiptValidationCategory(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            field_name="payment_info",
            field_category="creditcard",
            status="valid",
            reasoning="",
            result_summary={"total": 3},
            validation_timestamp="2023-05-15T10:30:00",
        )


@pytest.mark.unit
def test_validation_category_init_invalid_result_summary():
    """Test initialization with invalid result_summary"""
    with pytest.raises(ValueError, match="result_summary must be a dictionary"):
        ReceiptValidationCategory(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            field_name="payment_info",
            field_category="creditcard",
            status="valid",
            reasoning="All payment information validated successfully",
            result_summary="not a dict",
            validation_timestamp="2023-05-15T10:30:00",
        )


@pytest.mark.unit
def test_validation_category_init_invalid_validation_timestamp():
    """Test initialization with invalid validation_timestamp"""
    with pytest.raises(ValueError, match="validation_timestamp must be a string"):
        ReceiptValidationCategory(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            field_name="payment_info",
            field_category="creditcard",
            status="valid",
            reasoning="All payment information validated successfully",
            result_summary={"total": 3},
            validation_timestamp=123,
        )


@pytest.mark.unit
def test_validation_category_init_invalid_metadata():
    """Test initialization with invalid metadata"""
    with pytest.raises(ValueError, match="metadata must be a dictionary"):
        ReceiptValidationCategory(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            field_name="payment_info",
            field_category="creditcard",
            status="valid",
            reasoning="All payment information validated successfully",
            result_summary={"total": 3},
            validation_timestamp="2023-05-15T10:30:00",
            metadata="not a dict",
        )


@pytest.mark.unit
def test_key(example_validation_category):
    """Test the key property"""
    assert example_validation_category.key == {
        "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": "RECEIPT#1#ANALYSIS#VALIDATION#CATEGORY#payment_info"},
    }


@pytest.mark.unit
def test_gsi1_key(example_validation_category):
    """Test the gsi1_key property"""
    assert example_validation_category.gsi1_key == {
        "GSI1PK": {"S": "ANALYSIS_TYPE"},
        "GSI1SK": {"S": "VALIDATION#2023-05-15T10:30:00#CATEGORY#payment_info"},
    }


@pytest.mark.unit
def test_gsi2_key(example_validation_category):
    """Test the gsi2_key property"""
    assert example_validation_category.gsi2_key == {
        "GSI2PK": {"S": "RECEIPT"},
        "GSI2SK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3#RECEIPT#1#VALIDATION"},
    }


@pytest.mark.unit
def test_gsi3_key(example_validation_category):
    """Test the gsi3_key property"""
    assert example_validation_category.gsi3_key == {
        "GSI3PK": {"S": "FIELD_STATUS#payment_info#valid"},
        "GSI3SK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3#RECEIPT#1"},
    }


@pytest.mark.unit
def test_to_item(example_validation_category):
    """Test the to_item method"""
    item = example_validation_category.to_item()
    
    # Check that the basic keys are present
    assert item["PK"] == {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"}
    assert item["SK"] == {"S": "RECEIPT#1#ANALYSIS#VALIDATION#CATEGORY#payment_info"}
    assert item["GSI1PK"] == {"S": "ANALYSIS_TYPE"}
    assert item["GSI1SK"] == {"S": "VALIDATION#2023-05-15T10:30:00#CATEGORY#payment_info"}
    assert item["GSI2PK"] == {"S": "RECEIPT"}
    assert item["GSI2SK"] == {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3#RECEIPT#1#VALIDATION"}
    assert item["GSI3PK"] == {"S": "FIELD_STATUS#payment_info#valid"}
    assert item["GSI3SK"] == {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3#RECEIPT#1"}
    
    # Check that the required fields are present
    assert item["field_category"] == {"S": "creditcard"}
    assert item["status"] == {"S": "valid"}
    assert item["reasoning"] == {"S": "All payment information validated successfully"}
    assert item["result_summary"] == {"M": {
        "total": {"N": "3"},
        "valid": {"N": "3"},
        "invalid": {"N": "0"},
        "warnings": {"N": "0"}
    }}
    assert item["validation_timestamp"] == {"S": "2023-05-15T10:30:00"}
    assert item["metadata"] == {"M": {
        "source_info": {"M": {"model": {"S": "validation-v1"}}},
        "confidence": {"N": "0.95"}
    }}


@pytest.mark.unit
def test_from_item(example_validation_category):
    """Test the from_item method"""
    # Convert to item and back
    item = example_validation_category.to_item()
    category = ReceiptValidationCategory.from_item(item)
    
    # Check that all fields match
    assert category.receipt_id == example_validation_category.receipt_id
    assert category.image_id == example_validation_category.image_id
    assert category.field_name == example_validation_category.field_name
    assert category.field_category == example_validation_category.field_category
    assert category.status == example_validation_category.status
    assert category.reasoning == example_validation_category.reasoning
    assert category.result_summary == example_validation_category.result_summary
    assert category.validation_timestamp == example_validation_category.validation_timestamp
    assert category.metadata == example_validation_category.metadata


@pytest.mark.unit
def test_eq(example_validation_category):
    """Test equality comparison"""
    # Same attributes should be equal
    category1 = example_validation_category
    category2 = deepcopy(example_validation_category)
    assert category1 == category2
    
    # Different receipt_id should not be equal
    category2.receipt_id = 2
    assert category1 != category2
    
    # Different image_id should not be equal
    category2.receipt_id = 1
    category2.image_id = "4f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert category1 != category2
    
    # Different field_name should not be equal
    category2.image_id = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    category2.field_name = "merchant_info"
    assert category1 != category2
    
    # Different status should not be equal
    category2.field_name = "payment_info"
    category2.status = "invalid"
    assert category1 != category2
    
    # Non-ReceiptValidationCategory should not be equal
    assert category1 != "not a ReceiptValidationCategory"


@pytest.mark.unit
def test_repr(example_validation_category):
    """Test string representation"""
    repr_str = repr(example_validation_category)
    assert "ReceiptValidationCategory" in repr_str
    assert "receipt_id=1" in repr_str
    assert "image_id=" in repr_str
    assert "3f52804b-2fad-4e00-92c8-b593da3a8ed3" in repr_str
    assert "field_name=payment_info" in repr_str
    assert "status=valid" in repr_str


@pytest.mark.unit
def test_itemToReceiptValidationCategory(example_validation_category):
    """Test the itemToReceiptValidationCategory function"""
    # Convert to item using to_item
    item = example_validation_category.to_item()
    
    # Use the conversion function
    category = itemToReceiptValidationCategory(item)
    
    # Check that the result matches the original
    assert category.receipt_id == example_validation_category.receipt_id
    assert category.image_id == example_validation_category.image_id
    assert category.field_name == example_validation_category.field_name
    assert category.field_category == example_validation_category.field_category
    assert category.status == example_validation_category.status
    assert category.reasoning == example_validation_category.reasoning
    assert category.result_summary == example_validation_category.result_summary
    assert category.validation_timestamp == example_validation_category.validation_timestamp
    assert category.metadata == example_validation_category.metadata
