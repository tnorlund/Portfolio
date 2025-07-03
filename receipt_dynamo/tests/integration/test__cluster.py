# test__cluster.py
import pytest

from receipt_dynamo.data._cluster import dbscan_lines
from receipt_dynamo.entities import Line


@pytest.mark.integration
def test_dbscan_lines_clusters():
    """
    An integration-ish test for dbscan_lines that:
      1. Creates multiple Lines with centroid coordinates close/far apart.
      2. Runs dbscan_lines on them.
      3. Asserts that lines are clustered as expected.
    """

    # We'll define a small helper to create a Line with a given centroid
    # (cx, cy). The corners are placed so that the centroid is roughly at
    # (cx, cy).
    def make_line(line_id, cx, cy):
        # For simplicity, define corners as a small square around (cx, cy).
        half_size = 1.0
        return Line(
            image_id="29984038-5cb5-4ce9-bcf0-856dcfca3125",
            line_id=line_id,
            text=f"Line {line_id}",
            bounding_box={
                "x": cx - half_size,
                "y": cy - half_size,
                "width": 2,
                "height": 2,
            },
            top_right={"x": cx + half_size, "y": cy + half_size},
            top_left={"x": cx - half_size, "y": cy + half_size},
            bottom_right={"x": cx + half_size, "y": cy - half_size},
            bottom_left={"x": cx - half_size, "y": cy - half_size},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=1.0,
        )

    # Create lines in two obvious clusters + 1 noise line
    # Cluster A around (10, 10)
    line1 = make_line(1, 10.0, 10.0)
    line2 = make_line(2, 11.5, 9.7)

    # Cluster B around (40, 40)
    line3 = make_line(3, 40.2, 39.9)
    line4 = make_line(4, 41.0, 41.1)

    # A line far away from both clusters -> expected noise
    line5 = make_line(5, 100.0, 100.0)

    lines = [line1, line2, line3, line4, line5]

    # We'll cluster with eps=3, min_samples=2
    # => lines within distance 3 of each other with at least 2 neighbors
    # => same cluster
    eps = 3.0
    min_samples = 2

    clusters = dbscan_lines(lines, eps=eps, min_samples=min_samples)
    # clusters is a dict => {cluster_label: [Line, Line, ...], ...}
    # Typically, cluster labels >=0 are real clusters, -1 is noise.

    # Let's gather them by label.
    # cluster_labels might be something like 0 => [line1, line2], 1 => [line3,
    # line4], -1 => [line5].
    assert (
        len(clusters) == 3
    ), "We expect 2 real clusters plus a noise cluster (-1)."

    # Ensure we find -1 label for the noise line
    assert -1 in clusters, "Line5 should be labeled as noise."
    noise_cluster = clusters[-1]
    assert noise_cluster == [line5], "Line5 is alone in the noise cluster."

    # Find the labels for line1 and line3
    label_for_line1 = None
    label_for_line3 = None
    for lbl, group in clusters.items():
        if line1 in group:
            label_for_line1 = lbl
        if line3 in group:
            label_for_line3 = lbl

    assert label_for_line1 is not None, "Line1 must be in some cluster label."
    assert label_for_line3 is not None, "Line3 must be in some cluster label."
    assert label_for_line1 != -1, "Line1 is not noise."
    assert label_for_line3 != -1, "Line3 is not noise."

    # Already found label_for_line1 and label_for_line3
    label_for_line2 = None
    label_for_line4 = None
    for lbl, group in clusters.items():
        if line2 in group:
            label_for_line2 = lbl
        if line4 in group:
            label_for_line4 = lbl

    # Lines 1 and 2 share the same cluster label:
    assert (
        label_for_line2 == label_for_line1
    ), "Line1 and Line2 must be in the same cluster."

    # Lines 3 and 4 share the same cluster label:
    assert (
        label_for_line4 == label_for_line3
    ), "Line3 and Line4 must be in the same cluster."
    # Actually simpler approach: just check membership
    cluster_a = clusters[label_for_line1]
    cluster_b = clusters[label_for_line3]

    # cluster_a should have lines 1,2 and cluster_b should have lines 3,4
    # but let's do it by sets, ignoring ordering
    set_a = set(cluster_a)
    set_b = set(cluster_b)

    expected_a = {line1, line2}
    expected_b = {line3, line4}

    # Just ensure we got exactly those pairs
    assert (
        set_a == expected_a or set_b == expected_a
    ), "One cluster should be lines 1 & 2"
    assert (
        set_a == expected_b or set_b == expected_b
    ), "Another cluster should be lines 3 & 4"
