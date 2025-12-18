"""Comprehensive tests for boundary fitting and the horizontal line fix.

This consolidates the key tests from multiple debug/exploration files.
"""

import json
from pathlib import Path
from typing import Dict, List, Tuple

import pytest
from receipt_upload.cluster import dbscan_lines
from receipt_upload.geometry import (
    compute_final_receipt_tilt,
    compute_hull_centroid,
    compute_receipt_box_from_boundaries,
    convex_hull,
    create_boundary_line_from_points,
    create_boundary_line_from_theil_sen,
    create_horizontal_boundary_line_from_points,
    find_hull_extremes_along_angle,
    find_line_edges_at_secondary_extremes,
    refine_hull_extremes_with_hull_edge_alignment,
    theil_sen,
)
from receipt_upload.ocr import process_ocr_dict_as_image
from receipt_upload.route_images import classify_image_layout


class TestBoundaryFitting:
    """Test suite for boundary fitting functionality."""

    @pytest.mark.unit
    def test_theil_sen_horizontal_line_bug(self) -> None:
        """Test that demonstrates the Theil-Sen horizontal line bug.

        When given points forming a horizontal line, theil_sen returns slope=0
        which gets misinterpreted as a vertical line.
        """
        # Horizontal line at y = 0.180233
        horizontal_points = [(0.257752, 0.180233), (0.500000, 0.180233)]

        # Theil-Sen result
        result = theil_sen(horizontal_points)
        assert result["slope"] == 0.0
        assert abs(result["intercept"] - 0.180233) < 1e-6

        # Current behavior: misinterprets as vertical line
        boundary = create_boundary_line_from_theil_sen(result)
        assert boundary["isVertical"] == True  # This is the bug!
        assert abs(boundary["x"] - 0.180233) < 1e-6

    @pytest.mark.unit
    def test_horizontal_boundary_line_from_points(self) -> None:
        """Test the create_horizontal_boundary_line_from_points function."""
        # Horizontal points
        points = [(0.2, 0.5), (0.4, 0.5), (0.6, 0.5), (0.8, 0.5)]

        boundary = create_horizontal_boundary_line_from_points(points)

        # Should create a proper horizontal line
        assert boundary["isVertical"] == False
        assert boundary["slope"] == 0.0
        assert abs(boundary["intercept"] - 0.5) < 1e-6

    @pytest.mark.unit
    def test_boundary_corner_bounds(self) -> None:
        """Test that properly handled boundaries produce corners within [0,1]."""
        # Bar receipt boundaries with FIXED bottom boundary
        boundaries = {
            "top": {
                "isVertical": False,
                "isInverted": True,
                "slope": -61.333328444811286,
                "intercept": 57.546507147981885,
            },
            "bottom": {
                "isVertical": False,
                "isInverted": False,  # Fixed: horizontal line
                "slope": 0.0,
                "intercept": 0.180233,
            },
            "left": {
                "isVertical": True,
                "x": 0.23449612316018242,
                "slope": 0.0,
                "intercept": 0.0,
            },
            "right": {
                "isVertical": False,
                "slope": -13.548385902431102,
                "intercept": 10.358264931625259,
            },
        }

        centroid = (0.4788584657767676, 0.544328338549982)

        # Compute box corners
        box = compute_receipt_box_from_boundaries(
            boundaries["top"],
            boundaries["bottom"],
            boundaries["left"],
            boundaries["right"],
            centroid,
        )

        # All corners should be within [0,1]
        assert len(box) == 4
        for i, (x, y) in enumerate(box):
            assert 0 <= x <= 1, f"Corner {i} X={x} outside [0,1]"
            assert 0 <= y <= 1, f"Corner {i} Y={y} outside [0,1]"

    @pytest.mark.unit
    def test_hardcoded_boundary_values(self) -> None:
        """Test with hardcoded boundary values to ensure consistency."""
        # Simple axis-aligned boundaries
        boundaries = {
            "top": {"isVertical": False, "slope": 0.0, "intercept": 0.82},
            "bottom": {"isVertical": False, "slope": 0.0, "intercept": 0.18},
            "left": {
                "isVertical": True,
                "x": 0.234,
                "slope": 0.0,
                "intercept": 0.0,
            },
            "right": {
                "isVertical": True,
                "x": 0.726,
                "slope": 0.0,
                "intercept": 0.0,
            },
        }

        centroid = (0.48, 0.5)

        box = compute_receipt_box_from_boundaries(
            boundaries["top"],
            boundaries["bottom"],
            boundaries["left"],
            boundaries["right"],
            centroid,
        )

        # Expected corners for axis-aligned box
        expected = [
            (0.234, 0.82),  # top_left
            (0.726, 0.82),  # top_right
            (0.726, 0.18),  # bottom_right
            (0.234, 0.18),  # bottom_left
        ]

        tolerance = 1e-6
        for i, ((ex, ey), (ax, ay)) in enumerate(zip(expected, box)):
            assert abs(ex - ax) < tolerance, f"Corner {i} X mismatch"
            assert abs(ey - ay) < tolerance, f"Corner {i} Y mismatch"


