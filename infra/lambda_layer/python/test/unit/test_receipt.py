import pytest
from datetime import datetime
from decimal import Decimal
from dynamo import Receipt, itemToReceipt


@pytest.fixture
def example_receipt():
    return Receipt(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        id=1,
        width=200,
        height=100,
        timestamp_added="2021-01-01T00:00:00",
        raw_s3_bucket="test-bucket",
        raw_s3_key="test/key/receipt.jpg",
        top_left={"x": 0.0, "y": 0.0},
        top_right={"x": 200.0, "y": 0.0},
        bottom_left={"x": 0.0, "y": 100.0},
        bottom_right={"x": 200.0, "y": 100.0},
        sha256="abc123",
    )


def test_receipt_construction_valid(example_receipt):
    """Test constructing a valid Receipt."""
    assert example_receipt.image_id == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert example_receipt.id == 1
    assert example_receipt.width == 200
    assert example_receipt.height == 100
    assert example_receipt.timestamp_added == "2021-01-01T00:00:00"
    assert example_receipt.raw_s3_bucket == "test-bucket"
    assert example_receipt.raw_s3_key == "test/key/receipt.jpg"
    assert example_receipt.top_left == {"x": 0.0, "y": 0.0}
    assert example_receipt.top_right == {"x": 200.0, "y": 0.0}
    assert example_receipt.bottom_left == {"x": 0.0, "y": 100.0}
    assert example_receipt.bottom_right == {"x": 200.0, "y": 100.0}
    assert example_receipt.sha256 == "abc123"


def test_receipt_invalid_image_id():
    """Test that constructing a Receipt with invalid image_id raises ValueError."""
    with pytest.raises(ValueError, match="uuid must be a string"):
        Receipt(
            image_id=1,
            id=1,
            width=200,
            height=100,
            timestamp_added="2021-01-01T00:00:00",
            raw_s3_bucket="test-bucket",
            raw_s3_key="test/key/receipt.jpg",
            top_left={"x": 0.0, "y": 0.0},
            top_right={"x": 200.0, "y": 0.0},
            bottom_left={"x": 0.0, "y": 100.0},
            bottom_right={"x": 200.0, "y": 100.0},
            sha256="abc123",
        )


def test_receipt_invalid_id():
    """Test that constructing a Receipt with invalid id raises ValueError."""
    with pytest.raises(ValueError):
        Receipt(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        id=-1,
        width=200,
        height=100,
        timestamp_added="2021-01-01T00:00:00",
        raw_s3_bucket="test-bucket",
        raw_s3_key="test/key/receipt.jpg",
        top_left={"x": 0.0, "y": 0.0},
        top_right={"x": 200.0, "y": 0.0},
        bottom_left={"x": 0.0, "y": 100.0},
        bottom_right={"x": 200.0, "y": 100.0},
        sha256="abc123",
    )
    with pytest.raises(ValueError):
        Receipt(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        id='not-an-int',
        width=200,
        height=100,
        timestamp_added="2021-01-01T00:00:00",
        raw_s3_bucket="test-bucket",
        raw_s3_key="test/key/receipt.jpg",
        top_left={"x": 0.0, "y": 0.0},
        top_right={"x": 200.0, "y": 0.0},
        bottom_left={"x": 0.0, "y": 100.0},
        bottom_right={"x": 200.0, "y": 100.0},
        sha256="abc123",
    )


def test_receipt_invalid_dimensions(valid_receipt_args):
    """Test that constructing a Receipt with invalid width/height raises ValueError."""
    # Invalid width
    with pytest.raises(ValueError):
        Receipt(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            id=1,
            width=-200,
            height=100,
            timestamp_added="2021-01-01T00:00:00",
            raw_s3_bucket="test-bucket",
            raw_s3_key="test/key/receipt.jpg",
            top_left={"x": 0.0, "y": 0.0},
            top_right={"x": 200.0, "y": 0.0},
            bottom_left={"x": 0.0, "y": 100.0},
            bottom_right={"x": 200.0, "y": 100.0},
            sha256="abc123",
        )

    with pytest.raises(ValueError):
       Receipt(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            id=1,
            width=200,
            height=-100,
            timestamp_added="2021-01-01T00:00:00",
            raw_s3_bucket="test-bucket",
            raw_s3_key="test/key/receipt.jpg",
            top_left={"x": 0.0, "y": 0.0},
            top_right={"x": 200.0, "y": 0.0},
            bottom_left={"x": 0.0, "y": 100.0},
            bottom_right={"x": 200.0, "y": 100.0},
            sha256="abc123",
        )


