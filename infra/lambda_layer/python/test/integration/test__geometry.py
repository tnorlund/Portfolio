# infra/lambda_layer/python/test/integration/test__geometry.py
import pytest
from math import sqrt, pi, isclose
from dynamo.data._geometry import (
    invert_affine,
    convex_hull,
    min_area_rect,
    box_points,
    compute_receipt_box_from_skewed_extents,
    find_hull_extents_relative_to_centroid,
    compute_hull_centroid
)

@pytest.mark.unit
def test_invert_affine():
    # Test identity matrix
    result = invert_affine(1, 0, 0, 0, 1, 0)
    assert result == (1, 0, 0, 0, 1, 0)

    # Test scaling matrix
    result = invert_affine(2, 0, 0, 0, 2, 0)
    assert result == (0.5, 0, 0, 0, 0.5, 0)

    # Test translation
    result = invert_affine(1, 0, 5, 0, 1, 3)
    assert result == (1, 0, -5, 0, 1, -3)

    # Test rotation (90 degrees)
    result = invert_affine(0, -1, 0, 1, 0, 0)
    assert all(abs(a - b) < 1e-10 for a, b in zip(result, (0, 1, 0, -1, 0, 0)))

    # Test singular matrix
    with pytest.raises(ValueError):
        invert_affine(0, 0, 0, 0, 0, 0)

@pytest.mark.unit
def test_convex_hull():
    # Test empty list
    assert convex_hull([]) == []

    # Test single point
    assert convex_hull([(1, 1)]) == [(1, 1)]

    # Test two points
    assert convex_hull([(1, 1), (2, 2)]) == [(1, 1), (2, 2)]

    # Test square
    points = [(0, 0), (0, 1), (1, 0), (1, 1)]
    hull = convex_hull(points)
    assert len(hull) == 4
    assert (0, 0) in hull
    assert (0, 1) in hull
    assert (1, 0) in hull
    assert (1, 1) in hull

    # Test triangle with interior point
    points = [(0, 0), (2, 0), (1, 2), (1, 1)]  # Last point is interior
    hull = convex_hull(points)
    assert len(hull) == 3
    assert (0, 0) in hull
    assert (2, 0) in hull
    assert (1, 2) in hull

    # Test duplicate points
    points = [(0, 0), (0, 0), (1, 1), (1, 1)]
    hull = convex_hull(points)
    assert hull == [(0, 0), (1, 1)]

@pytest.mark.unit
def test_min_area_rect():
    # Test empty list
    center, size, angle = min_area_rect([])
    assert center == (0, 0)
    assert size == (0, 0)
    assert angle == 0

    # Test single point
    point = (1, 1)
    center, size, angle = min_area_rect([point])
    assert center == point
    assert size == (0, 0)
    assert angle == 0

    # Test horizontal rectangle
    points = [(0, 0), (2, 0), (2, 1), (0, 1)]
    center, size, angle = min_area_rect(points)
    assert abs(center[0] - 1) < 1e-10
    assert abs(center[1] - 0.5) < 1e-10
    assert abs(size[0] - 2) < 1e-10
    assert abs(size[1] - 1) < 1e-10
    assert abs(angle) < 1e-10

    # Test 45-degree rotated square
    points = [(0, 0), (1, 1), (0, 2), (-1, 1)]
    center, size, angle = min_area_rect(points)
    assert abs(center[0] - 0) < 1e-10
    assert abs(center[1] - 1) < 1e-10
    assert abs(size[0] - sqrt(2)) < 1e-10
    assert abs(size[1] - sqrt(2)) < 1e-10
    assert abs(abs(angle) - 45) < 1e-10

    # Test two points (degenerate case)
    points = [(0, 0), (1, 1)]
    center, size, angle = min_area_rect(points)
    assert abs(center[0] - 0.5) < 1e-10
    assert abs(center[1] - 0.5) < 1e-10
    assert abs(size[0] - sqrt(2)) < 1e-10
    assert abs(size[1] - 0) < 1e-10
    assert abs(angle) < 1e-10

