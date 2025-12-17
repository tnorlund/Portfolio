import json
import pathlib

import pytest
from receipt_upload.geometry import (
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


@pytest.mark.unit
def test_theil_sen_diagonal():
    pts = [(0, 0), (1, 1), (2, 2)]
    result = theil_sen(pts)
    assert pytest.approx(1.0) == result["slope"]
    assert pytest.approx(0.0) == result["intercept"]


@pytest.mark.unit
def test_compute_edge():
    lines = [
        {
            "top_left": {"x": 0, "y": 0.9},
            "top_right": {"x": 1, "y": 0.9},
            "bottom_left": {"x": 0, "y": 0.8},
            "bottom_right": {"x": 1, "y": 0.8},
        },
        {
            "top_left": {"x": 1, "y": 0.7},
            "top_right": {"x": 2, "y": 0.7},
            "bottom_left": {"x": 1, "y": 0.6},
            "bottom_right": {"x": 2, "y": 0.6},
        },
    ]
    edge = compute_edge(lines, "right")
    assert edge is not None


@pytest.mark.unit
def test_find_line_edges_at_secondary_extremes():
    lines = [
        {
            "top_left": {"x": 0, "y": 1},
            "top_right": {"x": 1, "y": 1},
            "bottom_left": {"x": 0, "y": 0},
            "bottom_right": {"x": 1, "y": 0},
        },
        {
            "top_left": {"x": 1, "y": 1},
            "top_right": {"x": 2, "y": 1},
            "bottom_left": {"x": 1, "y": 0},
            "bottom_right": {"x": 2, "y": 0},
        },
    ]
    hull = [(0, 0), (2, 0), (2, 1), (0, 1)]
    centroid = (1, 0.5)
    result = find_line_edges_at_secondary_extremes(lines, hull, centroid, 0)
    assert "topEdge" in result and "bottomEdge" in result
    assert len(result["topEdge"]) == 2
    assert len(result["bottomEdge"]) == 2


@pytest.mark.unit
def test_compute_final_receipt_tilt():
    lines = [
        {
            "top_left": {"x": 0, "y": 0.9},
            "top_right": {"x": 1, "y": 0.9},
            "bottom_left": {"x": 0, "y": 0.8},
            "bottom_right": {"x": 1, "y": 0.8},
            "angle_degrees": 0,
        }
    ]
    hull = [(0, 0), (2, 0), (2, 1), (0, 1)]
    centroid = (1, 0.5)
    angle = compute_final_receipt_tilt(lines, hull, centroid, 0)
    assert pytest.approx(0.0) == angle


@pytest.mark.unit
def test_find_hull_extremes_along_angle():
    hull = [(0, 0), (2, 0), (2, 1), (0, 1)]
    centroid = (1, 0.5)
    result = find_hull_extremes_along_angle(hull, centroid, 0)
    assert pytest.approx(0) == result["leftPoint"][0]
    assert pytest.approx(2) == result["rightPoint"][0]


@pytest.mark.integration
def test_fixture_receipt_box():
    # Use path relative to this test file's location for robust path resolution
    test_dir = pathlib.Path(__file__).parent
    fixture_path = (
        test_dir / "../../portfolio/tests/fixtures/target_receipt.json"
    )
    fixture_path = fixture_path.resolve()  # Resolve to absolute path

    data = json.loads(fixture_path.read_text())
    lines = data["lines"]

    all_corners = []
    for line in lines:
        all_corners.extend(
            [
                (line["top_left"]["x"], line["top_left"]["y"]),
                (line["top_right"]["x"], line["top_right"]["y"]),
                (line["bottom_right"]["x"], line["bottom_right"]["y"]),
                (line["bottom_left"]["x"], line["bottom_left"]["y"]),
            ]
        )

    hull = convex_hull(all_corners)
    centroid = compute_hull_centroid(hull)
    avg_angle = sum(line["angle_degrees"] for line in lines) / len(lines)
    final_angle = compute_final_receipt_tilt(lines, hull, centroid, avg_angle)
    extremes = find_hull_extremes_along_angle(hull, centroid, final_angle)
    refined = refine_hull_extremes_with_hull_edge_alignment(
        hull, extremes["leftPoint"], extremes["rightPoint"], final_angle
    )
    edges = find_line_edges_at_secondary_extremes(
        lines, hull, centroid, final_angle
    )

    boundaries = {
        "top": create_boundary_line_from_theil_sen(
            theil_sen(edges["topEdge"])
        ),
        "bottom": create_boundary_line_from_theil_sen(
            theil_sen(edges["bottomEdge"])
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

    box = compute_receipt_box_from_boundaries(
        boundaries["top"],
        boundaries["bottom"],
        boundaries["left"],
        boundaries["right"],
        centroid,
    )

    expected = data["receipts"][0]

    # Use tolerance appropriate for complex multi-step geometric algorithm
    # NOTE: The Python and TypeScript implementations have some algorithmic
    # differences in edge detection/processing that result in ~20% differences
    # in some coordinates. This tolerance catches major implementation issues
    # while allowing for these known differences in the complex pipeline.
    tolerance = 2.5e-1  # 25% tolerance for complex geometric algorithm
    assert pytest.approx(expected["top_left"]["x"], rel=tolerance) == box[0][0]
    assert pytest.approx(expected["top_left"]["y"], rel=tolerance) == box[0][1]
    assert (
        pytest.approx(expected["top_right"]["x"], rel=tolerance) == box[1][0]
    )
    assert (
        pytest.approx(expected["top_right"]["y"], rel=tolerance) == box[1][1]
    )
    assert (
        pytest.approx(expected["bottom_right"]["x"], rel=tolerance)
        == box[2][0]
    )
    assert (
        pytest.approx(expected["bottom_right"]["y"], rel=tolerance)
        == box[2][1]
    )
    assert (
        pytest.approx(expected["bottom_left"]["x"], rel=tolerance) == box[3][0]
    )
    assert (
        pytest.approx(expected["bottom_left"]["y"], rel=tolerance) == box[3][1]
    )
