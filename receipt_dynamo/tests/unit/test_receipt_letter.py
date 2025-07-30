from copy import deepcopy

import pytest

from receipt_dynamo import ReceiptLetter, item_to_receipt_letter


@pytest.fixture
def example_receipt_letter():
    """A pytest fixture for a sample ReceiptLetter object."""
    return ReceiptLetter(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=3,
        word_id=4,
        letter_id=5,
        text="A",
        bounding_box={
            "x": 0.1,
            "y": 0.2,
            "width": 0.05,
            "height": 0.05,
        },
        top_right={"x": 0.15, "y": 0.25},
        top_left={"x": 0.1, "y": 0.25},
        bottom_right={"x": 0.15, "y": 0.2},
        bottom_left={"x": 0.1, "y": 0.2},
        angle_degrees=5.0,
        angle_radians=0.0872665,
        confidence=0.98,
    )


@pytest.mark.unit
def test_receipt_letter_init_valid(example_receipt_letter):
    assert example_receipt_letter.receipt_id == 1
    assert (
        example_receipt_letter.image_id
        == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    )
    assert example_receipt_letter.line_id == 3
    assert example_receipt_letter.word_id == 4
    assert example_receipt_letter.letter_id == 5
    assert example_receipt_letter.text == "A"
    assert example_receipt_letter.bounding_box == {
        "x": 0.1,
        "y": 0.2,
        "width": 0.05,
        "height": 0.05,
    }
    assert example_receipt_letter.top_right == {"x": 0.15, "y": 0.25}
    assert example_receipt_letter.top_left == {"x": 0.1, "y": 0.25}
    assert example_receipt_letter.bottom_right == {"x": 0.15, "y": 0.2}
    assert example_receipt_letter.bottom_left == {"x": 0.1, "y": 0.2}
    assert example_receipt_letter.angle_degrees == 5.0
    assert example_receipt_letter.angle_radians == 0.0872665
    assert example_receipt_letter.confidence == 0.98


@pytest.mark.unit
def test_receipt_letter_init_invalid_receipt_id():
    """ReceiptLetter raises a ValueError if the receipt_id is not an integer"""
    with pytest.raises(ValueError, match="receipt_id must be an integer"):
        ReceiptLetter(
            receipt_id="1",
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=3,
            word_id=4,
            letter_id=5,
            text="A",
            bounding_box={
                "x": 0.1,
                "y": 0.2,
                "width": 0.05,
                "height": 0.05,
            },
            top_right={"x": 0.15, "y": 0.25},
            top_left={"x": 0.1, "y": 0.25},
            bottom_right={"x": 0.15, "y": 0.2},
            bottom_left={"x": 0.1, "y": 0.2},
            angle_degrees=5.0,
            angle_radians=0.0872665,
            confidence=0.98,
        )
    with pytest.raises(ValueError, match="receipt_id must be positive"):
        ReceiptLetter(
            receipt_id=-1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=3,
            word_id=4,
            letter_id=5,
            text="A",
            bounding_box={
                "x": 0.1,
                "y": 0.2,
                "width": 0.05,
                "height": 0.05,
            },
            top_right={"x": 0.15, "y": 0.25},
            top_left={"x": 0.1, "y": 0.25},
            bottom_right={"x": 0.15, "y": 0.2},
            bottom_left={"x": 0.1, "y": 0.2},
            angle_degrees=5.0,
            angle_radians=0.0872665,
            confidence=0.98,
        )


@pytest.mark.unit
def test_receipt_letter_init_invalid_uuid():
    """ReceiptLetter raises a ValueError if the image_id is not a string"""
    with pytest.raises(ValueError, match="uuid must be a string"):
        ReceiptLetter(
            receipt_id=1,
            image_id=1,
            line_id=3,
            word_id=4,
            letter_id=5,
            text="A",
            bounding_box={
                "x": 0.1,
                "y": 0.2,
                "width": 0.05,
                "height": 0.05,
            },
            top_right={"x": 0.15, "y": 0.25},
            top_left={"x": 0.1, "y": 0.25},
            bottom_right={"x": 0.15, "y": 0.2},
            bottom_left={"x": 0.1, "y": 0.2},
            angle_degrees=5.0,
            angle_radians=0.0872665,
            confidence=0.98,
        )
    with pytest.raises(ValueError, match="uuid must be a valid UUID"):
        ReceiptLetter(
            receipt_id=1,
            image_id="not-a-uuid",
            line_id=3,
            word_id=4,
            letter_id=5,
            text="A",
            bounding_box={
                "x": 0.1,
                "y": 0.2,
                "width": 0.05,
                "height": 0.05,
            },
            top_right={"x": 0.15, "y": 0.25},
            top_left={"x": 0.1, "y": 0.25},
            bottom_right={"x": 0.15, "y": 0.2},
            bottom_left={"x": 0.1, "y": 0.2},
            angle_degrees=5.0,
            angle_radians=0.0872665,
            confidence=0.98,
        )


