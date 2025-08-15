"""Unit tests for the ReceiptLine entity."""

import json

import pytest

from receipt_dynamo import ReceiptLine, item_to_receipt_line


@pytest.fixture(name="example_receipt_line")
def _example_receipt_line() -> ReceiptLine:
    """Return a sample ReceiptLine for testing."""
    return ReceiptLine(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=10,
        text="Line text",
        bounding_box={"x": 0.1, "y": 0.2, "width": 0.5, "height": 0.2},
        top_left={"x": 0.1, "y": 0.2},
        top_right={"x": 0.6, "y": 0.2},
        bottom_left={"x": 0.1, "y": 0.4},
        bottom_right={"x": 0.6, "y": 0.4},
        angle_degrees=0.0,
        angle_radians=0.0,
        confidence=0.95,
        is_noise=False,
    )


@pytest.mark.unit
def test_receipt_line_init_valid(example_receipt_line: ReceiptLine) -> None:
    """Test valid initialization of ReceiptLine."""
    assert example_receipt_line.receipt_id == 1
    assert example_receipt_line.image_id == (
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    )
    assert example_receipt_line.line_id == 10
    assert example_receipt_line.text == "Line text"
    assert example_receipt_line.bounding_box == {
        "x": 0.1,
        "y": 0.2,
        "width": 0.5,
        "height": 0.2,
    }
    assert example_receipt_line.top_left == {"x": 0.1, "y": 0.2}
    assert example_receipt_line.top_right == {"x": 0.6, "y": 0.2}
    assert example_receipt_line.bottom_left == {"x": 0.1, "y": 0.4}
    assert example_receipt_line.bottom_right == {"x": 0.6, "y": 0.4}
    assert example_receipt_line.angle_degrees == 0.0
    assert example_receipt_line.angle_radians == 0.0
    assert example_receipt_line.confidence == 0.95


@pytest.mark.unit
def test_receipt_line_init_invalid_receipt_id() -> None:
    """Test that ReceiptLine raises an error for an invalid receipt ID."""
    with pytest.raises(ValueError, match="^receipt_id must be an integer"):
        ReceiptLine(
            receipt_id="1",  # invalid
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=1,
            text="Line text",
            bounding_box={"x": 0, "y": 0, "width": 1, "height": 1},
            top_left={"x": 0.1, "y": 0.2},
            top_right={"x": 0.6, "y": 0.2},
            bottom_left={"x": 0.1, "y": 0.4},
            bottom_right={"x": 0.6, "y": 0.4},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.95,
        )
    with pytest.raises(ValueError, match="^receipt_id must be positive"):
        ReceiptLine(
            receipt_id=-1,  # invalid
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=1,
            text="Line text",
            bounding_box={"x": 0, "y": 0, "width": 1, "height": 1},
            top_left={"x": 0.1, "y": 0.2},
            top_right={"x": 0.6, "y": 0.2},
            bottom_left={"x": 0.1, "y": 0.4},
            bottom_right={"x": 0.6, "y": 0.4},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.95,
        )


@pytest.mark.unit
def test_receipt_line_init_invalid_image_id() -> None:
    """Test that ReceiptLine raises an error for an invalid image ID."""
    with pytest.raises(ValueError, match="uuid must be a string"):
        ReceiptLine(
            receipt_id=1,
            image_id=1,  # invalid
            line_id=1,
            text="Line text",
            bounding_box={"x": 0, "y": 0, "width": 1, "height": 1},
            top_left={"x": 0.1, "y": 0.2},
            top_right={"x": 0.6, "y": 0.2},
            bottom_left={"x": 0.1, "y": 0.4},
            bottom_right={"x": 0.6, "y": 0.4},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.95,
        )
    with pytest.raises(ValueError, match="uuid must be a valid UUID"):
        ReceiptLine(
            receipt_id=1,
            image_id="invalid",  # invalid
            line_id=1,
            text="Line text",
            bounding_box={"x": 0, "y": 0, "width": 1, "height": 1},
            top_left={"x": 0.1, "y": 0.2},
            top_right={"x": 0.6, "y": 0.2},
            bottom_left={"x": 0.1, "y": 0.4},
            bottom_right={"x": 0.6, "y": 0.4},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.95,
        )