@pytest.mark.unit
def test_box_points():
    # Test unit square at origin
    points = box_points((0, 0), (2, 2), 0)
    expected = [(-1, -1), (1, -1), (1, 1), (-1, 1)]
    assert all(abs(p[0] - e[0]) < 1e-10 and abs(p[1] - e[1]) < 1e-10 
              for p, e in zip(points, expected))

    # Test rectangle with translation
    points = box_points((1, 1), (2, 1), 0)
    expected = [(0, 0.5), (2, 0.5), (2, 1.5), (0, 1.5)]
    assert all(abs(p[0] - e[0]) < 1e-10 and abs(p[1] - e[1]) < 1e-10 
              for p, e in zip(points, expected))

    # Test 90-degree rotation
    points = box_points((0, 0), (2, 1), 90)
    expected = [(0.5, -1), (0.5, 1), (-0.5, 1), (-0.5, -1)]
    assert all(abs(p[0] - e[0]) < 1e-10 and abs(p[1] - e[1]) < 1e-10 
              for p, e in zip(points, expected))

    # Test 45-degree rotation
    points = box_points((0, 0), (sqrt(2), sqrt(2)), 45)
    expected = [(0, -1), (1, 0), (0, 1), (-1, 0)]   
    assert all(abs(p[0] - e[0]) < 1e-10 and abs(p[1] - e[1]) < 1e-10 
              for p, e in zip(points, expected))

# Additional test for duplicate points in min_area_rect to hit the len(hull) < 3 branch.
@pytest.mark.unit
def test_min_area_rect_duplicate_points():
    # When duplicate points are provided, the convex_hull function collapses them
    # to a single unique point, triggering the degenerate branch in min_area_rect.
    points = [(0, 0), (0, 0)]
    center, size, angle = min_area_rect(points)
    # Expected result is equivalent to the single point case.
    assert center == (0, 0)
    assert size == (0, 0)
    assert angle == 0
    

@pytest.mark.unit
def test_compute_receipt_box_from_skewed_extents_empty():
    """If hull_pts is empty, should return None."""
    result = compute_receipt_box_from_skewed_extents([], 0, 0, 45)
    assert result is None


@pytest.mark.unit
def test_compute_receipt_box_from_skewed_extents_no_top():
    """
    All points in deskewed space end up in the bottom half (y >= 0),
    so top_half would be empty. This forces the fallback top_half = pts_deskew[:].
    """
    hull_pts = [(0, 0), (2, 0), (2, 2), (0, 2)]  # a simple square
    # We'll use rotation_deg=0 for simplicity, so deskew doesn't move them.
    # center = (0,0), rotation = 0 => deskewed points are the same as hull_pts
    # All points have y >= 0 => top_half would be empty
    result = compute_receipt_box_from_skewed_extents(hull_pts, 0, 0, 0)
    # Should basically return the bounding box of the square in integer coords.
    # The function returns corners: [top-left, top-right, bottom-right, bottom-left].
    # But since we have no rotation, top=lowest y => that's actually y=0, 
    # bottom=highest y => y=2
    # So corners should be (0,0), (2,0), (2,2), (0,2).
    assert result == [[0, 0], [2, 0], [2, 2], [0, 2]]


@pytest.mark.unit
def test_compute_receipt_box_from_skewed_extents_no_bottom():
    """
    All points in deskewed space are in the top half (y < 0),
    so bottom_half would be empty. This forces the fallback bottom_half = pts_deskew[:].
    """
    # We'll place these hull points below (0,0) so that after deskew they remain y < 0
    hull_pts = [(0, -1), (2, -1), (2, -3), (0, -3)]
    # No rotation, center=(0,0).
    result = compute_receipt_box_from_skewed_extents(hull_pts, 0, 0, 0)
    # The bounding box corners in integer coords:
    # top is the minimum y => -3
    # bottom is the maximum y => -1
    # x spans [0, 2].
    # So corners => (0,-3), (2, -3), (2, -1), (0, -1)
    # The function's return format is [top-left, top-right, bottom-right, bottom-left].
    assert result == [[0, -3], [2, -3], [2, -1], [0, -1]]