@pytest.mark.unit
def test_receipt_letter_init_invalid_line_id():
    """ReceiptLetter raises a ValueError if the line_id is not an integer"""
    with pytest.raises(ValueError, match="line_id must be an integer"):
        ReceiptLetter(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id="3",
            word_id=4,
            letter_id=5,
            text="A",
            bounding_box={
                "x": 0.1,
                "y": 0.2,
                "width": 0.05,
                "height": 0.05,
            },
            top_right={"x": 0.15, "y": 0.25},
            top_left={"x": 0.1, "y": 0.25},
            bottom_right={"x": 0.15, "y": 0.2},
            bottom_left={"x": 0.1, "y": 0.2},
            angle_degrees=5.0,
            angle_radians=0.0872665,
            confidence=0.98,
        )
    with pytest.raises(ValueError, match="line_id must be positive"):
        ReceiptLetter(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=-3,
            word_id=4,
            letter_id=5,
            text="A",
            bounding_box={
                "x": 0.1,
                "y": 0.2,
                "width": 0.05,
                "height": 0.05,
            },
            top_right={"x": 0.15, "y": 0.25},
            top_left={"x": 0.1, "y": 0.25},
            bottom_right={"x": 0.15, "y": 0.2},
            bottom_left={"x": 0.1, "y": 0.2},
            angle_degrees=5.0,
            angle_radians=0.0872665,
            confidence=0.98,
        )


@pytest.mark.unit
def test_receipt_letter_init_invalid_word_id():
    """ReceiptLetter raises a ValueError if the word_id is not an integer"""
    with pytest.raises(ValueError, match="word_id must be an integer"):
        ReceiptLetter(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=3,
            word_id="4",
            letter_id=5,
            text="A",
            bounding_box={
                "x": 0.1,
                "y": 0.2,
                "width": 0.05,
                "height": 0.05,
            },
            top_right={"x": 0.15, "y": 0.25},
            top_left={"x": 0.1, "y": 0.25},
            bottom_right={"x": 0.15, "y": 0.2},
            bottom_left={"x": 0.1, "y": 0.2},
            angle_degrees=5.0,
            angle_radians=0.0872665,
            confidence=0.98,
        )
    with pytest.raises(ValueError, match="word_id must be positive"):
        ReceiptLetter(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=3,
            word_id=-4,
            letter_id=5,
            text="A",
            bounding_box={
                "x": 0.1,
                "y": 0.2,
                "width": 0.05,
                "height": 0.05,
            },
            top_right={"x": 0.15, "y": 0.25},
            top_left={"x": 0.1, "y": 0.25},
            bottom_right={"x": 0.15, "y": 0.2},
            bottom_left={"x": 0.1, "y": 0.2},
            angle_degrees=5.0,
            angle_radians=0.0872665,
            confidence=0.98,
        )


@pytest.mark.unit
def test_receipt_letter_init_invalid_id():
    """ReceiptLetter raises a ValueError if the id is not an integer"""
    with pytest.raises(ValueError, match="id must be an integer"):
        ReceiptLetter(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=3,
            word_id=4,
            letter_id="5",
            text="A",
            bounding_box={
                "x": 0.1,
                "y": 0.2,
                "width": 0.05,
                "height": 0.05,
            },
            top_right={"x": 0.15, "y": 0.25},
            top_left={"x": 0.1, "y": 0.25},
            bottom_right={"x": 0.15, "y": 0.2},
            bottom_left={"x": 0.1, "y": 0.2},
            angle_degrees=5.0,
            angle_radians=0.0872665,
            confidence=0.98,
        )
    with pytest.raises(ValueError, match="id must be positive"):
        ReceiptLetter(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=3,
            word_id=4,
            letter_id=-5,
            text="A",
            bounding_box={
                "x": 0.1,
                "y": 0.2,
                "width": 0.05,
                "height": 0.05,
            },
            top_right={"x": 0.15, "y": 0.25},
            top_left={"x": 0.1, "y": 0.25},
            bottom_right={"x": 0.15, "y": 0.2},
            bottom_left={"x": 0.1, "y": 0.2},
            angle_degrees=5.0,
            angle_radians=0.0872665,
            confidence=0.98,
        )


