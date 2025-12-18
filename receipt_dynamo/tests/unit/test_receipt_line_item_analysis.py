import uuid
from datetime import datetime
from decimal import Decimal

import pytest

from receipt_dynamo.entities.receipt_line_item_analysis import (
    ReceiptLineItemAnalysis,
    item_to_receipt_line_item_analysis,
)


@pytest.fixture
def example_receipt_line_item_analysis():
    """Provides a sample ReceiptLineItemAnalysis for testing."""
    return ReceiptLineItemAnalysis(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=1,
        timestamp_added="2021-01-01T00:00:00",
        items=[
            {
                "description": "Test Item 1",
                "quantity": {"amount": Decimal("1"), "unit": "each"},
                "price": {
                    "unit_price": Decimal("10.99"),
                    "extended_price": Decimal("10.99"),
                },
                "reasoning": "This is a test item",
                "line_ids": [1, 2],
            },
            {
                "description": "Test Item 2",
                "quantity": {"amount": Decimal("2"), "unit": "each"},
                "price": {
                    "unit_price": Decimal("5.99"),
                    "extended_price": Decimal("11.98"),
                },
                "reasoning": "This is another test item",
                "line_ids": [3, 4],
            },
        ],
        reasoning="This is a test analysis",
        version="1.0",
        subtotal="22.97",
        tax="2.30",
        total="25.27",
        fees="0.00",
        discounts="0.00",
        tips="0.00",
        word_labels={(1, 1): {"label": "item_description", "confidence": 0.95}},
        timestamp_updated="2021-01-02T00:00:00",
    )


@pytest.fixture
def example_receipt_line_item_analysis_minimal():
    """Provides a minimal ReceiptLineItemAnalysis for testing."""
    return ReceiptLineItemAnalysis(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=1,
        timestamp_added="2021-01-01T00:00:00",
        items=[
            {
                "description": "Test Item 1",
                "line_ids": [1],
            },
        ],
        reasoning="This is a test analysis",
        version="1.0",
    )


@pytest.mark.unit
def test_receipt_line_item_analysis_init_valid(
    example_receipt_line_item_analysis,
):
    """Test the ReceiptLineItemAnalysis constructor with valid parameters."""
    assert (
        example_receipt_line_item_analysis.image_id
        == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    )
    assert example_receipt_line_item_analysis.receipt_id == 1
    assert example_receipt_line_item_analysis.timestamp_added == "2021-01-01T00:00:00"
    assert example_receipt_line_item_analysis.timestamp_updated == "2021-01-02T00:00:00"
    assert len(example_receipt_line_item_analysis.items) == 2
    assert example_receipt_line_item_analysis.reasoning == "This is a test analysis"
    assert example_receipt_line_item_analysis.version == "1.0"
    assert example_receipt_line_item_analysis.subtotal == Decimal("22.97")
    assert example_receipt_line_item_analysis.tax == Decimal("2.30")
    assert example_receipt_line_item_analysis.total == Decimal("25.27")
    assert example_receipt_line_item_analysis.fees == Decimal("0.00")
    assert example_receipt_line_item_analysis.discounts == Decimal("0.00")
    assert example_receipt_line_item_analysis.tips == Decimal("0.00")
    assert example_receipt_line_item_analysis.total_found == 2
    assert example_receipt_line_item_analysis.discrepancies == []
    assert (1, 1) in example_receipt_line_item_analysis.word_labels
    assert (
        example_receipt_line_item_analysis.word_labels[(1, 1)]["label"]
        == "item_description"
    )


