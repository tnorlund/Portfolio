import pytest

from receipt_dynamo import ReceiptWord, itemToReceiptWord


@pytest.fixture
def example_receipt_word():
    return ReceiptWord(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=3,
        word_id=4,
        text="Test",
        bounding_box={
            "x": 0.1,
            "y": 0.2,
            "width": 0.3,
            "height": 0.4,
        },
        top_right={"x": 1.0, "y": 2.0},
        top_left={"x": 1.0, "y": 3.0},
        bottom_right={"x": 4.0, "y": 2.0},
        bottom_left={"x": 1.0, "y": 1.0},
        angle_degrees=1.0,
        angle_radians=5.0,
        confidence=0.9,
    )


@pytest.fixture
def example_receipt_word_with_tags():
    return ReceiptWord(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=3,
        word_id=4,
        text="Test",
        bounding_box={
            "x": 0.1,
            "y": 0.2,
            "width": 0.3,
            "height": 0.4,
        },
        top_right={"x": 1.0, "y": 2.0},
        top_left={"x": 1.0, "y": 3.0},
        bottom_right={"x": 4.0, "y": 2.0},
        bottom_left={"x": 1.0, "y": 1.0},
        angle_degrees=1.0,
        angle_radians=5.0,
        confidence=0.9,
        tags=["example", "word"],
    )


@pytest.mark.unit
def test_receipt_word_init_valid(
    example_receipt_word, example_receipt_word_with_tags
):
    """Test that a ReceiptWord with valid arguments initializes correctly."""
    assert example_receipt_word.receipt_id == 1
    assert (
        example_receipt_word.image_id == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    )
    assert example_receipt_word.line_id == 3
    assert example_receipt_word.word_id == 4
    assert example_receipt_word.text == "Test"
    assert example_receipt_word.bounding_box == {
        "x": 0.1,
        "y": 0.2,
        "width": 0.3,
        "height": 0.4,
    }
    assert example_receipt_word.top_right == {"x": 1.0, "y": 2.0}
    assert example_receipt_word.top_left == {"x": 1.0, "y": 3.0}
    assert example_receipt_word.bottom_right == {"x": 4.0, "y": 2.0}
    assert example_receipt_word.bottom_left == {"x": 1.0, "y": 1.0}
    assert example_receipt_word.angle_degrees == 1.0
    assert example_receipt_word.angle_radians == 5.0
    assert example_receipt_word.confidence == 0.9
    assert example_receipt_word_with_tags.tags == ["example", "word"]


@pytest.mark.unit
def test_receipt_word_init_invalid_receipt_id():
    with pytest.raises(ValueError, match="^receipt_id must be an integer"):
        ReceiptWord(
            receipt_id="1",  # Not an integer
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=3,
            word_id=4,
            text="Test",
            bounding_box={
                "x": 0.1,
                "y": 0.2,
                "width": 0.3,
                "height": 0.4,
            },
            top_right={"x": 1.0, "y": 2.0},
            top_left={"x": 1.0, "y": 3.0},
            bottom_right={"x": 4.0, "y": 2.0},
            bottom_left={"x": 1.0, "y": 1.0},
            angle_degrees=1.0,
            angle_radians=5.0,
            confidence=0.9,
        )
    with pytest.raises(ValueError, match="^receipt_id must be positive"):
        ReceiptWord(
            receipt_id=-1,  # Negative
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=3,
            word_id=4,
            text="Test",
            bounding_box={
                "x": 0.1,
                "y": 0.2,
                "width": 0.3,
                "height": 0.4,
            },
            top_right={"x": 1.0, "y": 2.0},
            top_left={"x": 1.0, "y": 3.0},
            bottom_right={"x": 4.0, "y": 2.0},
            bottom_left={"x": 1.0, "y": 1.0},
            angle_degrees=1.0,
            angle_radians=5.0,
            confidence=0.9,
        )


