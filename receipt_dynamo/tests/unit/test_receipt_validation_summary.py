from copy import deepcopy
from datetime import datetime
from typing import Any, Dict, List, Optional

import pytest

from receipt_dynamo import (
    ReceiptValidationSummary,
    item_to_receipt_validation_summary,
)


@pytest.fixture
def example_validation_summary():
    """Create a sample ReceiptValidationSummary for testing"""
    return ReceiptValidationSummary(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        overall_status="valid",
        overall_reasoning="Receipt validation passed all checks",
        field_summary={
            "business_identity": {
                "status": "valid",
                "count": 2,
                "has_errors": False,
                "has_warnings": False,
            },
            "address_verification": {
                "status": "valid",
                "count": 1,
                "has_errors": False,
                "has_warnings": False,
            },
        },
        validation_timestamp="2023-05-15T10:30:00",
        version="1.0.0",
        metadata={
            "source_info": {"model": "test-model", "version": "1.0"},
            "processing_metrics": {"execution_time_ms": 150},
            "processing_history": [
                {
                    "event_type": "validation_started",
                    "timestamp": "2023-05-15T10:29:00",
                }
            ],
        },
        timestamp_added=datetime(2023, 5, 15, 10, 30, 0),
        timestamp_updated=datetime(2023, 5, 15, 10, 30, 0),
    )


@pytest.mark.unit
def test_validation_summary_init_valid(example_validation_summary):
    """Test initialization with valid parameters"""
    assert example_validation_summary.receipt_id == 1
    assert (
        example_validation_summary.image_id
        == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    )
    assert example_validation_summary.overall_status == "valid"
    assert (
        example_validation_summary.overall_reasoning
        == "Receipt validation passed all checks"
    )

    # Direct access to field_summary
    assert "business_identity" in example_validation_summary.field_summary
    assert "address_verification" in example_validation_summary.field_summary

    assert (
        example_validation_summary.validation_timestamp
        == "2023-05-15T10:30:00"
    )
    assert example_validation_summary.version == "1.0.0"

    # Check metadata structure
    assert "source_info" in example_validation_summary.metadata
    assert "processing_metrics" in example_validation_summary.metadata
    assert "processing_history" in example_validation_summary.metadata

    assert (
        example_validation_summary.metadata["source_info"]["model"]
        == "test-model"
    )
    assert (
        example_validation_summary.metadata["processing_metrics"][
            "execution_time_ms"
        ]
        == 150
    )
    assert len(example_validation_summary.metadata["processing_history"]) == 1

    assert example_validation_summary.timestamp_added == "2023-05-15T10:30:00"
    assert (
        example_validation_summary.timestamp_updated == "2023-05-15T10:30:00"
    )


@pytest.mark.unit
def test_validation_summary_init_default_metadata():
    """Test initialization with default metadata"""
    summary = ReceiptValidationSummary(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        overall_status="valid",
        overall_reasoning="Receipt validation passed all checks",
        field_summary={},
        validation_timestamp="2023-05-15T10:30:00",
        timestamp_added=datetime(2023, 5, 15, 10, 30, 0),
    )

    # Check default metadata structure
    assert "source_info" in summary.metadata
    assert "processing_metrics" in summary.metadata
    assert "processing_history" in summary.metadata

    # Check empty dictionaries/lists
    assert isinstance(summary.metadata["source_info"], dict)
    assert isinstance(summary.metadata["processing_metrics"], dict)
    assert isinstance(summary.metadata["processing_history"], list)


@pytest.mark.unit
def test_add_processing_metric(example_validation_summary):
    """Test adding processing metrics"""
    example_validation_summary.add_processing_metric("validation_time_ms", 250)
    example_validation_summary.add_processing_metric("api_calls", 3)

    assert (
        example_validation_summary.metadata["processing_metrics"][
            "validation_time_ms"
        ]
        == 250
    )
    assert (
        example_validation_summary.metadata["processing_metrics"]["api_calls"]
        == 3
    )

    # Existing metrics should still be there
    assert (
        example_validation_summary.metadata["processing_metrics"][
            "execution_time_ms"
        ]
        == 150
    )


@pytest.mark.unit
def test_add_history_event(example_validation_summary):
    """Test adding history events"""
    # Add a new event
    example_validation_summary.add_history_event(
        "validation_completed", {"status": "success"}
    )

    # Check that the history was updated
    history = example_validation_summary.metadata["processing_history"]
    assert len(history) == 2

    # First event should remain unchanged
    assert history[0]["event_type"] == "validation_started"

    # New event should be added with details
    assert history[1]["event_type"] == "validation_completed"
    assert history[1]["status"] == "success"
    assert "timestamp" in history[1]


