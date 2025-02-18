# infra/lambda_layer/python/test/unit/test_receipt_window.py
import pytest
from dynamo.entities.receipt_window import ReceiptWindow, itemToReceiptWindow


@pytest.fixture
def example_receipt_window():
    """Provides a sample valid ReceiptWindow for testing."""
    # Pass inner_corner_coordinates as a tuple of ints.
    return ReceiptWindow(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=123,
        cdn_s3_bucket="my_bucket",
        cdn_s3_key="my_key",
        corner_name="TOP_LEFT",  # Constructor will uppercase it.
        width=100,
        height=200,
        inner_corner_coordinates=(10, 20),
        gpt_guess=[1, 2, 3],
    )

@pytest.fixture
def example_receipt_window_no_gpt_guess():
    """Provides a ReceiptWindow without gpt_guess."""
    return ReceiptWindow(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=123,
        cdn_s3_bucket="my_bucket",
        cdn_s3_key="my_key",
        corner_name="BOTTOM_RIGHT",
        width=100,
        height=200,
        inner_corner_coordinates=(10, 20),
        gpt_guess=None,
    )


@pytest.mark.unit
def test_receipt_window_init_valid(example_receipt_window):
    """Test the ReceiptWindow constructor with valid data."""
    assert example_receipt_window.image_id == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert example_receipt_window.receipt_id == 123
    assert example_receipt_window.cdn_s3_bucket == "my_bucket"
    assert example_receipt_window.cdn_s3_key == "my_key"
    # The constructor uppercases corner_name, so we expect "TOP_LEFT"
    assert example_receipt_window.corner_name == "TOP_LEFT"
    assert example_receipt_window.width == 100
    assert example_receipt_window.height == 200
    # We used a tuple of dicts so it would round-trip from to_item()
    assert example_receipt_window.inner_corner_coordinates == ({"N": "10"}, {"N": "20"})
    assert example_receipt_window.gpt_guess == [1, 2, 3]


@pytest.mark.unit
def test_receipt_window_init_invalid_image_id():
    """Test invalid image_id values."""
    with pytest.raises(ValueError, match="uuid must be a string"):
        ReceiptWindow(
            image_id=42,  # not a string
            receipt_id=123,
            cdn_s3_bucket="bucket",
            cdn_s3_key="key",
            corner_name="TOP_LEFT",
            width=100,
            height=200,
            inner_corner_coordinates=({"N": "10"}, {"N": "20"}),
        )
    with pytest.raises(ValueError, match="uuid must be a valid UUID"):
        ReceiptWindow(
            image_id="not-a-uuid",
            receipt_id=123,
            cdn_s3_bucket="bucket",
            cdn_s3_key="key",
            corner_name="TOP_LEFT",
            width=100,
            height=200,
            inner_corner_coordinates=({"N": "10"}, {"N": "20"}),
        )


@pytest.mark.unit
def test_receipt_window_init_invalid_receipt_id():
    """Test invalid (non-positive) receipt_id values."""
    with pytest.raises(ValueError, match="id must be positive"):
        ReceiptWindow(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=0,
            cdn_s3_bucket="bucket",
            cdn_s3_key="key",
            corner_name="TOP_LEFT",
            width=100,
            height=200,
            inner_corner_coordinates=({"N": "10"}, {"N": "20"}),
        )
    with pytest.raises(ValueError, match="id must be positive"):
        ReceiptWindow(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=-5,
            cdn_s3_bucket="bucket",
            cdn_s3_key="key",
            corner_name="TOP_LEFT",
            width=100,
            height=200,
            inner_corner_coordinates=({"N": "10"}, {"N": "20"}),
        )


@pytest.mark.unit
def test_receipt_window_init_invalid_cdn_s3_bucket():
    """Test that cdn_s3_bucket must be a string if provided."""
    with pytest.raises(ValueError, match="cdn_s3_bucket must be a string"):
        ReceiptWindow(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=123,
            cdn_s3_bucket=42,  # not a string
            cdn_s3_key="my_key",
            corner_name="TOP_LEFT",
            width=100,
            height=200,
            inner_corner_coordinates=({"N": "10"}, {"N": "20"}),
        )


@pytest.mark.unit
def test_receipt_window_init_invalid_cdn_s3_key():
    """Test that cdn_s3_key must be a string if provided."""
    with pytest.raises(ValueError, match="cdn_s3_key must be a string"):
        ReceiptWindow(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=123,
            cdn_s3_bucket="my_bucket",
            cdn_s3_key=42,  # not a string
            corner_name="TOP_LEFT",
            width=100,
            height=200,
            inner_corner_coordinates=({"N": "10"}, {"N": "20"}),
        )