@pytest.mark.unit
def test_receipt_word_init_invalid_uuid():
    """Test that invalid UUIDs raise ValueError."""
    with pytest.raises(ValueError, match="uuid must be a string"):
        ReceiptWord(
            receipt_id=1,
            image_id=3,  # Not a string
            line_id=3,
            word_id=4,
            text="Test",
            bounding_box={
                "x": 0.1,
                "y": 0.2,
                "width": 0.3,
                "height": 0.4,
            },
            top_right={"x": 1.0, "y": 2.0},
            top_left={"x": 1.0, "y": 3.0},
            bottom_right={"x": 4.0, "y": 2.0},
            bottom_left={"x": 1.0, "y": 1.0},
            angle_degrees=1.0,
            angle_radians=5.0,
            confidence=0.9,
        )
    with pytest.raises(ValueError, match="uuid must be a valid UUIDv4"):
        ReceiptWord(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed",
            line_id=3,
            word_id=4,
            text="Test",
            bounding_box={
                "x": 0.1,
                "y": 0.2,
                "width": 0.3,
                "height": 0.4,
            },
            top_right={"x": 1.0, "y": 2.0},
            top_left={"x": 1.0, "y": 3.0},
            bottom_right={"x": 4.0, "y": 2.0},
            bottom_left={"x": 1.0, "y": 1.0},
            angle_degrees=1.0,
            angle_radians=5.0,
            confidence=0.9,
        )


@pytest.mark.unit
def test_receipt_word_init_invalid_line_id():
    with pytest.raises(ValueError, match="^line_id must be an integer"):
        ReceiptWord(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id="3",  # Not an integer
            word_id=4,
            text="Test",
            bounding_box={
                "x": 0.1,
                "y": 0.2,
                "width": 0.3,
                "height": 0.4,
            },
            top_right={"x": 1.0, "y": 2.0},
            top_left={"x": 1.0, "y": 3.0},
            bottom_right={"x": 4.0, "y": 2.0},
            bottom_left={"x": 1.0, "y": 1.0},
            angle_degrees=1.0,
            angle_radians=5.0,
            confidence=0.9,
        )
    with pytest.raises(ValueError, match="^line_id must be positive"):
        ReceiptWord(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=-3,  # Negative
            word_id=4,
            text="Test",
            bounding_box={
                "x": 0.1,
                "y": 0.2,
                "width": 0.3,
                "height": 0.4,
            },
            top_right={"x": 1.0, "y": 2.0},
            top_left={"x": 1.0, "y": 3.0},
            bottom_right={"x": 4.0, "y": 2.0},
            bottom_left={"x": 1.0, "y": 1.0},
            angle_degrees=1.0,
            angle_radians=5.0,
            confidence=0.9,
        )


@pytest.mark.unit
def test_receipt_word_init_invalid_id():
    with pytest.raises(ValueError, match="^id must be an integer"):
        ReceiptWord(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=3,
            word_id="4",  # Not an integer
            text="Test",
            bounding_box={
                "x": 0.1,
                "y": 0.2,
                "width": 0.3,
                "height": 0.4,
            },
            top_right={"x": 1.0, "y": 2.0},
            top_left={"x": 1.0, "y": 3.0},
            bottom_right={"x": 4.0, "y": 2.0},
            bottom_left={"x": 1.0, "y": 1.0},
            angle_degrees=1.0,
            angle_radians=5.0,
            confidence=0.9,
        )
    with pytest.raises(ValueError, match="^id must be positive"):
        ReceiptWord(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=3,
            word_id=-4,  # Negative
            text="Test",
            bounding_box={
                "x": 0.1,
                "y": 0.2,
                "width": 0.3,
                "height": 0.4,
            },
            top_right={"x": 1.0, "y": 2.0},
            top_left={"x": 1.0, "y": 3.0},
            bottom_right={"x": 4.0, "y": 2.0},
            bottom_left={"x": 1.0, "y": 1.0},
            angle_degrees=1.0,
            angle_radians=5.0,
            confidence=0.9,
        )


