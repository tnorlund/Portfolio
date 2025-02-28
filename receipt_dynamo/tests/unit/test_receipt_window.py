# infra/lambda_layer/python/test/unit/test_receipt_window.py
import pytest

from receipt_dynamo.entities.receipt_window import (
    ReceiptWindow,
    itemToReceiptWindow,
)

# Use a valid UUID for testing. (Assuming assert_valid_uuid accepts this
# format.)
VALID_UUID = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"


@pytest.fixture
def example_receipt_window():
    """Provides a sample valid ReceiptWindow for testing."""
    return ReceiptWindow(
        image_id=VALID_UUID,
        receipt_id=123,
        cdn_s3_bucket="my_bucket",
        cdn_s3_key="my_key",
        corner_name="TOP_LEFT",  # Will be uppercased by the constructor.
        width=100,
        height=200,
        inner_corner_coordinates=(10, 20),
        gpt_guess=[1, 2, 3],
    )


@pytest.fixture
def example_receipt_window_no_gpt_guess():
    """Provides a ReceiptWindow without gpt_guess."""
    return ReceiptWindow(
        image_id=VALID_UUID,
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
    assert example_receipt_window.image_id == VALID_UUID
    assert example_receipt_window.receipt_id == 123
    assert example_receipt_window.cdn_s3_bucket == "my_bucket"
    assert example_receipt_window.cdn_s3_key == "my_key"
    # The constructor uppercases corner_name.
    assert example_receipt_window.corner_name == "TOP_LEFT"
    assert example_receipt_window.width == 100
    assert example_receipt_window.height == 200
    # inner_corner_coordinates should remain as a tuple of ints.
    assert example_receipt_window.inner_corner_coordinates == (10, 20)
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
            inner_corner_coordinates=(10, 20),
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
            inner_corner_coordinates=(10, 20),
        )


@pytest.mark.unit
def test_receipt_window_init_invalid_receipt_id():
    """Test invalid (non-positive) receipt_id values."""
    with pytest.raises(ValueError, match="id must be positive"):
        ReceiptWindow(
            image_id=VALID_UUID,
            receipt_id=0,
            cdn_s3_bucket="bucket",
            cdn_s3_key="key",
            corner_name="TOP_LEFT",
            width=100,
            height=200,
            inner_corner_coordinates=(10, 20),
        )
    with pytest.raises(ValueError, match="id must be positive"):
        ReceiptWindow(
            image_id=VALID_UUID,
            receipt_id=-5,
            cdn_s3_bucket="bucket",
            cdn_s3_key="key",
            corner_name="TOP_LEFT",
            width=100,
            height=200,
            inner_corner_coordinates=(10, 20),
        )


@pytest.mark.unit
def test_receipt_window_init_invalid_cdn_s3_bucket():
    """Test that cdn_s3_bucket must be a string if provided."""
    with pytest.raises(ValueError, match="cdn_s3_bucket must be a string"):
        ReceiptWindow(
            image_id=VALID_UUID,
            receipt_id=123,
            cdn_s3_bucket=42,  # not a string
            cdn_s3_key="my_key",
            corner_name="TOP_LEFT",
            width=100,
            height=200,
            inner_corner_coordinates=(10, 20),
        )


@pytest.mark.unit
def test_receipt_window_init_invalid_cdn_s3_key():
    """Test that cdn_s3_key must be a string if provided."""
    with pytest.raises(ValueError, match="cdn_s3_key must be a string"):
        ReceiptWindow(
            image_id=VALID_UUID,
            receipt_id=123,
            cdn_s3_bucket="my_bucket",
            cdn_s3_key=42,  # not a string
            corner_name="TOP_LEFT",
            width=100,
            height=200,
            inner_corner_coordinates=(10, 20),
        )


@pytest.mark.unit
def test_receipt_window_init_non_string_corner_name():
    """Test that corner_name must be a string."""
    with pytest.raises(ValueError, match="corner_name must be a string"):
        ReceiptWindow(
            image_id=VALID_UUID,
            receipt_id=123,
            cdn_s3_bucket="bucket",
            cdn_s3_key="key",
            corner_name=123,  # non-string
            width=100,
            height=200,
            inner_corner_coordinates=(10, 20),
        )


