from datetime import datetime, timedelta

import pytest

from receipt_dynamo.entities.receipt_label_analysis import (
    ReceiptLabelAnalysis,
    item_to_receipt_label_analysis,
)


@pytest.fixture
def example_receipt_label_analysis():
    """Create a sample ReceiptLabelAnalysis for testing."""
    labels = [
        {
            "label_type": "business_name",
            "line_id": 1,
            "word_id": 2,
            "text": "ACME",
            "reasoning": "This word appears to be a business name at the top of the receipt",
            "bounding_box": {
                "top_left": {"x": 10, "y": 10},
                "top_right": {"x": 50, "y": 10},
                "bottom_left": {"x": 10, "y": 20},
                "bottom_right": {"x": 50, "y": 20},
            },
        },
        {
            "label_type": "total",
            "line_id": 10,
            "word_id": 3,
            "text": "42.99",
            "reasoning": "This appears to be the total amount due",
            "bounding_box": {
                "top_left": {"x": 100, "y": 200},
                "top_right": {"x": 150, "y": 200},
                "bottom_left": {"x": 100, "y": 220},
                "bottom_right": {"x": 150, "y": 220},
            },
        },
    ]

    metadata = {
        "processing_metrics": {"processing_time_ms": 250, "api_calls": 2},
        "processing_history": [
            {
                "event_type": "creation",
                "timestamp": datetime.now().isoformat(),
                "description": "Initial creation of label analysis",
                "model_version": "gpt-4",
            }
        ],
        "source_information": {
            "model_name": "gpt-4",
            "model_version": "2023-09",
            "algorithm": "llm-field-labeling",
            "configuration": {"temperature": 0.2},
        },
    }

    return ReceiptLabelAnalysis(
        image_id="test_image_123",
        receipt_id=456,
        labels=labels,
        timestamp_added=datetime.now(),
        version="1.2",
        overall_reasoning="Receipt contains standard business information and transaction details",
        metadata=metadata,
    )


@pytest.mark.unit
def test_receipt_label_analysis_init_valid(example_receipt_label_analysis):
    """Test that ReceiptLabelAnalysis initializes correctly with valid parameters."""
    assert example_receipt_label_analysis.image_id == "test_image_123"
    assert example_receipt_label_analysis.receipt_id == 456
    assert len(example_receipt_label_analysis.labels) == 2
    assert isinstance(example_receipt_label_analysis.timestamp_added, str)
    assert example_receipt_label_analysis.version == "1.2"
    assert (
        example_receipt_label_analysis.overall_reasoning
        == "Receipt contains standard business information and transaction details"
    )
    assert "processing_metrics" in example_receipt_label_analysis.metadata
    assert "processing_history" in example_receipt_label_analysis.metadata
    assert "source_information" in example_receipt_label_analysis.metadata


@pytest.mark.unit
def test_receipt_label_analysis_init_invalid_image_id():
    """Test that ReceiptLabelAnalysis raises ValueError with invalid image_id."""
    with pytest.raises(ValueError, match="image_id must be a string"):
        ReceiptLabelAnalysis(
            image_id=123,  # Should be a string
            receipt_id=456,
            labels=[],
            timestamp_added=datetime.now(),
        )


@pytest.mark.unit
def test_receipt_label_analysis_init_invalid_receipt_id():
    """Test that ReceiptLabelAnalysis raises ValueError with invalid receipt_id."""
    with pytest.raises(ValueError, match="receipt_id must be an integer"):
        ReceiptLabelAnalysis(
            image_id="test_image_123",
            receipt_id="456",  # Should be an integer
            labels=[],
            timestamp_added=datetime.now(),
        )


@pytest.mark.unit
def test_receipt_label_analysis_init_invalid_labels():
    """Test that ReceiptLabelAnalysis raises ValueError with invalid labels."""
    with pytest.raises(ValueError, match="labels must be a list"):
        ReceiptLabelAnalysis(
            image_id="test_image_123",
            receipt_id=456,
            labels="not a list",  # Should be a list
            timestamp_added=datetime.now(),
        )


