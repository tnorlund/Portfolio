# infra/lambda_layer/python/test/integration/test__geometry.py
from math import isclose, pi, sqrt

import pytest

from receipt_dynamo.data._geometry import (box_points,
    compute_hull_centroid,
    compute_receipt_box_from_skewed_extents,
    convex_hull,
    find_hull_extents_relative_to_centroid,
    find_perspective_coeffs,
    invert_affine,
    invert_warp,
    min_area_rect,
    pad_corners_opposite,
    solve_8x8_system, )


def multiply_perspective(m1, m2):
    """
    Multiplies two 8-coefficient perspective transforms:
    m1, m2 => [a, b, c, d, e, f, g, h]
    Interpreted as 3x3:
       [a b c]
       [d e f]
       [g h 1]
    Returns the 8-coefficient result of m1*m2 as a list [a2, b2, c2, d2, e2, f2, g2, h2].
    """
    # Convert to 3x3
    M1 = [[m1[0], m1[1], m1[2]],
        [m1[3], m1[4], m1[5]],
        [m1[6], m1[7], 1.0], ]
    M2 = [[m2[0], m2[1], m2[2]],
        [m2[3], m2[4], m2[5]],
        [m2[6], m2[7], 1.0], ]
    # Multiply
    M = [[0.0] * 3 for _ in range(3)]
    for r in range(3):
        for c in range(3):
            M[r][c] = sum(M1[r][k] * M2[k][c] for k in range(3))
    # Convert back to 8-list
    return [M[0][0],
        M[0][1],
        M[0][2],
        M[1][0],
        M[1][1],
        M[1][2],
        M[2][0],
        M[2][1], ]


@pytest.mark.unit
def test_invert_warp_identity():
    """
    Inverting the identity warp => identity again.
    Identity warp => x' = x, y' = y, so [a, b, c, d, e, f, g, h] = [1, 0, 0, 0, 1, 0, 0, 0].
    """
    # Identity perspective warp
    identity = [1, 0, 0, 0, 1, 0, 0, 0]
    inv = invert_warp(*identity)  # unpack => (1, 0, 0, 0, 1, 0, 0, 0)
    # Should still be the identity warp
    assert (inv == identity), f"Inverse of identity should be identity, got {inv}"


@pytest.mark.unit
def test_invert_warp_det_zero():
    """
    Pass a warp that leads to determinant=0 => expect ValueError.
    For instance, let g=0, h=0 but a, e=0 => or any degenerate 3x3 matrix.
    We'll just pick a=0,e=0 => guaranteed zero determinant.
    """
    # We want the 3x3:
    # [0 b c]
    # [d 0 f]
    # [g h 1]
    # This is likely degenerate if everything else is 0 or trivial
    # For a quick guaranteed zero det, do this:
    # i.e. entire matrix is 0 except last element => [g,h,1]? We'll keep g,h=0
    # => last row => [0,0,1].
    degenerate = [0,
        0,
        0,
        0,
        0,
        0,
        0,
        0, ]
    # So effectively [0,0,0],[0,0,0],[0,0,1] => determinant=0
    with pytest.raises(ValueError, match="Cannot invert perspective matrix"):
        invert_warp(*degenerate)


@pytest.mark.unit
def test_invert_warp_known_good_matrices():
    """
    Tests invert_warp on a few well-chosen matrices that are definitely invertible
    and not too ill-conditioned.
    """
    known_transforms = [# identity
        [1, 0, 0, 0, 1, 0, 0, 0],
        # mild shear/perspective
        [1, 0.2, 3, 0.1, 1, -2, 0.001, 0.002],
        # moderate but invertible
        [0.8, -0.3, 1.5, 0.2, 1.2, -1.0, -0.1, 0.1],
        # another stable example
        [0.9, -0.2, 0.3, 0.4, 1.1, 2.0, 0.05, -0.02], ]
    for mat in known_transforms:
        inv = invert_warp(*mat)
        prod = multiply_perspective(mat, inv)
        identity = [1, 0, 0, 0, 1, 0, 0, 0]
        for i in range(8):
            assert isclose(prod[i], identity[i], abs_tol=0.5), f"Matrix {mat}: product index {i} => {prod[i]} vs {identity[i]}"


@pytest.mark.unit
def test_pad_corners_opposite_square_positive():
    """
    Test a simple square. corners = (0, 0), (10, 0), (10, 10), (0, 10).
    pad=10 => each corner moves ~7.07 px outward along the diagonal away from its opposite corner.
    """
    square = [(0, 0), (10, 0), (10, 10), (0, 10)]
    padded = pad_corners_opposite(square, 10.0)
    # We'll check just one corner for the approximate shift.
    # e.g. top-left corner (0,0) is opposite bottom-right (10,10).
    # vector = (0-10, 0-10) = (-10, -10), distance=~14.142
    # unit=(-0.707..., -0.707...), shift=10 => ~(-7.07, -7.07)
    # new corner => (0 + -7.07, 0 + -7.07) => (~-7.07, ~-7.07)
    # We'll do an approximate check:
    tl = padded[0]
    assert abs(tl[0] + 7.07) < 0.2  # within ~0.2
    assert abs(tl[1] + 7.07) < 0.2

    # Similarly, check bottom-right:
    br = padded[2]
    # Opposite corner is top-left => the vector is (10,10)-(0,0)=(10,10)
    # distance=14.142..., shift=10 => ~(+7.07, +7.07)
    # new corner => (17.07, 17.07)
    assert br[0] > 16.8 and br[0] < 17.3
    assert br[1] > 16.8 and br[1] < 17.3