@pytest.mark.unit
def test_receipt_line_item_analysis_init_minimal(
    example_receipt_line_item_analysis_minimal,
):
    """Test the ReceiptLineItemAnalysis constructor with minimal parameters."""
    assert (
        example_receipt_line_item_analysis_minimal.image_id
        == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    )
    assert example_receipt_line_item_analysis_minimal.receipt_id == 1
    assert (
        example_receipt_line_item_analysis_minimal.timestamp_added
        == "2021-01-01T00:00:00"
    )
    assert example_receipt_line_item_analysis_minimal.timestamp_updated is None
    assert len(example_receipt_line_item_analysis_minimal.items) == 1
    assert (
        example_receipt_line_item_analysis_minimal.reasoning
        == "This is a test analysis"
    )
    assert example_receipt_line_item_analysis_minimal.version == "1.0"
    assert example_receipt_line_item_analysis_minimal.subtotal is None
    assert example_receipt_line_item_analysis_minimal.tax is None
    assert example_receipt_line_item_analysis_minimal.total is None
    assert example_receipt_line_item_analysis_minimal.fees is None
    assert example_receipt_line_item_analysis_minimal.discounts is None
    assert example_receipt_line_item_analysis_minimal.tips is None
    assert example_receipt_line_item_analysis_minimal.total_found == 1
    assert example_receipt_line_item_analysis_minimal.discrepancies == []
    assert example_receipt_line_item_analysis_minimal.word_labels == {}


@pytest.mark.unit
def test_receipt_line_item_analysis_init_invalid_image_id():
    """Test ReceiptLineItemAnalysis constructor with invalid image_id."""
    with pytest.raises(ValueError, match="uuid must be a string"):
        ReceiptLineItemAnalysis(
            image_id=123,
            receipt_id=1,
            timestamp_added="2021-01-01T00:00:00",
            items=[],
            reasoning="This is a test analysis",
            version="1.0",
        )

    with pytest.raises(ValueError, match="uuid must be a valid UUID"):
        ReceiptLineItemAnalysis(
            image_id="not-a-uuid",
            receipt_id=1,
            timestamp_added="2021-01-01T00:00:00",
            items=[],
            reasoning="This is a test analysis",
            version="1.0",
        )


@pytest.mark.unit
def test_receipt_line_item_analysis_init_invalid_receipt_id():
    """Test ReceiptLineItemAnalysis constructor with invalid receipt_id."""
    with pytest.raises(ValueError, match="receipt_id must be an integer"):
        ReceiptLineItemAnalysis(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id="not-an-integer",
            timestamp_added="2021-01-01T00:00:00",
            items=[],
            reasoning="This is a test analysis",
            version="1.0",
        )

    with pytest.raises(ValueError, match="receipt_id must be positive"):
        ReceiptLineItemAnalysis(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=0,
            timestamp_added="2021-01-01T00:00:00",
            items=[],
            reasoning="This is a test analysis",
            version="1.0",
        )

    with pytest.raises(ValueError, match="receipt_id must be positive"):
        ReceiptLineItemAnalysis(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=-1,
            timestamp_added="2021-01-01T00:00:00",
            items=[],
            reasoning="This is a test analysis",
            version="1.0",
        )


@pytest.mark.unit
def test_receipt_line_item_analysis_init_invalid_timestamp():
    """Test ReceiptLineItemAnalysis constructor with invalid timestamp."""
    with pytest.raises(
        ValueError,
        match="timestamp_added must be a datetime object or a string",
    ):
        ReceiptLineItemAnalysis(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            timestamp_added=123,
            items=[],
            reasoning="This is a test analysis",
            version="1.0",
        )


@pytest.mark.unit
def test_receipt_line_item_analysis_init_invalid_items():
    """Test ReceiptLineItemAnalysis constructor with invalid items."""
    with pytest.raises(ValueError, match="items must be a list"):
        ReceiptLineItemAnalysis(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            timestamp_added="2021-01-01T00:00:00",
            items="not-a-list",
            reasoning="This is a test analysis",
            version="1.0",
        )


@pytest.mark.unit
def test_receipt_line_item_analysis_init_invalid_reasoning():
    """Test ReceiptLineItemAnalysis constructor with invalid reasoning."""
    with pytest.raises(ValueError, match="reasoning must be a string"):
        ReceiptLineItemAnalysis(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            timestamp_added="2021-01-01T00:00:00",
            items=[],
            reasoning=123,
            version="1.0",
        )


@pytest.mark.unit
def test_receipt_line_item_analysis_init_invalid_version():
    """Test ReceiptLineItemAnalysis constructor with invalid version."""
    with pytest.raises(ValueError, match="version must be a string"):
        ReceiptLineItemAnalysis(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            timestamp_added="2021-01-01T00:00:00",
            items=[],
            reasoning="This is a test analysis",
            version=123,
        )