@pytest.mark.unit
def test_receipt_window_init_invalid_corner_name():
    """Test corner_name must be one of the specified valid values."""
    with pytest.raises(ValueError, match="corner_name must be one of:"):
        ReceiptWindow(
            image_id=VALID_UUID,
            receipt_id=123,
            cdn_s3_bucket="bucket",
            cdn_s3_key="key",
            corner_name="top_side",  # invalid
            width=100,
            height=200,
            inner_corner_coordinates=(10, 20),
        )


@pytest.mark.unit
def test_receipt_window_init_invalid_width_and_height():
    """Test that width and height must be positive."""
    with pytest.raises(ValueError, match="width must be positive"):
        ReceiptWindow(
            image_id=VALID_UUID,
            receipt_id=123,
            cdn_s3_bucket="bucket",
            cdn_s3_key="key",
            corner_name="TOP_LEFT",
            width=0,
            height=200,
            inner_corner_coordinates=(10, 20),
        )
    with pytest.raises(ValueError, match="height must be positive"):
        ReceiptWindow(
            image_id=VALID_UUID,
            receipt_id=123,
            cdn_s3_bucket="bucket",
            cdn_s3_key="key",
            corner_name="TOP_LEFT",
            width=100,
            height=0,
            inner_corner_coordinates=(10, 20),
        )


@pytest.mark.unit
def test_receipt_window_init_valid_inner_corner_coordinates():
    """Test inner_corner_coordinates can be a tuple or list if provided.

    This was added to support writing the inner_corner_coordinates as a list in a JSON file.
    """

    rw = ReceiptWindow(
        image_id=VALID_UUID,
        receipt_id=123,
        cdn_s3_bucket="bucket",
        cdn_s3_key="key",
        corner_name="TOP_LEFT",
        width=100,
        height=200,
        inner_corner_coordinates=[10, 20],  # not a tuple
    )
    assert rw.inner_corner_coordinates == (10, 20)


def test_receipt_window_init_invalid_inner_corner_coordinates():
    """Test inner_corner_coordinates must be a tuple or a list if provided."""
    with pytest.raises(
        ValueError, match="inner_corner_coordinates must be a tuple or list"
    ):
        ReceiptWindow(
            image_id=VALID_UUID,
            receipt_id=123,
            cdn_s3_bucket="bucket",
            cdn_s3_key="key",
            corner_name="TOP_LEFT",
            width=100,
            height=200,
            inner_corner_coordinates="not-a-tuple-or-list",
        )


