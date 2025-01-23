import pytest
from datetime import datetime
from decimal import Decimal
from dynamo import Receipt, itemToReceipt


@pytest.fixture
def valid_receipt_args():
    """Provides a dictionary of valid constructor arguments for Receipt."""
    now = datetime.now()
    return {
        "image_id": 1,
        "id": 1,
        "width": 200,
        "height": 100,
        "timestamp_added": now,
        "raw_s3_bucket": "test-bucket",
        "raw_s3_key": "test/key/receipt.jpg",
        "top_left": {"x": 0.0, "y": 0.0},
        "top_right": {"x": 200.0, "y": 0.0},
        "bottom_left": {"x": 0.0, "y": 100.0},
        "bottom_right": {"x": 200.0, "y": 100.0},
        "sha256": "abc123",
    }


def test_receipt_construction_valid(valid_receipt_args):
    """Test constructing a valid Receipt."""
    r = Receipt(**valid_receipt_args)

    assert r.image_id == 1
    assert r.id == 1
    assert r.width == 200
    assert r.height == 100

    # Check that the date in the string matches today's date
    now = valid_receipt_args["timestamp_added"]
    assert r.timestamp_added.startswith(str(now.date()))

    assert r.raw_s3_bucket == "test-bucket"
    assert r.raw_s3_key == "test/key/receipt.jpg"
    assert r.top_left == {"x": 0.0, "y": 0.0}
    assert r.top_right == {"x": 200.0, "y": 0.0}
    assert r.bottom_left == {"x": 0.0, "y": 100.0}
    assert r.bottom_right == {"x": 200.0, "y": 100.0}
    assert r.sha256 == "abc123"


def test_receipt_construction_with_string_timestamp(valid_receipt_args):
    """Test constructing a Receipt with a string timestamp."""
    args = valid_receipt_args.copy()
    args["timestamp_added"] = "2023-01-01T12:34:56"
    r = Receipt(**args)
    assert r.timestamp_added == "2023-01-01T12:34:56"


def test_receipt_invalid_image_id(valid_receipt_args):
    """Test that constructing a Receipt with invalid image_id raises ValueError."""
    args = valid_receipt_args.copy()
    args["image_id"] = 0  # Invalid
    with pytest.raises(ValueError):
        Receipt(**args)


def test_receipt_invalid_id(valid_receipt_args):
    """Test that constructing a Receipt with invalid id raises ValueError."""
    args = valid_receipt_args.copy()
    args["id"] = -5  # Invalid
    with pytest.raises(ValueError):
        Receipt(**args)


def test_receipt_invalid_dimensions(valid_receipt_args):
    """Test that constructing a Receipt with invalid width/height raises ValueError."""
    # Invalid width
    args = valid_receipt_args.copy()
    args["width"] = -10
    with pytest.raises(ValueError):
        Receipt(**args)

    # Invalid height
    args = valid_receipt_args.copy()
    args["height"] = 0
    with pytest.raises(ValueError):
        Receipt(**args)


def test_receipt_invalid_timestamp(valid_receipt_args):
    """Test that constructing a Receipt with an invalid timestamp raises ValueError."""
    args = valid_receipt_args.copy()
    args["timestamp_added"] = 123  # Neither datetime nor string
    with pytest.raises(ValueError):
        Receipt(**args)


def test_receipt_invalid_point_types(valid_receipt_args):
    """Test that constructing a Receipt with invalid point data raises ValueError."""
    args = valid_receipt_args.copy()
    args["top_left"] = {"x": "not_a_float", "y": 0.0}
    with pytest.raises(ValueError):
        Receipt(**args)


def test_key_generation(valid_receipt_args):
    """Test that the primary key is correctly generated."""
    r = Receipt(**valid_receipt_args)
    expected_key = {
        "PK": {"S": "IMAGE#00001"},
        "SK": {"S": "RECEIPT#00001"},
    }
    assert r.key() == expected_key


def test_gsi1_key_generation(valid_receipt_args):
    """Test that the GSI1 key is correctly generated."""
    r = Receipt(**valid_receipt_args)
    expected_key = {
        "GSI1PK": {"S": "IMAGE"},
        "GSI1SK": {"S": "IMAGE#00001#RECEIPT#00001"},
    }
    assert r.gsi1_key() == expected_key


def test_to_item(valid_receipt_args):
    """Test converting a Receipt to a DynamoDB item."""
    r = Receipt(**valid_receipt_args)
    item = r.to_item()

    # Check keys
    assert "PK" in item
    assert "SK" in item
    assert "GSI1PK" in item
    assert "GSI1SK" in item
    assert "width" in item
    assert "height" in item
    assert "timestamp_added" in item
    assert "raw_s3_bucket" in item
    assert "raw_s3_key" in item
    assert "top_left" in item
    assert "top_right" in item
    assert "bottom_left" in item
    assert "bottom_right" in item
    assert "sha256" in item

    # Check numeric values
    assert item["width"]["N"] == "200"
    assert item["height"]["N"] == "100"

    # Check float formatting in topLeft x & y
    assert "N" in item["top_left"]["M"]["x"]
    assert "N" in item["top_left"]["M"]["y"]


def test_receipt_equality(valid_receipt_args):
    """Test that two Receipt objects with identical data are equal."""
    r1 = Receipt(**valid_receipt_args)
    r2 = Receipt(**valid_receipt_args)
    assert r1 == r2

    # Change a single field
    args = valid_receipt_args.copy()
    args["id"] = 2
    r3 = Receipt(**args)
    assert r1 != r3

    # Change the image ID
    r3 = Receipt(**{**valid_receipt_args, "image_id": 2})
    assert r1 != r3


def test_item_to_receipt_valid(valid_receipt_args):
    """Test itemToReceipt with a valid DynamoDB item."""
    r = Receipt(**valid_receipt_args)
    item = r.to_item()

    # Convert back from the item dict to a Receipt
    restored_receipt = itemToReceipt(item)
    assert restored_receipt == r


def test_item_to_receipt_missing_keys():
    """Test that itemToReceipt raises ValueError when required keys are missing."""
    incomplete_item = {
        "PK": {"S": "IMAGE#00001"},
        "SK": {"S": "RECEIPT#00001"},
        # Missing width, height, etc.
    }
    with pytest.raises(ValueError):
        itemToReceipt(incomplete_item)


def test_item_to_receipt_invalid_format(valid_receipt_args):
    """Test that itemToReceipt raises ValueError when keys are incorrectly formatted."""
    now = valid_receipt_args["timestamp_added"]
    invalid_item = {
        "PK": {"S": "IMAGE#00001"},
        "SK": {"S": "RECEIPT#00001"},
        "width": {"S": "200"},  # Should be {"N": "200"}
        "height": {"N": "100"},
        "timestamp_added": {"S": now.isoformat()},
        "s3_bucket": {"S": "test-bucket"},
        "s3_key": {"S": "test/key/receipt.jpg"},
        "topLeft": {"M": {"x": {"N": "0.0"}, "y": {"N": "0.0"}}},
        "topRight": {"M": {"x": {"N": "200.0"}, "y": {"N": "0.0"}}},
        "bottomLeft": {"M": {"x": {"N": "0.0"}, "y": {"N": "100.0"}}},
        "bottomRight": {"M": {"x": {"N": "200.0"}, "y": {"N": "100.0"}}},
    }
    with pytest.raises(ValueError):
        itemToReceipt(invalid_item)