@pytest.mark.unit
def test_receipt_window_init_invalid_corner_name():
    """Test corner_name must be one of the specified valid values."""
    with pytest.raises(ValueError, match="corner_name must be one of:"):
        ReceiptWindow(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=123,
            cdn_s3_bucket="bucket",
            cdn_s3_key="key",
            corner_name="top_side",  # invalid
            width=100,
            height=200,
            inner_corner_coordinates=({"N": "10"}, {"N": "20"}),
        )


@pytest.mark.unit
def test_receipt_window_init_invalid_width_and_height():
    """Test that width and height must be positive."""
    with pytest.raises(ValueError, match="width must be positive"):
        ReceiptWindow(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=123,
            cdn_s3_bucket="bucket",
            cdn_s3_key="key",
            corner_name="TOP_LEFT",
            width=0,
            height=200,
            inner_corner_coordinates=({"N": "10"}, {"N": "20"}),
        )
    with pytest.raises(ValueError, match="height must be positive"):
        ReceiptWindow(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=123,
            cdn_s3_bucket="bucket",
            cdn_s3_key="key",
            corner_name="TOP_LEFT",
            width=100,
            height=0,
            inner_corner_coordinates=({"N": "10"}, {"N": "20"}),
        )


@pytest.mark.unit
def test_receipt_window_init_invalid_inner_corner_coordinates():
    """Test inner_corner_coordinates must be a tuple if provided."""
    with pytest.raises(ValueError, match="inner_corner_coordinates must be a tuple"):
        ReceiptWindow(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=123,
            cdn_s3_bucket="bucket",
            cdn_s3_key="key",
            corner_name="TOP_LEFT",
            width=100,
            height=200,
            inner_corner_coordinates=[10, 20],  # not a tuple
        )


@pytest.mark.unit
def test_receipt_window_key(example_receipt_window):
    """Test the ReceiptWindow.key() method."""
    assert example_receipt_window.key() == {
        "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": "RECEIPT#00123#RECEIPT_WINDOW#TOP_LEFT"},
    }


@pytest.mark.unit
def test_receipt_window_gsi3_key(example_receipt_window):
    """Test the ReceiptWindow.gsi3_key() method."""
    assert example_receipt_window.gsi3_key() == {
        "GSI3PK": {"S": "RECEIPT"},
        "GSI3SK": {"S": "RECEIPT#00123#RECEIPT_WINDOW#TOP_LEFT"},
    }

@pytest.mark.unit
def test_receipt_window_to_item(example_receipt_window, example_receipt_window_no_gpt_guess):
    """Test the ReceiptWindow.to_item() method for both a gpt_guess and no gpt_guess."""
    # Case: with gpt_guess
    assert example_receipt_window.to_item() == {
        "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": "RECEIPT#00123#RECEIPT_WINDOW#TOP_LEFT"},
        "GSI3PK": {"S": "RECEIPT"},
        "GSI3SK": {"S": "RECEIPT#00123#RECEIPT_WINDOW#TOP_LEFT"},
        "TYPE": {"S": "RECEIPT_WINDOW"},
        "cdn_s3_bucket": {"S": "my_bucket"},
        "cdn_s3_key": {"S": "my_key"},
        "corner_name": {"S": "TOP_LEFT"},
        "width": {"N": "100"},
        "height": {"N": "200"},
        "inner_corner_coordinates": {"L": [{"N": "10"}, {"N": "20"}]},
        "gpt_guess": {"L": [{"N": "1"}, {"N": "2"}, {"N": "3"}]},
    }

    # Case: without gpt_guess
    assert example_receipt_window_no_gpt_guess.to_item() == {
        "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": "RECEIPT#00123#RECEIPT_WINDOW#BOTTOM_RIGHT"},
        "GSI3PK": {"S": "RECEIPT"},
        "GSI3SK": {"S": "RECEIPT#00123#RECEIPT_WINDOW#BOTTOM_RIGHT"},
        "TYPE": {"S": "RECEIPT_WINDOW"},
        "cdn_s3_bucket": {"S": "my_bucket"},
        "cdn_s3_key": {"S": "my_key"},
        "corner_name": {"S": "BOTTOM_RIGHT"},
        "width": {"N": "100"},
        "height": {"N": "200"},
        "inner_corner_coordinates": {"L": [{"N": "10"}, {"N": "20"}]},
        "gpt_guess": {"NULL": True},
    }