@pytest.mark.unit
def test_receipt_line_init_invalid_id() -> None:
    """Test that ReceiptLine raises an error for an invalid line ID."""
    with pytest.raises(ValueError, match="^id must be an integer"):
        ReceiptLine(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id="1",  # invalid
            text="Line text",
            bounding_box={"x": 0, "y": 0, "width": 1, "height": 1},
            top_left={"x": 0.1, "y": 0.2},
            top_right={"x": 0.6, "y": 0.2},
            bottom_left={"x": 0.1, "y": 0.4},
            bottom_right={"x": 0.6, "y": 0.4},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.95,
        )
    with pytest.raises(ValueError, match="^id must be positive"):
        ReceiptLine(
            receipt_id=1,  # invalid
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=-1,
            text="Line text",
            bounding_box={"x": 0, "y": 0, "width": 1, "height": 1},
            top_left={"x": 0.1, "y": 0.2},
            top_right={"x": 0.6, "y": 0.2},
            bottom_left={"x": 0.1, "y": 0.4},
            bottom_right={"x": 0.6, "y": 0.4},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.95,
        )


@pytest.mark.unit
def test_receipt_line_init_invalid_text() -> None:
    """Test that ReceiptLine raises an error for invalid text."""
    with pytest.raises(ValueError, match="text must be a string"):
        ReceiptLine(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=1,
            text=1,  # invalid
            bounding_box={"x": 0, "y": 0, "width": 1, "height": 1},
            top_left={"x": 0.1, "y": 0.2},
            top_right={"x": 0.6, "y": 0.2},
            bottom_left={"x": 0.1, "y": 0.4},
            bottom_right={"x": 0.6, "y": 0.4},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.95,
        )


@pytest.mark.unit
def test_receipt_line_init_invalid_angles() -> None:
    """Test that ReceiptLine raises an error for invalid angles."""
    with pytest.raises(
        ValueError,
        match="angle_degrees must be float or int, got",
    ):
        ReceiptLine(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=1,
            text="Line text",
            bounding_box={"x": 0, "y": 0, "width": 1, "height": 1},
            top_left={"x": 0.1, "y": 0.2},
            top_right={"x": 0.6, "y": 0.2},
            bottom_left={"x": 0.1, "y": 0.4},
            bottom_right={"x": 0.6, "y": 0.4},
            angle_degrees="0.0",  # invalid
            angle_radians=0.0,
            confidence=0.95,
        )
    with pytest.raises(
        ValueError,
        match="angle_radians must be float or int, got",
    ):
        ReceiptLine(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=1,
            text="Line text",
            bounding_box={"x": 0, "y": 0, "width": 1, "height": 1},
            top_left={"x": 0.1, "y": 0.2},
            top_right={"x": 0.6, "y": 0.2},
            bottom_left={"x": 0.1, "y": 0.4},
            bottom_right={"x": 0.6, "y": 0.4},
            angle_degrees=0.0,
            angle_radians="0.0",  # invalid
            confidence=0.95,
        )


@pytest.mark.unit
def test_receipt_line_init_invalid_confidence() -> None:
    """Test that ReceiptLine raises an error for invalid confidence."""
    with pytest.raises(
        ValueError, match="confidence must be float or int, got"
    ):
        ReceiptLine(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=1,
            text="Line text",
            bounding_box={"x": 0, "y": 0, "width": 1, "height": 1},
            top_left={"x": 0.1, "y": 0.2},
            top_right={"x": 0.6, "y": 0.2},
            bottom_left={"x": 0.1, "y": 0.4},
            bottom_right={"x": 0.6, "y": 0.4},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence="0.95",
        )
    receipt = ReceiptLine(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=1,
        text="Line text",
        bounding_box={"x": 0, "y": 0, "width": 1, "height": 1},
        top_left={"x": 0.1, "y": 0.2},
        top_right={"x": 0.6, "y": 0.2},
        bottom_left={"x": 0.1, "y": 0.4},
        bottom_right={"x": 0.6, "y": 0.4},
        angle_degrees=0.0,
        angle_radians=0.0,
        confidence=1,
    )
    assert receipt.confidence == 1.0
    with pytest.raises(
        ValueError, match="confidence must be between 0 and 1, got"
    ):
        ReceiptLine(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=1,
            text="Line text",
            bounding_box={"x": 0, "y": 0, "width": 1, "height": 1},
            top_left={"x": 0.1, "y": 0.2},
            top_right={"x": 0.6, "y": 0.2},
            bottom_left={"x": 0.1, "y": 0.4},
            bottom_right={"x": 0.6, "y": 0.4},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=-0.95,
        )
    with pytest.raises(
        ValueError, match="confidence must be between 0 and 1, got"
    ):
        ReceiptLine(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=1,
            text="Line text",
            bounding_box={"x": 0, "y": 0, "width": 1, "height": 1},
            top_left={"x": 0.1, "y": 0.2},
            top_right={"x": 0.6, "y": 0.2},
            bottom_left={"x": 0.1, "y": 0.4},
            bottom_right={"x": 0.6, "y": 0.4},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=1.1,
        )


