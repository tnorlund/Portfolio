"""Tests for the simplified PHOTO corner detection logic.

Phase 1 simplifies the PHOTO perspective transform by:
- Using top/bottom line corners directly (instead of complex boundary fitting)
- Still using convex hull to constrain left/right edges

This test file validates the new approach works correctly.
"""

import pytest
from receipt_dynamo.entities import Line
from receipt_upload.geometry import convex_hull


def create_mock_line(
    line_id: int,
    text: str,
    top_left: dict,
    top_right: dict,
    bottom_left: dict,
    bottom_right: dict,
    confidence: float = 0.95,
    image_id: str = "3f52804b-2fad-4e00-92c8-b593da3a8ed3",  # Valid UUIDv4
    angle_degrees: float = 0.0,
    angle_radians: float = 0.0,
) -> Line:
    """Create a mock Line entity for testing."""
    # Compute bounding box from corners
    min_x = min(top_left["x"], bottom_left["x"])
    max_x = max(top_right["x"], bottom_right["x"])
    min_y = min(bottom_left["y"], bottom_right["y"])
    max_y = max(top_left["y"], top_right["y"])
    bounding_box = {
        "x": min_x,
        "y": min_y,
        "width": max_x - min_x,
        "height": max_y - min_y,
    }
    return Line(
        image_id=image_id,
        line_id=line_id,
        text=text,
        bounding_box=bounding_box,
        top_left=top_left,
        top_right=top_right,
        bottom_left=bottom_left,
        bottom_right=bottom_right,
        angle_degrees=angle_degrees,
        angle_radians=angle_radians,
        confidence=confidence,
    )


