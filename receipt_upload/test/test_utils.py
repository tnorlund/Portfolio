import pytest
from receipt_upload.utils import image_ocr_to_receipt_ocr

from receipt_dynamo.entities import (
    Letter,
    Line,
    ReceiptLetter,
    ReceiptLine,
    ReceiptWord,
    Word,
)


@pytest.fixture
def image_lines_words_letters():
    return [
        [
            Line(
                image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
                line_id=1,
                text="Hello",
                bounding_box={
                    "x": 10.0,
                    "y": 20.0,
                    "width": 5.0,
                    "height": 2.0,
                },
                top_right={"x": 15.0, "y": 20.0},
                top_left={"x": 10.0, "y": 20.0},
                bottom_right={"x": 15.0, "y": 22.0},
                bottom_left={"x": 10.0, "y": 22.0},
                angle_degrees=0,
                angle_radians=0,
                confidence=1.0,
            )
        ],
        [
            Word(
                image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
                line_id=1,
                word_id=1,
                text="Hello",
                bounding_box={
                    "x": 10.0,
                    "y": 20.0,
                    "width": 5.0,
                    "height": 2.0,
                },
                top_right={"x": 15.0, "y": 20.0},
                top_left={"x": 10.0, "y": 20.0},
                bottom_right={"x": 15.0, "y": 22.0},
                bottom_left={"x": 10.0, "y": 22.0},
                angle_degrees=0,
                angle_radians=0,
                confidence=1.0,
            )
        ],
        [
            Letter(
                image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
                line_id=1,
                word_id=1,
                letter_id=1,
                text="H",
                bounding_box={
                    "x": 10.0,
                    "y": 20.0,
                    "width": 5.0,
                    "height": 2.0,
                },
                top_right={"x": 15.0, "y": 20.0},
                top_left={"x": 10.0, "y": 20.0},
                bottom_right={"x": 15.0, "y": 22.0},
                bottom_left={"x": 10.0, "y": 22.0},
                angle_degrees=0,
                angle_radians=0,
                confidence=1.0,
            )
        ],
    ]


@pytest.mark.unit
def test_image_ocr_to_receipt_ocr(image_lines_words_letters):
    receipt_lines, receipt_words, receipt_letters = image_ocr_to_receipt_ocr(
        image_lines_words_letters[0],
        image_lines_words_letters[1],
        image_lines_words_letters[2],
        receipt_id=1,
    )

    # Check return types
    assert isinstance(receipt_lines, list)
    assert isinstance(receipt_words, list)
    assert isinstance(receipt_letters, list)

    # Check that each list contains the correct type
    assert all(isinstance(line, ReceiptLine) for line in receipt_lines)
    assert all(isinstance(word, ReceiptWord) for word in receipt_words)
    assert all(isinstance(letter, ReceiptLetter) for letter in receipt_letters)

    # Check that we have the same number of items
    assert len(receipt_lines) == len(image_lines_words_letters[0])
    assert len(receipt_words) == len(image_lines_words_letters[1])
    assert len(receipt_letters) == len(image_lines_words_letters[2])