@pytest.mark.unit
def test_receipt_line_item_analysis_init_invalid_subtotal():
    """Test ReceiptLineItemAnalysis constructor with invalid subtotal."""
    with pytest.raises(ValueError, match="subtotal must be a Decimal, string, or None"):
        ReceiptLineItemAnalysis(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            timestamp_added="2021-01-01T00:00:00",
            items=[],
            reasoning="This is a test analysis",
            version="1.0",
            subtotal=[],
        )


@pytest.mark.unit
def test_receipt_line_item_analysis_init_invalid_tax():
    """Test ReceiptLineItemAnalysis constructor with invalid tax."""
    with pytest.raises(ValueError, match="tax must be a Decimal, string, or None"):
        ReceiptLineItemAnalysis(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            timestamp_added="2021-01-01T00:00:00",
            items=[],
            reasoning="This is a test analysis",
            version="1.0",
            tax=[],
        )


@pytest.mark.unit
def test_receipt_line_item_analysis_key(example_receipt_line_item_analysis):
    """Test ReceiptLineItemAnalysis.key method."""
    expected = {
        "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": "RECEIPT#00001#ANALYSIS#LINE_ITEMS"},
    }
    assert example_receipt_line_item_analysis.key == expected


@pytest.mark.unit
def test_receipt_line_item_analysis_gsi1_key(
    example_receipt_line_item_analysis,
):
    """Test ReceiptLineItemAnalysis.gsi1_key() method."""
    expected = {
        "GSI1PK": {"S": "ANALYSIS_TYPE"},
        "GSI1SK": {"S": "LINE_ITEMS#2021-01-01T00:00:00"},
    }
    assert example_receipt_line_item_analysis.gsi1_key() == expected


@pytest.mark.unit
def test_receipt_line_item_analysis_gsi2_key(
    example_receipt_line_item_analysis,
):
    """Test ReceiptLineItemAnalysis.gsi2_key() method."""
    expected = {
        "GSI2PK": {"S": "RECEIPT"},
        "GSI2SK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3#RECEIPT#00001"},
    }
    assert example_receipt_line_item_analysis.gsi2_key() == expected


@pytest.mark.unit
def test_receipt_line_item_analysis_to_item(
    example_receipt_line_item_analysis,
):
    """Test ReceiptLineItemAnalysis.to_item() method."""
    item = example_receipt_line_item_analysis.to_item()

    # Check basic attributes
    assert item["PK"]["S"] == "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert item["SK"]["S"] == "RECEIPT#00001#ANALYSIS#LINE_ITEMS"
    assert item["GSI1PK"]["S"] == "ANALYSIS_TYPE"
    assert item["GSI1SK"]["S"] == "LINE_ITEMS#2021-01-01T00:00:00"
    assert item["GSI2PK"]["S"] == "RECEIPT"
    assert (
        item["GSI2SK"]["S"]
        == "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3#RECEIPT#00001"
    )
    assert item["timestamp_updated"]["S"] == "2021-01-02T00:00:00"
    assert item["version"]["S"] == "1.0"
    assert item["reasoning"]["S"] == "This is a test analysis"
    assert item["total_found"]["N"] == "2"

    # Check financial fields
    assert item["subtotal"]["S"] == "22.97"
    assert item["tax"]["S"] == "2.30"
    assert item["total"]["S"] == "25.27"
    assert item["fees"]["S"] == "0.00"
    assert item["discounts"]["S"] == "0.00"
    assert item["tips"]["S"] == "0.00"

    # Check items list
    assert len(item["items"]["L"]) == 2

    # Check word_labels
    assert "word_labels" in item
    assert "1:1" in item["word_labels"]["M"]
    assert item["word_labels"]["M"]["1:1"]["M"]["label"]["S"] == "item_description"