class TestBarReceiptBoundaries:
    """Test suite specifically for the bar receipt fixture."""

    @pytest.fixture
    def bar_receipt_data(self):
        """Load and process the bar receipt fixture."""
        test_dir = Path(__file__).parent
        raw_fixture = (test_dir / "bar_receipt.json").resolve()
        expected_fixture = (
            test_dir / "../../portfolio/tests/fixtures/bar_receipt.json"
        ).resolve()

        raw_data = json.loads(raw_fixture.read_text())
        expected_data = json.loads(expected_fixture.read_text())

        return raw_data, expected_data

    @pytest.mark.unit
    def test_bar_receipt_boundary_detection(self, bar_receipt_data) -> None:
        """Test boundary detection on the bar receipt fixture."""
        raw_data, expected_data = bar_receipt_data

        image_id = expected_data["image"]["image_id"]
        lines, words, letters = process_ocr_dict_as_image(raw_data, image_id)

        # Process clustering and hull
        avg_diag = sum(l.calculate_diagonal_length() for l in lines) / len(lines)
        clusters = dbscan_lines(lines, eps=avg_diag * 2, min_samples=10)
        cluster_lines = [c for cid, c in clusters.items() if cid != -1][0]
        line_ids = [l.line_id for l in cluster_lines]
        cluster_words = [w for w in words if w.line_id in line_ids]

        # Create hull with flipped Y coordinates
        all_word_corners = []
        for word in cluster_words:
            all_word_corners.extend(
                [
                    (word.top_left["x"], 1 - word.top_left["y"]),
                    (word.top_right["x"], 1 - word.top_right["y"]),
                    (word.bottom_right["x"], 1 - word.bottom_right["y"]),
                    (word.bottom_left["x"], 1 - word.bottom_left["y"]),
                ]
            )

        hull = convex_hull(all_word_corners)
        centroid = compute_hull_centroid(hull)

        # Get edges
        avg_angle = sum(l.angle_degrees for l in cluster_lines) / len(cluster_lines)
        final_angle = compute_final_receipt_tilt(
            cluster_lines, hull, centroid, avg_angle
        )
        extremes = find_hull_extremes_along_angle(hull, centroid, final_angle)
        refined = refine_hull_extremes_with_hull_edge_alignment(
            hull, extremes["leftPoint"], extremes["rightPoint"], final_angle
        )
        edges = find_line_edges_at_secondary_extremes(
            cluster_lines, hull, centroid, final_angle
        )

        # Check bottom edge is horizontal
        bottom_edge = edges["bottomEdge"]
        assert len(bottom_edge) >= 2

        # All points should have same Y coordinate (horizontal line)
        y_values = [p[1] for p in bottom_edge]
        y_range = max(y_values) - min(y_values)
        assert y_range < 1e-6, "Bottom edge should be horizontal"

        # Verify boundaries are created
        boundaries = {
            "top": create_boundary_line_from_theil_sen(theil_sen(edges["topEdge"])),
            "bottom": create_horizontal_boundary_line_from_points(
                edges["bottomEdge"]  # Use horizontal function for bottom
            ),
            "left": create_boundary_line_from_points(
                refined["leftSegment"]["extreme"],
                refined["leftSegment"]["optimizedNeighbor"],
            ),
            "right": create_boundary_line_from_points(
                refined["rightSegment"]["extreme"],
                refined["rightSegment"]["optimizedNeighbor"],
            ),
        }

        # Bottom should be horizontal
        assert boundaries["bottom"]["isVertical"] == False
        assert boundaries["bottom"]["slope"] == 0.0

        # Left should be vertical
        assert boundaries["left"]["isVertical"] == True