@pytest.mark.unit
def test_receipt_label_analysis_init_invalid_timestamp():
    """Test that ReceiptLabelAnalysis raises ValueError with invalid timestamp_added."""
    with pytest.raises(
        ValueError,
        match="timestamp_added must be a datetime object or a string",
    ):
        ReceiptLabelAnalysis(
            image_id="test_image_123",
            receipt_id=456,
            labels=[],
            timestamp_added=123,  # Should be a datetime object or string
        )


@pytest.mark.unit
def test_receipt_label_analysis_init_invalid_version():
    """Test that ReceiptLabelAnalysis raises ValueError with invalid version."""
    with pytest.raises(ValueError, match="version must be a string"):
        ReceiptLabelAnalysis(
            image_id="test_image_123",
            receipt_id=456,
            labels=[],
            timestamp_added=datetime.now(),
            version=1.0,  # Should be a string
        )


@pytest.mark.unit
def test_receipt_label_analysis_init_invalid_reasoning():
    """Test that ReceiptLabelAnalysis raises ValueError with invalid overall_reasoning."""
    with pytest.raises(ValueError, match="overall_reasoning must be a string"):
        ReceiptLabelAnalysis(
            image_id="test_image_123",
            receipt_id=456,
            labels=[],
            timestamp_added=datetime.now(),
            overall_reasoning=123,  # Should be a string
        )


@pytest.mark.unit
def test_receipt_label_analysis_default_metadata():
    """Test that ReceiptLabelAnalysis creates default metadata when none is provided."""
    label_analysis = ReceiptLabelAnalysis(
        image_id="test_image_123",
        receipt_id=456,
        labels=[],
        timestamp_added=datetime.now(),
    )

    assert "processing_metrics" in label_analysis.metadata
    assert "processing_history" in label_analysis.metadata
    assert "source_information" in label_analysis.metadata
    assert len(label_analysis.metadata["processing_history"]) == 1
    assert (
        label_analysis.metadata["processing_history"][0]["event_type"]
        == "creation"
    )


@pytest.mark.unit
def test_receipt_label_analysis_key_generation(example_receipt_label_analysis):
    """Test that key() generates the correct DynamoDB primary key."""
    key = example_receipt_label_analysis.key

    assert key["PK"]["S"] == "IMAGE#test_image_123"
    assert key["SK"]["S"] == "RECEIPT#00456#ANALYSIS#LABELS"


@pytest.mark.unit
def test_receipt_label_analysis_gsi1_key_generation(
    example_receipt_label_analysis,
):
    """Test that gsi1_key() generates the correct GSI1 key."""
    gsi1_key = example_receipt_label_analysis.gsi1_key()

    assert gsi1_key["GSI1PK"]["S"] == "ANALYSIS_TYPE"
    assert gsi1_key["GSI1SK"]["S"].startswith("LABELS#")


@pytest.mark.unit
def test_receipt_label_analysis_gsi2_key_generation(
    example_receipt_label_analysis,
):
    """Test that gsi2_key() generates the correct GSI2 key."""
    gsi2_key = example_receipt_label_analysis.gsi2_key()

    assert gsi2_key["GSI2PK"]["S"] == "RECEIPT"
    assert gsi2_key["GSI2SK"]["S"] == "IMAGE#test_image_123#RECEIPT#00456"


@pytest.mark.unit
def test_receipt_label_analysis_to_item(example_receipt_label_analysis):
    """Test that to_item() generates the correct DynamoDB item."""
    item = example_receipt_label_analysis.to_item()

    # Check keys
    assert item["PK"]["S"] == "IMAGE#test_image_123"
    assert item["SK"]["S"] == "RECEIPT#00456#ANALYSIS#LABELS"
    assert item["GSI1PK"]["S"] == "ANALYSIS_TYPE"
    assert item["GSI1SK"]["S"].startswith("LABELS#")
    assert item["GSI2PK"]["S"] == "RECEIPT"
    assert item["GSI2SK"]["S"] == "IMAGE#test_image_123#RECEIPT#00456"

    # Check attributes
    assert item["TYPE"]["S"] == "RECEIPT_LABEL_ANALYSIS"
    assert len(item["labels"]["L"]) == 2

    # Check first label
    first_label = item["labels"]["L"][0]["M"]
    assert first_label["label_type"]["S"] == "business_name"
    assert first_label["line_id"]["N"] == "1"
    assert first_label["word_id"]["N"] == "2"
    assert first_label["text"]["S"] == "ACME"
    assert (
        first_label["reasoning"]["S"]
        == "This word appears to be a business name at the top of the receipt"
    )

    # Check bounding box
    bounding_box = first_label["bounding_box"]["M"]
    assert bounding_box["top_left"]["M"]["x"]["N"] == "10"
    assert bounding_box["top_left"]["M"]["y"]["N"] == "10"

    # Check other attributes
    assert (
        item["timestamp_added"]["S"]
        == example_receipt_label_analysis.timestamp_added
    )
    assert item["version"]["S"] == "1.2"
    assert (
        item["overall_reasoning"]["S"]
        == "Receipt contains standard business information and transaction details"
    )
    assert "metadata" in item

    # Can deserialize the metadata JSON
    import json

    metadata = json.loads(item["metadata"]["S"])
    assert "processing_metrics" in metadata
    assert "processing_history" in metadata
    assert "source_information" in metadata


