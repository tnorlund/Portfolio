import pytest
from decimal import Decimal
from dynamo import ReceiptWord, itemToReceiptWord

def test_receipt_word_valid_init():
    """Test that a ReceiptWord with valid arguments initializes correctly."""
    bounding_box = {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4}
    top_right    = {"x": 1.1, "y": 1.2}
    top_left     = {"x": 2.1, "y": 2.2}
    bottom_right = {"x": 3.1, "y": 3.2}
    bottom_left  = {"x": 4.1, "y": 4.2}

    word = ReceiptWord(
        receipt_id=1,
        image_id=2,
        line_id=3,
        id=4,
        text="Test",
        bounding_box=bounding_box,
        top_right=top_right,
        top_left=top_left,
        bottom_right=bottom_right,
        bottom_left=bottom_left,
        angle_degrees=45.0,
        angle_radians=0.785398,
        confidence=0.99,
        tags=["example", "word"],
    )

    assert word.receipt_id == 1
    assert word.image_id == 2
    assert word.line_id == 3
    assert word.id == 4
    assert word.text == "Test"
    assert word.bounding_box == bounding_box
    assert word.top_right == top_right
    assert word.top_left == top_left
    assert word.bottom_right == bottom_right
    assert word.bottom_left == bottom_left
    assert word.angle_degrees == 45.0
    assert word.angle_radians == 0.785398
    assert word.confidence == 0.99
    assert word.tags == ["example", "word"]

@pytest.mark.parametrize(
    "receipt_id,image_id,line_id,id_val,expect_error",
    [
        (-1, 1, 1, 1, True),  # Negative receipt_id
        (1, -2, 1, 1, True),  # Negative image_id
        (1, 2, -3, 1, True),  # Negative line_id
        (1, 2, 3, -4, True),  # Negative id
        (1, 2, 3, 4, False),  # All valid
    ],
)
def test_receipt_word_id_constraints(
    receipt_id, image_id, line_id, id_val, expect_error
):
    """Test that invalid IDs raise ValueError."""
    bounding_box = {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4}
    point        = {"x": 1.0, "y": 2.0}

    if expect_error:
        with pytest.raises(ValueError):
            ReceiptWord(
                receipt_id=receipt_id,
                image_id=image_id,
                line_id=line_id,
                id=id_val,
                text="Test",
                bounding_box=bounding_box,
                top_right=point,
                top_left=point,
                bottom_right=point,
                bottom_left=point,
                angle_degrees=0.0,
                angle_radians=0.0,
                confidence=0.9,
            )
    else:
        # Should not raise
        word = ReceiptWord(
            receipt_id=receipt_id,
            image_id=image_id,
            line_id=line_id,
            id=id_val,
            text="Test",
            bounding_box=bounding_box,
            top_right=point,
            top_left=point,
            bottom_right=point,
            bottom_left=point,
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.9,
        )
        assert word.receipt_id == receipt_id

def test_receipt_word_bounding_box_validation():
    """Test that invalid bounding_box keys or types raise ValueError."""
    # Missing width key
    bad_bounding_box = {"x": 0.1, "y": 0.2, "height": 0.4}
    point = {"x": 1.0, "y": 2.0}

    with pytest.raises(ValueError, match="bounding_box must contain the key 'width'"):
        ReceiptWord(
            receipt_id=1,
            image_id=2,
            line_id=3,
            id=4,
            text="Test",
            bounding_box=bad_bounding_box,
            top_right=point,
            top_left=point,
            bottom_right=point,
            bottom_left=point,
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.9,
        )

def test_receipt_word_confidence_validation():
    """Test that confidence outside (0,1] raises ValueError."""
    bounding_box = {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}
    point        = {"x": 1.0, "y": 2.0}

    with pytest.raises(ValueError, match="confidence must be a float between 0 and 1"):
        ReceiptWord(
            receipt_id=1,
            image_id=1,
            line_id=1,
            id=1,
            text="Test",
            bounding_box=bounding_box,
            top_right=point,
            top_left=point,
            bottom_right=point,
            bottom_left=point,
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=1.1,
        )

def test_receipt_word_to_item():
    """Test that to_item() returns a properly formatted DynamoDB item."""
    bounding_box = {"x": 0.123456789012, "y": 0.2, "width": 0.3, "height": 0.4}
    point        = {"x": 1.0001, "y": 2.0001}

    word = ReceiptWord(
        receipt_id=1,
        image_id=2,
        line_id=3,
        id=4,
        text="TestWord",
        bounding_box=bounding_box,
        top_right=point,
        top_left=point,
        bottom_right=point,
        bottom_left=point,
        angle_degrees=45.0,
        angle_radians=0.7853981634,
        confidence=0.95,
        tags=["tag1", "tag2"]
    )
    item = word.to_item()

    # Check keys
    assert item["PK"]["S"] == "IMAGE#00002"
    assert item["SK"]["S"] == "RECEIPT#00001#LINE#00003#WORD#00004"
    assert item["bounding_box"]["M"]["x"]["N"]  # numeric
    assert "SS" in item["tags"]

def test_item_to_receipt_word_round_trip():
    """Test that converting an item to ReceiptWord and back is consistent."""
    bounding_box = {"M": {
        "x": {"N": "0.123456789012"},
        "y": {"N": "0.200000000000"},
        "width": {"N": "0.300000000000"},
        "height": {"N": "0.400000000000"}
    }}
    point = {"M": {
        "x": {"N": "1.0001000000"},
        "y": {"N": "2.0001000000"}
    }}
    item = {
        "PK": {"S": "IMAGE#00002"},
        "SK": {"S": "RECEIPT#00001#LINE#00003#WORD#00004"},
        "TYPE": {"S": "RECEIPT_WORD"},
        "text": {"S": "TestWord"},
        "bounding_box": bounding_box,
        "top_right": point,
        "top_left": point,
        "bottom_right": point,
        "bottom_left": point,
        "angle_degrees": {"N": "45.0"},
        "angle_radians": {"N": "0.7853981634"},
        "confidence": {"N": "0.95"},
        "tags": {"SS": ["tag1", "tag2"]},
    }

    word = itemToReceiptWord(item)
    assert word.receipt_id == 1
    assert word.image_id == 2
    assert word.line_id == 3
    assert word.id == 4
    assert word.text == "TestWord"
    assert word.tags == ["tag1", "tag2"]
    
    # Convert back to item and ensure it still has the same top-level keys
    round_trip_item = word.to_item()
    for k in ["PK", "SK", "TYPE", "text", "bounding_box", "top_right"]:
        assert k in round_trip_item