@pytest.mark.unit
def test_receipt_line_item_analysis_to_item_minimal(
    example_receipt_line_item_analysis_minimal,
):
    """Test ReceiptLineItemAnalysis.to_item() method with minimal object."""
    item = example_receipt_line_item_analysis_minimal.to_item()

    # Check basic attributes
    assert item["PK"]["S"] == "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert item["SK"]["S"] == "RECEIPT#00001#ANALYSIS#LINE_ITEMS"
    assert item["version"]["S"] == "1.0"
    assert item["reasoning"]["S"] == "This is a test analysis"
    assert item["total_found"]["N"] == "1"

    # Check optional fields
    assert item["subtotal"]["NULL"] is True
    assert item["tax"]["NULL"] is True
    assert item["total"]["NULL"] is True
    assert item["fees"]["NULL"] is True
    assert item["discounts"]["NULL"] is True
    assert item["tips"]["NULL"] is True
    assert "timestamp_updated" not in item

    # Check items list
    assert len(item["items"]["L"]) == 1
    assert item["items"]["L"][0]["M"]["description"]["S"] == "Test Item 1"

    # Check word_labels
    assert item["word_labels"]["NULL"] is True


@pytest.mark.unit
def test_receipt_line_item_analysis_metadata_methods(
    example_receipt_line_item_analysis,
):
    """Test add_processing_metric and add_history_event methods."""
    # Test add_processing_metric
    example_receipt_line_item_analysis.add_processing_metric("test_metric", 42)
    assert (
        example_receipt_line_item_analysis.metadata["processing_metrics"]["test_metric"]
        == 42
    )

    # Test add_history_event
    example_receipt_line_item_analysis.add_history_event(
        "test_event", {"detail": "This is a test event"}
    )
    latest_event = example_receipt_line_item_analysis.metadata["processing_history"][-1]
    assert latest_event["event_type"] == "test_event"
    assert latest_event["detail"] == "This is a test event"
    assert "timestamp" in latest_event


@pytest.mark.unit
def test_receipt_line_item_analysis_generate_reasoning(
    example_receipt_line_item_analysis,
):
    """Test generate_reasoning method."""
    reasoning = example_receipt_line_item_analysis.generate_reasoning()

    assert "Analyzed 2 line items from the receipt" in reasoning
    assert "Subtotal: $22.97" in reasoning
    assert "Tax: $2.30" in reasoning
    assert "Total: $25.27" in reasoning


@pytest.mark.unit
def test_receipt_line_item_analysis_get_item_by_description(
    example_receipt_line_item_analysis,
):
    """Test get_item_by_description method."""
    item = example_receipt_line_item_analysis.get_item_by_description("Test Item 1")
    assert item is not None
    assert item["description"] == "Test Item 1"
    assert item["quantity"]["amount"] == Decimal("1")

    # Test case insensitive
    item = example_receipt_line_item_analysis.get_item_by_description("test item 1")
    assert item is not None
    assert item["description"] == "Test Item 1"

    # Test not found
    item = example_receipt_line_item_analysis.get_item_by_description(
        "Non-existent Item"
    )
    assert item is None


@pytest.mark.unit
def test_receipt_line_item_analysis_repr(example_receipt_line_item_analysis):
    """Test ReceiptLineItemAnalysis.__repr__() method."""
    repr_str = repr(example_receipt_line_item_analysis)

    assert "ReceiptLineItemAnalysis" in repr_str
    assert "image_id='3f52804b-2fad-4e00-92c8-b593da3a8ed3'" in repr_str
    assert "receipt_id=1" in repr_str
    assert "items=[2 items]" in repr_str
    assert "subtotal=22.97" in repr_str
    assert "tax=2.30" in repr_str
    assert "total=25.27" in repr_str


