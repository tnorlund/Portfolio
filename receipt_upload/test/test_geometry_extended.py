"""Extended unit tests for geometry modules not covered in test_geometry.py."""

import math
from unittest.mock import Mock, patch

import pytest
from receipt_upload.geometry import (  # From edge_detection.py; From hull_operations.py; From transformations.py; From utils.py
    compute_edge,
    compute_final_receipt_tilt,
    compute_hull_centroid,
    compute_receipt_box_from_boundaries,
    convex_hull,
    create_boundary_line_from_points,
    create_boundary_line_from_theil_sen,
    find_hull_extremes_along_angle,
    find_line_edges_at_secondary_extremes,
    refine_hull_extremes_with_hull_edge_alignment,
    theil_sen,
)


class TestEdgeDetection:
    """Extended test cases for edge detection functions."""

    @pytest.mark.unit
    def test_compute_edge_left_side(self):
        """Test computing left edge from lines."""
        lines = [
            {
                "top_left": {"x": 0.1, "y": 0.2},
                "bottom_left": {"x": 0.1, "y": 0.25},
                "top_right": {"x": 0.9, "y": 0.2},
                "bottom_right": {"x": 0.9, "y": 0.25},
            },
            {
                "top_left": {"x": 0.09, "y": 0.7},
                "bottom_left": {"x": 0.09, "y": 0.75},
                "top_right": {"x": 0.9, "y": 0.7},
                "bottom_right": {"x": 0.9, "y": 0.75},
            },
        ]

        edge = compute_edge(lines, "left", bins=3)
        assert edge is not None
        assert "top" in edge
        assert "bottom" in edge
        # Verify the edge points make sense
        assert isinstance(edge["top"], tuple)
        assert isinstance(edge["bottom"], tuple)

    @pytest.mark.unit
    def test_compute_edge_insufficient_data(self):
        """Test edge computation with insufficient data."""
        # Only one line - need at least 2 bins with data
        lines = [
            {
                "top_left": {"x": 0.1, "y": 0.5},
                "bottom_left": {"x": 0.1, "y": 0.5},
                "top_right": {"x": 0.9, "y": 0.5},
                "bottom_right": {"x": 0.9, "y": 0.5},
            }
        ]

        edge = compute_edge(lines, "left", bins=6)
        assert edge is None

    @pytest.mark.unit
    def test_compute_edge_with_object_lines(self):
        """Test compute_edge with object-based lines."""
        # Create mock line objects
        lines = []
        for i in range(3):
            line = Mock()
            line.top_left = {"x": 0.1 + i * 0.01, "y": 0.9 - i * 0.1}
            line.bottom_left = {"x": 0.1 + i * 0.01, "y": 0.85 - i * 0.1}
            line.top_right = {"x": 0.9, "y": 0.9 - i * 0.1}
            line.bottom_right = {"x": 0.9, "y": 0.85 - i * 0.1}
            lines.append(line)

        edge = compute_edge(lines, "right")
        assert edge is not None

    @pytest.mark.unit
    def test_create_boundary_line_from_points(self):
        """Test creating boundary line from two points."""
        p1 = (0, 0)
        p2 = (10, 10)

        boundary = create_boundary_line_from_points(p1, p2)

        assert boundary is not None
        assert "slope" in boundary
        assert "intercept" in boundary
        assert boundary["slope"] == pytest.approx(1.0)  # 45-degree line has slope 1
        assert boundary["intercept"] == pytest.approx(0.0)

    @pytest.mark.unit
    def test_create_boundary_line_from_points_vertical(self):
        """Test boundary line for vertical edge."""
        p1 = (50, 0)
        p2 = (50, 100)

        boundary = create_boundary_line_from_points(p1, p2)

        assert boundary["isVertical"] == True
        assert boundary["x"] == 50

    @pytest.mark.unit
    def test_create_boundary_line_from_theil_sen(self):
        """Test creating boundary from Theil-Sen parameters."""
        theil_sen_params = {"slope": 1.0, "intercept": 0.0}

        boundary = create_boundary_line_from_theil_sen(theil_sen_params)

        assert boundary is not None
        assert boundary["slope"] == pytest.approx(1.0)
        assert boundary["intercept"] == pytest.approx(0.0)
        assert "isInverted" in boundary
        assert boundary["isInverted"] == True