def create_boundary_line_from_theil_sen_fixed(
    theil_result: Dict[str, float],
) -> Dict[str, float]:
    """Fixed version of create_boundary_line_from_theil_sen.

    This is the proposed fix that correctly handles horizontal lines.
    """
    slope = theil_result["slope"]
    intercept = theil_result["intercept"]

    # Fixed: When slope is near 0 in inverted coordinates,
    # it represents a horizontal line, not vertical
    if abs(slope) < 1e-6:
        return {
            "isVertical": False,
            "isInverted": False,
            "slope": 0.0,
            "intercept": intercept,
        }

    # Otherwise, near-vertical line in inverted coordinates
    return {
        "isVertical": False,
        "isInverted": True,
        "slope": slope,
        "intercept": intercept,
    }


class TestProposedFix:
    """Test suite for the proposed fix."""

    @pytest.mark.unit
    def test_fixed_theil_sen_interpretation(self) -> None:
        """Test the fixed interpretation of Theil-Sen results."""
        # Horizontal line case
        horizontal_result = {"slope": 0.0, "intercept": 0.180233}
        fixed_boundary = create_boundary_line_from_theil_sen_fixed(horizontal_result)

        assert fixed_boundary["isVertical"] == False
        assert fixed_boundary.get("isInverted", False) == False
        assert fixed_boundary["slope"] == 0.0
        assert abs(fixed_boundary["intercept"] - 0.180233) < 1e-6

        # Near-vertical line case
        vertical_result = {"slope": -61.333, "intercept": 57.547}
        fixed_boundary = create_boundary_line_from_theil_sen_fixed(vertical_result)

        assert fixed_boundary["isVertical"] == False
        assert fixed_boundary["isInverted"] == True
        assert abs(fixed_boundary["slope"] - (-61.333)) < 1e-6

    @pytest.mark.unit
    def test_fixed_produces_valid_corners(self) -> None:
        """Test that the fix produces corners within [0,1] bounds."""
        # Bar receipt boundaries with fixed interpretation
        boundaries = {
            "top": create_boundary_line_from_theil_sen_fixed(
                {"slope": -61.333328444811286, "intercept": 57.546507147981885}
            ),
            "bottom": create_boundary_line_from_theil_sen_fixed(
                {"slope": 0.0, "intercept": 0.1802325584269746}
            ),
            "left": {
                "isVertical": True,
                "x": 0.23449612316018242,
                "slope": 0.0,
                "intercept": 0.0,
            },
            "right": {
                "isVertical": False,
                "slope": -13.548385902431102,
                "intercept": 10.358264931625259,
            },
        }

        centroid = (0.4788584657767676, 0.544328338549982)

        box = compute_receipt_box_from_boundaries(
            boundaries["top"],
            boundaries["bottom"],
            boundaries["left"],
            boundaries["right"],
            centroid,
        )

        # All corners should be within bounds
        for i, (x, y) in enumerate(box):
            assert -0.1 <= x <= 1.1, f"Corner {i} X={x} far outside [0,1]"
            assert -0.1 <= y <= 1.1, f"Corner {i} Y={y} far outside [0,1]"

        # Specifically check bottom_right is fixed
        bottom_right = box[2]
        assert 0.7 < bottom_right[0] < 0.8  # X around 0.75
        assert 0.1 < bottom_right[1] < 0.3  # Y around 0.18, not 7.9!