@pytest.mark.unit
def test_metadata_persistence_in_item_conversion(example_validation_summary):
    """Test that metadata is properly preserved during to_item/from_item conversion"""
    # Add some additional metadata
    example_validation_summary.add_processing_metric("token_count", 1250)
    example_validation_summary.add_history_event(
        "additional_validation", {"details": "Extra checks"}
    )

    # Record the number of history events before conversion
    original_history_count = len(
        example_validation_summary.metadata["processing_history"]
    )

    # Convert to DynamoDB item and back
    item = example_validation_summary.to_item()
    reconstructed = ReceiptValidationSummary.from_item(item)

    # Verify the metadata was preserved
    assert reconstructed.metadata["processing_metrics"]["token_count"] == 1250
    assert (
        reconstructed.metadata["processing_metrics"]["execution_time_ms"]
        == 150
    )

    # Verify history was preserved
    history = reconstructed.metadata["processing_history"]
    assert len(history) == original_history_count
    assert history[0]["event_type"] == "validation_started"
    assert history[-1]["event_type"] == "additional_validation"
    assert history[-1]["details"] == "Extra checks"


@pytest.mark.unit
def test_validation_summary_init_invalid_receipt_id():
    """Test initialization with invalid receipt_id"""
    with pytest.raises(ValueError, match="receipt_id must be an integer"):
        ReceiptValidationSummary(
            receipt_id="1",
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            overall_status="valid",
            overall_reasoning="Receipt validation passed all checks",
            field_summary={},
            validation_timestamp="2023-05-15T10:30:00",
        )

    with pytest.raises(ValueError, match="receipt_id must be positive"):
        ReceiptValidationSummary(
            receipt_id=-1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            overall_status="valid",
            overall_reasoning="Receipt validation passed all checks",
            field_summary={},
            validation_timestamp="2023-05-15T10:30:00",
        )


@pytest.mark.unit
def test_validation_summary_init_invalid_uuid():
    """Test initialization with invalid image_id (UUID)"""
    with pytest.raises(ValueError, match="uuid must be a string"):
        ReceiptValidationSummary(
            receipt_id=1,
            image_id=123,
            overall_status="valid",
            overall_reasoning="Receipt validation passed all checks",
            field_summary={},
            validation_timestamp="2023-05-15T10:30:00",
        )

    with pytest.raises(ValueError, match="uuid must be a valid UUID"):
        ReceiptValidationSummary(
            receipt_id=1,
            image_id="not-a-valid-uuid",
            overall_status="valid",
            overall_reasoning="Receipt validation passed all checks",
            field_summary={},
            validation_timestamp="2023-05-15T10:30:00",
        )


@pytest.mark.unit
def test_validation_summary_init_invalid_overall_status():
    """Test initialization with invalid overall_status"""
    with pytest.raises(ValueError, match="overall_status must be a string"):
        ReceiptValidationSummary(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            overall_status=123,
            overall_reasoning="Receipt validation passed all checks",
            field_summary={},
            validation_timestamp="2023-05-15T10:30:00",
        )


@pytest.mark.unit
def test_validation_summary_init_invalid_overall_reasoning():
    """Test initialization with invalid overall_reasoning"""
    with pytest.raises(ValueError, match="overall_reasoning must be a string"):
        ReceiptValidationSummary(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            overall_status="valid",
            overall_reasoning=123,
            field_summary={},
            validation_timestamp="2023-05-15T10:30:00",
        )


@pytest.mark.unit
def test_validation_summary_init_invalid_field_summary():
    """Test initialization with invalid field_summary"""
    with pytest.raises(ValueError, match="field_summary must be a dictionary"):
        ReceiptValidationSummary(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            overall_status="valid",
            overall_reasoning="Receipt validation passed all checks",
            field_summary="not a dict",
            validation_timestamp="2023-05-15T10:30:00",
        )


@pytest.mark.unit
def test_validation_summary_init_invalid_validation_timestamp():
    """Test initialization with invalid validation_timestamp"""
    with pytest.raises(
        ValueError, match="validation_timestamp must be a string"
    ):
        ReceiptValidationSummary(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            overall_status="valid",
            overall_reasoning="Receipt validation passed all checks",
            field_summary={},
            validation_timestamp=123,
        )


@pytest.mark.unit
def test_validation_summary_init_invalid_version():
    """Test initialization with invalid version"""
    with pytest.raises(ValueError, match="version must be a string"):
        ReceiptValidationSummary(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            overall_status="valid",
            overall_reasoning="Receipt validation passed all checks",
            field_summary={},
            validation_timestamp="2023-05-15T10:30:00",
            version=123,
        )