@pytest.mark.unit
def test_convert_bounding_box(example_receipt_label_analysis):
    """Test that _convert_bounding_box() properly converts a bounding box to DynamoDB format."""
    bbox = {
        "top_left": {"x": 10, "y": 10},
        "top_right": {"x": 50, "y": 10},
        "bottom_left": {"x": 10, "y": 20},
        "bottom_right": {"x": 50, "y": 20},
    }

    result = example_receipt_label_analysis._convert_bounding_box(bbox)

    assert "top_left" in result
    assert "top_right" in result
    assert "bottom_left" in result
    assert "bottom_right" in result

    assert result["top_left"]["M"]["x"]["N"] == "10"
    assert result["top_left"]["M"]["y"]["N"] == "10"
    assert result["top_right"]["M"]["x"]["N"] == "50"
    assert result["top_right"]["M"]["y"]["N"] == "10"


@pytest.mark.unit
def test_convert_bounding_box_empty():
    """Test that _convert_bounding_box() handles empty bounding box."""
    analysis = ReceiptLabelAnalysis(
        image_id="test_image_123",
        receipt_id=456,
        labels=[],
        timestamp_added=datetime.now(),
    )

    result = analysis._convert_bounding_box({})
    assert result == {}

    result = analysis._convert_bounding_box(None)
    assert result == {}


@pytest.mark.unit
def test_receipt_label_analysis_repr(example_receipt_label_analysis):
    """Test that __repr__() returns the expected string representation."""
    repr_str = repr(example_receipt_label_analysis)

    assert "ReceiptLabelAnalysis" in repr_str
    assert "image_id=test_image_123" in repr_str
    assert "receipt_id=456" in repr_str
    assert "labels_count=2" in repr_str
    assert "version=1.2" in repr_str


@pytest.mark.unit
def test_receipt_label_analysis_iter(example_receipt_label_analysis):
    """Test that __iter__() returns all expected attributes."""
    items = dict(example_receipt_label_analysis)

    assert items["image_id"] == "test_image_123"
    assert items["receipt_id"] == 456
    assert len(items["labels"]) == 2
    assert isinstance(items["timestamp_added"], str)
    assert items["version"] == "1.2"
    assert (
        items["overall_reasoning"]
        == "Receipt contains standard business information and transaction details"
    )
    assert "processing_metrics" in items["metadata"]


@pytest.mark.unit
def test_receipt_label_analysis_eq(example_receipt_label_analysis):
    """Test that __eq__() correctly compares two ReceiptLabelAnalysis objects."""
    # Create a copy with the same values
    same_analysis = ReceiptLabelAnalysis(
        image_id="test_image_123",
        receipt_id=456,
        labels=example_receipt_label_analysis.labels,
        timestamp_added=datetime.fromisoformat(
            example_receipt_label_analysis.timestamp_added
        ),
        version="1.2",
        overall_reasoning="Receipt contains standard business information and transaction details",
        metadata=example_receipt_label_analysis.metadata,
    )

    # Create a different analysis
    different_analysis = ReceiptLabelAnalysis(
        image_id="different_image",
        receipt_id=789,
        labels=[],
        timestamp_added=datetime.now(),
    )

    assert example_receipt_label_analysis == same_analysis
    assert example_receipt_label_analysis != different_analysis
    assert example_receipt_label_analysis != "not an analysis object"