@pytest.mark.unit
def test_receipt_letter_init_invalid_text():
    """ReceiptLetter raises a ValueError if the text is not a string"""
    with pytest.raises(ValueError, match="text must be a string"):
        ReceiptLetter(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=3,
            word_id=4,
            letter_id=5,
            text=1,
            bounding_box={
                "x": 0.1,
                "y": 0.2,
                "width": 0.05,
                "height": 0.05,
            },
            top_right={"x": 0.15, "y": 0.25},
            top_left={"x": 0.1, "y": 0.25},
            bottom_right={"x": 0.15, "y": 0.2},
            bottom_left={"x": 0.1, "y": 0.2},
            angle_degrees=5.0,
            angle_radians=0.0872665,
            confidence=0.98,
        )
    with pytest.raises(ValueError, match="text must be exactly one character"):
        ReceiptLetter(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=3,
            word_id=4,
            letter_id=5,
            text="AB",
            bounding_box={
                "x": 0.1,
                "y": 0.2,
                "width": 0.05,
                "height": 0.05,
            },
            top_right={"x": 0.15, "y": 0.25},
            top_left={"x": 0.1, "y": 0.25},
            bottom_right={"x": 0.15, "y": 0.2},
            bottom_left={"x": 0.1, "y": 0.2},
            angle_degrees=5.0,
            angle_radians=0.0872665,
            confidence=0.98,
        )


@pytest.mark.unit
def test_receipt_letter_init_invalid_bounding_box():
    """ReceiptLetter raises a ValueError if the bounding_box is not a dict"""
    with pytest.raises(ValueError, match="bounding_box must be a dictionary"):
        ReceiptLetter(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=3,
            word_id=4,
            letter_id=5,
            text="A",
            bounding_box=1,
            top_right={"x": 0.15, "y": 0.25},
            top_left={"x": 0.1, "y": 0.25},
            bottom_right={"x": 0.15, "y": 0.2},
            bottom_left={"x": 0.1, "y": 0.2},
            angle_degrees=5.0,
            angle_radians=0.0872665,
            confidence=0.98,
        )
    with pytest.raises(
        ValueError,
        match="^bounding_box must contain the key ",
    ):
        ReceiptLetter(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=3,
            word_id=4,
            letter_id=5,
            text="A",
            bounding_box={"x": 0.1, "y": 0.2, "width": 0.05},
            top_right={"x": 0.15, "y": 0.25},
            top_left={"x": 0.1, "y": 0.25},
            bottom_right={"x": 0.15, "y": 0.2},
            bottom_left={"x": 0.1, "y": 0.2},
            angle_degrees=5.0,
            angle_radians=0.0872665,
            confidence=0.98,
        )


@pytest.mark.unit
def test_receipt_letter_init_invalid_top_right():
    """ReceiptLetter raises a ValueError if the top_right is not a dict"""
    with pytest.raises(ValueError, match="point must be a dictionary"):
        ReceiptLetter(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=3,
            word_id=4,
            letter_id=5,
            text="A",
            bounding_box={
                "x": 0.1,
                "y": 0.2,
                "width": 0.05,
                "height": 0.05,
            },
            top_right=1,
            top_left={"x": 0.1, "y": 0.25},
            bottom_right={"x": 0.15, "y": 0.2},
            bottom_left={"x": 0.1, "y": 0.2},
            angle_degrees=5.0,
            angle_radians=0.0872665,
            confidence=0.98,
        )
    with pytest.raises(ValueError, match="^point must contain the key "):
        ReceiptLetter(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=3,
            word_id=4,
            letter_id=5,
            text="A",
            bounding_box={
                "x": 0.1,
                "y": 0.2,
                "width": 0.05,
                "height": 0.05,
            },
            top_right={"x": 0.15},
            top_left={"x": 0.1, "y": 0.25},
            bottom_right={"x": 0.15, "y": 0.2},
            bottom_left={"x": 0.1, "y": 0.2},
            angle_degrees=5.0,
            angle_radians=0.0872665,
            confidence=0.98,
        )