class TestSimplifiedCornerDetection:
    """Tests for the Phase 1 simplified PHOTO corner detection."""

    @pytest.fixture
    def vertical_receipt_lines(self):
        """Create lines for a typical vertical receipt (tall, narrow)."""
        # Simulates a receipt with normalized coordinates
        # Y=1 is at the top of the image in normalized coords
        # Lines arranged from top to bottom (decreasing Y)
        return [
            create_mock_line(
                line_id=1,
                text="STORE NAME",
                top_left={"x": 0.2, "y": 0.9},  # Top line (highest Y)
                top_right={"x": 0.8, "y": 0.9},
                bottom_left={"x": 0.2, "y": 0.85},
                bottom_right={"x": 0.8, "y": 0.85},
            ),
            create_mock_line(
                line_id=2,
                text="123 Main St",
                top_left={"x": 0.2, "y": 0.75},
                top_right={"x": 0.8, "y": 0.75},
                bottom_left={"x": 0.2, "y": 0.70},
                bottom_right={"x": 0.8, "y": 0.70},
            ),
            create_mock_line(
                line_id=3,
                text="Item 1 $5.00",
                top_left={"x": 0.2, "y": 0.50},
                top_right={"x": 0.8, "y": 0.50},
                bottom_left={"x": 0.2, "y": 0.45},
                bottom_right={"x": 0.8, "y": 0.45},
            ),
            create_mock_line(
                line_id=4,
                text="TOTAL $5.00",
                top_left={"x": 0.2, "y": 0.20},  # Bottom line (lowest Y)
                top_right={"x": 0.8, "y": 0.20},
                bottom_left={"x": 0.2, "y": 0.15},
                bottom_right={"x": 0.8, "y": 0.15},
            ),
        ]

    @pytest.fixture
    def tilted_receipt_lines(self):
        """Create lines for a tilted receipt."""
        # Receipt rotated ~5 degrees counterclockwise
        return [
            create_mock_line(
                line_id=1,
                text="STORE NAME",
                top_left={"x": 0.15, "y": 0.92},
                top_right={"x": 0.75, "y": 0.88},  # Right side lower
                bottom_left={"x": 0.17, "y": 0.87},
                bottom_right={"x": 0.77, "y": 0.83},
            ),
            create_mock_line(
                line_id=2,
                text="TOTAL $10.00",
                top_left={"x": 0.12, "y": 0.22},
                top_right={"x": 0.72, "y": 0.18},
                bottom_left={"x": 0.14, "y": 0.17},
                bottom_right={"x": 0.74, "y": 0.13},
            ),
        ]

    @pytest.mark.unit
    def test_find_top_bottom_lines_by_y_position(self, vertical_receipt_lines):
        """Test that sorting by Y position correctly identifies top and bottom lines."""
        # Sort by top_left["y"] descending (highest Y first = topmost in normalized)
        sorted_lines = sorted(
            vertical_receipt_lines,
            key=lambda line: line.top_left["y"],
            reverse=True,
        )

        top_line = sorted_lines[0]
        bottom_line = sorted_lines[-1]

        assert top_line.text == "STORE NAME"
        assert bottom_line.text == "TOTAL $5.00"
        assert top_line.top_left["y"] > bottom_line.top_left["y"]

    @pytest.mark.unit
    def test_top_line_corners_become_receipt_top_edge(self, vertical_receipt_lines):
        """Test that top line's TL/TR become receipt's top edge."""
        image_width = 4032
        image_height = 3024

        sorted_lines = sorted(
            vertical_receipt_lines,
            key=lambda line: line.top_left["y"],
            reverse=True,
        )
        top_line = sorted_lines[0]

        # Get corners with flip_y=True for image coordinates
        corners = top_line.calculate_corners(
            width=image_width, height=image_height, flip_y=True
        )
        # corners = (TL, TR, BL, BR)
        top_left = corners[0]
        top_right = corners[1]

        # Check X positions are correct
        assert top_left[0] == pytest.approx(0.2 * image_width)
        assert top_right[0] == pytest.approx(0.8 * image_width)

        # Check Y positions are flipped (Y=0.9 normalized -> Y=0.1*height in image coords)
        assert top_left[1] == pytest.approx((1 - 0.9) * image_height)
        assert top_right[1] == pytest.approx((1 - 0.9) * image_height)

    @pytest.mark.unit
    def test_bottom_line_corners_become_receipt_bottom_edge(
        self, vertical_receipt_lines
    ):
        """Test that bottom line's BL/BR become receipt's bottom edge."""
        image_width = 4032
        image_height = 3024

        sorted_lines = sorted(
            vertical_receipt_lines,
            key=lambda line: line.top_left["y"],
            reverse=True,
        )
        bottom_line = sorted_lines[-1]

        corners = bottom_line.calculate_corners(
            width=image_width, height=image_height, flip_y=True
        )
        # corners = (TL, TR, BL, BR)
        bottom_left = corners[2]
        bottom_right = corners[3]

        # Check X positions
        assert bottom_left[0] == pytest.approx(0.2 * image_width)
        assert bottom_right[0] == pytest.approx(0.8 * image_width)

        # Check Y positions (Y=0.15 normalized -> Y=0.85*height in image coords)
        assert bottom_left[1] == pytest.approx((1 - 0.15) * image_height)
        assert bottom_right[1] == pytest.approx((1 - 0.15) * image_height)

    @pytest.mark.unit
    def test_hull_constrains_left_right_edges(self, vertical_receipt_lines):
        """Test that convex hull constrains left/right X bounds."""
        image_width = 1000
        image_height = 800

        # Get all word corners for hull computation
        all_corners = []
        for line in vertical_receipt_lines:
            corners = line.calculate_corners(
                width=image_width, height=image_height, flip_y=True
            )
            all_corners.extend([(int(x), int(y)) for x, y in corners])

        hull = convex_hull(all_corners)
        hull_xs = [p[0] for p in hull]
        min_hull_x = min(hull_xs)
        max_hull_x = max(hull_xs)

        # Hull should bound all line corners
        assert min_hull_x == pytest.approx(0.2 * image_width, abs=1)
        assert max_hull_x == pytest.approx(0.8 * image_width, abs=1)

    @pytest.mark.unit
    def test_receipt_box_corners_construction(self, vertical_receipt_lines):
        """Test full receipt box corner construction."""
        image_width = 4032
        image_height = 3024

        # Sort lines by Y
        sorted_lines = sorted(
            vertical_receipt_lines,
            key=lambda line: line.top_left["y"],
            reverse=True,
        )
        top_line = sorted_lines[0]
        bottom_line = sorted_lines[-1]

        # Get corners
        top_line_corners = top_line.calculate_corners(
            width=image_width, height=image_height, flip_y=True
        )
        bottom_line_corners = bottom_line.calculate_corners(
            width=image_width, height=image_height, flip_y=True
        )

        # Get hull for X constraints
        all_corners = []
        for line in vertical_receipt_lines:
            corners = line.calculate_corners(
                width=image_width, height=image_height, flip_y=True
            )
            all_corners.extend([(int(x), int(y)) for x, y in corners])
        hull = convex_hull(all_corners)
        hull_xs = [p[0] for p in hull]
        min_hull_x = min(hull_xs)
        max_hull_x = max(hull_xs)

        # Construct receipt box (as done in photo.py)
        receipt_top_left = (
            max(min_hull_x, top_line_corners[0][0]),
            top_line_corners[0][1],
        )
        receipt_top_right = (
            min(max_hull_x, top_line_corners[1][0]),
            top_line_corners[1][1],
        )
        receipt_bottom_left = (
            max(min_hull_x, bottom_line_corners[2][0]),
            bottom_line_corners[2][1],
        )
        receipt_bottom_right = (
            min(max_hull_x, bottom_line_corners[3][0]),
            bottom_line_corners[3][1],
        )

        receipt_box = [
            receipt_top_left,
            receipt_top_right,
            receipt_bottom_right,
            receipt_bottom_left,
        ]

        # Validate receipt box properties
        # Top edge should be above bottom edge
        assert receipt_top_left[1] < receipt_bottom_left[1]
        assert receipt_top_right[1] < receipt_bottom_right[1]

        # Left edge should be left of right edge
        assert receipt_top_left[0] < receipt_top_right[0]
        assert receipt_bottom_left[0] < receipt_bottom_right[0]

        # Width and height should be reasonable
        width = receipt_top_right[0] - receipt_top_left[0]
        height = receipt_bottom_left[1] - receipt_top_left[1]
        assert width > 100  # At least 100 pixels wide
        assert height > 100  # At least 100 pixels tall

    @pytest.mark.unit
    def test_tilted_receipt_corners(self, tilted_receipt_lines):
        """Test corner detection for a tilted receipt."""
        image_width = 4032
        image_height = 3024

        sorted_lines = sorted(
            tilted_receipt_lines,
            key=lambda line: line.top_left["y"],
            reverse=True,
        )
        top_line = sorted_lines[0]
        bottom_line = sorted_lines[-1]

        top_corners = top_line.calculate_corners(
            width=image_width, height=image_height, flip_y=True
        )
        bottom_corners = bottom_line.calculate_corners(
            width=image_width, height=image_height, flip_y=True
        )

        # For tilted receipt, Y values won't be perfectly horizontal
        # Top line: TL.y (0.08*h) should be different from TR.y (0.12*h)
        top_left_y = top_corners[0][1]
        top_right_y = top_corners[1][1]

        # Verify tilt is preserved (top right is lower in image = higher Y)
        assert top_right_y > top_left_y

    @pytest.mark.unit
    def test_empty_lines_raises_no_error(self):
        """Test handling of empty line list."""
        lines = []
        # Should not crash when sorting empty list
        sorted_lines = sorted(
            lines,
            key=lambda line: line.top_left["y"],
            reverse=True,
        )
        assert len(sorted_lines) == 0

    @pytest.mark.unit
    def test_single_line_becomes_both_top_and_bottom(self):
        """Test that a single line is both top and bottom."""
        line = create_mock_line(
            line_id=1,
            text="SINGLE LINE",
            top_left={"x": 0.2, "y": 0.5},
            top_right={"x": 0.8, "y": 0.5},
            bottom_left={"x": 0.2, "y": 0.45},
            bottom_right={"x": 0.8, "y": 0.45},
        )
        lines = [line]

        sorted_lines = sorted(
            lines,
            key=lambda line: line.top_left["y"],
            reverse=True,
        )

        top_line = sorted_lines[0]
        bottom_line = sorted_lines[-1]

        # Both should be the same line
        assert top_line.line_id == bottom_line.line_id == 1


