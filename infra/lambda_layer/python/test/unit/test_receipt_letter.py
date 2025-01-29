import pytest
from dynamo import ReceiptLetter, itemToReceiptLetter

def test_receipt_letter_valid_init():
    letter = ReceiptLetter(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=3,
        word_id=4,
        id=5,
        text="A",
        bounding_box={"x": 0.1, "y": 0.2, "width": 0.05, "height": 0.05},
        top_right={"x": 0.15, "y": 0.25},
        top_left={"x": 0.1, "y": 0.25},
        bottom_right={"x": 0.15, "y": 0.2},
        bottom_left={"x": 0.1, "y": 0.2},
        angle_degrees=5.0,
        angle_radians=0.0872665,
        confidence=0.98
    )
    assert letter.text == "A"
    assert letter.bounding_box["width"] == 0.05

def test_receipt_letter_invalid_confidence():
    with pytest.raises(ValueError):
        ReceiptLetter(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=3,
            word_id=4,
            id=5,
            text="A",
            bounding_box={"x": 0, "y": 0, "width": 1, "height": 1},
            top_right={"x": 0.15, "y": 0.25},
        top_left={"x": 0.1, "y": 0.25},
        bottom_right={"x": 0.15, "y": 0.2},
        bottom_left={"x": 0.1, "y": 0.2},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=1.1  # invalid
        )

def test_receipt_letter_to_item():
    letter = ReceiptLetter(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=3,
        word_id=4,
        id=5,
        text="B",
        bounding_box={"x": 0.05, "y": 0.10, "width": 0.05, "height": 0.05},
        top_right={"x": 0.15, "y": 0.25},
        top_left={"x": 0.1, "y": 0.25},
        bottom_right={"x": 0.15, "y": 0.2},
        bottom_left={"x": 0.1, "y": 0.2},
        angle_degrees=0.0,
        angle_radians=0.0,
        confidence=0.99
    )
    item = letter.to_item()
    assert item["PK"]["S"] == "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert item["SK"]["S"] == "RECEIPT#00001#LINE#00003#WORD#00004#LETTER#00005"
    assert "text" in item
    assert item["confidence"]["N"] == "0.99"

def test_equal_receipt_letter():
    letter1 = ReceiptLetter(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=3,
        word_id=4,
        id=5,
        text="B",
        bounding_box={"x": 0.05, "y": 0.10, "width": 0.05, "height": 0.05},
        top_right={"x": 0.15, "y": 0.25},
        top_left={"x": 0.1, "y": 0.25},
        bottom_right={"x": 0.15, "y": 0.2},
        bottom_left={"x": 0.1, "y": 0.2},
        angle_degrees=0.0,
        angle_radians=0.0,
        confidence=0.99
    )
    letter2 = ReceiptLetter(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=3,
        word_id=4,
        id=5,
        text="B",
        bounding_box={"x": 0.05, "y": 0.10, "width": 0.05, "height": 0.05},
        top_right={"x": 0.15, "y": 0.25},
        top_left={"x": 0.1, "y": 0.25},
        bottom_right={"x": 0.15, "y": 0.2},
        bottom_left={"x": 0.1, "y": 0.2},
        angle_degrees=0.0,
        angle_radians=0.0,
        confidence=0.99
    )
    assert letter1 == letter2

def test_item_to_receipt_letter_round_trip():
    item = {
        "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": "RECEIPT#00001#LINE#00003#WORD#00004#LETTER#00005"},
        "TYPE": {"S": "RECEIPT_LETTER"},
        "text": {"S": "C"},
        "bounding_box": {
            "M": {
                "x": {"N": "0.0500000000"},
                "y": {"N": "0.1000000000"},
                "width": {"N": "0.0500000000"},
                "height": {"N": "0.0500000000"},
            }
        },
        "top_right": {
            "M": {
                "x": {"N": "0.1500000000"},
                "y": {"N": "0.2500000000"},
            }
        },
        "top_left": {
            "M": {
                "x": {"N": "0.1000000000"},
                "y": {"N": "0.2500000000"},
            }
        },
        "bottom_right": {
            "M": {
                "x": {"N": "0.1500000000"},
                "y": {"N": "0.2000000000"},
            }
        },
        "bottom_left": {
            "M": {
                "x": {"N": "0.1000000000"},
                "y": {"N": "0.2000000000"},
            }
        },
        "angle_degrees": {"N": "0.0"},
        "angle_radians": {"N": "0.0"},
        "confidence": {"N": "0.99"},
    }
    letter = itemToReceiptLetter(item)
    assert letter.text == "C"
    assert letter.confidence == 0.99

    new_item = letter.to_item()
    for k in ["PK", "SK", "TYPE", "text"]:
        assert k in new_item