@pytest.mark.unit
def test_validation_summary_init_invalid_metadata():
    """Test initialization with invalid metadata"""
    with pytest.raises(ValueError, match="metadata must be a dictionary"):
        ReceiptValidationSummary(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            overall_status="valid",
            overall_reasoning="Receipt validation passed all checks",
            field_summary={},
            validation_timestamp="2023-05-15T10:30:00",
            metadata="not a dict",
        )


@pytest.mark.unit
def test_validation_summary_init_invalid_timestamp_added():
    """Test initialization with invalid timestamp_added"""
    with pytest.raises(ValueError):
        # Don't specify exact error message pattern as it may vary
        ReceiptValidationSummary(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            overall_status="valid",
            overall_reasoning="Receipt validation passed all checks",
            field_summary={},
            validation_timestamp="2023-05-15T10:30:00",
            timestamp_added=123,
        )


@pytest.mark.unit
def test_validation_summary_init_invalid_timestamp_updated():
    """Test initialization with invalid timestamp_updated"""
    with pytest.raises(ValueError):
        # Don't specify exact error message pattern as it may vary
        ReceiptValidationSummary(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            overall_status="valid",
            overall_reasoning="Receipt validation passed all checks",
            field_summary={},
            validation_timestamp="2023-05-15T10:30:00",
            timestamp_updated=123,
        )


@pytest.mark.unit
def test_key(example_validation_summary):
    """Test the key property"""
    assert example_validation_summary.key == {
        "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": "RECEIPT#00001#ANALYSIS#VALIDATION"},
    }


@pytest.mark.unit
def test_gsi1_key(example_validation_summary):
    """Test the gsi1_key property"""
    assert example_validation_summary.gsi1_key() == {
        "GSI1PK": {"S": "ANALYSIS_TYPE"},
        "GSI1SK": {"S": "VALIDATION#2023-05-15T10:30:00"},
    }


@pytest.mark.unit
def test_gsi3_key(example_validation_summary):
    """Test the gsi3_key property"""
    assert example_validation_summary.gsi3_key() == {
        "GSI3PK": {"S": "VALIDATION_STATUS#valid"},
        "GSI3SK": {"S": "TIMESTAMP#2023-05-15T10:30:00"},
    }


@pytest.mark.unit
def test_to_item(example_validation_summary):
    """Test the to_item method"""
    item = example_validation_summary.to_item()

    # Check basic structure with proper DynamoDB types
    assert item["PK"]["S"] == "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert item["SK"]["S"] == "RECEIPT#00001#ANALYSIS#VALIDATION"
    assert item["GSI1PK"]["S"] == "ANALYSIS_TYPE"
    assert item["GSI1SK"]["S"] == "VALIDATION#2023-05-15T10:30:00"
    assert item["GSI3PK"]["S"] == "VALIDATION_STATUS#valid"
    assert item["GSI3SK"]["S"] == "TIMESTAMP#2023-05-15T10:30:00"
    assert item["TYPE"]["S"] == "RECEIPT_VALIDATION_SUMMARY"
    assert item["overall_status"]["S"] == "valid"
    assert (
        item["overall_reasoning"]["S"]
        == "Receipt validation passed all checks"
    )
    assert item["validation_timestamp"]["S"] == "2023-05-15T10:30:00"
    assert item["version"]["S"] == "1.0.0"

    # Check complex nested structures
    assert "field_summary" in item
    assert "M" in item["field_summary"]
    assert "business_identity" in item["field_summary"]["M"]
    assert "address_verification" in item["field_summary"]["M"]

    # Check metadata
    assert "metadata" in item
    assert "M" in item["metadata"]
    assert "source_info" in item["metadata"]["M"]
    assert "processing_metrics" in item["metadata"]["M"]
    assert "processing_history" in item["metadata"]["M"]

    # Check timestamps
    assert item["timestamp_added"]["S"] == "2023-05-15T10:30:00"
    assert item["timestamp_updated"]["S"] == "2023-05-15T10:30:00"