@pytest.mark.unit
def test_receipt_window_key(example_receipt_window):
    """Test the ReceiptWindow.key() method."""
    assert example_receipt_window.key() == {
        "PK": {"S": f"IMAGE#{VALID_UUID}"},
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
def test_receipt_window_to_item(
    example_receipt_window, example_receipt_window_no_gpt_guess
):
    """Test the ReceiptWindow.to_item() method for both a gpt_guess and no gpt_guess."""
    # Case: with gpt_guess
    assert example_receipt_window.to_item() == {
        "PK": {"S": f"IMAGE#{VALID_UUID}"},
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
        "PK": {"S": f"IMAGE#{VALID_UUID}"},
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
    expected = (
        "ReceiptWindow(image_id=3f52804b-2fad-4e00-92c8-b593da3a8ed3, "
        "receipt_id=123, "
        "corner_name=TOP_LEFT, "
        "width=100, "
        "height=200, "
        "gpt_guess=[1, 2, 3])"
    )
    assert repr(example_receipt_window) == expected


@pytest.mark.unit
def test_receipt_window_repr_no_gpt_guess(example_receipt_window_no_gpt_guess):
    """Test __repr__ when gpt_guess is None."""
    rep = repr(example_receipt_window_no_gpt_guess)
    assert "gpt_guess=None" in rep


@pytest.mark.unit
def test_receipt_window_iter(example_receipt_window):
    """Test the ReceiptWindow.__iter__() method."""
    assert dict(example_receipt_window) == {
        "image_id": VALID_UUID,
        "receipt_id": 123,
        "cdn_s3_bucket": "my_bucket",
        "cdn_s3_key": "my_key",
        "corner_name": "TOP_LEFT",
        "width": 100,
        "height": 200,
        "inner_corner_coordinates": (10, 20),
        "gpt_guess": [1, 2, 3],
    }


@pytest.mark.unit
def test_receipt_window_eq():
    """Test the ReceiptWindow.__eq__() method."""
    r1 = ReceiptWindow(
        VALID_UUID,
        123,
        "bucket",
        "key",
        "TOP_LEFT",
        100,
        200,
        (10, 20),
        [1, 2, 3],
    )
    r2 = ReceiptWindow(
        VALID_UUID,
        123,
        "bucket",
        "key",
        "top_left",
        100,
        200,
        (10, 20),
        [1, 2, 3],
    )
    r3 = ReceiptWindow(
        "29984038-5cb5-4ce9-bcf0-856dcfca3125",
        123,
        "bucket",
        "key",
        "TOP_LEFT",
        100,
        200,
        (10, 20),
        [1, 2, 3],
    )
    r4 = ReceiptWindow(
        VALID_UUID,
        321,
        "bucket",
        "key",
        "TOP_LEFT",
        100,
        200,
        (10, 20),
        [1, 2, 3],
    )
    r5 = ReceiptWindow(
        VALID_UUID,
        123,
        "Bucket",
        "key",
        "TOP_LEFT",
        100,
        200,
        (10, 20),
        [1, 2, 3],
    )

    assert (
        r1 == r2
    ), "corner_name is case-insensitive on input, so these should be equal"
    assert r1 != r3, "Different image_id"
    assert r1 != r4, "Different receipt_id"
    assert r1 != r5, "Different cdn_s3_bucket"

    # Compare with a non-ReceiptWindow object.
    assert r1 != 42, "Should return False (different type)"


@pytest.mark.unit
def test_receipt_window_hash(example_receipt_window):
    """Test the ReceiptWindow.__hash__() method."""
    h = hash(example_receipt_window)
    hashable_coords = tuple(
        float(x) for x in example_receipt_window.inner_corner_coordinates
    )

    expected = hash(
        (
            example_receipt_window.image_id,
            example_receipt_window.receipt_id,
            example_receipt_window.cdn_s3_bucket,
            example_receipt_window.cdn_s3_key,
            example_receipt_window.corner_name,
            hashable_coords,
            (
                tuple(example_receipt_window.gpt_guess)
                if example_receipt_window.gpt_guess
                else None
            ),
        )
    )
    assert h == expected


@pytest.mark.unit
def test_itemToReceiptWindow(
    example_receipt_window, example_receipt_window_no_gpt_guess
):
    """Test the itemToReceiptWindow() function with valid and invalid items."""
    # Round-trip: to_item() -> itemToReceiptWindow -> compare
    item_with_guess = example_receipt_window.to_item()
    new_rw_with_guess = itemToReceiptWindow(item_with_guess)
    assert (
        new_rw_with_guess == example_receipt_window
    ), "Should convert item back to an equivalent ReceiptWindow object (with gpt_guess)."

    item_no_guess = example_receipt_window_no_gpt_guess.to_item()
    new_rw_no_guess = itemToReceiptWindow(item_no_guess)
    assert (
        new_rw_no_guess == example_receipt_window_no_gpt_guess
    ), "Should convert item back to an equivalent ReceiptWindow object (no gpt_guess)."

    # Missing a required key
    with pytest.raises(
        ValueError, match=r"^Invalid item format\nmissing keys: "
    ):
        itemToReceiptWindow(
            {
                "PK": {"S": f"IMAGE#{VALID_UUID}"},
                "SK": {"S": "RECEIPT#00123#RECEIPT_WINDOW#TOP_LEFT"},
                # "TYPE" and other required keys are missing...
            }
        )

    # Bad data type in item
    bad_item = {
        "PK": {"S": f"IMAGE#{VALID_UUID}"},
        "SK": {"S": "RECEIPT#00123#RECEIPT_WINDOW#TOP_LEFT"},
        "TYPE": {"S": "RECEIPT_WINDOW"},
        "cdn_s3_bucket": {"S": "my_bucket"},
        "cdn_s3_key": {"S": "my_key"},
        "corner_name": {"S": "TOP_LEFT"},
        "width": {"S": "wrong-type"},  # Should be {"N": "..."}
        "height": {"N": "200"},
        "inner_corner_coordinates": {"L": [{"N": "10"}, {"N": "20"}]},
    }
    with pytest.raises(
        ValueError, match="Error converting item to ReceiptWindow:"
    ):
        itemToReceiptWindow(bad_item)


@pytest.mark.unit
def test_itemToReceiptWindow_missing_gpt_guess_field(example_receipt_window):
    """Test itemToReceiptWindow when the gpt_guess field is missing."""
    item = example_receipt_window.to_item()
    # Remove the gpt_guess field.
    del item["gpt_guess"]
    new_rw = itemToReceiptWindow(item)
    # Since gpt_guess is not present, it should be interpreted as None.
    assert new_rw.gpt_guess is None