@pytest.mark.unit
def test_receipt_word_init_invalid_text():
    with pytest.raises(ValueError, match="^text must be a string"):
        ReceiptWord(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=3,
            word_id=4,
            text=1,  # Not a string
            bounding_box={
                "x": 0.1,
                "y": 0.2,
                "width": 0.3,
                "height": 0.4,
            },
            top_right={"x": 1.0, "y": 2.0},
            top_left={"x": 1.0, "y": 3.0},
            bottom_right={"x": 4.0, "y": 2.0},
            bottom_left={"x": 1.0, "y": 1.0},
            angle_degrees=1.0,
            angle_radians=5.0,
            confidence=0.9,
        )


@pytest.mark.unit
def test_receipt_word_init_invalid_bounding_box():
    """Test that invalid bounding_box keys or types raise ValueError."""
    with pytest.raises(
        ValueError,
        match="bounding_box must contain the key 'width'",
    ):
        ReceiptWord(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=3,
            word_id=4,
            text="Test",
            bounding_box={"x": 0.1, "y": 0.2, "height": 0.4},  # Missing width
            top_right={"x": 1.0, "y": 2.0},
            top_left={"x": 1.0, "y": 2.0},
            bottom_right={"x": 1.0, "y": 2.0},
            bottom_left={"x": 1.0, "y": 2.0},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.9,
        )


@pytest.mark.unit
def test_receipt_word_corners():
    """Test that invalid point keys or types raise ValueError."""
    with pytest.raises(ValueError, match="point must contain the key 'y'"):
        ReceiptWord(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=3,
            word_id=4,
            text="Test",
            bounding_box={
                "x": 0.1,
                "y": 0.2,
                "width": 0.3,
                "height": 0.4,
            },
            top_right={"x": 1.0},  # Missing y
            top_left={"x": 1.0, "y": 2.0},
            bottom_right={"x": 1.0, "y": 2.0},
            bottom_left={"x": 1.0, "y": 2.0},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.9,
        )


