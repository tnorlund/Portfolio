"""Unit tests for word formatting utilities."""

import pytest

from receipt_chroma.embedding.formatting.word_format import (
    format_word_context_embedding_input,
    get_word_neighbors,
    parse_left_right_from_formatted,
)


class MockReceiptWord:
    """Mock ReceiptWord for testing.

    Implements WordLike protocol for use in word formatting functions.
    """

    def __init__(
        self,
        image_id: str,
        receipt_id: int,
        line_id: int,
        word_id: int,
        text: str,
        x: float = 0.5,
        y: float = 0.5,
        width: float = 0.1,
        height: float = 0.05,
    ) -> None:
        self.image_id = image_id
        self.receipt_id = receipt_id
        self.line_id = line_id
        self.word_id = word_id
        self.text = text
        self.bounding_box: dict[str, float] = {
            "x": x,
            "y": y,
            "width": width,
            "height": height,
        }

    def calculate_centroid(self) -> tuple[float, float]:
        """Calculate centroid coordinates."""
        x_center = self.bounding_box["x"] + self.bounding_box["width"] / 2
        y_center = self.bounding_box["y"] + self.bounding_box["height"] / 2
        return (x_center, y_center)


@pytest.mark.unit
class TestFormatWordContextEmbeddingInput:
    """Test word context formatting with new simple format."""

    def test_format_single_word(self) -> None:
        """Test formatting a single word (at edge)."""
        word = MockReceiptWord("img1", 1, 1, 1, "hello")
        result = format_word_context_embedding_input(
            word, [word], context_size=2
        )
        # Should have <EDGE> tags for missing context
        assert "<EDGE>" in result
        assert "hello" in result
        # Format: "<EDGE> <EDGE> hello <EDGE> <EDGE>"
        parts = result.split()
        assert parts[2] == "hello"  # Word is in the middle

    def test_format_with_neighbors(self) -> None:
        """Test formatting with left and right neighbors."""
        words = [
            MockReceiptWord("img1", 1, 1, 1, "left", x=0.1, y=0.5),
            MockReceiptWord("img1", 1, 1, 2, "target", x=0.5, y=0.5),
            MockReceiptWord("img1", 1, 1, 3, "right", x=0.9, y=0.5),
        ]
        target = words[1]
        result = format_word_context_embedding_input(
            target, words, context_size=2
        )
        # Format should be: "<EDGE> left target right <EDGE>" or similar
        assert "target" in result
        assert "left" in result
        assert "right" in result

    def test_format_edge_word(self) -> None:
        """Test formatting word at edge (no neighbors)."""
        word = MockReceiptWord("img1", 1, 1, 1, "hello", x=0.1, y=0.5)
        result = format_word_context_embedding_input(
            word, [word], context_size=2
        )
        # Should have multiple <EDGE> tags
        assert "<EDGE>" in result
        assert "hello" in result
        edge_count = result.split().count("<EDGE>")
        assert edge_count >= 2  # At least 2 <EDGE> tags for 2-word context

    def test_format_with_multiple_context(self) -> None:
        """Test formatting with context_size=2 (multiple words)."""
        words = [
            MockReceiptWord("img1", 1, 1, 1, "item1", x=0.1, y=0.5),
            MockReceiptWord("img1", 1, 1, 2, "item2", x=0.3, y=0.5),
            MockReceiptWord("img1", 1, 1, 3, "target", x=0.5, y=0.5),
            MockReceiptWord("img1", 1, 1, 4, "item4", x=0.7, y=0.5),
            MockReceiptWord("img1", 1, 1, 5, "item5", x=0.9, y=0.5),
        ]
        target = words[2]
        result = format_word_context_embedding_input(
            target, words, context_size=2
        )
        # Should have 2 words on each side in reading order (left-to-right)
        assert result == "item1 item2 target item4 item5"