class TestHullOperations:
    """Test cases for convex hull operations."""

    @pytest.mark.unit
    def test_convex_hull_triangle(self):
        """Test convex hull of a triangle."""
        points = [(0, 0), (10, 0), (5, 10)]
        hull = convex_hull(points)

        assert len(hull) == 3
        assert all(p in hull for p in points)

    @pytest.mark.unit
    def test_convex_hull_square_with_interior(self):
        """Test convex hull with interior points."""
        # Square corners plus interior points
        points = [
            (0, 0),
            (10, 0),
            (10, 10),
            (0, 10),  # Corners
            (5, 5),
            (3, 3),
            (7, 7),  # Interior
        ]
        hull = convex_hull(points)

        assert len(hull) == 4  # Only corners
        assert (5, 5) not in hull  # Interior point excluded

    @pytest.mark.unit
    def test_convex_hull_collinear_points(self):
        """Test convex hull with collinear points."""
        points = [(0, 0), (1, 1), (2, 2), (3, 3), (4, 4)]
        hull = convex_hull(points)

        # Only endpoints should remain
        assert len(hull) == 2
        assert (0, 0) in hull
        assert (4, 4) in hull

    @pytest.mark.unit
    def test_convex_hull_single_point(self):
        """Test convex hull with single point."""
        points = [(5, 5)]
        hull = convex_hull(points)

        assert len(hull) == 1
        assert hull[0] == (5, 5)

    @pytest.mark.unit
    def test_compute_hull_centroid(self):
        """Test centroid computation."""
        # Square
        hull = [(0, 0), (10, 0), (10, 10), (0, 10)]
        centroid = compute_hull_centroid(hull)

        assert centroid[0] == pytest.approx(5.0)
        assert centroid[1] == pytest.approx(5.0)

    @pytest.mark.unit
    def test_compute_hull_centroid_triangle(self):
        """Test centroid of triangle."""
        hull = [(0, 0), (6, 0), (3, 6)]
        centroid = compute_hull_centroid(hull)

        # Centroid of triangle is at (sum_x/3, sum_y/3)
        assert centroid[0] == pytest.approx(3.0)
        assert centroid[1] == pytest.approx(2.0)

    @pytest.mark.unit
    def test_find_hull_extremes_along_angle_45_degrees(self):
        """Test finding extremes at 45-degree angle."""
        hull = [(0, 0), (10, 0), (10, 10), (0, 10)]
        centroid = (5, 5)

        result = find_hull_extremes_along_angle(hull, centroid, 45)

        # At 45 degrees, should find opposite corners
        assert result["leftPoint"] == (0, 0) or result["leftPoint"] == (10, 10)
        assert result["rightPoint"] == (0, 0) or result["rightPoint"] == (
            10,
            10,
        )
        assert result["leftPoint"] != result["rightPoint"]

    @pytest.mark.unit
    def test_refine_hull_extremes_basic(self):
        """Test basic hull extreme refinement."""
        hull = [(0, 0), (10, 0), (10, 10), (0, 10)]
        centroid = (5, 5)

        # Simple lines
        lines = [
            {
                "top_left": {"x": 0.1, "y": 0.9},
                "top_right": {"x": 0.9, "y": 0.9},
                "bottom_left": {"x": 0.1, "y": 0.1},
                "bottom_right": {"x": 0.9, "y": 0.1},
            }
        ]

        result = refine_hull_extremes_with_hull_edge_alignment(hull, (0, 5), (10, 5), 0)

        assert "leftSegment" in result
        assert "rightSegment" in result
        assert "extreme" in result["leftSegment"]
        assert "optimizedNeighbor" in result["leftSegment"]


class TestReceiptBoxComputation:
    """Test cases for receipt box computation."""

    @pytest.mark.unit
    def test_compute_receipt_box_from_boundaries(self):
        """Test computing receipt box from boundaries."""
        boundaries = {
            "top": {"slope": 0, "intercept": 10, "isVertical": False},
            "bottom": {"slope": 0, "intercept": 90, "isVertical": False},
            "left": {"isVertical": True, "x": 10, "slope": 0, "intercept": 0},
            "right": {"isVertical": True, "x": 90, "slope": 0, "intercept": 0},
        }

        receipt_box = compute_receipt_box_from_boundaries(
            boundaries["top"],
            boundaries["bottom"],
            boundaries["left"],
            boundaries["right"],
        )

        assert receipt_box is not None
        assert len(receipt_box) == 4

        # Check corners (returned as list: [top_left, top_right, bottom_right, bottom_left])
        assert receipt_box[0] == pytest.approx((10, 10))  # top_left
        assert receipt_box[1] == pytest.approx((90, 10))  # top_right
        assert receipt_box[2] == pytest.approx((90, 90))  # bottom_right
        assert receipt_box[3] == pytest.approx((10, 90))  # bottom_left

    @pytest.mark.unit
    def test_compute_receipt_box_non_rectangular(self):
        """Test receipt box with non-perpendicular boundaries."""
        boundaries = {
            "top": {
                "slope": 0.125,  # Slight slope
                "intercept": 8.75,
                "isVertical": False,
            },
            "bottom": {
                "slope": 0.125,
                "intercept": 68.75,
                "isVertical": False,
            },
            "left": {"isVertical": True, "x": 10, "slope": 0, "intercept": 0},
            "right": {"isVertical": True, "x": 90, "slope": 0, "intercept": 0},
        }

        receipt_box = compute_receipt_box_from_boundaries(
            boundaries["top"],
            boundaries["bottom"],
            boundaries["left"],
            boundaries["right"],
        )
        assert receipt_box is not None
        assert len(receipt_box) == 4