@pytest.mark.unit
def test_receipt_label_analysis_hash(example_receipt_label_analysis):
    """Test that __hash__() works correctly."""
    # Just make sure it doesn't throw an exception
    hash_value = hash(example_receipt_label_analysis)
    assert isinstance(hash_value, int)


@pytest.mark.unit
def test_itemToReceiptLabelAnalysis_valid_input():
    """Test that item_to_receipt_label_analysis works with valid input."""
    # Create a DynamoDB item
    now = datetime.now()
    now_str = now.isoformat()

    item = {
        "PK": {"S": "IMAGE#test_image_123"},
        "SK": {"S": "RECEIPT#456#ANALYSIS#LABELS#1.0"},
        "labels": {
            "L": [
                {
                    "M": {
                        "label_type": {"S": "business_name"},
                        "line_id": {"N": "1"},
                        "word_id": {"N": "2"},
                        "text": {"S": "ACME"},
                        "reasoning": {"S": "This is a business name"},
                        "bounding_box": {
                            "M": {
                                "top_left": {
                                    "M": {"x": {"N": "10"}, "y": {"N": "10"}}
                                },
                                "top_right": {
                                    "M": {"x": {"N": "50"}, "y": {"N": "10"}}
                                },
                                "bottom_left": {
                                    "M": {"x": {"N": "10"}, "y": {"N": "20"}}
                                },
                                "bottom_right": {
                                    "M": {"x": {"N": "50"}, "y": {"N": "20"}}
                                },
                            }
                        },
                    }
                }
            ]
        },
        "timestamp_added": {"S": now_str},
        "version": {"S": "1.0"},
        "overall_reasoning": {"S": "Analysis complete"},
        "metadata": {
            "S": '{"processing_metrics": {"processing_time_ms": 100}}'
        },
    }

    # Convert to ReceiptLabelAnalysis
    result = item_to_receipt_label_analysis(item)

    # Verify conversion
    assert result.image_id == "test_image_123"
    assert result.receipt_id == 456
    assert len(result.labels) == 1
    assert result.labels[0]["label_type"] == "business_name"
    assert result.labels[0]["line_id"] == 1
    assert result.labels[0]["word_id"] == 2
    assert result.timestamp_added == now_str
    assert result.version == "1.0"
    assert result.overall_reasoning == "Analysis complete"
    assert result.metadata["processing_metrics"]["processing_time_ms"] == 100


@pytest.mark.unit
def test_itemToReceiptLabelAnalysis_missing_keys():
    """Test that item_to_receipt_label_analysis raises ValueError with missing keys."""
    with pytest.raises(
        ValueError, match="Item must have PK and SK attributes"
    ):
        item_to_receipt_label_analysis({})

    with pytest.raises(
        ValueError, match="Item must have PK and SK attributes"
    ):
        item_to_receipt_label_analysis({"PK": {"S": "IMAGE#test"}})


@pytest.mark.unit
def test_itemToReceiptLabelAnalysis_invalid_format():
    """Test that item_to_receipt_label_analysis raises ValueError with invalid SK format."""
    item = {"PK": {"S": "IMAGE#test_image_123"}, "SK": {"S": "INVALID_FORMAT"}}

    with pytest.raises(
        ValueError, match="Invalid SK format for ReceiptLabelAnalysis"
    ):
        item_to_receipt_label_analysis(item)


@pytest.mark.unit
def test_itemToReceiptLabelAnalysis_with_defaults():
    """Test that item_to_receipt_label_analysis provides default values."""
    item = {
        "PK": {"S": "IMAGE#test_image_123"},
        "SK": {"S": "RECEIPT#456#ANALYSIS#LABELS#1.0"},
    }

    result = item_to_receipt_label_analysis(item)

    assert result.image_id == "test_image_123"
    assert result.receipt_id == 456
    assert result.labels == []
    assert result.version == "1.0"
    assert result.overall_reasoning == ""
    assert "processing_metrics" in result.metadata
    assert "processing_history" in result.metadata
    assert "source_information" in result.metadata
    assert result.metadata["processing_history"][0]["event_type"] == "creation"