@pytest.mark.unit
class TestGetWordNeighbors:
    """Test getting word neighbors with configurable context size."""

    def test_get_neighbors_single(self) -> None:
        """Test getting single left and right neighbor."""
        words = [
            MockReceiptWord("img1", 1, 1, 1, "left", x=0.1, y=0.5),
            MockReceiptWord("img1", 1, 1, 2, "target", x=0.5, y=0.5),
            MockReceiptWord("img1", 1, 1, 3, "right", x=0.9, y=0.5),
        ]
        target = words[1]
        left_words, right_words = get_word_neighbors(
            target, words, context_size=1
        )
        assert len(left_words) == 1
        assert len(right_words) == 1
        assert left_words[0] == "left"
        assert right_words[0] == "right"

    def test_get_neighbors_multiple(self) -> None:
        """Test getting multiple neighbors with context_size=2."""
        words = [
            MockReceiptWord("img1", 1, 1, 1, "left1", x=0.1, y=0.5),
            MockReceiptWord("img1", 1, 1, 2, "left2", x=0.3, y=0.5),
            MockReceiptWord("img1", 1, 1, 3, "target", x=0.5, y=0.5),
            MockReceiptWord("img1", 1, 1, 4, "right1", x=0.7, y=0.5),
            MockReceiptWord("img1", 1, 1, 5, "right2", x=0.9, y=0.5),
        ]
        target = words[2]
        left_words, right_words = get_word_neighbors(
            target, words, context_size=2
        )
        assert len(left_words) == 2
        assert len(right_words) == 2
        assert "left1" in left_words
        assert "left2" in left_words
        assert "right1" in right_words
        assert "right2" in right_words

    def test_get_neighbors_edge(self) -> None:
        """Test getting neighbors for word at edge."""
        word = MockReceiptWord("img1", 1, 1, 1, "hello", x=0.1, y=0.5)
        left_words, right_words = get_word_neighbors(
            word, [word], context_size=2
        )
        # Should return empty lists (no neighbors found)
        assert len(left_words) == 0
        assert len(right_words) == 0

    def test_get_neighbors_different_lines_far_apart(self) -> None:
        """Words on very different lines (y diff > 0.05) are NOT neighbors."""
        words = [
            MockReceiptWord("img1", 1, 1, 1, "left", x=0.1, y=0.8),
            MockReceiptWord("img1", 1, 2, 2, "target", x=0.5, y=0.5),
            MockReceiptWord("img1", 1, 3, 3, "right", x=0.9, y=0.2),
        ]
        target = words[1]
        left_words, right_words = get_word_neighbors(
            target, words, context_size=2
        )
        # Words on very different lines (y diff of 0.3) should NOT be neighbors
        # Line-aware algo only considers same-line or nearby-line words
        assert len(left_words) == 0
        assert len(right_words) == 0

    def test_get_neighbors_same_line_far_apart(self) -> None:
        """Neighbors on same line that are far apart horizontally."""
        # Words on same line (same y-coordinate) but far apart horizontally
        words = [
            MockReceiptWord(
                "img1", 1, 1, 1, "far_left", x=0.05, y=0.5, height=0.05
            ),
            MockReceiptWord(
                "img1", 1, 1, 2, "left", x=0.2, y=0.5, height=0.05
            ),
            MockReceiptWord(
                "img1", 1, 1, 3, "target", x=0.5, y=0.5, height=0.05
            ),
            MockReceiptWord(
                "img1", 1, 1, 4, "right", x=0.7, y=0.5, height=0.05
            ),
            MockReceiptWord(
                "img1", 1, 1, 5, "far_right", x=0.9, y=0.5, height=0.05
            ),
        ]
        target = words[2]  # "target" at x=0.5
        left_words, right_words = get_word_neighbors(
            target, words, context_size=2
        )
        # Should find neighbors on same line even if far apart
        assert len(left_words) == 2
        assert len(right_words) == 2
        assert "left" in left_words
        assert "far_left" in left_words
        assert "right" in right_words
        assert "far_right" in right_words

    def test_get_neighbors_nearby_lines(self) -> None:
        """Include words from nearby lines (y diff < 0.05) as neighbors."""
        # Words on nearby lines (within y_proximity_threshold of 0.05)
        # and within x_proximity_threshold (0.25) for nearby-line candidates
        words = [
            MockReceiptWord(
                "img1",
                1,
                1,
                1,
                "left_nearby",
                x=0.35,  # x diff of 0.15 from target (within 0.25 threshold)
                y=0.52,  # y diff of 0.02 from target (within 0.05 threshold)
                height=0.05,
            ),
            MockReceiptWord(
                "img1", 1, 2, 2, "target", x=0.5, y=0.5, height=0.05
            ),
            MockReceiptWord(
                "img1",
                1,
                3,
                3,
                "right_nearby",
                x=0.65,  # x diff of 0.15 from target (within 0.25 threshold)
                y=0.48,  # y diff of 0.02 from target (within 0.05 threshold)
                height=0.05,
            ),
        ]
        target = words[1]  # "target" at x=0.5
        left_words, right_words = get_word_neighbors(
            target, words, context_size=2
        )
        # Nearby lines (y diff < 0.05, x diff < 0.25) should be neighbors
        assert len(left_words) == 1
        assert len(right_words) == 1
        assert "left_nearby" in left_words
        assert "right_nearby" in right_words

    def test_get_neighbors_same_x_different_lines_far_apart(self) -> None:
        """Words at same x but on very different lines are NOT neighbors."""
        # Words at same x but very different y (different lines, y diff > 0.05)
        words = [
            MockReceiptWord(
                "img1",
                1,
                1,
                1,
                "above",
                x=0.5,
                y=0.7,  # y diff of 0.2 from target
                height=0.05,
            ),
            MockReceiptWord(
                "img1", 1, 2, 2, "target", x=0.5, y=0.5, height=0.05
            ),
            MockReceiptWord(
                "img1",
                1,
                3,
                3,
                "below",
                x=0.5,
                y=0.3,  # y diff of 0.2 from target
                height=0.05,
            ),
        ]
        target = words[1]  # "target" at x=0.5, middle of list
        left_words, right_words = get_word_neighbors(
            target, words, context_size=2
        )
        # Words on very different lines (y diff of 0.2) should NOT be neighbors
        # even if they have the same x-coordinate
        assert len(left_words) == 0
        assert len(right_words) == 0


@pytest.mark.unit
class TestParseLeftRightFromFormatted:
    """Test parsing left/right from new formatted string."""

    def test_parse_valid_format(self) -> None:
        """Test parsing valid formatted string with context."""
        fmt = "left1 left2 word right1 right2"
        left_words, right_words = parse_left_right_from_formatted(
            fmt, context_size=2
        )
        assert len(left_words) == 2
        assert len(right_words) == 2
        assert "left1" in left_words
        assert "left2" in left_words
        assert "right1" in right_words
        assert "right2" in right_words

    def test_parse_with_edge(self) -> None:
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

    def test_parse_all_edges(self) -> None:
        """Test parsing word at edge with all <EDGE> tags."""
        fmt = "<EDGE> <EDGE> word <EDGE> <EDGE>"
        left_words, right_words = parse_left_right_from_formatted(
            fmt, context_size=2
        )
        assert len(left_words) == 2
        assert len(right_words) == 2
        assert all(w == "<EDGE>" for w in left_words)
        assert all(w == "<EDGE>" for w in right_words)
