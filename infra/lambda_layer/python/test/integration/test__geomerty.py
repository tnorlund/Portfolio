import pytest
from math import sqrt, pi
from dynamo.data._geometry import (
    invert_affine,
    convex_hull,
    min_area_rect,
    box_points
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
    