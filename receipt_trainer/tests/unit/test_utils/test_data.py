"""Unit tests for data processing utilities."""

import pytest
from unittest.mock import Mock
from receipt_trainer.utils.data import process_receipt_details, create_sliding_windows


@pytest.fixture
def mock_receipt_details():
    """Create mock receipt details."""
    return {
        "receipt": Mock(
            image_id="test_img", receipt_id="test_rec", width=1000, height=1000
        ),
        "words": [
            Mock(
                word_id=1,
                line_id=1,
                text="Store",
                top_left={"x": 0.1, "y": 0.1},
                top_right={"x": 0.2, "y": 0.1},
                bottom_left={"x": 0.1, "y": 0.2},
                bottom_right={"x": 0.2, "y": 0.2},
            ),
            Mock(
                word_id=2,
                line_id=1,
                text="Name",
                top_left={"x": 0.3, "y": 0.1},
                top_right={"x": 0.4, "y": 0.1},
                bottom_left={"x": 0.3, "y": 0.2},
                bottom_right={"x": 0.4, "y": 0.2},
            ),
        ],
        "word_tags": [
            Mock(word_id=1, tag="store_name", human_validated=True),
            Mock(word_id=2, tag="store_name", human_validated=True),
        ],
    }


def test_process_receipt_details(mock_receipt_details):
    """Test receipt details processing."""
    result = process_receipt_details(mock_receipt_details)

    assert result is not None
    assert "words" in result
    assert "bboxes" in result
    assert "labels" in result
    assert len(result["words"]) == 2
    assert len(result["bboxes"]) == 2
    assert len(result["labels"]) == 2

    # Check IOB format
    assert result["labels"][0] == "B-store_name"  # First word should be B-
    assert result["labels"][1] == "I-store_name"  # Second word should be I-

    # Check coordinate scaling
    assert all(0 <= coord <= 1000 for bbox in result["bboxes"] for coord in bbox)


def test_process_receipt_details_no_validated_words():
    """Test processing with no validated words."""
    details = {
        "receipt": Mock(
            image_id="test_img", receipt_id="test_rec", width=1000, height=1000
        ),
        "words": [
            Mock(
                word_id=1,
                line_id=1,
                text="Test",
                top_left={"x": 0.1, "y": 0.1},
                top_right={"x": 0.2, "y": 0.1},
                bottom_left={"x": 0.1, "y": 0.2},
                bottom_right={"x": 0.2, "y": 0.2},
            )
        ],
        "word_tags": [
            Mock(word_id=1, tag="test_tag", human_validated=False)  # Not validated
        ],
    }

    result = process_receipt_details(details)
    assert result is None


def test_create_sliding_windows():
    """Test sliding window creation."""
    words = ["word1", "word2", "word3", "word4", "word5"]
    bboxes = [[0, 0, 10, 10]] * 5
    labels = ["O"] * 5

    # Test with window size larger than input
    windows = create_sliding_windows(
        words=words, bboxes=bboxes, labels=labels, window_size=10, overlap=2
    )
    assert len(windows) == 1
    assert len(windows[0]["words"]) == 5

    # Test with smaller window size
    windows = create_sliding_windows(
        words=words, bboxes=bboxes, labels=labels, window_size=3, overlap=1
    )
    # For 5 words with window_size=3 and overlap=1, we should get:
    # Window 1: [word1, word2, word3]
    # Window 2: [word2, word3, word4]
    # Window 3: [word3, word4, word5]
    assert len(windows) == 3

    # Check window sizes
    assert len(windows[0]["words"]) == 3
    assert len(windows[1]["words"]) == 3
    assert len(windows[2]["words"]) == 3

    # Check window contents
    assert windows[0]["words"] == ["word1", "word2", "word3"]
    assert windows[1]["words"] == ["word2", "word3", "word4"]
    assert windows[2]["words"] == ["word3", "word4", "word5"]

    # Check bboxes and labels match words
    for window in windows:
        assert len(window["words"]) == len(window["bboxes"])
        assert len(window["words"]) == len(window["labels"])