@pytest.mark.unit
def test_solve_8x8_system_singular_pivot():
    """
    Force A[i][i] to be zero during pivot, ensuring we hit the
    'Matrix is singular or poorly conditioned' ValueError.
    """
    # Make an 8x8 matrix full of zeros, so pivot_val = 0 on the first row
    A = [[0.0] * 8 for _ in range(8)]
    b = [0.0] * 8
    with pytest.raises(ValueError, match="singular or poorly conditioned"):
        solve_8x8_system(A, b)


@pytest.mark.unit
def test_pad_corners_opposite_square_negative():
    """
    Test pad < 0 => corners move inward toward the opposite corner.
    """
    square = [(0, 0), (10, 0), (10, 10), (0, 10)]
    padded = pad_corners_opposite(square, -5)
    # Top-left corner => (0,0) moves ~3.535 right & down => (~3.535, ~3.535)
    tl = padded[0]
    assert abs(tl[0] - 3.535) < 0.2
    assert abs(tl[1] - 3.535) < 0.2

    # Similarly, check bottom-right => original (10,10)
    # Opposite corner is (0,0) => dx=10,dy=10 => unit=(0.7071,0.7071)
    # pad=-5 => shift= -5*(0.7071,0.7071) => (-3.5355, -3.5355)
    # new => (10-3.5355, 10-3.5355) => (6.4645, 6.4645)
    br = padded[2]
    assert 6.26 < br[0] < 6.66
    assert 6.26 < br[1] < 6.66


@pytest.mark.unit
def test_pad_corners_opposite_identical_corners():
    """
    If two opposite corners are the same point, no shift occurs (distance=0).
    """
    corners = [(0, 0), (10, 10), (0, 0), (10, 10)]
    result = pad_corners_opposite(corners, 10)
    assert result[0] == (0, 0)  # no shift
    assert result[2] == (0, 0)


@pytest.mark.unit
def test_find_perspective_coeffs_basic_scaling():
    """
    Basic test: src is 100x50 rectangle, dst is 200x100 rectangle => 2x scaling.
    We'll verify that the midpoint also transforms correctly under the derived matrix.
    """
    src_points = [(0, 0), (100, 0), (100, 50), (0, 50)]
    dst_points = [(0, 0), (200, 0), (200, 100), (0, 100)]
    coeffs = find_perspective_coeffs(src_points, dst_points)
    # Coeffs define x_src = f(x_dst,y_dst), y_src = f(x_dst,y_dst) in
    # projective form.

    # Let's check a corner in the "destination space" -> "source space"
    # For instance, (x_dst, y_dst) = (200, 100) => should map to (100, 50).
    # x_src = (a*200 + b*100 + c) / (1 + g*200 + h*100)
    # y_src = (d*200 + e*100 + f) / (1 + g*200 + h*100)
    def transform_pt(coeffs, xd, yd):
        a, b, c, d, e, f, g, h = coeffs
        denom = 1 + g * xd + h * yd
        x_src = (a * xd + b * yd + c) / denom
        y_src = (d * xd + e * yd + f) / denom
        return (x_src, y_src)

    # Check bottom-right corner in dst => (200,100)
    br_src = transform_pt(coeffs, 200, 100)
    assert abs(br_src[0] - 100) < 1e-7
    assert abs(br_src[1] - 50) < 1e-7

    # Check the midpoint in dst => (100,50) => should map to (50,25) in src
    mid_src = transform_pt(coeffs, 100, 50)
    assert abs(mid_src[0] - 50) < 1e-7
    assert abs(mid_src[1] - 25) < 1e-7


@pytest.mark.unit
def test_find_perspective_coeffs_singular():
    """
    If src_points == dst_points, effectively no transformation is needed,
    but also if we pass something truly degenerate, we might get a ValueError or an
    invalid matrix. This checks we can handle a 'same points' scenario (should produce
    an identity-like transform).
    """
    src_points = [(0, 0), (1, 0), (1, 1), (0, 1)]
    dst_points = [(0, 0), (1, 0), (1, 1), (0, 1)]  # identical
    coeffs = find_perspective_coeffs(src_points, dst_points)

    # The transform is basically identity => check that the corners map exactly
    # or close to themselves.
    def transform_pt(coeffs, xd, yd):
        a, b, c, d, e, f, g, h = coeffs
        denom = 1 + g * xd + h * yd
        x_src = (a * xd + b * yd + c) / denom
        y_src = (d * xd + e * yd + f) / denom
        return (x_src, y_src)

    # (1,1) => hopefully (1,1)
    test_pt = transform_pt(coeffs, 1, 1)
    assert abs(test_pt[0] - 1) < 1e-10
    assert abs(test_pt[1] - 1) < 1e-10


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