@pytest.mark.unit
def test_from_item(example_validation_summary):
    """Test the from_item method"""
    # Convert to DynamoDB item and back
    item = example_validation_summary.to_item()
    summary = ReceiptValidationSummary.from_item(item)

    # Verify basic fields
    assert summary.receipt_id == example_validation_summary.receipt_id
    assert summary.image_id == example_validation_summary.image_id
    assert summary.overall_status == example_validation_summary.overall_status
    assert (
        summary.overall_reasoning
        == example_validation_summary.overall_reasoning
    )

    # Verify complex fields
    assert summary.field_summary == example_validation_summary.field_summary
    assert (
        summary.validation_timestamp
        == example_validation_summary.validation_timestamp
    )
    assert summary.version == example_validation_summary.version

    # Verify metadata structure
    assert "source_info" in summary.metadata
    assert "processing_metrics" in summary.metadata
    assert "processing_history" in summary.metadata

    # Verify timestamps
    assert (
        summary.timestamp_added == example_validation_summary.timestamp_added
    )
    assert (
        summary.timestamp_updated
        == example_validation_summary.timestamp_updated
    )


@pytest.mark.unit
def test_eq(example_validation_summary):
    """Test equality comparison"""
    summary1 = example_validation_summary
    summary2 = deepcopy(example_validation_summary)
    assert summary1 == summary2

    summary2.overall_status = "invalid"
    assert summary1 != summary2

    summary2.overall_status = "valid"
    summary2.validation_timestamp = "2023-05-15T10:35:00"
    assert summary1 != summary2

    assert summary1 != "not a ReceiptValidationSummary"


@pytest.mark.unit
def test_repr(example_validation_summary):
    """Test string representation"""
    repr_str = repr(example_validation_summary)
    assert "ReceiptValidationSummary" in repr_str
    assert "receipt_id=1" in repr_str
    # The image_id might not have quotes in the string representation
    assert "image_id" in repr_str
    assert "3f52804b-2fad-4e00-92c8-b593da3a8ed3" in repr_str
    assert "overall_status" in repr_str