@pytest.mark.unit
def test_receipt_line_item_analysis_iter(example_receipt_line_item_analysis):
    """Test ReceiptLineItemAnalysis.__iter__() method."""
    item_dict = dict(example_receipt_line_item_analysis)

    assert item_dict["image_id"] == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert item_dict["receipt_id"] == 1
    assert item_dict["timestamp_added"] == "2021-01-01T00:00:00"
    assert item_dict["timestamp_updated"] == "2021-01-02T00:00:00"
    assert len(item_dict["items"]) == 2
    assert item_dict["reasoning"] == "This is a test analysis"
    assert item_dict["version"] == "1.0"
    assert item_dict["subtotal"] == Decimal("22.97")
    assert item_dict["tax"] == Decimal("2.30")
    assert item_dict["total"] == Decimal("25.27")
    assert item_dict["fees"] == Decimal("0.00")
    assert item_dict["discounts"] == Decimal("0.00")
    assert item_dict["tips"] == Decimal("0.00")
    assert item_dict["total_found"] == 2
    assert item_dict["discrepancies"] == []
    assert (1, 1) in item_dict["word_labels"]


@pytest.mark.unit
def test_receipt_line_item_analysis_eq():
    """Test ReceiptLineItemAnalysis.__eq__() method."""
    # Create two identical objects
    rlia1 = ReceiptLineItemAnalysis(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=1,
        timestamp_added="2021-01-01T00:00:00",
        items=[{"description": "Test Item"}],
        reasoning="Test reasoning",
        version="1.0",
    )

    rlia2 = ReceiptLineItemAnalysis(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=1,
        timestamp_added="2021-01-01T00:00:00",
        items=[{"description": "Test Item"}],
        reasoning="Test reasoning",
        version="1.0",
    )

    # Create another object with a different value
    rlia3 = ReceiptLineItemAnalysis(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=1,
        timestamp_added="2021-01-01T00:00:00",
        items=[{"description": "Different Item"}],
        reasoning="Test reasoning",
        version="1.0",
    )

    assert rlia1 == rlia2
    assert rlia1 != rlia3
    assert rlia1 != "not an analysis object"


@pytest.mark.unit
def test_receipt_line_item_analysis_hash(example_receipt_line_item_analysis):
    """Test ReceiptLineItemAnalysis.__hash__() method."""
    # Test that hash is stable for the same object
    hash1 = hash(example_receipt_line_item_analysis)
    hash2 = hash(example_receipt_line_item_analysis)
    assert hash1 == hash2

    # Test that different objects have different hashes
    different_analysis = ReceiptLineItemAnalysis(
        image_id=str(uuid.uuid4()),
        receipt_id=2,
        timestamp_added="2021-01-01T00:00:00",
        items=[],
        reasoning="Different reasoning",
        version="1.0",
    )

    assert hash(example_receipt_line_item_analysis) != hash(different_analysis)


@pytest.mark.unit
def test_itemToReceiptLineItemAnalysis(example_receipt_line_item_analysis):
    """Test item_to_receipt_line_item_analysis() function."""
    # Convert to item and back
    item = example_receipt_line_item_analysis.to_item()
    reconstructed = item_to_receipt_line_item_analysis(item)

    # Check equality
    assert reconstructed.image_id == example_receipt_line_item_analysis.image_id
    assert reconstructed.receipt_id == example_receipt_line_item_analysis.receipt_id
    assert (
        reconstructed.timestamp_added
        == example_receipt_line_item_analysis.timestamp_added
    )
    assert (
        reconstructed.timestamp_updated
        == example_receipt_line_item_analysis.timestamp_updated
    )
    assert reconstructed.version == example_receipt_line_item_analysis.version
    assert reconstructed.reasoning == example_receipt_line_item_analysis.reasoning
    assert reconstructed.total_found == example_receipt_line_item_analysis.total_found
    assert reconstructed.subtotal == example_receipt_line_item_analysis.subtotal
    assert reconstructed.tax == example_receipt_line_item_analysis.tax
    assert reconstructed.total == example_receipt_line_item_analysis.total

    # Check items
    assert len(reconstructed.items) == len(example_receipt_line_item_analysis.items)
    assert (
        reconstructed.items[0]["description"]
        == example_receipt_line_item_analysis.items[0]["description"]
    )

    # Check that word_labels are reconstructed correctly
    assert (1, 1) in reconstructed.word_labels
    assert reconstructed.word_labels[(1, 1)]["label"] == "item_description"