class TestSimplifiedVsComplexApproach:
    """Comparison tests between simplified and complex approaches."""

    @pytest.fixture
    def straight_receipt_lines(self):
        """Lines for a perfectly aligned receipt."""
        return [
            create_mock_line(
                line_id=i,
                text=f"Line {i}",
                top_left={"x": 0.2, "y": 0.9 - (i - 1) * 0.1},
                top_right={"x": 0.8, "y": 0.9 - (i - 1) * 0.1},
                bottom_left={"x": 0.2, "y": 0.85 - (i - 1) * 0.1},
                bottom_right={"x": 0.8, "y": 0.85 - (i - 1) * 0.1},
            )
            for i in range(1, 9)  # line_id must be positive
        ]

    @pytest.mark.unit
    def test_simplified_produces_valid_quadrilateral(self, straight_receipt_lines):
        """Test simplified approach produces a valid quadrilateral."""
        image_width = 1000
        image_height = 800

        sorted_lines = sorted(
            straight_receipt_lines,
            key=lambda line: line.top_left["y"],
            reverse=True,
        )
        top_line = sorted_lines[0]
        bottom_line = sorted_lines[-1]

        top_corners = top_line.calculate_corners(
            width=image_width, height=image_height, flip_y=True
        )
        bottom_corners = bottom_line.calculate_corners(
            width=image_width, height=image_height, flip_y=True
        )

        # Build receipt box
        all_corners = []
        for line in straight_receipt_lines:
            corners = line.calculate_corners(
                width=image_width, height=image_height, flip_y=True
            )
            all_corners.extend([(int(x), int(y)) for x, y in corners])
        hull = convex_hull(all_corners)
        hull_xs = [p[0] for p in hull]
        min_hull_x = min(hull_xs)
        max_hull_x = max(hull_xs)

        receipt_box = [
            (max(min_hull_x, top_corners[0][0]), top_corners[0][1]),  # TL
            (min(max_hull_x, top_corners[1][0]), top_corners[1][1]),  # TR
            (min(max_hull_x, bottom_corners[3][0]), bottom_corners[3][1]),  # BR
            (max(min_hull_x, bottom_corners[2][0]), bottom_corners[2][1]),  # BL
        ]

        # Check it's a valid quadrilateral (4 distinct points)
        assert len(receipt_box) == 4
        assert len(set(receipt_box)) == 4  # All points are distinct

        # Check corners form a valid rectangle-like shape
        # Top should be above bottom
        assert receipt_box[0][1] < receipt_box[3][1]  # TL.y < BL.y
        assert receipt_box[1][1] < receipt_box[2][1]  # TR.y < BR.y

        # Left should be left of right
        assert receipt_box[0][0] < receipt_box[1][0]  # TL.x < TR.x
        assert receipt_box[3][0] < receipt_box[2][0]  # BL.x < BR.x

    @pytest.mark.unit
    def test_hull_constraints_prevent_outlier_corners(self):
        """Test that hull X constraints prevent outlier line corners."""
        image_width = 1000
        image_height = 800

        # Create lines where one line extends further than others
        lines = [
            create_mock_line(
                line_id=1,
                text="WIDE TOP LINE",
                top_left={"x": 0.1, "y": 0.9},  # Extends further left
                top_right={"x": 0.9, "y": 0.9},  # Extends further right
                bottom_left={"x": 0.1, "y": 0.85},
                bottom_right={"x": 0.9, "y": 0.85},
            ),
            create_mock_line(
                line_id=2,
                text="NORMAL LINE",
                top_left={"x": 0.2, "y": 0.5},
                top_right={"x": 0.8, "y": 0.5},
                bottom_left={"x": 0.2, "y": 0.45},
                bottom_right={"x": 0.8, "y": 0.45},
            ),
            create_mock_line(
                line_id=3,
                text="BOTTOM LINE",
                top_left={"x": 0.2, "y": 0.2},
                top_right={"x": 0.8, "y": 0.2},
                bottom_left={"x": 0.2, "y": 0.15},
                bottom_right={"x": 0.8, "y": 0.15},
            ),
        ]

        # Get hull from all corners
        all_corners = []
        for line in lines:
            corners = line.calculate_corners(
                width=image_width, height=image_height, flip_y=True
            )
            all_corners.extend([(int(x), int(y)) for x, y in corners])
        hull = convex_hull(all_corners)
        hull_xs = [p[0] for p in hull]

        # Hull should include the wide line's extent
        assert min(hull_xs) == pytest.approx(0.1 * image_width, abs=1)
        assert max(hull_xs) == pytest.approx(0.9 * image_width, abs=1)

        # But when we constrain, it respects the hull
        sorted_lines = sorted(lines, key=lambda l: l.top_left["y"], reverse=True)
        top_line = sorted_lines[0]
        top_corners = top_line.calculate_corners(
            width=image_width, height=image_height, flip_y=True
        )

        # Hull constraint should allow the wide top line through
        constrained_tl_x = max(min(hull_xs), top_corners[0][0])
        assert constrained_tl_x == pytest.approx(0.1 * image_width, abs=1)


