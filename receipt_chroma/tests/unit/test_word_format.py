"""Unit tests for word formatting utilities."""

import pytest

from receipt_chroma.embedding.formatting.word_format import (
    format_word_context_embedding_input,
    get_word_neighbors,
    parse_left_right_from_formatted,
)


class MockReceiptWord:
    """Mock ReceiptWord for testing."""

    def __init__(
        self,
        image_id: str,
        receipt_id: str,
        line_id: str,
        word_id: str,
        text: str,
        x: float = 0.5,
        y: float = 0.5,
        width: float = 0.1,
        height: float = 0.05,
    ):
        self.image_id = image_id
        self.receipt_id = receipt_id
        self.line_id = line_id
        self.word_id = word_id
        self.text = text
        self.bounding_box = {
            "x": x,
            "y": y,
            "width": width,
            "height": height,
        }
        self.top_left = {"x": x, "y": y + height}
        self.bottom_left = {"x": x, "y": y}

    def calculate_centroid(self) -> tuple[float, float]:
        """Calculate centroid coordinates."""
        x_center = self.bounding_box["x"] + self.bounding_box["width"] / 2
        y_center = self.bounding_box["y"] + self.bounding_box["height"] / 2
        return (x_center, y_center)


@pytest.mark.unit
class TestFormatWordContextEmbeddingInput:
    """Test word context formatting."""

    def test_format_single_word(self):
        """Test formatting a single word."""
        word = MockReceiptWord("img1", "rec1", "line1", "word1", "hello")
        result = format_word_context_embedding_input(word, [word])
        assert "<TARGET>hello</TARGET>" in result
        assert "<POS>" in result
        assert "<CONTEXT>" in result

    def test_format_with_neighbors(self):
        """Test formatting with left and right neighbors."""
        words = [
            MockReceiptWord(
                "img1", "rec1", "line1", "word1", "left", x=0.1, y=0.5
            ),
            MockReceiptWord(
                "img1", "rec1", "line1", "word2", "target", x=0.5, y=0.5
            ),
            MockReceiptWord(
                "img1", "rec1", "line1", "word3", "right", x=0.9, y=0.5
            ),
        ]
        target = words[1]
        result = format_word_context_embedding_input(target, words)
        assert "<TARGET>target</TARGET>" in result
        assert "left" in result or "<EDGE>" in result
        assert "right" in result or "<EDGE>" in result

    def test_format_edge_word(self):
        """Test formatting word at edge (no neighbors)."""
        word = MockReceiptWord(
            "img1", "rec1", "line1", "word1", "hello", x=0.1, y=0.5
        )
        result = format_word_context_embedding_input(word, [word])
        assert "<EDGE>" in result

    def test_position_calculation(self):
        """Test position calculation in 3x3 grid."""
        # Top-left
        word = MockReceiptWord(
            "img1", "rec1", "line1", "word1", "test", x=0.1, y=0.8
        )
        result = format_word_context_embedding_input(word, [word])
        assert "top-left" in result

        # Middle-center
        word = MockReceiptWord(
            "img1", "rec1", "line1", "word1", "test", x=0.5, y=0.5
        )
        result = format_word_context_embedding_input(word, [word])
        assert "middle-center" in result

        # Bottom-right
        word = MockReceiptWord(
            "img1", "rec1", "line1", "word1", "test", x=0.9, y=0.1
        )
        result = format_word_context_embedding_input(word, [word])
        assert "bottom-right" in result


@pytest.mark.unit
class TestGetWordNeighbors:
    """Test getting word neighbors."""

    def test_get_neighbors(self):
        """Test getting left and right neighbors."""
        words = [
            MockReceiptWord(
                "img1", "rec1", "line1", "word1", "left", x=0.1, y=0.5
            ),
            MockReceiptWord(
                "img1", "rec1", "line1", "word2", "target", x=0.5, y=0.5
            ),
            MockReceiptWord(
                "img1", "rec1", "line1", "word3", "right", x=0.9, y=0.5
            ),
        ]
        target = words[1]
        left, right = get_word_neighbors(target, words)
        assert left in ["left", "<EDGE>"]
        assert right in ["right", "<EDGE>"]

    def test_get_neighbors_no_overlap(self):
        """Test neighbors when words don't overlap vertically."""
        words = [
            MockReceiptWord(
                "img1", "rec1", "line1", "word1", "top", x=0.1, y=0.8
            ),
            MockReceiptWord(
                "img1", "rec1", "line1", "word2", "target", x=0.5, y=0.5
            ),
            MockReceiptWord(
                "img1", "rec1", "line1", "word3", "bottom", x=0.9, y=0.2
            ),
        ]
        target = words[1]
        left, right = get_word_neighbors(target, words)
        # Should return <EDGE> if no vertical overlap
        assert left == "<EDGE>" or right == "<EDGE>"


@pytest.mark.unit
class TestParseLeftRightFromFormatted:
    """Test parsing left/right from formatted string."""

    def test_parse_valid_format(self):
        """Test parsing valid formatted string."""
        fmt = "<TARGET>word</TARGET> <POS>middle-center</POS> <CONTEXT>left right</CONTEXT>"
        left, right = parse_left_right_from_formatted(fmt)
        assert left == "left"
        assert right == "right"

    def test_parse_with_edge(self):
        """Test parsing with edge markers."""
        fmt = "<TARGET>word</TARGET> <POS>top-left</POS> <CONTEXT><EDGE> right</CONTEXT>"
        left, right = parse_left_right_from_formatted(fmt)
        assert left == "<EDGE>"
        assert right == "right"

    def test_parse_missing_context(self):
        """Test parsing with missing context tag."""
        fmt = "<TARGET>word</TARGET> <POS>middle-center</POS>"
        with pytest.raises(ValueError, match="No <CONTEXT>"):
            parse_left_right_from_formatted(fmt)

    def test_parse_single_word_context(self):
        """Test parsing with single word in context."""
        fmt = "<TARGET>word</TARGET> <POS>middle-center</POS> <CONTEXT>single</CONTEXT>"
        left, right = parse_left_right_from_formatted(fmt)
        assert left == "single"
        assert right == "<EDGE>"
