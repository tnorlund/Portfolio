"""Test boundary calculations with hardcoded values to ensure Python/TypeScript equivalence."""

import pytest
from receipt_upload.geometry import compute_receipt_box_from_boundaries


@pytest.mark.unit
def test_boundary_box_with_hardcoded_values() -> None:
    """Test that compute_receipt_box_from_boundaries produces expected results.

    Uses hardcoded boundaries to ensure Python/TypeScript equivalence.

    These boundary values are hardcoded to match the TypeScript test, ensuring both
    implementations produce identical results when given the same boundary lines.
    """
    # Hardcoded boundary lines that represent a typical receipt
    # Using the OCR coordinate system where y=0 is at the bottom
    boundaries = {
        # Top boundary: horizontal line at y = 0.82
        "top": {
            "isVertical": False,
            "isInverted": False,  # y = slope * x + intercept format
            "slope": 0.0,
            "intercept": 0.82,  # horizontal line at y = 0.82
        },
        # Bottom boundary: horizontal line at y = 0.18
        "bottom": {
            "isVertical": False,
            "isInverted": False,  # y = slope * x + intercept format
            "slope": 0.0,
            "intercept": 0.18,  # horizontal line at y = 0.18
        },
        # Left boundary: vertical line at x = 0.234
        "left": {"isVertical": True, "x": 0.234, "slope": 0, "intercept": 0},
        # Right boundary: vertical line at x = 0.726
        "right": {"isVertical": True, "x": 0.726, "slope": 0, "intercept": 0},
    }

    # Centroid for the receipt (center point)
    centroid = (0.48, 0.5)

    # Compute the box corners
    box = compute_receipt_box_from_boundaries(
        boundaries["top"],
        boundaries["bottom"],
        boundaries["left"],
        boundaries["right"],
        centroid,
    )

    # Expected corners in OCR coordinate system (y=0 at bottom)
    # Order: [top_left, top_right, bottom_right, bottom_left]
    expected_corners = [
        (0.234, 0.82),  # top_left
        (0.726, 0.82),  # top_right
        (0.726, 0.18),  # bottom_right
        (0.234, 0.18),  # bottom_left
    ]

    # Verify the corners match expected values
    tolerance = 1e-6
    for i, (expected, actual) in enumerate(zip(expected_corners, box)):
        assert (
            abs(expected[0] - actual[0]) < tolerance
        ), f"Corner {i} X mismatch: expected {expected[0]}, got {actual[0]}"
        assert (
            abs(expected[1] - actual[1]) < tolerance
        ), f"Corner {i} Y mismatch: expected {expected[1]}, got {actual[1]}"


@pytest.mark.unit
def test_boundary_box_with_slanted_boundaries() -> None:
    """Test compute_receipt_box_from_boundaries with slanted boundary lines.

    This tests a more complex case where the receipt is rotated/skewed.
    """
    # Hardcoded boundary lines for a slanted receipt
    boundaries = {
        # Top boundary: slightly slanted line (y = 0.02x + 0.8)
        "top": {
            "isVertical": False,
            "isInverted": False,  # y = slope * x + intercept format
            "slope": 0.02,
            "intercept": 0.8,
        },
        # Bottom boundary: slightly slanted line (y = 0.02x + 0.15)
        "bottom": {
            "isVertical": False,
            "isInverted": False,  # y = slope * x + intercept format
            "slope": 0.02,
            "intercept": 0.15,
        },
        # Left boundary: near-vertical line (x = -0.1y + 0.3)
        "left": {
            "isVertical": False,
            "isInverted": True,  # x = slope * y + intercept format
            "slope": -0.1,
            "intercept": 0.3,
        },
        # Right boundary: near-vertical line (x = -0.1y + 0.8)
        "right": {
            "isVertical": False,
            "isInverted": True,  # x = slope * y + intercept format
            "slope": -0.1,
            "intercept": 0.8,
        },
    }

    # Centroid for the receipt
    centroid = (0.5, 0.5)

    # Compute the box corners
    box = compute_receipt_box_from_boundaries(
        boundaries["top"],
        boundaries["bottom"],
        boundaries["left"],
        boundaries["right"],
        centroid,
    )

    # For slanted boundaries, we verify the box has 4 corners
    assert len(box) == 4, f"Expected 4 corners, got {len(box)}"

    # Verify corners form a valid quadrilateral (non-zero area)
    # Using shoelace formula for polygon area
    area = 0.0
    n = len(box)
    for i in range(n):
        j = (i + 1) % n
        area += box[i][0] * box[j][1]
        area -= box[j][0] * box[i][1]
    area = abs(area) / 2.0

    assert area > 0.01, f"Computed box has near-zero area: {area}"