@pytest.mark.unit
def test_receipt_window_repr(example_receipt_window):
    """Test the ReceiptWindow.__repr__() method."""
    assert repr(example_receipt_window) == (
        "ReceiptWindow("
        "image_id=3f52804b-2fad-4e00-92c8-b593da3a8ed3, "
        "receipt_id=123, "
        "corner_name=TOP_LEFT, "
        "width=100, "
        "height=200, "
        "gpt_guess=[1, 2, 3])"
    )


@pytest.mark.unit
def test_receipt_window_iter(example_receipt_window):
    """Test the ReceiptWindow.__iter__() method."""
    assert dict(example_receipt_window) == {
        "image_id": "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "receipt_id": 123,
        "cdn_s3_bucket": "my_bucket",
        "cdn_s3_key": "my_key",
        "corner_name": "TOP_LEFT",
        "width": 100,
        "height": 200,
        "inner_corner_coordinates": ({"N": "10"}, {"N": "20"}),
        "gpt_guess": [1, 2, 3],
    }


@pytest.mark.unit
def test_receipt_window_eq():
    """Test the ReceiptWindow.__eq__() method."""
    # fmt: off
    # Use same corner_name uppercase for consistency
    r1 = ReceiptWindow(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3", 123, "bucket", "key", "TOP_LEFT", 100, 200,
        ({"N": "10"}, {"N": "20"}), [1, 2, 3]
    )
    r2 = ReceiptWindow(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3", 123, "bucket", "key", "top_left", 100, 200,
        ({"N": "10"}, {"N": "20"}), [1, 2, 3]
    )
    r3 = ReceiptWindow(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed4", 123, "bucket", "key", "TOP_LEFT", 100, 200,
        ({"N": "10"}, {"N": "20"}), [1, 2, 3]  # different image_id
    )
    r4 = ReceiptWindow(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3", 321, "bucket", "key", "TOP_LEFT", 100, 200,
        ({"N": "10"}, {"N": "20"}), [1, 2, 3]  # different receipt_id
    )
    r5 = ReceiptWindow(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3", 123, "Bucket", "key", "TOP_LEFT", 100, 200,
        ({"N": "10"}, {"N": "20"}), [1, 2, 3]  # different cdn_s3_bucket
    )
    # fmt: on

    assert r1 == r2, "corner_name is case-insensitive on input, so these should be equal"
    assert r1 != r3, "Different image_id"
    assert r1 != r4, "Different receipt_id"
    assert r1 != r5, "Different cdn_s3_bucket"

    # Compare with a non-ReceiptWindow object
    assert r1 != 42, "Should return False (different type)"


@pytest.mark.unit
def test_itemToReceiptWindow(example_receipt_window, example_receipt_window_no_gpt_guess):
    """Test the itemToReceiptWindow() function with valid and invalid items."""
    # Round-trip: to_item() -> itemToReceiptWindow -> compare
    item_with_guess = example_receipt_window.to_item()
    new_rw_with_guess = itemToReceiptWindow(item_with_guess)
    assert new_rw_with_guess == example_receipt_window, (
        "Should convert item back to an equivalent ReceiptWindow object (with gpt_guess)."
    )

    item_no_guess = example_receipt_window_no_gpt_guess.to_item()
    new_rw_no_guess = itemToReceiptWindow(item_no_guess)
    assert new_rw_no_guess == example_receipt_window_no_gpt_guess, (
        "Should convert item back to an equivalent ReceiptWindow object (no gpt_guess)."
    )

    # Missing a required key
    with pytest.raises(ValueError, match=r"^Invalid item format\nmissing keys: "):
        itemToReceiptWindow({
            "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
            "SK": {"S": "RECEIPT#00123#RECEIPT_WINDOW#TOP_LEFT"},
            # "TYPE" is missing and others...
        })

    # Bad data type in item
    bad_item = {
        "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": "RECEIPT#00123#RECEIPT_WINDOW#TOP_LEFT"},
        "TYPE": {"S": "RECEIPT_WINDOW"},
        "cdn_s3_bucket": {"S": "my_bucket"},
        "cdn_s3_key": {"S": "my_key"},
        "corner_name": {"S": "TOP_LEFT"},
        "width": {"S": "wrong-type"},  # Should be {"N": "..."}
        "height": {"N": "200"},
        "inner_corner_coordinates": {"L": [{"N": "10"}, {"N": "20"}]},
    }
    with pytest.raises(ValueError, match="Error converting item to ReceiptWindow:"):
        itemToReceiptWindow(bad_item)