@pytest.mark.unit
def test_receipt_letter_init_invalid_top_left():
    """ReceiptLetter raises a ValueError if the top_left is not a dict"""
    with pytest.raises(ValueError, match="point must be a dictionary"):
        ReceiptLetter(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=3,
            word_id=4,
            letter_id=5,
            text="A",
            bounding_box={
                "x": 0.1,
                "y": 0.2,
                "width": 0.05,
                "height": 0.05,
            },
            top_right={"x": 0.15, "y": 0.25},
            top_left=1,
            bottom_right={"x": 0.15, "y": 0.2},
            bottom_left={"x": 0.1, "y": 0.2},
            angle_degrees=5.0,
            angle_radians=0.0872665,
            confidence=0.98,
        )
    with pytest.raises(ValueError, match="^point must contain the key "):
        ReceiptLetter(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=3,
            word_id=4,
            letter_id=5,
            text="A",
            bounding_box={
                "x": 0.1,
                "y": 0.2,
                "width": 0.05,
                "height": 0.05,
            },
            top_right={"x": 0.15, "y": 0.25},
            top_left={"x": 0.1},
            bottom_right={"x": 0.15, "y": 0.2},
            bottom_left={"x": 0.1, "y": 0.2},
            angle_degrees=5.0,
            angle_radians=0.0872665,
            confidence=0.98,
        )


@pytest.mark.unit
def test_receipt_letter_init_invalid_bottom_right():
    """ReceiptLetter raises a ValueError if the bottom_right is not a dict"""
    with pytest.raises(ValueError, match="point must be a dictionary"):
        ReceiptLetter(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=3,
            word_id=4,
            letter_id=5,
            text="A",
            bounding_box={
                "x": 0.1,
                "y": 0.2,
                "width": 0.05,
                "height": 0.05,
            },
            top_right={"x": 0.15, "y": 0.25},
            top_left={"x": 0.1, "y": 0.25},
            bottom_right=1,
            bottom_left={"x": 0.1, "y": 0.2},
            angle_degrees=5.0,
            angle_radians=0.0872665,
            confidence=0.98,
        )
    with pytest.raises(ValueError, match="^point must contain the key "):
        ReceiptLetter(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=3,
            word_id=4,
            letter_id=5,
            text="A",
            bounding_box={
                "x": 0.1,
                "y": 0.2,
                "width": 0.05,
                "height": 0.05,
            },
            top_right={"x": 0.15, "y": 0.25},
            top_left={"x": 0.1, "y": 0.25},
            bottom_right={"x": 0.15},
            bottom_left={"x": 0.1, "y": 0.2},
            angle_degrees=5.0,
            angle_radians=0.0872665,
            confidence=0.98,
        )


@pytest.mark.unit
def test_receipt_letter_init_invalid_bottom_left():
    """ReceiptLetter raises a ValueError if the bottom_left is not a dict"""
    with pytest.raises(ValueError, match="point must be a dictionary"):
        ReceiptLetter(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=3,
            word_id=4,
            letter_id=5,
            text="A",
            bounding_box={
                "x": 0.1,
                "y": 0.2,
                "width": 0.05,
                "height": 0.05,
            },
            top_right={"x": 0.15, "y": 0.25},
            top_left={"x": 0.1, "y": 0.25},
            bottom_right={"x": 0.15, "y": 0.2},
            bottom_left=1,
            angle_degrees=5.0,
            angle_radians=0.0872665,
            confidence=0.98,
        )
    with pytest.raises(ValueError, match="^point must contain the key "):
        ReceiptLetter(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=3,
            word_id=4,
            letter_id=5,
            text="A",
            bounding_box={
                "x": 0.1,
                "y": 0.2,
                "width": 0.05,
                "height": 0.05,
            },
            top_right={"x": 0.15, "y": 0.25},
            top_left={"x": 0.1, "y": 0.25},
            bottom_right={"x": 0.15, "y": 0.2},
            bottom_left={"x": 0.1},
            angle_degrees=5.0,
            angle_radians=0.0872665,
            confidence=0.98,
        )