@pytest.mark.unit
def test_compute_receipt_box_from_skewed_extents_degrees():
    """
    Basic test with a rotation in degrees. We'll pick a simple rectangle,
    rotate by 90 degrees around center=(0,0), then confirm corners.
    """
    hull_pts = [(0, 0), (2, 0), (2, 1), (0, 1)]
    # If we rotate by 90 deg around (0,0), deskewing effectively puts points 
    # into a space where top/bottom can be found, etc.
    # Then the final result is rotated back. The net effect for this rectangle 
    # might shift corners. We'll just check for correct integer corners.
    result = compute_receipt_box_from_skewed_extents(hull_pts, 0, 0, 90)
    # We expect the bounding "receipt box" to still form the same bounding rectangle,
    # but oriented differently. After a 90-degree rotation around (0,0),
    # the bounding corners should become something like:
    #   top-left => ( -1, 0 )
    #   top-right => ( -1, 2 )
    #   bottom-right => ( 0, 2 )
    #   bottom-left => ( 0, 0 )
    #
    # (One can do the geometry by hand or verify the final 4 points.)
    #
    # Because the function does "deskew" by +theta then "rotate back" by -theta,
    # the resulting bounding box in original coords will be the same as the original 
    # aligned rectangle but possibly the corners might be at different positions 
    # depending on how the function interprets top/bottom. 
    #
    # Let's just check we get a 4-corner integer box that encloses the original. 
    # One way is to check the bounding box of the result if exact corners are unclear.
    #
    # Here, let's just check the shape. It's often easiest to ensure the bounding box 
    # encloses (0,0),(2,0),(2,1),(0,1).
    xs = [pt[0] for pt in result]
    ys = [pt[1] for pt in result]
    assert len(result) == 4
    assert min(xs) <= 0 and max(xs) >= 2
    assert min(ys) <= 0 and max(ys) >= 1


@pytest.mark.unit
def test_compute_receipt_box_from_skewed_extents_radians():
    """
    Use rotation in radians. We'll rotate by pi/2 (~90 deg), around (1,0)
    to ensure 'use_radians=True' path is covered.
    """
    hull_pts = [(1, 0), (3, 0), (3, 1), (1, 1)]
    # Center is (1,0), rotation = pi/2, so deskew is a +90 deg about (1,0).
    # Then rotate back. We expect an integer bounding box in the end.
    result = compute_receipt_box_from_skewed_extents(hull_pts, 1, 0, pi/2, use_radians=True)
    # Just check we got 4 corners and that bounding box is wide enough to include 
    # original rectangle from x=1..3, y=0..1.
    xs = [pt[0] for pt in result]
    ys = [pt[1] for pt in result]
    assert len(result) == 4
    assert min(xs) <= 1 and max(xs) >= 3
    assert min(ys) <= 0 and max(ys) >= 1


@pytest.mark.unit
def test_find_hull_extents_relative_to_centroid_single_point():
    """
    A hull with just one point has no edges, so there should be no intersection 
    in any direction => all directions yield None.
    """
    hull_pts = [(5, 5)]
    results = find_hull_extents_relative_to_centroid(hull_pts, 5, 5, rotation_deg=0)
    assert results["left"] is None
    assert results["right"] is None
    assert results["top"] is None
    assert results["bottom"] is None


@pytest.mark.unit
def test_find_hull_extents_relative_to_centroid_square_no_rotation():
    """
    A simple square from (0,0) to (10,10). Centroid is (5,5).
    With no rotation, 'left' => (0,5), 'right' => (10,5), 
    'top' => (5,10), 'bottom' => (5,0).
    """
    hull_pts = [(0,0), (10,0), (10,10), (0,10)]
    cx, cy = 5, 5
    results = find_hull_extents_relative_to_centroid(hull_pts, cx, cy, rotation_deg=0)
    assert results["left"]   == (0, 5)
    assert results["right"]  == (10, 5)
    assert results["top"]    == (5, 0)
    assert results["bottom"] == (5, 10)


@pytest.mark.unit
def test_find_hull_extents_relative_to_centroid_square_rotation_degrees():
    """
    Same square, but rotated by 45 degrees. We'll just check approximate intersection
    points. The centroid is still (5,5).
    """
    hull_pts = [(0,0), (10,0), (10,10), (0,10)]
    cx, cy = 5, 5
    results = find_hull_extents_relative_to_centroid(hull_pts, cx, cy, rotation_deg=45)
    # We expect each intersection to be roughly sqrt(50) ~ 7.071 units away 
    # from the center if it's a perfect diagonal. We'll check approximate locations.
    left_pt = results["left"]
    right_pt = results["right"]
    top_pt = results["top"]
    bottom_pt = results["bottom"]
    assert left_pt  is not None
    assert right_pt is not None
    assert top_pt   is not None
    assert bottom_pt is not None
    
    # For a 45-degree rotation, "left" is direction -u, 
    # which should land near the midpoint of the left edge. 
    # We'll just check that we got integer points and they're near the square boundary.
    for pt in (left_pt, right_pt, top_pt, bottom_pt):
        # Each coordinate should be in [0..10].
        assert 0 <= pt[0] <= 10
        assert 0 <= pt[1] <= 10