# Additional test for duplicate points in min_area_rect to hit the
# len(hull) < 3 branch.
@pytest.mark.unit
def test_min_area_rect_duplicate_points():
    # When duplicate points are provided, the convex_hull function collapses them
    # to a single unique point, triggering the degenerate branch in
    # min_area_rect.
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
    # We'll place these hull points below (0,0) so that after deskew they
    # remain y < 0
    hull_pts = [(0, -1), (2, -1), (2, -3), (0, -3)]
    # No rotation, center=(0,0).
    result = compute_receipt_box_from_skewed_extents(hull_pts, 0, 0, 0)
    # The bounding box corners in integer coords:
    # top is the minimum y => -3
    # bottom is the maximum y => -1
    # x spans [0, 2].
    # So corners => (0,-3), (2, -3), (2, -1), (0, -1)
    # The function's return format is [top-left, top-right, bottom-right,
    # bottom-left].
    assert result == [[0, -3], [2, -3], [2, -1], [0, -1]]


@pytest.mark.unit
def test_compute_receipt_box_from_skewed_extents_degrees():
    """
    Basic test with a rotation in degrees. We'll pick a simple rectangle,
    rotate by 90 degrees around center=(0, 0), then confirm corners.
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
    #   top-left => (-1, 0)
    #   top-right => (-1, 2)
    #   bottom-right => (0, 2)
    #   bottom-left => (0, 0)
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
    result = compute_receipt_box_from_skewed_extents(hull_pts, 1, 0, pi / 2, use_radians=True)
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
    A simple square from (0, 0) to (10, 10). Centroid is (5, 5).
    With no rotation, 'left' => (0, 5), 'right' => (10, 5),
    'top' => (5, 10), 'bottom' => (5, 0).
    """
    hull_pts = [(0, 0), (10, 0), (10, 10), (0, 10)]
    cx, cy = 5, 5
    results = find_hull_extents_relative_to_centroid(hull_pts, cx, cy, rotation_deg=0)
    assert results["left"] == (0, 5)
    assert results["right"] == (10, 5)
    assert results["top"] == (5, 0)
    assert results["bottom"] == (5, 10)


@pytest.mark.unit
def test_find_hull_extents_relative_to_centroid_square_rotation_degrees():
    """
    Same square, but rotated by 45 degrees. We'll just check approximate intersection
    points. The centroid is still (5, 5).
    """
    hull_pts = [(0, 0), (10, 0), (10, 10), (0, 10)]
    cx, cy = 5, 5
    results = find_hull_extents_relative_to_centroid(hull_pts, cx, cy, rotation_deg=45)
    # We expect each intersection to be roughly sqrt(50) ~ 7.071 units away
    # from the center if it's a perfect diagonal. We'll check approximate
    # locations.
    left_pt = results["left"]
    right_pt = results["right"]
    top_pt = results["top"]
    bottom_pt = results["bottom"]
    assert left_pt is not None
    assert right_pt is not None
    assert top_pt is not None
    assert bottom_pt is not None

    # For a 45-degree rotation, "left" is direction -u,
    # which should land near the midpoint of the left edge.
    # We'll just check that we got integer points and they're near the square
    # boundary.
    for pt in (left_pt, right_pt, top_pt, bottom_pt):
        # Each coordinate should be in [0..10].
        assert 0 <= pt[0] <= 10
        assert 0 <= pt[1] <= 10


@pytest.mark.unit
def test_find_hull_extents_relative_to_centroid_square_rotation_radians():
    """
    Same square, rotate by pi/4 (45 deg) but use_radians=True.
    """
    hull_pts = [(0, 0), (10, 0), (10, 10), (0, 10)]
    cx, cy = 5, 5
    results = find_hull_extents_relative_to_centroid(hull_pts, cx, cy, rotation_deg=pi / 4, use_radians=True)
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
    For a hull defined by a single horizontal edge from (0, 0) to (10, 0) with centroid at (5, 0):
      - Horizontal rays ('left' and 'right') are collinear with the edge and yield no intersection (None).
      - Vertical rays ('top' and 'bottom') intersect at the centroid, yielding (5, 0).
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
    The centroid should be (2, 2).
    """
    pts = [(0, 0), (4, 0), (4, 4), (0, 4)]
    cx, cy = compute_hull_centroid(pts)
    assert isclose(cx, 2.0, abs_tol=1e-9)
    assert isclose(cy, 2.0, abs_tol=1e-9)


@pytest.mark.unit
def test_compute_hull_centroid_degenerate_polygon():
    """
    Collinear points (area=0) -> returns average of hull points.
    For example, a line along x=0..4, y=0 => hull is just the same line.
    The average of (0, 0), (2, 0), (4, 0) is (2, 0).
    """
    pts = [(0, 0), (2, 0), (4, 0)]
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
    # Expected centroid: ((0+4+4+0)/4, (0+0+1e-15+1e-15)/4) => (2, 0.5e-15)
    from math import isclose

    assert isclose(cx, 2.0, abs_tol=1e-12)
    assert isclose(cy, 0.5e-15, abs_tol=1e-12)