class TestEdgeCases:
    """Test edge cases for the simplified corner detection."""

    @pytest.mark.unit
    def test_overlapping_lines_y_positions(self):
        """Test lines with overlapping Y positions."""
        lines = [
            create_mock_line(
                line_id=1,
                text="Line A",
                top_left={"x": 0.2, "y": 0.5},  # Same Y as Line B
                top_right={"x": 0.8, "y": 0.5},
                bottom_left={"x": 0.2, "y": 0.45},
                bottom_right={"x": 0.8, "y": 0.45},
            ),
            create_mock_line(
                line_id=2,
                text="Line B",
                top_left={"x": 0.2, "y": 0.5},  # Same Y as Line A
                top_right={"x": 0.8, "y": 0.5},
                bottom_left={"x": 0.2, "y": 0.45},
                bottom_right={"x": 0.8, "y": 0.45},
            ),
        ]

        sorted_lines = sorted(
            lines,
            key=lambda line: line.top_left["y"],
            reverse=True,
        )

        # Should still work, just picks one arbitrarily as top
        assert len(sorted_lines) == 2
        assert sorted_lines[0].top_left["y"] == sorted_lines[-1].top_left["y"]

    @pytest.mark.unit
    def test_very_small_receipt(self):
        """Test a receipt with very small extent."""
        line = create_mock_line(
            line_id=1,
            text="TINY",
            top_left={"x": 0.49, "y": 0.51},
            top_right={"x": 0.51, "y": 0.51},
            bottom_left={"x": 0.49, "y": 0.49},
            bottom_right={"x": 0.51, "y": 0.49},
        )

        corners = line.calculate_corners(width=1000, height=1000, flip_y=True)

        # Should produce valid but small corners
        width = corners[1][0] - corners[0][0]  # TR.x - TL.x
        height = corners[2][1] - corners[0][1]  # BL.y - TL.y

        assert width == pytest.approx(20, abs=1)  # 0.02 * 1000
        assert height == pytest.approx(20, abs=1)  # 0.02 * 1000
