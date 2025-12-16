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
    """Test word context formatting with new simple format."""

    def test_format_single_word(self):
        """Test formatting a single word (at edge)."""
        word = MockReceiptWord("img1", "rec1", "line1", "word1", "hello")
        result = format_word_context_embedding_input(
            word, [word], context_size=2
        )
        # Should have <EDGE> tags for missing context
        assert "<EDGE>" in result
        assert "hello" in result
        # Format: "<EDGE> <EDGE> hello <EDGE> <EDGE>"
        parts = result.split()
        assert parts[2] == "hello"  # Word is in the middle

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
        result = format_word_context_embedding_input(
            target, words, context_size=2
        )
        # Format should be: "<EDGE> left target right <EDGE>" or similar
        assert "target" in result
        assert "left" in result or "<EDGE>" in result
        assert "right" in result or "<EDGE>" in result

    def test_format_edge_word(self):
        """Test formatting word at edge (no neighbors)."""
        word = MockReceiptWord(
            "img1", "rec1", "line1", "word1", "hello", x=0.1, y=0.5
        )
        result = format_word_context_embedding_input(
            word, [word], context_size=2
        )
        # Should have multiple <EDGE> tags
        assert "<EDGE>" in result
        assert "hello" in result
        edge_count = result.split().count("<EDGE>")
        assert edge_count >= 2  # At least 2 <EDGE> tags for 2-word context

    def test_format_with_multiple_context(self):
        """Test formatting with context_size=2 (multiple words)."""
        words = [
            MockReceiptWord(
                "img1", "rec1", "line1", "w1", "item1", x=0.1, y=0.5
            ),
            MockReceiptWord(
                "img1", "rec1", "line1", "w2", "item2", x=0.3, y=0.5
            ),
            MockReceiptWord(
                "img1", "rec1", "line1", "w3", "target", x=0.5, y=0.5
            ),
            MockReceiptWord(
                "img1", "rec1", "line1", "w4", "item4", x=0.7, y=0.5
            ),
            MockReceiptWord(
                "img1", "rec1", "line1", "w5", "item5", x=0.9, y=0.5
            ),
        ]
        target = words[2]
        result = format_word_context_embedding_input(
            target, words, context_size=2
        )
        # Should have 2 words on each side: "item1 item2 target item4 item5"
        assert "target" in result
        assert "item1" in result or "item2" in result
        assert "item4" in result or "item5" in result