@pytest.mark.unit
def test_itemToReceiptLineItemAnalysis_invalid():
    """Test item_to_receipt_line_item_analysis() with invalid inputs."""
    # Test missing required keys
    with pytest.raises(ValueError, match="Invalid item format"):
        item_to_receipt_line_item_analysis(
            {
                "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
                "SK": {"S": "RECEIPT#00001#ANALYSIS#LINE_ITEMS"},
            }
        )

    # Test invalid item format
    with pytest.raises(
        ValueError, match="Error converting item to ReceiptLineItemAnalysis"
    ):
        item_to_receipt_line_item_analysis(
            {
                "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
                "SK": {"S": "RECEIPT#00001#ANALYSIS#LINE_ITEMS"},
                "TYPE": {"S": "RECEIPT_LINE_ITEM_ANALYSIS"},
                "items": {"S": "not a list"},  # Wrong format
                "reasoning": {"S": "Test reasoning"},
                "timestamp_added": {"S": "2021-01-01T00:00:00"},
                "version": {"S": "1.0"},
                "total_found": {"N": "1"},
            }
        )


@pytest.mark.unit
def test_convert_item_to_dynamo():
    """Test _convert_item_to_dynamo internal method."""
    analysis = ReceiptLineItemAnalysis(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=1,
        timestamp_added="2021-01-01T00:00:00",
        items=[],
        reasoning="Test reasoning",
        version="1.0",
    )

    # Test with full item
    item = {
        "description": "Test Item",
        "quantity": {"amount": Decimal("1"), "unit": "each"},
        "price": {
            "unit_price": Decimal("10.99"),
            "extended_price": Decimal("10.99"),
        },
        "reasoning": "Test item reasoning",
        "line_ids": [1, 2],
        "metadata": {"source": "test"},
    }

    dynamo_item = analysis._convert_item_to_dynamo(item)

    assert dynamo_item["M"]["description"]["S"] == "Test Item"
    assert dynamo_item["M"]["reasoning"]["S"] == "Test item reasoning"
    assert len(dynamo_item["M"]["line_ids"]["L"]) == 2
    assert dynamo_item["M"]["quantity"]["M"]["amount"]["S"] == "1"
    assert dynamo_item["M"]["quantity"]["M"]["unit"]["S"] == "each"
    assert dynamo_item["M"]["price"]["M"]["unit_price"]["S"] == "10.99"
    assert dynamo_item["M"]["price"]["M"]["extended_price"]["S"] == "10.99"
    assert dynamo_item["M"]["metadata"]["M"]["source"]["S"] == "test"

    # Test with minimal item
    item = {"description": "Test Item"}

    dynamo_item = analysis._convert_item_to_dynamo(item)

    assert dynamo_item["M"]["description"]["S"] == "Test Item"
    assert "reasoning" not in dynamo_item["M"]
    assert "line_ids" not in dynamo_item["M"]
    assert "quantity" not in dynamo_item["M"]
    assert "price" not in dynamo_item["M"]


@pytest.mark.unit
def test_convert_dict_to_dynamo():
    """Test _convert_dict_to_dynamo internal method."""
    analysis = ReceiptLineItemAnalysis(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=1,
        timestamp_added="2021-01-01T00:00:00",
        items=[],
        reasoning="Test reasoning",
        version="1.0",
    )

    # Test with various data types
    test_dict = {
        "string": "test",
        "integer": 42,
        "decimal": Decimal("10.99"),
        "none": None,
        "list": ["a", "b", "c"],
        "nested_dict": {"key1": "value1", "key2": 123},
    }

    dynamo_dict = analysis._convert_dict_to_dynamo(test_dict)

    assert dynamo_dict["string"]["S"] == "test"
    assert dynamo_dict["integer"]["N"] == "42"
    assert dynamo_dict["decimal"]["N"] == "10.99"
    assert dynamo_dict["none"]["NULL"] is True
    assert len(dynamo_dict["list"]["L"]) == 3
    assert dynamo_dict["list"]["L"][0]["S"] == "a"
    assert dynamo_dict["nested_dict"]["M"]["key1"]["S"] == "value1"
    assert dynamo_dict["nested_dict"]["M"]["key2"]["N"] == "123"