@pytest.mark.unit
def test_receipt_letter_init_invalid_angles():
    """ReceiptLetter raises a ValueError if the angles are not floats"""
    with pytest.raises(
        ValueError, match="angle_degrees must be float or int, got"
    ):
        ReceiptLetter(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=3,
            word_id=4,
            letter_id=5,
            text="A",
            bounding_box={
                "x": 0.1,
                "y": 0.2,
                "width": 0.05,
                "height": 0.05,
            },
            top_right={"x": 0.15, "y": 0.25},
            top_left={"x": 0.1, "y": 0.25},
            bottom_right={"x": 0.15, "y": 0.2},
            bottom_left={"x": 0.1, "y": 0.2},
            angle_degrees="5.0",
            angle_radians=0.0872665,
            confidence=0.98,
        )
    with pytest.raises(
        ValueError, match="angle_radians must be float or int, got"
    ):
        ReceiptLetter(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=3,
            word_id=4,
            letter_id=5,
            text="A",
            bounding_box={
                "x": 0.1,
                "y": 0.2,
                "width": 0.05,
                "height": 0.05,
            },
            top_right={"x": 0.15, "y": 0.25},
            top_left={"x": 0.1, "y": 0.25},
            bottom_right={"x": 0.15, "y": 0.2},
            bottom_left={"x": 0.1, "y": 0.2},
            angle_degrees=5.0,
            angle_radians="0.0872665",
            confidence=0.98,
        )


@pytest.mark.unit
def test_receipt_letter_init_invalid_confidence():
    """ValueError if the confidence is not a float or between 0 and 1"""
    with pytest.raises(
        ValueError, match="confidence must be float or int, got"
    ):
        ReceiptLetter(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=3,
            word_id=4,
            letter_id=5,
            text="A",
            bounding_box={"x": 0, "y": 0, "width": 1, "height": 1},
            top_right={"x": 0.15, "y": 0.25},
            top_left={"x": 0.1, "y": 0.25},
            bottom_right={"x": 0.15, "y": 0.2},
            bottom_left={"x": 0.1, "y": 0.2},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence="1",
        )
    receipt = ReceiptLetter(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=3,
        word_id=4,
        letter_id=5,
        text="A",
        bounding_box={"x": 0, "y": 0, "width": 1, "height": 1},
        top_right={"x": 0.15, "y": 0.25},
        top_left={"x": 0.1, "y": 0.25},
        bottom_right={"x": 0.15, "y": 0.2},
        bottom_left={"x": 0.1, "y": 0.2},
        angle_degrees=0.0,
        angle_radians=0.0,
        confidence=1,
    )
    assert receipt.confidence == 1
    with pytest.raises(
        ValueError, match="confidence must be between 0 and 1, got"
    ):
        ReceiptLetter(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=3,
            word_id=4,
            letter_id=5,
            text="A",
            bounding_box={"x": 0, "y": 0, "width": 1, "height": 1},
            top_right={"x": 0.15, "y": 0.25},
            top_left={"x": 0.1, "y": 0.25},
            bottom_right={"x": 0.15, "y": 0.2},
            bottom_left={"x": 0.1, "y": 0.2},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=1.1,
        )


@pytest.mark.unit
def test_to_item(example_receipt_letter):
    """Test that ReceiptLetter.to_item() returns the expected dictionary"""
    assert example_receipt_letter.to_item() == {
        "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": "RECEIPT#00001#LINE#00003#WORD#00004#LETTER#00005"},
        "TYPE": {"S": "RECEIPT_LETTER"},
        "text": {"S": "A"},
        "bounding_box": {
            "M": {
                "height": {"N": "0.05000000000000000000"},
                "width": {"N": "0.05000000000000000000"},
                "x": {"N": "0.10000000000000000000"},
                "y": {"N": "0.20000000000000000000"},
            }
        },
        "top_right": {
            "M": {
                "x": {"N": "0.15000000000000000000"},
                "y": {"N": "0.25000000000000000000"},
            }
        },
        "top_left": {
            "M": {
                "x": {"N": "0.10000000000000000000"},
                "y": {"N": "0.25000000000000000000"},
            }
        },
        "bottom_right": {
            "M": {
                "x": {"N": "0.15000000000000000000"},
                "y": {"N": "0.20000000000000000000"},
            }
        },
        "bottom_left": {
            "M": {
                "x": {"N": "0.10000000000000000000"},
                "y": {"N": "0.20000000000000000000"},
            }
        },
        "angle_degrees": {"N": "5.000000000000000000"},
        "angle_radians": {"N": "0.087266500000000000"},
        "confidence": {"N": "0.98"},
    }


