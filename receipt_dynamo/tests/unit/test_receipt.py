from datetime import datetime

import pytest

from receipt_dynamo import Receipt, item_to_receipt


@pytest.fixture
def example_receipt():
    return Receipt(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=1,
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


@pytest.mark.unit
def test_receipt_init_valid(example_receipt):
    """Test constructing a valid Receipt."""
    assert example_receipt.image_id == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert example_receipt.receipt_id == 1
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


@pytest.mark.unit
def test_receipt_init_invalid_image_id():
    """Receipt with invalid image_id raises ValueError."""
    with pytest.raises(ValueError, match="uuid must be a string"):
        Receipt(
            image_id=1,
            receipt_id=1,
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


@pytest.mark.unit
def test_receipt_init_invalid_id():
    """Test that constructing a Receipt with invalid id raises ValueError."""
    with pytest.raises(ValueError):
        Receipt(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=-1,
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
            receipt_id="not-an-int",
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


@pytest.mark.unit
def test_receipt_init_invalid_dimensions():
    """Receipt with invalid width/height raises ValueError."""
    # Invalid width
    with pytest.raises(ValueError):
        Receipt(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
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
            receipt_id=1,
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


@pytest.mark.unit
def test_receipt_init_valid_timestamp():
    """Test that constructing a Receipt with a valid timestamp works."""
    Receipt(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=1,
        width=200,
        height=100,
        timestamp_added=datetime.now(),
        raw_s3_bucket="test-bucket",
        raw_s3_key="test/key/receipt.jpg",
        top_left={"x": 0.0, "y": 0.0},
        top_right={"x": 200.0, "y": 0.0},
        bottom_left={"x": 0.0, "y": 100.0},
        bottom_right={"x": 200.0, "y": 100.0},
        sha256="abc123",
    )


@pytest.mark.unit
def test_receipt_init_invalid_timestamp():
    """Constructing a Receipt with an invalid timestamp raises ValueError."""
    with pytest.raises(
        ValueError,
        match="timestamp_added must be a datetime object or a string",
    ):
        Receipt(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            width=200,
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


@pytest.mark.unit
def test_receipt_init_invalid_s3_bucket():
    """Receipt with invalid S3 bucket raises ValueError."""
    with pytest.raises(ValueError, match="raw_s3_bucket must be a string"):
        Receipt(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            width=200,
            height=100,
            timestamp_added="2021-01-01T00:00:00",
            raw_s3_bucket=123,
            raw_s3_key="test/key/receipt.jpg",
            top_left={"x": 0.0, "y": 0.0},
            top_right={"x": 200.0, "y": 0.0},
            bottom_left={"x": 0.0, "y": 100.0},
            bottom_right={"x": 200.0, "y": 100.0},
            sha256="abc123",
        )


@pytest.mark.unit
def test_receipt_init_invalid_s3_key():
    """Receipt with invalid S3 key raises ValueError."""
    with pytest.raises(ValueError, match="raw_s3_key must be a string"):
        Receipt(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            width=200,
            height=100,
            timestamp_added="2021-01-01T00:00:00",
            raw_s3_bucket="test-bucket",
            raw_s3_key=123,
            top_left={"x": 0.0, "y": 0.0},
            top_right={"x": 200.0, "y": 0.0},
            bottom_left={"x": 0.0, "y": 100.0},
            bottom_right={"x": 200.0, "y": 100.0},
            sha256="abc123",
        )


@pytest.mark.unit
def test_receipt_init_invalid_point_types():
    """Receipt with invalid point data raises ValueError."""
    with pytest.raises(ValueError):
        Receipt(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            width=200,
            height=100,
            timestamp_added="2021-01-01T00:00:00",
            raw_s3_bucket="test-bucket",
            raw_s3_key="test/key/receipt.jpg",
            top_left={"x": "not-a-float", "y": 0.0},
            top_right={"x": 200.0, "y": 0.0},
            bottom_left={"x": 0.0, "y": 100.0},
            bottom_right={"x": 200.0, "y": 100.0},
            sha256="abc123",
        )


@pytest.mark.unit
def test_receipt_init_invalid_sha256():
    """Receipt with invalid SHA256 raises ValueError."""
    with pytest.raises(ValueError, match="sha256 must be a string"):
        Receipt(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            width=200,
            height=100,
            timestamp_added="2021-01-01T00:00:00",
            raw_s3_bucket="test-bucket",
            raw_s3_key="test/key/receipt.jpg",
            top_left={"x": 0.0, "y": 0.0},
            top_right={"x": 200.0, "y": 0.0},
            bottom_left={"x": 0.0, "y": 100.0},
            bottom_right={"x": 200.0, "y": 100.0},
            sha256=123,
        )


@pytest.mark.unit
def test_receipt_init_invalid_cdn_bucket():
    """Receipt with invalid CDN bucket raises ValueError."""
    with pytest.raises(ValueError, match="cdn_s3_bucket must be a string"):
        Receipt(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
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
            cdn_s3_bucket=123,
        )


@pytest.mark.unit
def test_receipt_init_invalid_cdn_key():
    """Receipt with invalid CDN key raises ValueError."""
    with pytest.raises(ValueError, match="cdn_s3_key must be a string"):
        Receipt(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
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
            cdn_s3_key=123,
        )


@pytest.mark.unit
def test_receipt_key_generation(example_receipt):
    """Test that the primary key is correctly generated."""
    assert example_receipt.key == {
        "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": "RECEIPT#00001"},
    }


@pytest.mark.unit
def test_receipt_gsi1_key_generation(example_receipt):
    """Test that the GSI1 key is correctly generated."""
    assert example_receipt.gsi1_key() == {
        "GSI1PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "GSI1SK": {"S": "RECEIPT#00001"},
    }


@pytest.mark.unit
def test_receipt_gsi2_key_generation(example_receipt):
    """Test that the GSI2 key is correctly generated."""
    assert example_receipt.gsi2_key() == {
        "GSI2PK": {"S": "RECEIPT"},
        "GSI2SK": {
            "S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3#RECEIPT#00001"
        },
    }




@pytest.mark.unit
def test_receipt_to_item(example_receipt):
    """Test converting a Receipt to a DynamoDB item."""
    assert example_receipt.to_item() == {
        "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": "RECEIPT#00001"},
        "GSI1PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "GSI1SK": {"S": "RECEIPT#00001"},
        "GSI2PK": {"S": "RECEIPT"},
        "GSI2SK": {
            "S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3#RECEIPT#00001"
        },
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
        "cdn_webp_s3_key": {"NULL": True},
        "cdn_avif_s3_key": {"NULL": True},
        # Thumbnail versions
        "cdn_thumbnail_s3_key": {"NULL": True},
        "cdn_thumbnail_webp_s3_key": {"NULL": True},
        "cdn_thumbnail_avif_s3_key": {"NULL": True},
        # Small versions
        "cdn_small_s3_key": {"NULL": True},
        "cdn_small_webp_s3_key": {"NULL": True},
        "cdn_small_avif_s3_key": {"NULL": True},
        # Medium versions
        "cdn_medium_s3_key": {"NULL": True},
        "cdn_medium_webp_s3_key": {"NULL": True},
        "cdn_medium_avif_s3_key": {"NULL": True},
    }


@pytest.mark.unit
def test_receipt_repr(example_receipt):
    """Test the string representation of a Receipt."""
    assert str(example_receipt) == (
        "Receipt("
        "image_id='3f52804b-2fad-4e00-92c8-b593da3a8ed3', "
        "receipt_id=1, "
        "width=200, "
        "height=100, "
        "timestamp_added='2021-01-01T00:00:00', "
        "raw_s3_bucket='test-bucket', "
        "raw_s3_key='test/key/receipt.jpg', "
        "top_left={'x': 0.0, 'y': 0.0}, "
        "top_right={'x': 200.0, 'y': 0.0}, "
        "bottom_left={'x': 0.0, 'y': 100.0}, "
        "bottom_right={'x': 200.0, 'y': 100.0}, "
        "sha256='abc123', "
        "cdn_s3_bucket=None, "
        "cdn_s3_key=None, "
        "cdn_webp_s3_key=None, "
        "cdn_avif_s3_key=None, "
        "cdn_thumbnail_s3_key=None, "
        "cdn_thumbnail_webp_s3_key=None, "
        "cdn_thumbnail_avif_s3_key=None, "
        "cdn_small_s3_key=None, "
        "cdn_small_webp_s3_key=None, "
        "cdn_small_avif_s3_key=None, "
        "cdn_medium_s3_key=None, "
        "cdn_medium_webp_s3_key=None, "
        "cdn_medium_avif_s3_key=None"
        ")"
    )


@pytest.mark.unit
def test_receipt_iter(example_receipt):
    """Test that Receipt is iterable."""
    assert dict(example_receipt) == {
        "receipt_id": 1,
        "image_id": "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "width": 200,
        "height": 100,
        "timestamp_added": "2021-01-01T00:00:00",
        "raw_s3_bucket": "test-bucket",
        "raw_s3_key": "test/key/receipt.jpg",
        "top_left": {"x": 0.0, "y": 0.0},
        "top_right": {"x": 200.0, "y": 0.0},
        "bottom_left": {"x": 0.0, "y": 100.0},
        "bottom_right": {"x": 200.0, "y": 100.0},
        "sha256": "abc123",
        "cdn_s3_bucket": None,
        "cdn_s3_key": None,
        "cdn_webp_s3_key": None,
        "cdn_avif_s3_key": None,
        "cdn_thumbnail_s3_key": None,
        "cdn_thumbnail_webp_s3_key": None,
        "cdn_thumbnail_avif_s3_key": None,
        "cdn_small_s3_key": None,
        "cdn_small_webp_s3_key": None,
        "cdn_small_avif_s3_key": None,
        "cdn_medium_s3_key": None,
        "cdn_medium_webp_s3_key": None,
        "cdn_medium_avif_s3_key": None,
    }
    assert Receipt(**dict(example_receipt)) == example_receipt


@pytest.mark.unit
def test_receipt_eq(example_receipt):
    """Test that Receipt equality works as expected."""
    assert example_receipt == Receipt(**dict(example_receipt))
    assert example_receipt != Receipt(**dict(example_receipt, receipt_id=2))
    assert example_receipt is not None


@pytest.mark.unit
def test_item_to_receipt_valid_input(example_receipt):
    """Test item_to_receipt with a valid DynamoDB item."""
    item_to_receipt(example_receipt.to_item()) == example_receipt


@pytest.mark.unit
def test_item_to_receipt_missing_keys():
    """item_to_receipt raises ValueError when required keys are missing."""
    incomplete_item = {
        "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": "RECEIPT#00001"},
    }
    with pytest.raises(ValueError, match="Invalid item format\nmissing keys"):
        item_to_receipt(incomplete_item)


@pytest.mark.unit
def test_item_to_receipt_invalid_format():
    """item_to_receipt raises ValueError when keys are incorrectly formatted."""
    invalid_item = {
        "PK": {"S": "IMAGE#00001"},
        "SK": {"S": "RECEIPT#00001"},
        "width": {"S": "200"},  # Should be {"N": "200"}
        "height": {"N": "100"},
        "timestamp_added": {"S": "2021-01-01T00:00:00"},
        "raw_s3_bucket": {"S": "test-bucket"},
        "raw_s3_key": {"S": "test/key/receipt.jpg"},
        "top_left": {"M": {"x": {"N": "0.0"}, "y": {"N": "0.0"}}},
        "top_right": {"M": {"x": {"N": "200.0"}, "y": {"N": "0.0"}}},
        "bottom_left": {"M": {"x": {"N": "0.0"}, "y": {"N": "100.0"}}},
        "bottom_right": {"M": {"x": {"N": "200.0"}, "y": {"N": "100.0"}}},
    }
    with pytest.raises(ValueError, match="Error converting item to Receipt"):
        item_to_receipt(invalid_item)