@pytest.mark.unit
def test_receipt_word_angle_validation():
    """Test that angles outside [0, 360) raise ValueError."""
    with pytest.raises(
        ValueError,
        match="angle_degrees must be a float or int",
    ):
        ReceiptWord(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=1,
            word_id=1,
            text="Test",
            bounding_box={"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0},
            top_right={"x": 1.0, "y": 2.0},
            top_left={"x": 1.0, "y": 2.0},
            bottom_right={"x": 1.0, "y": 2.0},
            bottom_left={"x": 1.0, "y": 2.0},
            angle_degrees="0.0",
            angle_radians=0.0,
            confidence=0.9,
        )
    with pytest.raises(
        ValueError,
        match="angle_radians must be a float or int",
    ):
        ReceiptWord(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=1,
            word_id=1,
            text="Test",
            bounding_box={"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0},
            top_right={"x": 1.0, "y": 2.0},
            top_left={"x": 1.0, "y": 2.0},
            bottom_right={"x": 1.0, "y": 2.0},
            bottom_left={"x": 1.0, "y": 2.0},
            angle_degrees=0.0,
            angle_radians="0.0",
            confidence=0.9,
        )


@pytest.mark.unit
def test_receipt_word_init_invalid_confidence():
    """Test that confidence outside (0,1] raises ValueError."""
    with pytest.raises(ValueError, match="confidence must be a float"):
        ReceiptWord(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=1,
            word_id=1,
            text="Test",
            bounding_box={"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0},
            top_right={"x": 1.0, "y": 2.0},
            top_left={"x": 1.0, "y": 2.0},
            bottom_right={"x": 1.0, "y": 2.0},
            bottom_left={"x": 1.0, "y": 2.0},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence="0.9",
        )
    receipt = ReceiptWord(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=1,
        word_id=1,
        text="Test",
        bounding_box={"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0},
        top_right={"x": 1.0, "y": 2.0},
        top_left={"x": 1.0, "y": 2.0},
        bottom_right={"x": 1.0, "y": 2.0},
        bottom_left={"x": 1.0, "y": 2.0},
        angle_degrees=0.0,
        angle_radians=0.0,
        confidence=1,
    )
    assert receipt.confidence == 1.0
    with pytest.raises(ValueError, match="confidence must be between 0 and 1"):
        ReceiptWord(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=1,
            word_id=1,
            text="Test",
            bounding_box={"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0},
            top_right={"x": 1.0, "y": 2.0},
            top_left={"x": 1.0, "y": 2.0},
            bottom_right={"x": 1.0, "y": 2.0},
            bottom_left={"x": 1.0, "y": 2.0},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=1.1,
        )


@pytest.mark.unit
def test_receipt_word_init_invalid_tags():
    """Test that tags must be a list of strings."""
    with pytest.raises(ValueError, match="tags must be a list"):
        ReceiptWord(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=1,
            word_id=1,
            text="Test",
            bounding_box={"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0},
            top_right={"x": 1.0, "y": 2.0},
            top_left={"x": 1.0, "y": 2.0},
            bottom_right={"x": 1.0, "y": 2.0},
            bottom_left={"x": 1.0, "y": 2.0},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.9,
            tags="tag1",
        )


@pytest.mark.unit
def test_receipt_word_to_item():
    """Test that to_item() returns a properly formatted DynamoDB item."""
    bounding_box = {"x": 0.123456789012, "y": 0.2, "width": 0.3, "height": 0.4}
    point = {"x": 1.0001, "y": 2.0001}

    word = ReceiptWord(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=3,
        word_id=4,
        text="TestWord",
        bounding_box=bounding_box,
        top_right=point,
        top_left=point,
        bottom_right=point,
        bottom_left=point,
        angle_degrees=45.0,
        angle_radians=0.7853981634,
        confidence=0.95,
        tags=["tag1", "tag2"],
    )
    item = word.to_item()
    assert item["PK"]["S"] == "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert item["SK"]["S"] == "RECEIPT#00001#LINE#00003#WORD#00004"
    assert item["bounding_box"]["M"]["x"]["N"]
    assert "SS" in item["tags"]


@pytest.mark.unit
def test_repr(example_receipt_word):
    """Test that the __repr__ method returns a string."""
    assert isinstance(repr(example_receipt_word), str)
    assert str(example_receipt_word) == repr(example_receipt_word)
    # fmt: off
    assert (
        repr(example_receipt_word)
        == "ReceiptWord("
        "receipt_id=1, "
        "image_id='3f52804b-2fad-4e00-92c8-b593da3a8ed3', "
        "line_id=3, "
        "word_id=4, "
        "text='Test', "
        "bounding_box={'x': 0.1, 'y': 0.2, 'width': 0.3, 'height': 0.4}, "
        "top_right={'x': 1.0, 'y': 2.0}, "
        "top_left={'x': 1.0, 'y': 3.0}, "
        "bottom_right={'x': 4.0, 'y': 2.0}, "
        "bottom_left={'x': 1.0, 'y': 1.0}, "
        "angle_degrees=1.0, "
        "angle_radians=5.0, "
        "confidence=0.9)"
    )
    # fmt: on


@pytest.mark.unit
def test_receipt_word_eq():
    """Test that two ReceiptWords with the same attributes are equal."""
    bounding_box = {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4}
    point = {"x": 1.0, "y": 2.0}
    word1 = ReceiptWord(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=3,
        word_id=4,
        text="Test",
        bounding_box=bounding_box,
        top_right=point,
        top_left=point,
        bottom_right=point,
        bottom_left=point,
        angle_degrees=45.0,
        angle_radians=0.785398,
        confidence=0.99,
        tags=["example", "word"],
    )
    word2 = ReceiptWord(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=3,
        word_id=4,
        text="Test",
        bounding_box=bounding_box,
        top_right=point,
        top_left=point,
        bottom_right=point,
        bottom_left=point,
        angle_degrees=45.0,
        angle_radians=0.785398,
        confidence=0.99,
        tags=["example", "word"],
    )
    assert word1 == word2
    assert word1 != "Test"


@pytest.mark.unit
def test_receipt_word_iter(example_receipt_word_with_tags):
    """Test that the __iter__ method returns a dictionary."""
    receipt_word_dict = dict(example_receipt_word_with_tags)
    expected_keys = {
        "receipt_id",
        "image_id",
        "line_id",
        "word_id",
        "text",
        "bounding_box",
        "top_right",
        "top_left",
        "bottom_right",
        "bottom_left",
        "angle_degrees",
        "angle_radians",
        "confidence",
        "tags",
        "histogram",
        "num_chars",
    }
    assert set(receipt_word_dict.keys()) == expected_keys
    assert receipt_word_dict["receipt_id"] == 1
    assert (
        receipt_word_dict["image_id"] == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    )
    assert receipt_word_dict["line_id"] == 3
    assert receipt_word_dict["word_id"] == 4
    assert receipt_word_dict["text"] == "Test"
    assert receipt_word_dict["bounding_box"] == {
        "x": 0.1,
        "y": 0.2,
        "width": 0.3,
        "height": 0.4,
    }
    assert receipt_word_dict["top_right"] == {"x": 1.0, "y": 2.0}
    assert receipt_word_dict["top_left"] == {"x": 1.0, "y": 3.0}
    assert receipt_word_dict["bottom_right"] == {"x": 4.0, "y": 2.0}
    assert receipt_word_dict["bottom_left"] == {"x": 1.0, "y": 1.0}
    assert receipt_word_dict["angle_degrees"] == 1.0
    assert receipt_word_dict["angle_radians"] == 5.0
    assert receipt_word_dict["confidence"] == 0.9
    assert receipt_word_dict["tags"] == ["example", "word"]
    assert ReceiptWord(**receipt_word_dict) == example_receipt_word_with_tags


@pytest.mark.unit
def test_receipt_word_calculate_centroid(example_receipt_word):
    """Test that the centroid is calculated correctly."""
    assert example_receipt_word.calculate_centroid() == (1.75, 2.0)


@pytest.mark.unit
def test_receipt_word_distance_and_angle(example_receipt_word):
    other_receipt_word = ReceiptWord(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=3,
        word_id=5,
        text="Test",
        bounding_box={"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4},
        top_right={"x": 40.0, "y": 20.0},
        top_left={"x": 10.0, "y": 20.0},
        bottom_right={"x": 40.0, "y": 10.0},
        bottom_left={"x": 10.0, "y": 10.0},
        angle_degrees=1.0,
        angle_radians=5.0,
        confidence=0.9,
    )
    assert example_receipt_word.distance_and_angle_from_ReceiptWord(
        other_receipt_word
    ) == (26.637614382673235, 0.5098332286596837)


@pytest.mark.unit
def test_item_to_receipt_word_round_trip(example_receipt_word_with_tags):
    """Test that converting an item to ReceiptWord and back is consistent."""
    assert (
        itemToReceiptWord(example_receipt_word_with_tags.to_item())
        == example_receipt_word_with_tags
    )
    with pytest.raises(ValueError, match="^Item is missing required keys:"):
        itemToReceiptWord({})
    with pytest.raises(
        ValueError, match="^Error converting item to ReceiptWord"
    ):
        itemToReceiptWord(
            {
                "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
                "SK": {"S": "RECEIPT#00001#LINE#00003#WORD#00004"},
                "text": {"S": "Test"},
                "bounding_box": {"M": {"x": {"N": "0.1"}, "y": {"N": "0.2"}}},
                "top_right": {"M": {"x": {"N": "1.0"}, "y": {"N": "2.0"}}},
                "top_left": {"M": {"x": {"N": "1.0"}, "y": {"N": "3.0"}}},
                "bottom_right": {"M": {"x": {"N": "4.0"}, "y": {"N": "2.0"}}},
                "bottom_left": {"M": {"x": {"N": "1.0"}, "y": {"N": "1.0"}}},
                "angle_degrees": {"N": "1.0"},
                "angle_radians": {"N": "5.0"},
                "confidence": {"N": "0.9"},
                "tags": {"SS": ["example", "word"]},
            }
        )