@pytest.mark.unit
def test_receipt_line_to_item(example_receipt_line: ReceiptLine) -> None:
    """Test the to_item method of ReceiptLine."""
    item = example_receipt_line.to_item()
    assert item["PK"] == {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"}
    assert item["SK"] == {"S": "RECEIPT#00001#LINE#00010"}
    assert item["TYPE"] == {"S": "RECEIPT_LINE"}
    assert item["text"] == {"S": "Line text"}
    assert item["bounding_box"] == {
        "M": {
            "height": {"N": "0.20000000000000000000"},
            "width": {"N": "0.50000000000000000000"},
            "x": {"N": "0.10000000000000000000"},
            "y": {"N": "0.20000000000000000000"},
        }
    }
    assert item["top_right"] == {
        "M": {
            "x": {"N": "0.60000000000000000000"},
            "y": {"N": "0.20000000000000000000"},
        }
    }
    assert item["top_left"] == {
        "M": {
            "x": {"N": "0.10000000000000000000"},
            "y": {"N": "0.20000000000000000000"},
        }
    }
    assert item["bottom_right"] == {
        "M": {
            "x": {"N": "0.60000000000000000000"},
            "y": {"N": "0.40000000000000000000"},
        }
    }
    assert item["bottom_left"] == {
        "M": {
            "x": {"N": "0.10000000000000000000"},
            "y": {"N": "0.40000000000000000000"},
        }
    }
    assert item["angle_degrees"] == {"N": "0.000000000000000000"}
    assert item["angle_radians"] == {"N": "0.000000000000000000"}
    assert item["confidence"] == {"N": "0.95"}


@pytest.mark.unit
def test_receipt_line_eq():
    """Test the equality method of ReceiptLine."""
    line1 = ReceiptLine(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=3,
        text="Line text",
        bounding_box={"x": 0.1, "y": 0.2, "width": 0.5, "height": 0.2},
        top_left={"x": 0.1, "y": 0.2},
        top_right={"x": 0.6, "y": 0.2},
        bottom_left={"x": 0.1, "y": 0.4},
        bottom_right={"x": 0.6, "y": 0.4},
        angle_degrees=10.0,
        angle_radians=0.174533,
        confidence=0.90,
    )
    line2 = ReceiptLine(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=3,
        text="Line text",
        bounding_box={"x": 0.1, "y": 0.2, "width": 0.5, "height": 0.2},
        top_left={"x": 0.1, "y": 0.2},
        top_right={"x": 0.6, "y": 0.2},
        bottom_left={"x": 0.1, "y": 0.4},
        bottom_right={"x": 0.6, "y": 0.4},
        angle_degrees=10.0,
        angle_radians=0.174533,
        confidence=0.90,
    )
    assert line1 == line2
    assert line1 != "line1"


@pytest.mark.unit
def test_receipt_line_repr(example_receipt_line: ReceiptLine) -> None:
    """Test the repr method of ReceiptLine."""
    assert repr(example_receipt_line) == (
        "ReceiptLine("
        "receipt_id=1, "
        "image_id='3f52804b-2fad-4e00-92c8-b593da3a8ed3', "
        "line_id=10, "
        "text='Line text', "
        "bounding_box={'x': 0.1, 'y': 0.2, 'width': 0.5, 'height': 0.2}, "
        "top_right={'x': 0.6, 'y': 0.2}, "
        "top_left={'x': 0.1, 'y': 0.2}, "
        "bottom_right={'x': 0.6, 'y': 0.4}, "
        "bottom_left={'x': 0.1, 'y': 0.4}, "
        "angle_degrees=0.0, "
        "angle_radians=0.0, "
        "confidence=0.95, "
        "embedding_status=NONE, "
        "is_noise=False"
        ")"
    )


@pytest.mark.unit
def test_receipt_line_iter(example_receipt_line: ReceiptLine) -> None:
    """Test the iter method of ReceiptLine."""
    receipt_line_dict = dict(example_receipt_line)
    expected_keys = {
        "receipt_id",
        "image_id",
        "line_id",
        "text",
        "bounding_box",
        "top_left",
        "top_right",
        "bottom_left",
        "bottom_right",
        "angle_degrees",
        "angle_radians",
        "confidence",
        "embedding_status",
        "is_noise",
    }
    assert set(receipt_line_dict.keys()) == expected_keys
    assert receipt_line_dict["receipt_id"] == 1
    assert receipt_line_dict["image_id"] == (
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    )
    assert receipt_line_dict["line_id"] == 10
    assert receipt_line_dict["text"] == "Line text"
    assert receipt_line_dict["bounding_box"] == {
        "x": 0.1,
        "y": 0.2,
        "width": 0.5,
        "height": 0.2,
    }
    assert receipt_line_dict["top_left"] == {"x": 0.1, "y": 0.2}
    assert receipt_line_dict["top_right"] == {"x": 0.6, "y": 0.2}
    assert receipt_line_dict["bottom_left"] == {"x": 0.1, "y": 0.4}
    assert receipt_line_dict["bottom_right"] == {"x": 0.6, "y": 0.4}
    assert receipt_line_dict["angle_degrees"] == 0.0
    assert receipt_line_dict["angle_radians"] == 0.0
    assert receipt_line_dict["confidence"] == 0.95
    assert ReceiptLine(**receipt_line_dict) == example_receipt_line


@pytest.mark.unit
def test_receipt_line_serialize(example_receipt_line: ReceiptLine) -> None:
    """Test that ReceiptLine can be serialized and deserialized."""
    assert example_receipt_line == ReceiptLine(
        **json.loads(json.dumps(dict(example_receipt_line)))
    )


@pytest.mark.unit
def test_item_to_receipt_line(example_receipt_line: ReceiptLine) -> None:
    """Test the item_to_receipt_line function."""
    assert (
        item_to_receipt_line(example_receipt_line.to_item())
        == example_receipt_line
    )

    # Missing keys
    with pytest.raises(
        ValueError,
        match="^Item is missing required keys",
    ):
        item_to_receipt_line({})

    # Bad keys
    with pytest.raises(
        ValueError,
        match="^Error converting item to ReceiptLine",
    ):
        item_to_receipt_line(
            {
                "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
                "SK": {"S": "RECEIPT#00001#LINE#00010"},
                "TYPE": {"S": "RECEIPT_LINE"},
                "text": {"N": "0"},  # Invalid
                "bounding_box": {
                    "M": {
                        "height": {"N": "0.200000000000000000"},
                        "width": {"N": "0.500000000000000000"},
                        "x": {"N": "0.100000000000000000"},
                        "y": {"N": "0.200000000000000000"},
                    }
                },
                "top_right": {
                    "M": {
                        "x": {"N": "0.600000000000000000"},
                        "y": {"N": "0.200000000000000000"},
                    }
                },
                "top_left": {
                    "M": {
                        "x": {"N": "0.100000000000000000"},
                        "y": {"N": "0.200000000000000000"},
                    }
                },
                "bottom_right": {
                    "M": {
                        "x": {"N": "0.600000000000000000"},
                        "y": {"N": "0.400000000000000000"},
                    }
                },
                "bottom_left": {
                    "M": {
                        "x": {"N": "0.100000000000000000"},
                        "y": {"N": "0.400000000000000000"},
                    }
                },
                "angle_degrees": {"N": "0.0000000000"},
                "angle_radians": {"N": "0.0000000000"},
                "confidence": {"N": "0.95"},
                "embedding_status": {"S": "NONE"},
            }
        )