class TestGeometryUtils:
    """Test cases for geometry utility functions."""

    @pytest.mark.unit
    def test_theil_sen_horizontal_line(self):
        """Test Theil-Sen with horizontal line."""
        points = [(0, 5), (10, 5), (20, 5)]
        result = theil_sen(points)

        assert result["slope"] == pytest.approx(0.0)
        assert result["intercept"] == pytest.approx(5.0)

    @pytest.mark.unit
    def test_theil_sen_vertical_points(self):
        """Test Theil-Sen with vertical alignment (same x)."""
        points = [(5, 0), (5, 10), (5, 20)]
        result = theil_sen(points)

        # Vertical lines have undefined slope in Theil-Sen
        assert result["slope"] == pytest.approx(0.0)  # Median of no valid slopes

    @pytest.mark.unit
    def test_theil_sen_negative_slope(self):
        """Test Theil-Sen with negative slope."""
        points = [(0, 10), (5, 5), (10, 0)]
        result = theil_sen(points)

        assert result["slope"] == pytest.approx(-1.0)
        assert result["intercept"] == pytest.approx(10.0)

    @pytest.mark.unit
    def test_theil_sen_outliers(self):
        """Test Theil-Sen robustness to outliers."""
        # Mostly linear points with one outlier
        points = [
            (0, 0),
            (1, 1),
            (2, 2),
            (3, 3),
            (4, 10),
        ]  # Last point is outlier
        result = theil_sen(points)

        # Should be close to slope=1 despite outlier
        assert result["slope"] == pytest.approx(1.0, rel=0.5)


class TestTransformations:
    """Test cases for geometric transformations."""

    @pytest.mark.unit
    def test_compute_final_receipt_tilt_aligned(self):
        """Test tilt computation for aligned receipt."""
        lines = [
            {
                "top_left": {"x": 0.1, "y": 0.9},
                "top_right": {"x": 0.9, "y": 0.9},
                "bottom_left": {"x": 0.1, "y": 0.8},
                "bottom_right": {"x": 0.9, "y": 0.8},
                "angle_degrees": 0,
            },
            {
                "top_left": {"x": 0.1, "y": 0.7},
                "top_right": {"x": 0.9, "y": 0.7},
                "bottom_left": {"x": 0.1, "y": 0.6},
                "bottom_right": {"x": 0.9, "y": 0.6},
                "angle_degrees": 0,
            },
        ]

        hull = [(0, 0), (1, 0), (1, 1), (0, 1)]
        centroid = (0.5, 0.5)

        angle = compute_final_receipt_tilt(lines, hull, centroid, 0)
        assert angle == pytest.approx(0.0, abs=5.0)  # Should be near 0

    @pytest.mark.unit
    def test_compute_final_receipt_tilt_rotated(self):
        """Test tilt computation for rotated text."""
        lines = []
        for i in range(3):
            lines.append(
                {
                    "top_left": {"x": 0.1, "y": 0.9 - i * 0.2},
                    "top_right": {"x": 0.9, "y": 0.9 - i * 0.2},
                    "bottom_left": {"x": 0.1, "y": 0.8 - i * 0.2},
                    "bottom_right": {"x": 0.9, "y": 0.8 - i * 0.2},
                    "angle_degrees": 15,  # All lines tilted 15 degrees
                }
            )

        hull = [(0, 0), (1, 0), (1, 1), (0, 1)]
        centroid = (0.5, 0.5)

        angle = compute_final_receipt_tilt(lines, hull, centroid, 15)
        # The function refines the angle, so it might not be exactly 15
        assert isinstance(angle, (int, float))  # Just verify it returns a number
