import pytest
from dynamo import ReceiptLine, itemToReceiptLine

def test_receipt_line_valid_init():
    line = ReceiptLine(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        id=10,
        text="Line text",
        bounding_box={"x": 0.1, "y": 0.2, "width": 0.5, "height": 0.2},
        top_left={"x": 0.1, "y": 0.2},
        top_right={"x": 0.6, "y": 0.2},
        bottom_left={"x": 0.1, "y": 0.4},
        bottom_right={"x": 0.6, "y": 0.4},
        angle_degrees=0.0,
        angle_radians=0.0,
        confidence=0.95
    )
    assert line.receipt_id == 1
    assert line.image_id == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert line.id == 10
    assert line.text == "Line text"
    assert line.bounding_box["x"] == 0.1
    assert line.confidence == 0.95

def test_receipt_line_invalid_id():
    with pytest.raises(ValueError):
        ReceiptLine(
            receipt_id=-1,  # invalid
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            id=1,
            text="Line text",
            bounding_box={"x": 0, "y": 0, "width": 1, "height": 1},
            top_left={"x": 0.1, "y": 0.2},
        top_right={"x": 0.6, "y": 0.2},
        bottom_left={"x": 0.1, "y": 0.4},
        bottom_right={"x": 0.6, "y": 0.4},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.95
        )

def test_receipt_line_to_item():
    line = ReceiptLine(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        id=3,
        text="Line text",
        bounding_box={"x": 0.1, "y": 0.2, "width": 0.5, "height": 0.2},
        top_left={"x": 0.1, "y": 0.2},
        top_right={"x": 0.6, "y": 0.2},
        bottom_left={"x": 0.1, "y": 0.4},
        bottom_right={"x": 0.6, "y": 0.4},
        angle_degrees=10.0,
        angle_radians=0.174533,
        confidence=0.90
    )
    item = line.to_item()
    assert "PK" in item
    assert "SK" in item
    assert "bounding_box" in item

def test_equal_receipt_line():
    line1 = ReceiptLine(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        id=3,
        text="Line text",
        bounding_box={"x": 0.1, "y": 0.2, "width": 0.5, "height": 0.2},
        top_left={"x": 0.1, "y": 0.2},
        top_right={"x": 0.6, "y": 0.2},
        bottom_left={"x": 0.1, "y": 0.4},
        bottom_right={"x": 0.6, "y": 0.4},
        angle_degrees=10.0,
        angle_radians=0.174533,
        confidence=0.90
    )
    line2 = ReceiptLine(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        id=3,
        text="Line text",
        bounding_box={"x": 0.1, "y": 0.2, "width": 0.5, "height": 0.2},
        top_left={"x": 0.1, "y": 0.2},
        top_right={"x": 0.6, "y": 0.2},
        bottom_left={"x": 0.1, "y": 0.4},
        bottom_right={"x": 0.6, "y": 0.4},
        angle_degrees=10.0,
        angle_radians=0.174533,
        confidence=0.90
    )
    assert line1 == line2

def test_item_to_receipt_line_round_trip():
    # Example item structure
    item = {
        "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": "RECEIPT#00001#LINE#00003"},
        "TYPE": {"S": "RECEIPT_LINE"},
        "text": {"S": "Line text"},
        "bounding_box": {
            "M": {
                "x": {"N": "0.1000000000"},
                "y": {"N": "0.2000000000"},
                "width": {"N": "0.5000000000"},
                "height": {"N": "0.2000000000"},
            }
        },
        "top_right": {
            "M": {"x": {"N": "0.6000000000"}, "y": {"N": "0.2000000000"}}
        },
        "top_left": {"M": {"x": {"N": "0.1000000000"}, "y": {"N": "0.2000000000"}}},
        "bottom_right": {
            "M": {"x": {"N": "0.6000000000"}, "y": {"N": "0.4000000000"}}
        },
        "bottom_left": {"M": {"x": {"N": "0.1000000000"}, "y": {"N": "0.4000000000"}}},
        "angle_degrees": {"N": "10.0"},
        "angle_radians": {"N": "0.1745330000"},
        "confidence": {"N": "0.90"},
    }
    line = itemToReceiptLine(item)
    assert line.receipt_id == 1
    assert line.image_id == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert line.id == 3
    assert line.text == "Line text"
    assert line.confidence == 0.90

    new_item = line.to_item()
    for k in ["PK", "SK", "TYPE", "text"]:
        assert k in new_item