@pytest.mark.unit
class TestGetWordNeighbors:
    """Test getting word neighbors with configurable context size."""

    def test_get_neighbors_single(self):
        """Test getting single left and right neighbor."""
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
        left_words, right_words = get_word_neighbors(
            target, words, context_size=1
        )
        assert len(left_words) == 1
        assert len(right_words) == 1
        assert left_words[0] in ["left", "<EDGE>"]
        assert right_words[0] in ["right", "<EDGE>"]

    def test_get_neighbors_multiple(self):
        """Test getting multiple neighbors with context_size=2."""
        words = [
            MockReceiptWord(
                "img1", "rec1", "line1", "w1", "left1", x=0.1, y=0.5
            ),
            MockReceiptWord(
                "img1", "rec1", "line1", "w2", "left2", x=0.3, y=0.5
            ),
            MockReceiptWord(
                "img1", "rec1", "line1", "w3", "target", x=0.5, y=0.5
            ),
            MockReceiptWord(
                "img1", "rec1", "line1", "w4", "right1", x=0.7, y=0.5
            ),
            MockReceiptWord(
                "img1", "rec1", "line1", "w5", "right2", x=0.9, y=0.5
            ),
        ]
        target = words[2]
        left_words, right_words = get_word_neighbors(
            target, words, context_size=2
        )
        assert len(left_words) == 2
        assert len(right_words) == 2
        assert "left1" in left_words or "left2" in left_words
        assert "right1" in right_words or "right2" in right_words

    def test_get_neighbors_edge(self):
        """Test getting neighbors for word at edge."""
        word = MockReceiptWord(
            "img1", "rec1", "line1", "word1", "hello", x=0.1, y=0.5
        )
        left_words, right_words = get_word_neighbors(
            word, [word], context_size=2
        )
        # Should return empty lists (no neighbors found)
        assert len(left_words) == 0
        assert len(right_words) == 0

    def test_get_neighbors_different_lines(self):
        """Neighbors across lines with horizontal alignment."""
        words = [
            MockReceiptWord(
                "img1", "rec1", "line1", "word1", "left", x=0.1, y=0.8
            ),
            MockReceiptWord(
                "img1", "rec1", "line2", "word2", "target", x=0.5, y=0.5
            ),
            MockReceiptWord(
                "img1", "rec1", "line3", "word3", "right", x=0.9, y=0.2
            ),
        ]
        target = words[1]
        left_words, right_words = get_word_neighbors(
            target, words, context_size=2
        )
        # Neighbors selected by horizontal position, regardless of line
        assert len(left_words) > 0
        assert len(right_words) > 0
        assert "left" in left_words
        assert "right" in right_words

    def test_get_neighbors_same_line_far_apart(self):
        """Neighbors on same line that are far apart horizontally."""
        # Words on same line (same y-coordinate) but far apart horizontally
        words = [
            MockReceiptWord(
                "img1",
                "rec1",
                "line1",
                "word1",
                "far_left",
                x=0.05,
                y=0.5,
                height=0.05,
            ),
            MockReceiptWord(
                "img1",
                "rec1",
                "line1",
                "word2",
                "left",
                x=0.2,
                y=0.5,
                height=0.05,
            ),
            MockReceiptWord(
                "img1",
                "rec1",
                "line1",
                "word3",
                "target",
                x=0.5,
                y=0.5,
                height=0.05,
            ),
            MockReceiptWord(
                "img1",
                "rec1",
                "line1",
                "word4",
                "right",
                x=0.7,
                y=0.5,
                height=0.05,
            ),
            MockReceiptWord(
                "img1",
                "rec1",
                "line1",
                "word5",
                "far_right",
                x=0.9,
                y=0.5,
                height=0.05,
            ),
        ]
        target = words[2]  # "target" at x=0.5
        left_words, right_words = get_word_neighbors(
            target, words, context_size=2
        )
        # Should find neighbors on same line even if far apart
        assert len(left_words) > 0
        assert len(right_words) > 0
        assert "left" in left_words or "far_left" in left_words
        assert "right" in right_words or "far_right" in right_words

    def test_get_neighbors_includes_different_lines(self):
        """Include other lines when horizontally aligned."""
        # Words at different y (different lines) but different x positions
        words = [
            MockReceiptWord(
                "img1",
                "rec1",
                "line1",
                "word1",
                "left_above",
                x=0.2,
                y=0.7,
                height=0.05,
            ),
            MockReceiptWord(
                "img1",
                "rec1",
                "line2",
                "word2",
                "target",
                x=0.5,
                y=0.5,
                height=0.05,
            ),
            MockReceiptWord(
                "img1",
                "rec1",
                "line3",
                "word3",
                "right_below",
                x=0.8,
                y=0.3,
                height=0.05,
            ),
        ]
        target = words[1]  # "target" at x=0.5
        left_words, right_words = get_word_neighbors(
            target, words, context_size=2
        )
        # Should find words from other lines based on horizontal position
        assert len(left_words) > 0
        assert len(right_words) > 0
        assert "left_above" in left_words
        assert "right_below" in right_words

    def test_get_neighbors_same_x_different_lines(self):
        """Same x across lines; order by x then input order."""
        # Words at same x but different y (different lines)
        # When x is the same, order depends on list order (stable sort)
        words = [
            MockReceiptWord(
                "img1",
                "rec1",
                "line1",
                "word1",
                "above",
                x=0.5,
                y=0.7,
                height=0.05,
            ),
            MockReceiptWord(
                "img1",
                "rec1",
                "line2",
                "word2",
                "target",
                x=0.5,
                y=0.5,
                height=0.05,
            ),
            MockReceiptWord(
                "img1",
                "rec1",
                "line3",
                "word3",
                "below",
                x=0.5,
                y=0.3,
                height=0.05,
            ),
        ]
        target = words[1]  # "target" at x=0.5, middle of list
        left_words, right_words = get_word_neighbors(
            target, words, context_size=2
        )
        # When x is identical, stable sort preserves original order.
        # "above" (idx 0) before "target" (idx 1); "below" (idx 2) after.
        assert len(left_words) > 0
        assert len(right_words) > 0
        assert "above" in left_words
        assert "below" in right_words


@pytest.mark.unit
class TestParseLeftRightFromFormatted:
    """Test parsing left/right from new formatted string."""

    def test_parse_valid_format(self):
        """Test parsing valid formatted string with context."""
        fmt = "left1 left2 word right1 right2"
        left_words, right_words = parse_left_right_from_formatted(
            fmt, context_size=2
        )
        assert len(left_words) == 2
        assert len(right_words) == 2
        assert "left1" in left_words or "left2" in left_words
        assert "right1" in right_words or "right2" in right_words

    def test_parse_with_edge(self):
        """Test parsing with edge markers."""
        fmt = "<EDGE> left word right <EDGE>"
        left_words, right_words = parse_left_right_from_formatted(
            fmt, context_size=2
        )
        assert len(left_words) == 2
        assert len(right_words) == 2
        assert "<EDGE>" in left_words
        assert "left" in left_words
        assert "right" in right_words
        assert "<EDGE>" in right_words

    def test_parse_all_edges(self):
        """Test parsing word at edge with all <EDGE> tags."""
        fmt = "<EDGE> <EDGE> word <EDGE> <EDGE>"
        left_words, right_words = parse_left_right_from_formatted(
            fmt, context_size=2
        )
        assert len(left_words) == 2
        assert len(right_words) == 2
        assert all(w == "<EDGE>" for w in left_words)
        assert all(w == "<EDGE>" for w in right_words)