def test_receipt_invalid_timestamp():
    """Test that constructing a Receipt with an invalid timestamp raises ValueError."""
    with pytest.raises(ValueError):
        Receipt(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            id=1,
            width=-200,
            height=100,
            timestamp_added=123,
            raw_s3_bucket="test-bucket",
            raw_s3_key="test/key/receipt.jpg",
            top_left={"x": 0.0, "y": 0.0},
            top_right={"x": 200.0, "y": 0.0},
            bottom_left={"x": 0.0, "y": 100.0},
            bottom_right={"x": 200.0, "y": 100.0},
            sha256="abc123",
        )


def test_receipt_invalid_point_types():
    """Test that constructing a Receipt with invalid point data raises ValueError."""
    with pytest.raises(ValueError):
        Receipt(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        id=1,
        width=200,
        height=100,
        timestamp_added="2021-01-01T00:00:00",
        raw_s3_bucket="test-bucket",
        raw_s3_key="test/key/receipt.jpg",
        top_left={"x": 'not-a-float', "y": 0.0},
        top_right={"x": 200.0, "y": 0.0},
        bottom_left={"x": 0.0, "y": 100.0},
        bottom_right={"x": 200.0, "y": 100.0},
        sha256="abc123",
    )


def test_key_generation(example_receipt):
    """Test that the primary key is correctly generated."""
    assert example_receipt.key() == {
        "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": "RECEIPT#00001"},
    }


def test_gsi1_key_generation(example_receipt):
    """Test that the GSI1 key is correctly generated."""
    assert example_receipt.gsi1_key() == {
        "GSI1PK": {"S": "IMAGE"},
        "GSI1SK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3#RECEIPT#00001"},
    }

def test_gsi2_key_generation(example_receipt):
    """Test that the GSI2 key is correctly generated."""
    assert example_receipt.gsi2_key() == {
        "GSI2PK": {"S": "RECEIPT"},
        "GSI2SK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3#RECEIPT#00001"},
    }


def test_to_item(example_receipt):
    """Test converting a Receipt to a DynamoDB item."""
    assert example_receipt.to_item() == {
        "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": "RECEIPT#00001"},
        "GSI1PK": {"S": "IMAGE"},
        "GSI1SK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3#RECEIPT#00001"},
        "GSI2PK": {"S": "RECEIPT"},
        "GSI2SK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3#RECEIPT#00001"},
        "TYPE": {"S": "RECEIPT"},
        "width": {"N": "200"},
        "height": {"N": "100"},
        "timestamp_added": {"S": "2021-01-01T00:00:00"},
        "raw_s3_bucket": {"S": "test-bucket"},
        "raw_s3_key": {"S": "test/key/receipt.jpg"},
        "top_right": {
            "M": {
                "x": {"N": "200.000000000000000000"},
                "y": {"N": "0.000000000000000000"},
            }
        },
        "top_left": {
            "M": {
                "x": {"N": "0.000000000000000000"},
                "y": {"N": "0.000000000000000000"},
            }
        },
        "bottom_right": {
            "M": {
                "x": {"N": "200.000000000000000000"},
                "y": {"N": "100.000000000000000000"},
            }
        },
        "bottom_left": {
            "M": {
                "x": {"N": "0.000000000000000000"},
                "y": {"N": "100.000000000000000000"},
            }
        },
        "sha256": {"S": "abc123"},
        "cdn_s3_bucket": {"NULL": True},
        "cdn_s3_key": {"NULL": True},
    }


def test_item_to_receipt_valid(example_receipt):
    """Test itemToReceipt with a valid DynamoDB item."""
    itemToReceipt(example_receipt.to_item()) == example_receipt


def test_item_to_receipt_missing_keys():
    """Test that itemToReceipt raises ValueError when required keys are missing."""
    incomplete_item = {
        "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": "RECEIPT#00001"},
        # Missing width, height, etc.
    }
    with pytest.raises(ValueError):
        itemToReceipt(incomplete_item)


def test_item_to_receipt_invalid_format():
    """Test that itemToReceipt raises ValueError when keys are incorrectly formatted."""
    invalid_item = {
        "PK": {"S": "IMAGE#00001"},
        "SK": {"S": "RECEIPT#00001"},
        "width": {"S": "200"},  # Should be {"N": "200"}
        "height": {"N": "100"},
        "s3_bucket": {"S": "test-bucket"},
        "s3_key": {"S": "test/key/receipt.jpg"},
        "topLeft": {"M": {"x": {"N": "0.0"}, "y": {"N": "0.0"}}},
        "topRight": {"M": {"x": {"N": "200.0"}, "y": {"N": "0.0"}}},
        "bottomLeft": {"M": {"x": {"N": "0.0"}, "y": {"N": "100.0"}}},
        "bottomRight": {"M": {"x": {"N": "200.0"}, "y": {"N": "100.0"}}},
    }
    with pytest.raises(ValueError):
        itemToReceipt(invalid_item)
