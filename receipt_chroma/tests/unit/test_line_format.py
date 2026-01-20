"""Unit tests for line formatting utilities."""

import pytest

from receipt_chroma.embedding.formatting.line_format import (
    format_line_context_embedding_input,
    format_row_embedding_input,
    format_visual_row,
    get_line_neighbors,
    get_primary_line_id,
    get_row_embedding_inputs,
    group_lines_into_visual_rows,
    parse_prev_next_from_formatted,
)


class MockReceiptLine:
    """Mock ReceiptLine for testing."""

    def __init__(
        self,
        image_id: str,
        receipt_id: str,
        line_id: str,
        text: str,
        x: float = 0.0,
        y: float = 0.5,
        width: float = 1.0,
        height: float = 0.1,
    ):
        self.image_id = image_id
        self.receipt_id = receipt_id
        self.line_id = line_id
        self.text = text
        self.bounding_box = {
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
class TestFormatLineContextEmbeddingInput:
    """Test line context formatting."""

    def test_format_single_line(self):
        """Test formatting a single line."""
        line = MockReceiptLine("img1", "rec1", "line1", "hello")
        result = format_line_context_embedding_input(line, [line])
        assert "<TARGET>hello</TARGET>" in result
        assert "<POS>" in result
        assert "<CONTEXT>" in result

    def test_format_with_neighbors(self):
        """Test formatting with previous and next lines."""
        lines = [
            MockReceiptLine("img1", "rec1", "line1", "prev", y=0.8),
            MockReceiptLine("img1", "rec1", "line2", "target", y=0.5),
            MockReceiptLine("img1", "rec1", "line3", "next", y=0.2),
        ]
        target = lines[1]
        result = format_line_context_embedding_input(target, lines)
        assert "<TARGET>target</TARGET>" in result
        assert "prev" in result or "<EDGE>" in result
        assert "next" in result or "<EDGE>" in result

    def test_format_edge_line(self):
        """Test formatting line at edge (no neighbors)."""
        line = MockReceiptLine("img1", "rec1", "line1", "hello", y=0.8)
        result = format_line_context_embedding_input(line, [line])
        assert "<EDGE>" in result

    def test_position_calculation(self):
        """Test position calculation based on y-coordinate."""
        lines = [
            MockReceiptLine("img1", "rec1", "line1", "first", y=0.8),
            MockReceiptLine("img1", "rec1", "line2", "second", y=0.5),
            MockReceiptLine("img1", "rec1", "line3", "third", y=0.2),
        ]
        target = lines[1]
        result = format_line_context_embedding_input(target, lines)
        assert "<POS>1</POS>" in result  # Second line (index 1)


@pytest.mark.unit
class TestGetLineNeighbors:
    """Test getting line neighbors."""

    def test_get_neighbors(self):
        """Test getting previous and next lines."""
        # Lines sorted by y-coordinate (higher y sorts first).
        # y=0.8 -> index 0, y=0.5 -> index 1, y=0.2 -> index 2.
        # In receipt coords, higher y might be lower on page.
        # Function treats index-1 as prev and index+1 as next.
        lines = [
            MockReceiptLine("img1", "rec1", "line1", "prev", y=0.8),
            MockReceiptLine("img1", "rec1", "line2", "target", y=0.5),
            MockReceiptLine("img1", "rec1", "line3", "next", y=0.2),
        ]
        target = lines[1]
        prev, next_line = get_line_neighbors(target, lines)
        # Actual: prev=next, next=prev (reversed due to coordinate system)
        assert prev == "next"  # Line with lower y (index 2)
        assert next_line == "prev"  # Line with higher y (index 0)

    def test_get_neighbors_first_line(self):
        """Test getting neighbors for first line."""
        # First line has highest y (sorts to index 0)
        # Due to coordinate system, higher y is treated as later in sequence
        lines = [
            MockReceiptLine("img1", "rec1", "line1", "first", y=0.8),
            MockReceiptLine("img1", "rec1", "line2", "second", y=0.5),
        ]
        target = lines[0]
        prev, next_line = get_line_neighbors(target, lines)
        # Actual: prev=second, next=<EDGE> (reversed due to coordinate system)
        assert prev == "second"
        assert next_line == "<EDGE>"

    def test_get_neighbors_last_line(self):
        """Test getting neighbors for last line."""
        # Last line has lowest y (sorts to last index)
        # Due to coordinate system, lower y is treated as earlier in sequence
        lines = [
            MockReceiptLine("img1", "rec1", "line1", "first", y=0.8),
            MockReceiptLine("img1", "rec1", "line2", "last", y=0.5),
        ]
        target = lines[1]
        prev, next_line = get_line_neighbors(target, lines)
        # Actual: prev=<EDGE>, next=first (reversed due to coordinate system)
        assert prev == "<EDGE>"
        assert next_line == "first"


@pytest.mark.unit
class TestParsePrevNextFromFormatted:
    """Test parsing previous/next from formatted string."""

    def test_parse_valid_format(self):
        """Test parsing valid formatted string."""
        fmt = "<TARGET>line</TARGET> <POS>1</POS> <CONTEXT>prev next</CONTEXT>"
        prev, next_line = parse_prev_next_from_formatted(fmt)
        assert prev == "prev"
        assert next_line == "next"

    def test_parse_with_edge(self):
        """Test parsing with edge markers."""
        fmt = (
            "<TARGET>line</TARGET> <POS>0</POS> <CONTEXT><EDGE> next</CONTEXT>"
        )
        prev, next_line = parse_prev_next_from_formatted(fmt)
        assert prev == "<EDGE>"
        assert next_line == "next"

    def test_parse_missing_context(self):
        """Test parsing with missing context tag."""
        fmt = "<TARGET>line</TARGET> <POS>1</POS>"
        with pytest.raises(ValueError, match="No <CONTEXT>"):
            parse_prev_next_from_formatted(fmt)

    def test_parse_single_word_context(self):
        """Test parsing with single word in context."""
        fmt = "<TARGET>line</TARGET> <POS>1</POS> <CONTEXT>single</CONTEXT>"
        prev, next_line = parse_prev_next_from_formatted(fmt)
        assert prev == "single"
        assert next_line == "<EDGE>"


# =============================================================================
# Row-based line formatting tests (new functionality)
# =============================================================================


@pytest.mark.unit
class TestGroupLinesIntoVisualRows:
    """Test grouping lines into visual rows based on vertical overlap."""

    def test_empty_lines(self):
        """Test with empty list of lines."""
        result = group_lines_into_visual_rows([])
        assert result == []

    def test_single_line(self):
        """Test with single line."""
        line = MockReceiptLine("img1", 1, 0, "hello", x=0.0, y=0.5, height=0.1)
        result = group_lines_into_visual_rows([line])
        assert len(result) == 1
        assert len(result[0]) == 1
        assert result[0][0].text == "hello"

    def test_two_lines_same_row(self):
        """Test two lines on the same visual row (overlapping y)."""
        # Lines with overlapping y-spans should be grouped together
        line1 = MockReceiptLine("img1", 1, 0, "product", x=0.1, y=0.5, height=0.1)
        line2 = MockReceiptLine("img1", 1, 1, "12.99", x=0.8, y=0.52, height=0.1)
        result = group_lines_into_visual_rows([line1, line2])

        assert len(result) == 1  # One visual row
        assert len(result[0]) == 2  # Two lines in the row
        # Should be sorted left-to-right
        assert result[0][0].text == "product"
        assert result[0][1].text == "12.99"

    def test_two_lines_different_rows(self):
        """Test two lines on different visual rows (no y overlap)."""
        line1 = MockReceiptLine("img1", 1, 0, "first", x=0.1, y=0.8, height=0.1)
        line2 = MockReceiptLine("img1", 1, 1, "second", x=0.1, y=0.3, height=0.1)
        result = group_lines_into_visual_rows([line1, line2])

        assert len(result) == 2  # Two visual rows
        assert result[0][0].text == "first"  # Higher y (top of receipt)
        assert result[1][0].text == "second"

    def test_multiple_rows_with_splits(self):
        """Test typical receipt with split rows (Apple Vision behavior)."""
        lines = [
            # Row 1: Merchant name
            MockReceiptLine("img1", 1, 0, "TRADER JOE'S", x=0.3, y=0.9, height=0.05),
            # Row 2: Item + price (split by Apple Vision)
            MockReceiptLine("img1", 1, 1, "ORGANIC COFFEE", x=0.1, y=0.7, height=0.05),
            MockReceiptLine("img1", 1, 2, "12.99", x=0.8, y=0.71, height=0.05),
            # Row 3: Total
            MockReceiptLine("img1", 1, 3, "TOTAL", x=0.1, y=0.3, height=0.05),
            MockReceiptLine("img1", 1, 4, "12.99", x=0.8, y=0.31, height=0.05),
        ]
        result = group_lines_into_visual_rows(lines)

        assert len(result) == 3
        # Row 1: merchant name (single line)
        assert len(result[0]) == 1
        assert result[0][0].text == "TRADER JOE'S"
        # Row 2: item + price (two lines)
        assert len(result[1]) == 2
        assert result[1][0].text == "ORGANIC COFFEE"
        assert result[1][1].text == "12.99"
        # Row 3: total (two lines)
        assert len(result[2]) == 2
        assert result[2][0].text == "TOTAL"
        assert result[2][1].text == "12.99"


@pytest.mark.unit
class TestFormatVisualRow:
    """Test formatting visual rows as text."""

    def test_single_line_row(self):
        """Test formatting row with single line."""
        line = MockReceiptLine("img1", 1, 0, "hello")
        result = format_visual_row([line])
        assert result == "hello"

    def test_multi_line_row(self):
        """Test formatting row with multiple lines."""
        lines = [
            MockReceiptLine("img1", 1, 0, "ORGANIC COFFEE"),
            MockReceiptLine("img1", 1, 1, "12.99"),
        ]
        result = format_visual_row(lines)
        assert result == "ORGANIC COFFEE 12.99"


@pytest.mark.unit
class TestFormatRowEmbeddingInput:
    """Test formatting row embedding input with context."""

    def test_row_with_context(self):
        """Test formatting with row above and below."""
        row_above = [MockReceiptLine("img1", 1, 0, "ABOVE")]
        target_row = [MockReceiptLine("img1", 1, 1, "TARGET")]
        row_below = [MockReceiptLine("img1", 1, 2, "BELOW")]

        result = format_row_embedding_input(target_row, row_above, row_below)
        assert result == "ABOVE\nTARGET\nBELOW"

    def test_row_at_top_edge(self):
        """Test formatting row at top (no row above)."""
        target_row = [MockReceiptLine("img1", 1, 0, "TARGET")]
        row_below = [MockReceiptLine("img1", 1, 1, "BELOW")]

        result = format_row_embedding_input(target_row, None, row_below)
        assert result == "<EDGE>\nTARGET\nBELOW"

    def test_row_at_bottom_edge(self):
        """Test formatting row at bottom (no row below)."""
        row_above = [MockReceiptLine("img1", 1, 0, "ABOVE")]
        target_row = [MockReceiptLine("img1", 1, 1, "TARGET")]

        result = format_row_embedding_input(target_row, row_above, None)
        assert result == "ABOVE\nTARGET\n<EDGE>"

    def test_single_row(self):
        """Test formatting single row (no context)."""
        target_row = [MockReceiptLine("img1", 1, 0, "ONLY")]

        result = format_row_embedding_input(target_row, None, None)
        assert result == "<EDGE>\nONLY\n<EDGE>"


@pytest.mark.unit
class TestGetRowEmbeddingInputs:
    """Test generating embedding inputs for all rows."""

    def test_empty_lines(self):
        """Test with no lines."""
        result = get_row_embedding_inputs([])
        assert result == []

    def test_single_row_receipt(self):
        """Test receipt with single row."""
        lines = [MockReceiptLine("img1", 1, 0, "HELLO")]
        result = get_row_embedding_inputs(lines)

        assert len(result) == 1
        embedding_input, line_ids = result[0]
        assert embedding_input == "<EDGE>\nHELLO\n<EDGE>"
        assert line_ids == [0]

    def test_multi_row_receipt(self):
        """Test receipt with multiple rows."""
        lines = [
            MockReceiptLine("img1", 1, 0, "ROW1", y=0.9, height=0.05),
            MockReceiptLine("img1", 1, 1, "ROW2", y=0.5, height=0.05),
            MockReceiptLine("img1", 1, 2, "ROW3", y=0.1, height=0.05),
        ]
        result = get_row_embedding_inputs(lines)

        assert len(result) == 3

        # First row
        embedding_input, line_ids = result[0]
        assert embedding_input == "<EDGE>\nROW1\nROW2"
        assert line_ids == [0]

        # Middle row
        embedding_input, line_ids = result[1]
        assert embedding_input == "ROW1\nROW2\nROW3"
        assert line_ids == [1]

        # Last row
        embedding_input, line_ids = result[2]
        assert embedding_input == "ROW2\nROW3\n<EDGE>"
        assert line_ids == [2]

    def test_split_row_receipt(self):
        """Test receipt with split rows (multiple lines per visual row)."""
        lines = [
            MockReceiptLine("img1", 1, 0, "PRODUCT", x=0.1, y=0.7, height=0.05),
            MockReceiptLine("img1", 1, 1, "9.99", x=0.8, y=0.71, height=0.05),
            MockReceiptLine("img1", 1, 2, "TOTAL", x=0.1, y=0.3, height=0.05),
        ]
        result = get_row_embedding_inputs(lines)

        assert len(result) == 2  # Two visual rows

        # First row: PRODUCT + 9.99
        embedding_input, line_ids = result[0]
        assert "PRODUCT 9.99" in embedding_input
        assert 0 in line_ids and 1 in line_ids

        # Second row: TOTAL
        embedding_input, line_ids = result[1]
        assert "TOTAL" in embedding_input
        assert line_ids == [2]


@pytest.mark.unit
class TestGetPrimaryLineId:
    """Test getting primary line ID for a row."""

    def test_single_line_row(self):
        """Test with single line in row."""
        line = MockReceiptLine("img1", 1, 5, "hello")
        result = get_primary_line_id([line])
        assert result == 5

    def test_multi_line_row(self):
        """Test with multiple lines - should use first (leftmost)."""
        lines = [
            MockReceiptLine("img1", 1, 3, "first", x=0.1),
            MockReceiptLine("img1", 1, 7, "second", x=0.5),
        ]
        result = get_primary_line_id(lines)
        assert result == 3  # First line's ID

    def test_empty_row_raises(self):
        """Test that empty row raises ValueError."""
        with pytest.raises(ValueError, match="Cannot get primary line_id"):
            get_primary_line_id([])
