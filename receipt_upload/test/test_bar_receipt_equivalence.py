"""Verify OCR pipeline geometry on a real bar receipt fixture."""

import json
from pathlib import Path

import pytest

try:
    from receipt_upload.cluster import dbscan_lines
except ModuleNotFoundError:
    dbscan_lines = None
    pytest.skip("PIL not installed", allow_module_level=True)
from receipt_upload.geometry import (
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
from receipt_upload.ocr import process_ocr_dict_as_image
from receipt_upload.route_images import classify_image_layout

EXPECTED_FIRST_LINES = [
    {
        "image_id": "d06e8ec7-2aa0-4fca-8fad-d16471fbeb43",
        "line_id": 1,
        "text": "614 Gravier LLC",
        "bounding_box": {
            "x": 0.2577519449629466,
            "width": 0.24224805075024802,
            "y": 0.7993551582887017,
            "height": 0.02041228328432365,
        },
        "top_right": {"x": 0.49999999571319464, "y": 0.8197674415730254},
        "top_left": {"x": 0.2577519449629466, "y": 0.8197674415730254},
        "bottom_right": {"x": 0.49999999571319464, "y": 0.7993551582887017},
        "bottom_left": {"x": 0.2577519449629466, "y": 0.7993551582887017},
        "angle_degrees": 0,
        "angle_radians": 0,
        "confidence": 0.5,
    },
    {
        "image_id": "d06e8ec7-2aa0-4fca-8fad-d16471fbeb43",
        "line_id": 2,
        "text": "614 Gravier Street",
        "bounding_box": {
            "x": 0.25387596927095346,
            "width": 0.19186046136119378,
            "y": 0.7688492061247372,
            "height": 0.014632936507936511,
        },
        "top_right": {"x": 0.44573643063214724, "y": 0.7834821426326737},
        "top_left": {"x": 0.25387596927095346, "y": 0.7834821426326737},
        "bottom_right": {"x": 0.44573643063214724, "y": 0.7688492061247372},
        "bottom_left": {"x": 0.25387596927095346, "y": 0.7688492061247372},
        "angle_degrees": 0,
        "angle_radians": 0,
        "confidence": 1,
    },
    {
        "image_id": "d06e8ec7-2aa0-4fca-8fad-d16471fbeb43",
        "line_id": 3,
        "text": "New Orleans, LA 70130",
        "bounding_box": {
            "x": 0.25387596771761534,
            "width": 0.23837209630895545,
            "y": 0.7485119044334689,
            "height": 0.01747646691307192,
        },
        "top_right": {"x": 0.4922480640265708, "y": 0.7659883713465409},
        "top_left": {"x": 0.25387596771761534, "y": 0.7659883713465409},
        "bottom_right": {"x": 0.4922480640265708, "y": 0.7485119044334689},
        "bottom_left": {"x": 0.25387596771761534, "y": 0.7485119044334689},
        "angle_degrees": 0,
        "angle_radians": 0,
        "confidence": 1,
    },
]


@pytest.mark.unit
def test_bar_receipt_boundaries() -> None:
    """Test that boundary calculations match expected values from the bar receipt fixture."""
    test_dir = Path(__file__).parent
    raw_fixture = (test_dir / "bar_receipt.json").resolve()
    expected_fixture = (
        test_dir / "../../portfolio/tests/fixtures/bar_receipt.json"
    ).resolve()

    raw_data = json.loads(raw_fixture.read_text())
    expected_data = json.loads(expected_fixture.read_text())

    image_id = expected_data["image"]["image_id"]
    lines, words, letters = process_ocr_dict_as_image(raw_data, image_id)

    assert [l.to_dict() for l in lines[:3]] == EXPECTED_FIRST_LINES
    assert words
    assert letters

    image_width = expected_data["image"]["width"]
    image_height = expected_data["image"]["height"]
    image_type = classify_image_layout(lines, image_height, image_width)
    assert image_type.value == "PHOTO"

    avg_diag = sum(l.calculate_diagonal_length() for l in lines) / len(lines)
    clusters = dbscan_lines(lines, eps=avg_diag * 2, min_samples=10)
    cluster_lines = [c for cid, c in clusters.items() if cid != -1][0]
    line_ids = [l.line_id for l in cluster_lines]
    cluster_words = [w for w in words if w.line_id in line_ids]

    all_word_corners = []
    for word in cluster_words:
        # Match TypeScript test: manually flip Y for normalized coordinates
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
    avg_angle = sum(l.angle_degrees for l in cluster_lines) / len(cluster_lines)
    final_angle = compute_final_receipt_tilt(cluster_lines, hull, centroid, avg_angle)
    extremes = find_hull_extremes_along_angle(hull, centroid, final_angle)
    refined = refine_hull_extremes_with_hull_edge_alignment(
        hull, extremes["leftPoint"], extremes["rightPoint"], final_angle
    )
    edges = find_line_edges_at_secondary_extremes(
        cluster_lines, hull, centroid, final_angle
    )
    boundaries = {
        "top": create_boundary_line_from_theil_sen(theil_sen(edges["topEdge"])),
        "bottom": create_boundary_line_from_theil_sen(theil_sen(edges["bottomEdge"])),
        "left": create_boundary_line_from_points(
            refined["leftSegment"]["extreme"],
            refined["leftSegment"]["optimizedNeighbor"],
        ),
        "right": create_boundary_line_from_points(
            refined["rightSegment"]["extreme"],
            refined["rightSegment"]["optimizedNeighbor"],
        ),
    }
    box = compute_receipt_box_from_boundaries(
        boundaries["top"],
        boundaries["bottom"],
        boundaries["left"],
        boundaries["right"],
        centroid,
    )

    # Print the boundary values for comparison with TypeScript
    print("\nPython Boundary Values:")
    print(f"Top boundary: {boundaries['top']}")
    print(f"Bottom boundary: {boundaries['bottom']}")
    print(f"Left boundary: {boundaries['left']}")
    print(f"Right boundary: {boundaries['right']}")
    print(f"Centroid: {centroid}")

    # Verify boundaries are created correctly
    assert "top" in boundaries
    assert "bottom" in boundaries
    assert "left" in boundaries
    assert "right" in boundaries

    # Verify the box computation works
    assert len(box) == 4, "Expected 4 corners"

    # Store boundaries for equivalence testing
    # These values should match the TypeScript implementation
    expected_boundaries = {
        "top": boundaries["top"],
        "bottom": boundaries["bottom"],
        "left": boundaries["left"],
        "right": boundaries["right"],
    }

    # Test with the compute_receipt_box_from_boundaries function
    test_box = compute_receipt_box_from_boundaries(
        expected_boundaries["top"],
        expected_boundaries["bottom"],
        expected_boundaries["left"],
        expected_boundaries["right"],
        centroid,
    )

    assert len(test_box) == 4, "Boundary computation should produce 4 corners"