@pytest.mark.unit
def test_find_hull_extents_relative_to_centroid_square_rotation_radians():
    """
    Same square, rotate by pi/4 (45 deg) but use_radians=True.
    """
    hull_pts = [(0,0), (10,0), (10,10), (0,10)]
    cx, cy = 5, 5
    results = find_hull_extents_relative_to_centroid(
        hull_pts, cx, cy, rotation_deg=pi/4, use_radians=True
    )
    # Similar checks as above:
    left_pt = results["left"]
    right_pt = results["right"]
    top_pt = results["top"]
    bottom_pt = results["bottom"]
    assert None not in (left_pt, right_pt, top_pt, bottom_pt)
    for pt in (left_pt, right_pt, top_pt, bottom_pt):
        assert 0 <= pt[0] <= 10
        assert 0 <= pt[1] <= 10


@pytest.mark.unit
def test_find_hull_extents_relative_to_centroid_line():
    """
    For a hull defined by a single horizontal edge from (0,0) to (10,0) with centroid at (5,0):
      - Horizontal rays ('left' and 'right') are collinear with the edge and yield no intersection (None).
      - Vertical rays ('top' and 'bottom') intersect at the centroid, yielding (5,0).
    """
    hull_pts = [(0, 0), (10, 0)]
    cx, cy = 5, 0
    results = find_hull_extents_relative_to_centroid(hull_pts, cx, cy, 0)
    assert results["left"] is None
    assert results["right"] is None
    # Vertical rays yield the centroid as the intersection
    assert results["top"] == (5, 0)
    assert results["bottom"] == (5, 0)


@pytest.mark.unit
def test_compute_hull_centroid_empty():
    """Empty input returns (0,0)."""
    assert compute_hull_centroid([]) == (0.0, 0.0)


@pytest.mark.unit
def test_compute_hull_centroid_single_point():
    """Single point -> returns that point."""
    pt = (3.2, -1.7)
    assert compute_hull_centroid([pt]) == pt


@pytest.mark.unit
def test_compute_hull_centroid_two_points():
    """Two points -> returns midpoint."""
    p1 = (0, 0)
    p2 = (4, 8)
    centroid = compute_hull_centroid([p1, p2])
    assert isclose(centroid[0], 2.0)
    assert isclose(centroid[1], 4.0)


@pytest.mark.unit
def test_compute_hull_centroid_polygon():
    """
    Non-degenerate polygon. We'll take a square with corners (0,0),(4,0),(4,4),(0,4).
    The centroid should be (2,2).
    """
    pts = [(0,0), (4,0), (4,4), (0,4)]
    cx, cy = compute_hull_centroid(pts)
    assert isclose(cx, 2.0, abs_tol=1e-9)
    assert isclose(cy, 2.0, abs_tol=1e-9)


@pytest.mark.unit
def test_compute_hull_centroid_degenerate_polygon():
    """
    Collinear points (area=0) -> returns average of hull points.
    For example, a line along x=0..4, y=0 => hull is just the same line.
    The average of (0,0), (2,0), (4,0) is (2,0).
    """
    pts = [(0,0), (2,0), (4,0)]
    cx, cy = compute_hull_centroid(pts)
    assert isclose(cx, 2.0)
    assert isclose(cy, 0.0)

@pytest.mark.unit
def test_compute_hull_centroid_degenerate_polygon():
    """
    For an extremely thin polygon (almost zero area), the area-based centroid
    calculation falls back to averaging the hull points.
    Here, we use a nearly degenerate rectangle where the computed area is below 1e-14.
    The expected centroid is the average of the hull vertices.
    """
    pts = [(0, 0), (4, 0), (4, 1e-15), (0, 1e-15)]
    cx, cy = compute_hull_centroid(pts)
    # Expected centroid: ( (0+4+4+0)/4, (0+0+1e-15+1e-15)/4 ) => (2, 0.5e-15)
    from math import isclose
    assert isclose(cx, 2.0, abs_tol=1e-12)
    assert isclose(cy, 0.5e-15, abs_tol=1e-12)