@pytest.mark.unit
def test_itemToReceiptValidationSummary(example_validation_summary):
    """Test the item_to_receipt_validation_summary function"""
    # Convert to DynamoDB item
    item = {
        "metadata": {
            "M": {
                "processing_metrics": {
                    "M": {
                        "validation_counts": {
                            "M": {
                                "business_identity": {"N": "0"},
                                "hours_verification": {"N": "0"},
                                "address_verification": {"N": "0"},
                                "line_item_validation": {"N": "0"},
                                "phone_validation": {"N": "0"},
                                "cross_field_consistency": {"N": "0"},
                            }
                        },
                        "validation_status": {"S": "incomplete"},
                        "result_types": {
                            "M": {
                                "warning": {"N": "0"},
                                "error": {"N": "0"},
                                "success": {"N": "0"},
                                "info": {"N": "0"},
                            }
                        },
                    }
                },
                "source_information": {
                    "M": {"package_version": {"S": "0.1.0"}}
                },
                "source_info": {"M": {}},
                "version": {"S": "0.1.0"},
                "processing_history": {
                    "L": [
                        {
                            "M": {
                                "action": {"S": "created"},
                                "version": {"S": "0.1.0"},
                                "timestamp": {
                                    "S": "2025-03-19T08:43:08.877848"
                                },
                            }
                        },
                        {
                            "M": {
                                "action": {"S": "validation_incomplete"},
                                "warning_count": {"N": "0"},
                                "error_count": {"N": "0"},
                                "version": {"S": "0.1.0"},
                                "timestamp": {
                                    "S": "2025-03-19T08:43:08.877865"
                                },
                            }
                        },
                    ]
                },
            }
        },
        "version": {"S": "0.1.0"},
        "timestamp_updated": {"S": "2025-03-19T08:43:08.877865"},
        "GSI1SK": {"S": "VALIDATION#2025-03-19T08:43:08.877826"},
        "overall_status": {"S": "valid"},
        "GSI3SK": {"S": "TIMESTAMP#2025-03-19T08:43:08.877826"},
        "TYPE": {"S": "RECEIPT_VALIDATION_SUMMARY"},
        "GSI2SK": {
            "S": "IMAGE#aabbf168-7a61-483b-97c7-e711de91ce5f#RECEIPT#00001"
        },
        "GSI2PK": {"S": "RECEIPT"},
        "GSI1PK": {"S": "ANALYSIS_TYPE"},
        "GSI3PK": {"S": "VALIDATION_STATUS#valid"},
        "timestamp_added": {"S": "2025-03-19T08:43:08.877845"},
        "field_summary": {
            "M": {
                "business_identity": {
                    "M": {
                        "has_errors": {"N": "0"},
                        "count": {"N": "0"},
                        "info_count": {"N": "0"},
                        "success_count": {"N": "0"},
                        "has_warnings": {"N": "0"},
                        "warning_count": {"N": "0"},
                        "error_count": {"N": "0"},
                        "status": {"S": "incomplete"},
                    }
                },
                "hours_verification": {
                    "M": {
                        "has_errors": {"N": "0"},
                        "count": {"N": "0"},
                        "info_count": {"N": "0"},
                        "success_count": {"N": "0"},
                        "has_warnings": {"N": "0"},
                        "warning_count": {"N": "0"},
                        "error_count": {"N": "0"},
                        "status": {"S": "incomplete"},
                    }
                },
                "address_verification": {
                    "M": {
                        "has_errors": {"N": "0"},
                        "count": {"N": "0"},
                        "info_count": {"N": "0"},
                        "success_count": {"N": "0"},
                        "has_warnings": {"N": "0"},
                        "warning_count": {"N": "0"},
                        "error_count": {"N": "0"},
                        "status": {"S": "incomplete"},
                    }
                },
                "line_item_validation": {
                    "M": {
                        "has_errors": {"N": "0"},
                        "count": {"N": "0"},
                        "info_count": {"N": "0"},
                        "success_count": {"N": "0"},
                        "has_warnings": {"N": "0"},
                        "warning_count": {"N": "0"},
                        "error_count": {"N": "0"},
                        "status": {"S": "incomplete"},
                    }
                },
                "phone_validation": {
                    "M": {
                        "has_errors": {"N": "0"},
                        "count": {"N": "0"},
                        "info_count": {"N": "0"},
                        "success_count": {"N": "0"},
                        "has_warnings": {"N": "0"},
                        "warning_count": {"N": "0"},
                        "error_count": {"N": "0"},
                        "status": {"S": "incomplete"},
                    }
                },
                "cross_field_consistency": {
                    "M": {
                        "has_errors": {"N": "0"},
                        "count": {"N": "0"},
                        "info_count": {"N": "0"},
                        "success_count": {"N": "0"},
                        "has_warnings": {"N": "0"},
                        "warning_count": {"N": "0"},
                        "error_count": {"N": "0"},
                        "status": {"S": "incomplete"},
                    }
                },
            }
        },
        "validation_timestamp": {"S": "2025-03-19T08:43:08.877826"},
        "SK": {"S": "RECEIPT#00001#ANALYSIS#VALIDATION"},
        "overall_reasoning": {"S": "No issues found during validation."},
        "PK": {"S": "IMAGE#aabbf168-7a61-483b-97c7-e711de91ce5f"},
    }

    # Use the conversion function
    summary = item_to_receipt_validation_summary(item)

    # Verify basic fields
    assert summary.receipt_id == 1
    assert summary.image_id == "aabbf168-7a61-483b-97c7-e711de91ce5f"
    assert summary.overall_status == "valid"
    assert summary.overall_reasoning == "No issues found during validation."

    # Verify complex fields
    assert summary.field_summary == {
        "address_verification": {
            "count": 0,
            "error_count": 0,
            "has_errors": 0,
            "has_warnings": 0,
            "info_count": 0,
            "status": "incomplete",
            "success_count": 0,
            "warning_count": 0,
        },
        "business_identity": {
            "count": 0,
            "error_count": 0,
            "has_errors": 0,
            "has_warnings": 0,
            "info_count": 0,
            "status": "incomplete",
            "success_count": 0,
            "warning_count": 0,
        },
        "cross_field_consistency": {
            "count": 0,
            "error_count": 0,
            "has_errors": 0,
            "has_warnings": 0,
            "info_count": 0,
            "status": "incomplete",
            "success_count": 0,
            "warning_count": 0,
        },
        "hours_verification": {
            "count": 0,
            "error_count": 0,
            "has_errors": 0,
            "has_warnings": 0,
            "info_count": 0,
            "status": "incomplete",
            "success_count": 0,
            "warning_count": 0,
        },
        "line_item_validation": {
            "count": 0,
            "error_count": 0,
            "has_errors": 0,
            "has_warnings": 0,
            "info_count": 0,
            "status": "incomplete",
            "success_count": 0,
            "warning_count": 0,
        },
        "phone_validation": {
            "count": 0,
            "error_count": 0,
            "has_errors": 0,
            "has_warnings": 0,
            "info_count": 0,
            "status": "incomplete",
            "success_count": 0,
            "warning_count": 0,
        },
    }
    assert summary.validation_timestamp == "2025-03-19T08:43:08.877826"
    assert summary.version == "0.1.0"

    # Test with missing keys
    with pytest.raises(ValueError, match="Item is missing required keys"):
        item_to_receipt_validation_summary({})

    # Test with incomplete keys
    with pytest.raises(ValueError):
        item_to_receipt_validation_summary({"PK": {"S": "test"}})
