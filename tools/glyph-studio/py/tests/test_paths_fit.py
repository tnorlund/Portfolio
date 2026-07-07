import numpy as np
from glyphstudio.fitcurve import (
    find_corners,
    fit_cubics,
    polyline_to_segments,
    segments_to_nodes,
)
from glyphstudio.paths import extract_paths


def test_extract_straight_line():
    skel = np.zeros((20, 50), dtype=bool)
    skel[10, 5:45] = True
    paths = extract_paths(skel)
    assert len(paths) == 1
    assert not paths[0]["closed"]
    assert len(paths[0]["points"]) >= 30


def test_extract_t_junction_yields_three_edges():
    skel = np.zeros((40, 40), dtype=bool)
    skel[20, 5:35] = True   # horizontal
    skel[5:20, 20] = True   # vertical meeting it
    paths = extract_paths(skel)
    assert len(paths) == 3  # left arm, right arm, vertical arm


def test_extract_ring_is_closed():
    yy, xx = np.mgrid[0:50, 0:50]
    r = np.sqrt((yy - 25) ** 2 + (xx - 25) ** 2)
    ring = (r > 14.5) & (r < 16.5)
    from glyphstudio.thin import zhang_suen
    skel = zhang_suen(ring)
    paths = extract_paths(skel)
    closed = [p for p in paths if p["closed"]]
    assert len(closed) == 1


def test_corner_detection_on_l():
    down = [(float(i), 10.0) for i in range(20)]
    right = [(19.0, 10.0 + float(i)) for i in range(1, 20)]
    pts = np.array(down + right)
    corners = find_corners(pts)
    assert len(corners) == 1
    assert abs(corners[0] - 19) <= 3


def test_line_run_becomes_line_segment():
    pts = np.array([(float(i), 5.0 + 0.01 * i) for i in range(30)])
    segs = polyline_to_segments(pts, tol=1.5, line_tol=0.9)
    assert len(segs) == 1
    assert segs[0]["kind"] == "line"


def test_fit_cubic_arc_error_bounded():
    t = np.linspace(0, np.pi, 60)
    pts = np.stack([20 * np.sin(t), 20 - 20 * np.cos(t)], axis=1)
    cubics = fit_cubics(pts, tol=1.0)
    assert 1 <= len(cubics) <= 4
    # error check: sample each cubic densely, nearest-point distance to input
    for ctrl in cubics:
        ts = np.linspace(0, 1, 50)
        mt = 1 - ts
        curve = (
            (mt ** 3)[:, None] * ctrl[0]
            + 3 * (mt ** 2 * ts)[:, None] * ctrl[1]
            + 3 * (mt * ts ** 2)[:, None] * ctrl[2]
            + (ts ** 3)[:, None] * ctrl[3]
        )
        d = np.min(
            np.linalg.norm(curve[:, None, :] - pts[None, :, :], axis=2),
            axis=1,
        )
        assert float(d.max()) < 1.6


def test_segments_to_nodes_l_shape():
    pts = np.array(
        [(float(i), 10.0) for i in range(20)]
        + [(19.0, 10.0 + float(i)) for i in range(1, 20)]
    )
    segs = polyline_to_segments(pts, tol=1.5, line_tol=0.9)
    nodes = segments_to_nodes(segs, closed=False)
    assert len(nodes) >= 3  # start, corner, end
    assert all("x" in n and "y" in n for n in nodes)