@pytest.mark.unit
def test_eq(example_receipt_letter):
    """Test that ReceiptLetter.__eq__() works as expected"""
    letter1 = example_receipt_letter
    letter2 = deepcopy(example_receipt_letter)
    assert letter1 == letter2

    letter2.text = "B"
    assert letter1 != letter2

    letter2.text = "A"
    letter2.confidence = 0.99
    assert letter1 != letter2

    letter2.confidence = 0.98
    letter2.angle_degrees = 0.0
    assert letter1 != letter2

    assert letter1 != "not a ReceiptLetter"


@pytest.mark.unit
def test_iter(example_receipt_letter):
    """Test that ReceiptLetter.__iter__() works as expected"""
    receipt_letter_dict = dict(example_receipt_letter)
    expected_keys = {
        "receipt_id",
        "image_id",
        "line_id",
        "word_id",
        "letter_id",
        "text",
        "bounding_box",
        "top_right",
        "top_left",
        "bottom_right",
        "bottom_left",
        "angle_degrees",
        "angle_radians",
        "confidence",
    }
    assert set(receipt_letter_dict.keys()) == expected_keys
    assert receipt_letter_dict["receipt_id"] == 1
    assert (
        receipt_letter_dict["image_id"]
        == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    )
    assert receipt_letter_dict["line_id"] == 3
    assert receipt_letter_dict["word_id"] == 4
    assert receipt_letter_dict["letter_id"] == 5
    assert receipt_letter_dict["text"] == "A"
    assert receipt_letter_dict["bounding_box"] == {
        "x": 0.1,
        "y": 0.2,
        "width": 0.05,
        "height": 0.05,
    }
    assert receipt_letter_dict["top_right"] == {"x": 0.15, "y": 0.25}
    assert receipt_letter_dict["top_left"] == {"x": 0.1, "y": 0.25}
    assert receipt_letter_dict["bottom_right"] == {"x": 0.15, "y": 0.2}
    assert receipt_letter_dict["bottom_left"] == {"x": 0.1, "y": 0.2}
    assert receipt_letter_dict["angle_degrees"] == 5.0
    assert receipt_letter_dict["angle_radians"] == 0.0872665
    assert receipt_letter_dict["confidence"] == 0.98
    assert ReceiptLetter(**receipt_letter_dict) == example_receipt_letter


@pytest.mark.unit
def test_repr(example_receipt_letter):
    """Test that ReceiptLetter.__repr__() works as expected"""
    assert repr(example_receipt_letter) == (
        "ReceiptLetter("
        "receipt_id=1, "
        "image_id='3f52804b-2fad-4e00-92c8-b593da3a8ed3', "
        "line_id=3, "
        "word_id=4, "
        "letter_id=5, "
        "text='A', "
        "bounding_box={'x': 0.1, 'y': 0.2, 'width': 0.05, 'height': 0.05}, "
        "top_right={'x': 0.15, 'y': 0.25}, "
        "top_left={'x': 0.1, 'y': 0.25}, "
        "bottom_right={'x': 0.15, 'y': 0.2}, "
        "bottom_left={'x': 0.1, 'y': 0.2}, "
        "angle_degrees=5.0, "
        "angle_radians=0.0872665, "
        "confidence=0.98"
        ")"
    )


@pytest.mark.unit
def test_item_to_word(example_receipt_letter):
    """Test that item_to_receipt_letter() works as expected"""
    assert (
        item_to_receipt_letter(example_receipt_letter.to_item())
        == example_receipt_letter
    )
    # Missing Keys
    with pytest.raises(
        ValueError,
        match="^Item is missing required keys: ",
    ):
        item_to_receipt_letter({})
    with pytest.raises(
        ValueError,
        match="^Field 'text' must be a string type",
    ):
        item_to_receipt_letter(
            {
                "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
                "SK": {
                    "S": "RECEIPT#00001#LINE#00003#WORD#00004#LETTER#00005"
                },
                "TYPE": {"S": "RECEIPT_LETTER"},
                "text": {"N": "1"},
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
        )
