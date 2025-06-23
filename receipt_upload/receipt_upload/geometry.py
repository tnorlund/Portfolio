# flake8: noqa
from math import atan2, cos, degrees, hypot, radians, sin, sqrt
from typing import Dict, List, Optional, Tuple


def invert_affine(a, b, c, d, e, f):
    """
    Inverts the 2x3 affine transform:

        [a  b  c]
        [d  e  f]
        [0  0  1]

    Returns the 6-tuple (a_inv, b_inv, c_inv, d_inv, e_inv, f_inv)
    for the inverse transform, provided the determinant is not zero.
    """
    det = a * e - b * d
    if abs(det) < 1e-14:
        raise ValueError("Singular transform cannot be inverted.")
    a_inv = e / det
    b_inv = -b / det
    c_inv = (b * f - c * e) / det
    d_inv = -d / det
    e_inv = a / det
    f_inv = (c * d - a * f) / det
    return (a_inv, b_inv, c_inv, d_inv, e_inv, f_inv)


def invert_warp(a, b, c, d, e, f, g, h):
    """
    Given the 8 perspective coefficients (a, b, c, d, e, f, g, h) for the mapping
      x_new = (a*x + b*y + c) / (1 + g*x + h*y)
      y_new = (d*x + e*y + f) / (1 + g*x + h*y)
    returns a new list of 8 coefficients [a2, b2, c2, d2, e2, f2, g2, h2]
    that perform the inverse mapping (x_new, y_new) -> (x, y).
    """
    # Form the 3x3 matrix
    M = [
        [a, b, c],
        [d, e, f],
        [g, h, 1],
    ]
    # Invert it
    M_inv = _invert_3x3(M)
    # Extract the top-left 8 elements
    # M_inv = [[A, B, C],
    #          [D, E, F],
    #          [G, H, I]]
    A = M_inv[0][0]
    B = M_inv[0][1]
    C = M_inv[0][2]
    D = M_inv[1][0]
    E = M_inv[1][1]
    F = M_inv[1][2]
    G = M_inv[2][0]
    H = M_inv[2][1]
    # The last element M_inv[2][2] would be 1 if not degenerate
    return [A, B, C, D, E, F, G, H]


def _invert_3x3(M):
    """Inverts a 3x3 matrix M using standard formula (or your own method)."""
    determinant = (
        M[0][0] * (M[1][1] * M[2][2] - M[1][2] * M[2][1])
        - M[0][1] * (M[1][0] * M[2][2] - M[1][2] * M[2][0])
        + M[0][2] * (M[1][0] * M[2][1] - M[1][1] * M[2][0])
    )
    if abs(determinant) < 1e-12:
        raise ValueError("Cannot invert perspective matrix (det=0).")

    inverse_determinant = 1.0 / determinant
    # Adjugate / cofactor method
    return [
        [
            inverse_determinant * ((M[1][1] * M[2][2] - M[1][2] * M[2][1])),
            inverse_determinant * (-(M[0][1] * M[2][2] - M[0][2] * M[2][1])),
            inverse_determinant * ((M[0][1] * M[1][2] - M[0][2] * M[1][1])),
        ],
        [
            inverse_determinant * (-(M[1][0] * M[2][2] - M[1][2] * M[2][0])),
            inverse_determinant * ((M[0][0] * M[2][2] - M[0][2] * M[2][0])),
            inverse_determinant * (-(M[0][0] * M[1][2] - M[0][2] * M[1][0])),
        ],
        [
            inverse_determinant * ((M[1][0] * M[2][1] - M[1][1] * M[2][0])),
            inverse_determinant * (-(M[0][0] * M[2][1] - M[0][1] * M[2][0])),
            inverse_determinant * ((M[0][0] * M[1][1] - M[0][1] * M[1][0])),
        ],
    ]


def pad_corners_opposite(corners, pad):
    """
    Moves each corner 'pad' pixels away from its opposite corner.

    corners: list of 4 (x, y) in consistent order, e.g.:
        [top-left, top-right, bottom-right, bottom-left]
    pad: positive means each corner moves outward
         (further from the opposite corner)
    """
    new_corners = []
    for i in range(4):
        x_i, y_i = corners[i]
        # Opposite corner is (i + 2) % 4
        x_opp, y_opp = corners[(i + 2) % 4]

        dx = x_i - x_opp
        dy = y_i - y_opp
        dist = hypot(dx, dy)
        if dist == 0:
            # corners coincide, no shift
            new_corners.append((x_i, y_i))
        else:
            nx = dx / dist  # unit vector x
            ny = dy / dist  # unit vector y
            new_x = x_i + pad * nx
            new_y = y_i + pad * ny
            new_corners.append((new_x, new_y))
    return new_corners


def solve_8x8_system(A, b):
    """
    Solve an 8x8 system A * x = b for x, where:
      - A is a list of lists (8 rows, each row has 8 floats).
      - b is a list of length 8.
    Returns x as a list of length 8.
    Uses Gaussian elimination with partial pivoting.
    """
    n = 8

    # Forward elimination
    for i in range(n):
        # 1) Find pivot row (partial pivot)
        pivot = i
        for r in range(i + 1, n):
            if abs(A[r][i]) > abs(A[pivot][i]):
                pivot = r
        # 2) Swap pivot row into position
        if pivot != i:
            A[i], A[pivot] = A[pivot], A[i]
            b[i], b[pivot] = b[pivot], b[i]

        # 3) Normalize pivot row (so A[i][i] = 1)
        pivot_val = A[i][i]
        if abs(pivot_val) < 1e-12:
            raise ValueError(
                "Matrix is singular or poorly conditioned for pivoting."
            )
        inv_pivot = 1.0 / pivot_val
        A[i] = [val * inv_pivot for val in A[i]]
        b[i] = b[i] * inv_pivot

        # 4) Eliminate below pivot
        for r in range(i + 1, n):
            factor = A[r][i]
            A[r] = [A[r][c] - factor * A[i][c] for c in range(n)]
            b[r] = b[r] - factor * b[i]

    # Back-substitution
    for i in reversed(range(n)):
        # b[i] is the value after subtracting known terms from the row
        for j in range(i + 1, n):
            b[i] -= A[i][j] * b[j]
        # A[i][i] should be 1.0 here from the normalization step
    return b


def find_perspective_coeffs(
    src_points: List[Tuple[float, float]],
    dst_points: List[Tuple[float, float]],
) -> List[float]:
    """
    src_points: list of 4 (x, y) source corners
    dst_points: list of 4 (x, y) destination corners

    Returns a list of 8 coefficients [a, b, c, d, e, f, g, h] for PIL's
    Image.transform(..., PERSPECTIVE, coeffs).

    The transform maps (x_dst, y_dst) back to (x_src, y_src) as:
        x_src = a*x_dst + b*y_dst + c
        y_src = d*x_dst + e*y_dst + f
    normalized by (1 + g*x_dst + h*y_dst).
    """
    # Validate inputs
    if len(src_points) != 4 or len(dst_points) != 4:
        raise ValueError(
            "find_perspective_coeffs requires exactly 4 source and 4 destination points"
        )

    # Check for degenerate cases: collinear points
    def are_collinear(p1, p2, p3):
        """Check if three points are collinear using cross product"""
        return (
            abs(
                (p2[0] - p1[0]) * (p3[1] - p1[1])
                - (p3[0] - p1[0]) * (p2[1] - p1[1])
            )
            < 1e-10
        )

    # Check if any 3 points in src_points are collinear
    for i in range(4):
        for j in range(i + 1, 4):
            for k in range(j + 1, 4):
                if are_collinear(src_points[i], src_points[j], src_points[k]):
                    raise ValueError(
                        f"Source points {i}, {j}, {k} are collinear - cannot compute perspective transform"
                    )

    # Check if any 3 points in dst_points are collinear
    for i in range(4):
        for j in range(i + 1, 4):
            for k in range(j + 1, 4):
                if are_collinear(dst_points[i], dst_points[j], dst_points[k]):
                    raise ValueError(
                        f"Destination points {i}, {j}, {k} are collinear - cannot compute perspective transform"
                    )

    # Check for duplicate points
    for i in range(4):
        for j in range(i + 1, 4):
            if (
                abs(src_points[i][0] - src_points[j][0]) < 1e-10
                and abs(src_points[i][1] - src_points[j][1]) < 1e-10
            ):
                raise ValueError(f"Source points {i} and {j} are identical")
            if (
                abs(dst_points[i][0] - dst_points[j][0]) < 1e-10
                and abs(dst_points[i][1] - dst_points[j][1]) < 1e-10
            ):
                raise ValueError(
                    f"Destination points {i} and {j} are identical"
                )

    # Each source→destination pair gives 2 linear equations:
    #   sx = a*dx + b*dy + c - g*dx*sx - h*dy*sx  (written in standard form)
    #   sy = d*dx + e*dy + f - g*dx*sy - h*dy*sy
    #
    # The matrix A is 8x8, vector b is length 8.
    A = []
    B = []
    for (sx, sy), (dx, dy) in zip(src_points, dst_points):
        A.append([dx, dy, 1, 0, 0, 0, -sx * dx, -sx * dy])
        B.append(sx)
        A.append([0, 0, 0, dx, dy, 1, -sy * dx, -sy * dy])
        B.append(sy)

    # Solve the system for [a, b, c, d, e, f, g, h]
    # Make a *copy* of A if you don't want to mutate the original:
    A_copy = [row[:] for row in A]
    B_copy = B[:]

    try:
        solution = solve_8x8_system(A_copy, B_copy)
        return solution
    except ValueError as e:
        raise ValueError(
            f"Failed to compute perspective transform: {e}. Source points may be too close to collinear."
        )


def compute_receipt_box_from_skewed_extents(
    hull_pts: List[Tuple[float, float]],
    cx: float,
    cy: float,
    rotation_deg: float,
    use_radians: bool = False,
) -> Optional[List[List[int]]]:
    """
    Compute a perspective-correct quadrilateral ("receipt box") from a set of convex hull points.

    This function first deskews the input points by translating them so that (cx, cy) becomes the origin
    and then rotating them by -rotation_deg (or the equivalent in radians if use_radians is True). In the
    deskewed coordinate system, the points are split into "top" (y < 0) and "bottom" (y >= 0) groups. If
    either group is empty, all deskewed points are used. The function then determines the extreme left and
    right vertices in both the top and bottom groups, and computes the overall vertical boundaries.

    Using linear interpolation along the segments connecting the extreme vertices, four boundary points
    are determined at the top and bottom y-values. These points form a quadrilateral in the deskewed space.
    Finally, the quadrilateral is transformed back to the original coordinate system by applying the inverse
    rotation and translation, and the resulting coordinates are rounded to the nearest integers.

    Parameters:
        hull_pts (List[Tuple[float, float]]): A list of (x, y) coordinates representing the convex hull.
        cx (float): X-coordinate of the translation center (deskew origin).
        cy (float): Y-coordinate of the translation center (deskew origin).
        rotation_deg (float): The skew angle. Interpreted as degrees by default (or as radians if use_radians is True).
        use_radians (bool, optional): If True, rotation_deg is treated as radians. Defaults to False.

    Returns:
        Optional[List[List[int]]]: A list of four [x, y] integer pairs representing the corners of the quadrilateral,
        ordered as [top-left, top-right, bottom-right, bottom-left]. Returns None if hull_pts is empty.
    """
    # If the user specifies use_radians=True, interpret rotation_deg as radians;
    # otherwise, treat rotation_deg as degrees and convert to radians.
    if use_radians:
        theta = rotation_deg
    else:
        theta = radians(rotation_deg)

    # ---------------------------------------------------
    # 1) Translate hull points to center & apply deskew
    #    The "deskew" rotation is -rotation_deg, so we
    #    rotate by +theta in the transform (since we
    #    typically do: deskew_pts = pts * R(+theta)).
    # ---------------------------------------------------
    # Deskew rotation matrix (2x2). We'll apply it manually:
    #   [cosθ   sinθ]
    #   [-sinθ   cosθ]
    #
    # This is effectively rotating by +theta because we want
    # to remove (subtract) rotation_deg from the points.
    def apply_deskew(x, y, cx, cy, cos_t, sin_t):
        # Translate: (x - cx, y - cy)
        dx = x - cx
        dy = y - cy
        # Multiply by rotation matrix:
        # new_x = dx*cosθ + dy*sinθ
        # new_y = -dx*sinθ + dy*cosθ
        return (dx * cos_t + dy * sin_t, -dx * sin_t + dy * cos_t)

    cos_t = cos(theta)
    sin_t = sin(theta)

    pts_deskew = []
    for x, y in hull_pts:
        px, py = apply_deskew(x, y, cx, cy, cos_t, sin_t)
        pts_deskew.append((px, py))

    if len(pts_deskew) == 0:
        return None  # No hull points

    # ---------------------------------------------------
    # 2) Split into top (y<0) and bottom (y>=0) halves
    # ---------------------------------------------------
    top_half = [(x, y) for (x, y) in pts_deskew if y < 0]
    bottom_half = [(x, y) for (x, y) in pts_deskew if y >= 0]
    if len(top_half) == 0:
        top_half = pts_deskew[:]  # all in bottom or empty
    if len(bottom_half) == 0:
        bottom_half = pts_deskew[:]  # all in top or empty

    # ---------------------------------------------------
    # 3) Find extreme X vertices in top/bottom
    # ---------------------------------------------------
    def min_x_vertex(points):
        return min(points, key=lambda p: p[0])

    def max_x_vertex(points):
        return max(points, key=lambda p: p[0])

    left_top_vertex = min_x_vertex(top_half)
    right_top_vertex = max_x_vertex(top_half)
    left_bottom_vertex = min_x_vertex(bottom_half)
    right_bottom_vertex = max_x_vertex(bottom_half)

    # ---------------------------------------------------
    # 4) Overall vertical boundaries in deskewed space
    # ---------------------------------------------------
    all_y = [py for (_, py) in pts_deskew]
    top_y = min(all_y)
    bottom_y = max(all_y)

    # ---------------------------------------------------
    # 5) Interpolate boundary points at top_y and bottom_y
    # ---------------------------------------------------
    def interpolate_vertex(v_top, v_bottom, desired_y):
        """
        Linear interpolation along the segment [v_top, v_bottom] in deskewed space.
        v_top, v_bottom: (x, y) tuples
        desired_y: The y-value to interpolate at
        returns: (x, desired_y)
        """
        (x1, y1) = v_top
        (x2, y2) = v_bottom
        dy = y2 - y1
        if abs(dy) < 1e-9:
            return (x1, desired_y)  # degenerate, nearly horizontal
        t = (desired_y - y1) / dy
        x_int = x1 + t * (x2 - x1)
        return (x_int, desired_y)

    left_top_point = interpolate_vertex(
        left_top_vertex, left_bottom_vertex, top_y
    )
    left_bottom_point = interpolate_vertex(
        left_top_vertex, left_bottom_vertex, bottom_y
    )
    right_top_point = interpolate_vertex(
        right_top_vertex, right_bottom_vertex, top_y
    )
    right_bottom_point = interpolate_vertex(
        right_top_vertex, right_bottom_vertex, bottom_y
    )

    # Quadrilateral in the deskewed space
    deskewed_corners = [
        left_top_point,
        right_top_point,
        right_bottom_point,
        left_bottom_point,
    ]

    # ---------------------------------------------------
    # 6) Rotate them back by the inverse transform
    #    (If deskew was R_deskew, we now apply R_inv)
    # ---------------------------------------------------
    # For the inverse rotation: rotate by -theta.
    cos_t_inv = cos(-theta)  # same as cos(theta)
    sin_t_inv = sin(-theta)  # same as -sin(theta)

    def apply_inverse_transform(x, y, cx, cy, cos_t, sin_t):
        # new_x = x*cos(-θ) + y*sin(-θ)
        # new_y = -x*sin(-θ) + y*cos(-θ)
        dx = x
        dy = y
        rx = dx * cos_t + dy * sin_t
        ry = -dx * sin_t + dy * cos_t
        return (rx + cx, ry + cy)

    original_corners = []
    for dx, dy in deskewed_corners:
        ox, oy = apply_inverse_transform(dx, dy, cx, cy, cos_t_inv, sin_t_inv)
        original_corners.append((ox, oy))

    # ---------------------------------------------------
    # 7) Round the coordinates to integers and return
    # ---------------------------------------------------
    result = [[int(round(x)), int(round(y))] for (x, y) in original_corners]
    return result


def find_hull_extents_relative_to_centroid(
    hull_pts: List[Tuple[float, float]],
    cx: float,
    cy: float,
    rotation_deg: float = 0.0,
    use_radians: bool = False,
) -> Dict[str, Optional[Tuple[int, int]]]:
    """
    Compute the intersection points between a convex hull and four rays emanating from a centroid,
    in a rotated coordinate system.

    The function defines a rotated coordinate system by rotating the standard axes by `rotation_deg`
    (interpreted as degrees by default; if `use_radians` is True, the value is treated as radians and
    converted to degrees). In this rotated system:
      - The positive X-axis is defined by the unit vector u = (cos(theta), sin(theta)).
      - The positive Y-axis is defined by the unit vector v = (-sin(theta), cos(theta)).
    The rays originate at (cx, cy) and extend in the following directions:
      - "left":   opposite to u,
      - "right":  along u,
      - "top":    opposite to v,
      - "bottom": along v.

    For each of these directions, the function finds the intersection point of the ray with the convex hull
    (provided as `hull_pts`, typically in counter-clockwise order) using the helper function
    `_intersection_point_for_direction`. If an intersection is found, its coordinates are rounded to the nearest
    integer; otherwise, the corresponding value is set to None.

    Parameters:
        hull_pts (List[Tuple[float, float]]): A list of (x, y) vertices defining the convex hull.
        cx (float): The x-coordinate of the centroid (ray origin).
        cy (float): The y-coordinate of the centroid (ray origin).
        rotation_deg (float, optional): The angle to rotate the coordinate system. Defaults to 0.0.
            If `use_radians` is False, this is interpreted in degrees.
        use_radians (bool, optional): If True, `rotation_deg` is interpreted as radians. Defaults to False.

    Returns:
        Dict[str, Optional[Tuple[int, int]]]: A dictionary with keys "left", "right", "top", and "bottom".
        Each key maps to an (x, y) tuple of integers representing the intersection point of the corresponding
        ray with the convex hull, or None if no valid intersection is found.
    """
    # If the caller says the angle is in radians, convert it to degrees first
    if use_radians:
        rotation_deg = degrees(rotation_deg)

    # Convert degrees -> radians for internal trigonometric usage
    theta = radians(rotation_deg)

    # Rotated X-axis unit vector
    u = (cos(theta), sin(theta))
    # Rotated Y-axis unit vector
    v = (-sin(theta), cos(theta))

    # Directions for intersection: left, right, top, bottom
    # "left" = negative X direction in the rotated system => -u
    # "right" = +u
    # "top" = -v
    # "bottom" = +v
    directions = {
        "left": (-u[0], -u[1]),
        "right": u,
        "top": (-v[0], -v[1]),
        "bottom": v,
    }

    results: Dict[str, Optional[Tuple[int, int]]] = {}
    for key, direction_vector in directions.items():
        pt = _intersection_point_for_direction(
            hull_pts, cx, cy, direction_vector
        )
        if pt is not None:
            x_int = int(round(pt[0]))
            y_int = int(round(pt[1]))
            results[key] = (x_int, y_int)
        else:
            results[key] = None

    return results


def _intersection_point_for_direction(
    hull_pts: List[Tuple[float, float]],
    cx: float,
    cy: float,
    direction: Tuple[float, float],
) -> Optional[Tuple[float, float]]:
    """
    Compute the intersection point between a ray and the edges of a convex polygon.

    The ray originates at (cx, cy) and extends in the specified direction vector.
    The polygon is defined by its vertices in `hull_pts` (order may be clockwise or
    counter-clockwise, though typically CCW). For each edge of the polygon, the function
    computes the intersection with the ray, parameterized as:

        intersection = (cx, cy) + t * direction

    where t >= 0. It only considers intersections that occur on the edge segment
    (i.e. where the edge parameter s is between 0 and 1). If multiple valid intersections
    are found, the one corresponding to the smallest nonnegative t is returned.

    Parameters:
        hull_pts (List[Tuple[float, float]]): List of (x, y) vertices defining the convex polygon.
        cx (float): X-coordinate of the ray's origin.
        cy (float): Y-coordinate of the ray's origin.
        direction (Tuple[float, float]): Direction vector (dx, dy) of the ray.

    Returns:
        Optional[Tuple[float, float]]: The intersection point (x, y) with the smallest
        nonnegative parameter t, or None if no valid intersection exists.
    """
    # Ray origin and direction
    r = (cx, cy)
    d = direction

    best_t = float("inf")
    best_point = None
    n = len(hull_pts)

    for i in range(n):
        # Current edge from hull_pts[i] to hull_pts[(i+1) % n]
        p = hull_pts[i]
        q = hull_pts[(i + 1) % n]
        # Edge vector
        e = (q[0] - p[0], q[1] - p[1])

        # Cross product for the denominator
        denom = d[0] * e[1] - d[1] * e[0]
        if abs(denom) < 1e-9:
            # Nearly parallel or zero-length edge
            continue

        # rp = p - r
        rp = (p[0] - r[0], p[1] - r[1])

        # Cross products for t and s
        cross_rp_e = rp[0] * e[1] - rp[1] * e[0]
        cross_rp_d = rp[0] * d[1] - rp[1] * d[0]

        # Param along ray = t, param along edge = s
        t = cross_rp_e / denom
        s = cross_rp_d / denom

        # We want intersection where t >= 0 (ray is forward) and s in [0..1]
        # (on segment)
        if t >= 0 and 0 <= s <= 1:
            if t < best_t:
                best_t = t
                # Intersection point = r + t*d
                best_point = (r[0] + t * d[0], r[1] + t * d[1])

    return best_point


def compute_hull_centroid(
    hull_vertices: List[Tuple[float, float]],
) -> Tuple[float, float]:
    """
    Compute the centroid (geometric center) of a polygon defined by its convex hull vertices.

    The convex hull is assumed to be provided as a list of points in counter-clockwise (CCW)
    order. The centroid is calculated as follows, based on the number of vertices:

      - If the hull is empty, returns (0.0, 0.0).
      - If the hull contains a single point, returns that point.
      - If the hull consists of two points, returns the midpoint of the segment connecting them.
      - If the hull contains three or more points, computes the area-based centroid using the
        standard shoelace formula:

          * Compute the cross product for each edge:
                cross_i = x_i * y_{i+1} - x_{i+1} * y_i
          * The polygon's area is given by:
                A = 0.5 * Σ(cross_i)
          * The centroid (C_x, C_y) is then:
                C_x = (1 / (6A)) * Σ((x_i + x_{i+1}) * cross_i)
                C_y = (1 / (6A)) * Σ((y_i + y_{i+1}) * cross_i)

    If the computed area is nearly zero (indicating a degenerate or very thin polygon), the function
    avoids numerical instability by returning the arithmetic mean of the hull vertices instead.

    Parameters:
        hull_vertices (List[Tuple[float, float]]): A list of (x, y) coordinates representing the vertices
            of the convex hull in counter-clockwise order.

    Returns:
        Tuple[float, float]: The (x, y) coordinates of the centroid.
    """
    n = len(hull_vertices)

    if n == 0:
        return (0.0, 0.0)
    elif n == 1:
        # Single point
        return (hull_vertices[0][0], hull_vertices[0][1])
    elif n == 2:
        # Midpoint of the two points
        x0, y0 = hull_vertices[0]
        x1, y1 = hull_vertices[1]
        return ((x0 + x1) / 2.0, (y0 + y1) / 2.0)
    else:
        # Compute the polygon centroid using the standard shoelace formula.
        # (hull is in CCW order by definition of 'convex_hull')
        area_sum = 0.0
        cx = 0.0
        cy = 0.0
        for i in range(n):
            x0, y0 = hull_vertices[i]
            x1, y1 = hull_vertices[(i + 1) % n]
            cross = x0 * y1 - x1 * y0
            area_sum += cross
            cx += (x0 + x1) * cross
            cy += (y0 + y1) * cross

        # Polygon area is half the cross sum. For a CCW polygon, area_sum
        # should be > 0.
        area = area_sum / 2.0

        # Centroid is (1/(6A)) * sum((x_i + x_{i+1}) * cross, (y_i + y_{i+1}) * cross)
        # (make sure area != 0 for safety)
        if abs(area) < 1e-14:
            # Very thin or degenerate polygon, gracefully handle
            # Here you might just return average of hull points if extremely
            # degenerate
            x_avg = sum(p[0] for p in hull_vertices) / n
            y_avg = sum(p[1] for p in hull_vertices) / n
            return (x_avg, y_avg)

        cx /= 6.0 * area
        cy /= 6.0 * area

        return (cx, cy)


def convex_hull(
    points: List[Tuple[float, float]],
) -> List[Tuple[float, float]]:
    """
    Compute the convex hull of a set of 2D points (in CCW order) using the
    monotone chain algorithm.
    """
    points = sorted(set(points))
    if len(points) <= 1:
        return points

    lower: List[Tuple[float, float]] = []
    for p in points:
        while (
            len(lower) >= 2
            and (
                (lower[-1][0] - lower[-2][0]) * (p[1] - lower[-2][1])
                - (lower[-1][1] - lower[-2][1]) * (p[0] - lower[-2][0])
            )
            <= 0
        ):
            lower.pop()
        lower.append(p)

    upper: List[Tuple[float, float]] = []
    for p in reversed(points):
        while (
            len(upper) >= 2
            and (
                (upper[-1][0] - upper[-2][0]) * (p[1] - upper[-2][1])
                - (upper[-1][1] - upper[-2][1]) * (p[0] - upper[-2][0])
            )
            <= 0
        ):
            upper.pop()
        upper.append(p)

    return lower[:-1] + upper[:-1]


def min_area_rect(
    points: List[Tuple[float, float]],
) -> Tuple[Tuple[float, float], Tuple[float, float], float]:
    """
    Compute the minimum-area bounding rectangle of a set of 2D points.
    Returns a tuple of:
      - center (cx, cy)
      - (width, height)
      - angle (in degrees) of rotation such that rotating back by that angle
        yields an axis-aligned rectangle.
    """
    if not points:
        return ((0, 0), (0, 0), 0)
    if len(points) == 1:
        return (points[0], (0, 0), 0)

    hull = convex_hull(points)
    if len(hull) == 2:
        # Two-point degenerate case: return a "line segment" as the minimal
        # rectangle.
        (x0, y0), (x1, y1) = hull
        center = ((x0 + x1) / 2.0, (y0 + y1) / 2.0)
        dx = x1 - x0
        dy = y1 - y0
        distance = (dx**2 + dy**2) ** 0.5
        # Here we force the result to be axis aligned.
        return (center, (distance, 0), 0.0)
    if len(hull) < 3:
        xs = [p[0] for p in hull]
        ys = [p[1] for p in hull]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        width, height = (max_x - min_x), (max_y - min_y)
        cx, cy = (min_x + width / 2.0), (min_y + height / 2.0)
        return ((cx, cy), (width, height), 0.0)

    n = len(hull)
    min_area = float("inf")
    best_rect: Tuple[Tuple[float, float], Tuple[float, float], float] = (
        (0.0, 0.0),
        (0.0, 0.0),
        0.0,
    )

    def edge_angle(p1, p2):
        return atan2(p2[1] - p1[1], p2[0] - p1[0])

    for i in range(n):
        p1 = hull[i]
        p2 = hull[(i + 1) % n]
        theta = -edge_angle(p1, p2)
        cos_t = cos(theta)
        sin_t = sin(theta)
        xs = [cos_t * px - sin_t * py for (px, py) in hull]
        ys = [sin_t * px + cos_t * py for (px, py) in hull]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        width = max_x - min_x
        height = max_y - min_y
        area = width * height
        if area < min_area:
            min_area = area
            cx_r = min_x + width / 2.0
            cy_r = min_y + height / 2.0
            cx = cos_t * cx_r + sin_t * cy_r
            cy = -sin_t * cx_r + cos_t * cy_r
            best_rect = ((cx, cy), (width, height), -degrees(theta))
    return best_rect


def box_points(
    center: Tuple[float, float], size: Tuple[float, float], angle_deg: float
) -> List[Tuple[float, float]]:
    """
    Given a rectangle defined by center, size, and rotation angle (in degrees),
    compute its 4 corner coordinates (in order).
    """
    cx, cy = center
    w, h = size
    angle = radians(angle_deg)
    cos_a = cos(angle)
    sin_a = sin(angle)
    hw = w / 2.0
    hh = h / 2.0
    # Corners in local space (before rotation).
    corners_local = [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)]
    corners_world = []
    for lx, ly in corners_local:
        rx = cos_a * lx - sin_a * ly
        ry = sin_a * lx + cos_a * ly
        corners_world.append((cx + rx, cy + ry))

    return corners_world


def theil_sen(pts: List[Tuple[float, float]]) -> Dict[str, float]:
    """Perform Theil–Sen regression to estimate a line."""
    if len(pts) < 2:
        return {"slope": 0.0, "intercept": pts[0][1] if pts else 0.0}

    slopes: List[float] = []
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            if pts[i][1] == pts[j][1]:
                continue
            slopes.append((pts[j][0] - pts[i][0]) / (pts[j][1] - pts[i][1]))

    if not slopes:
        return {"slope": 0.0, "intercept": pts[0][1]}

    slopes.sort()
    slope = slopes[len(slopes) // 2]

    intercepts = sorted(p[0] - slope * p[1] for p in pts)
    intercept = intercepts[len(intercepts) // 2]

    return {"slope": slope, "intercept": intercept}


def compute_edge(
    lines: List[object], pick: str, bins: int = 6
) -> Optional[Dict[str, Tuple[float, float]]]:
    """Estimate a straight edge from OCR line data."""

    def _pt(obj: object, key: str) -> Dict[str, float]:
        if isinstance(obj, dict):
            return obj[key]
        return getattr(obj, key)

    bin_pts: List[Optional[Tuple[float, float]]] = [None for _ in range(bins)]

    for line in lines:
        tl = _pt(line, "top_left")
        bl = _pt(line, "bottom_left")
        tr = _pt(line, "top_right")
        br = _pt(line, "bottom_right")

        y_mid = (tl["y"] + bl["y"]) / 2.0
        x = min(tl["x"], bl["x"]) if pick == "left" else max(tr["x"], br["x"])

        idx = min(bins - 1, int(y_mid * bins))
        current = bin_pts[idx]
        if current is None or (
            pick == "left"
            and x < current[0]
            or pick == "right"
            and x > current[0]
        ):
            bin_pts[idx] = (x, y_mid)

    selected = [p for p in bin_pts if p is not None]
    if len(selected) < 2:
        return None

    ts = theil_sen(selected)
    slope = ts["slope"]
    intercept = ts["intercept"]
    return {
        "top": (slope * 1 + intercept, 1.0),
        "bottom": (slope * 0 + intercept, 0.0),
    }


def find_line_edges_at_secondary_extremes(
    lines: List[object],
    hull: List[Tuple[float, float]],
    centroid: Tuple[float, float],
    avg_angle: float,
) -> Dict[str, List[Tuple[float, float]]]:
    """Locate points along text edges at extreme secondary-axis positions."""

    angle_rad = radians(avg_angle)
    secondary_axis_angle = angle_rad + radians(90)

    hull_projections = []
    for idx, point in enumerate(hull):
        rel_x = point[0] - centroid[0]
        rel_y = point[1] - centroid[1]
        projection = rel_x * cos(secondary_axis_angle) + rel_y * sin(
            secondary_axis_angle
        )
        hull_projections.append((projection, point))

    hull_projections.sort(key=lambda p: p[0], reverse=True)

    top_hull_points = [hp[1] for hp in hull_projections[:2]]
    bottom_hull_points = [hp[1] for hp in hull_projections[-2:]]

    return {"topEdge": top_hull_points, "bottomEdge": bottom_hull_points}


def _consistent_angle_from_points(
    pts: List[Tuple[float, float]],
) -> Optional[float]:
    if len(pts) < 2:
        return None

    sorted_pts = sorted(pts, key=lambda p: p[0])
    left = sorted_pts[0]
    right = sorted_pts[-1]

    dx = right[0] - left[0]
    dy = right[1] - left[1]

    angle_deg = degrees(atan2(dy, dx))
    if angle_deg < 0:
        angle_deg += 180
    if angle_deg >= 180:
        angle_deg -= 180
    if angle_deg > 90:
        angle_deg = 180 - angle_deg
    return angle_deg


def compute_final_receipt_tilt(
    lines: List[object],
    hull: List[Tuple[float, float]],
    centroid: Tuple[float, float],
    avg_angle: float,
) -> float:
    """Refine the receipt tilt angle using text line edges."""

    if not lines or len(hull) < 3:
        return avg_angle

    edges = find_line_edges_at_secondary_extremes(
        lines, hull, centroid, avg_angle
    )
    a_top = _consistent_angle_from_points(edges["topEdge"])
    a_bottom = _consistent_angle_from_points(edges["bottomEdge"])

    if a_top is None or a_bottom is None:
        return avg_angle
    return (a_top + a_bottom) / 2.0


def find_hull_extremes_along_angle(
    hull: List[Tuple[float, float]],
    centroid: Tuple[float, float],
    angle_deg: float,
) -> Dict[str, Tuple[float, float]]:
    """Find extreme hull points projected along a given angle."""

    if not hull:
        return {"leftPoint": centroid, "rightPoint": centroid}

    rad = radians(angle_deg)
    cos_a = cos(rad)
    sin_a = sin(rad)

    min_proj = float("inf")
    max_proj = float("-inf")
    left_point = hull[0]
    right_point = hull[0]

    for p in hull:
        rx = p[0] - centroid[0]
        ry = p[1] - centroid[1]
        proj = rx * cos_a + ry * sin_a
        if proj < min_proj:
            min_proj = proj
            left_point = p
        if proj > max_proj:
            max_proj = proj
            right_point = p

    return {"leftPoint": left_point, "rightPoint": right_point}


def refine_hull_extremes_with_hull_edge_alignment(
    hull: List[Tuple[float, float]],
    left_extreme: Tuple[float, float],
    right_extreme: Tuple[float, float],
    target_angle: float,
) -> Dict[str, Dict[str, Tuple[float, float]]]:
    """Refine extreme points by selecting CW/CCW neighbors."""

    if len(hull) < 3:
        return {
            "leftSegment": {
                "extreme": left_extreme,
                "optimizedNeighbor": left_extreme,
            },
            "rightSegment": {
                "extreme": right_extreme,
                "optimizedNeighbor": right_extreme,
            },
        }

    target_rad = radians(target_angle)

    def calc_score(
        extreme: Tuple[float, float],
        neighbor: Tuple[float, float],
        is_left: bool,
    ) -> float:
        try:
            extreme_index = hull.index(extreme)
        except ValueError:
            return 0.0

        dx = neighbor[0] - extreme[0]
        dy = neighbor[1] - extreme[1]
        distance = sqrt(dx * dx + dy * dy)

        if is_left:
            if dx <= 0:
                boundary_score = 1.0 + distance * 2.0
            elif dx < 0.03:
                boundary_score = 0.8
            else:
                boundary_score = 1.0 / (1 + dx * 20)
        else:
            if dx >= 0.01:
                boundary_score = 1.0 + distance
            elif dx >= -0.05:
                vertical_alignment = abs(dy) / (abs(dx) + 0.01)
                capped_vertical_bonus = min(vertical_alignment * 0.3, 0.5)
                boundary_score = 0.7 + capped_vertical_bonus
            else:
                boundary_score = 1.0 / (1 + abs(dx) * 15)

        prev_index = (extreme_index - 1 + len(hull)) % len(hull)
        next_index = (extreme_index + 1) % len(hull)

        prev_point = hull[prev_index]
        next_point = hull[next_index]

        edge1_angle = atan2(
            extreme[1] - prev_point[1], extreme[0] - prev_point[0]
        )
        edge2_angle = atan2(
            next_point[1] - extreme[1], next_point[0] - extreme[0]
        )

        line_angle = atan2(dy, dx)

        alignment_score1 = abs(cos(line_angle - edge1_angle))
        alignment_score2 = abs(cos(line_angle - edge2_angle))
        hull_edge_alignment = (alignment_score1 + alignment_score2) / 2.0

        target_alignment = abs(cos(line_angle - target_rad))

        return (
            boundary_score * 0.3
            + hull_edge_alignment * 0.6
            + target_alignment * 0.1
        )

    def find_neighbor(
        extreme: Tuple[float, float], is_left: bool
    ) -> Tuple[float, float]:
        try:
            extreme_index = hull.index(extreme)
        except ValueError:
            return extreme

        cw_index = (extreme_index + 1) % len(hull)
        ccw_index = (extreme_index - 1 + len(hull)) % len(hull)

        cw_neighbor = hull[cw_index]
        ccw_neighbor = hull[ccw_index]

        cw_score = calc_score(extreme, cw_neighbor, is_left)
        ccw_score = calc_score(extreme, ccw_neighbor, is_left)

        return cw_neighbor if cw_score > ccw_score else ccw_neighbor

    return {
        "leftSegment": {
            "extreme": left_extreme,
            "optimizedNeighbor": find_neighbor(left_extreme, True),
        },
        "rightSegment": {
            "extreme": right_extreme,
            "optimizedNeighbor": find_neighbor(right_extreme, False),
        },
    }


def create_boundary_line_from_points(
    point1: Tuple[float, float], point2: Tuple[float, float]
) -> Dict[str, float]:
    dx = point2[0] - point1[0]
    dy = point2[1] - point1[1]
    if abs(dx) < 1e-9:
        return {
            "isVertical": True,
            "x": point1[0],
            "slope": 0.0,
            "intercept": 0.0,
        }
    slope = dy / dx
    intercept = point1[1] - slope * point1[0]
    return {"isVertical": False, "slope": slope, "intercept": intercept}


def create_boundary_line_from_theil_sen(
    theil_sen_result: Dict[str, float],
) -> Dict[str, float]:
    # Check for degenerate case where slope is 0 (horizontal line in inverted form = vertical line)
    if abs(theil_sen_result["slope"]) < 1e-9:
        # This is actually a vertical line: x = intercept
        return {
            "isVertical": True,
            "x": theil_sen_result["intercept"],
            "slope": 0.0,
            "intercept": 0.0,
        }

    # Keep theil_sen results in their original x = slope * y + intercept format
    # Mark them as inverted so intersection calculation can handle them properly
    return {
        "isVertical": False,
        "isInverted": True,  # Indicates x = slope * y + intercept format
        "slope": theil_sen_result["slope"],
        "intercept": theil_sen_result["intercept"],
    }


def create_horizontal_boundary_line_from_points(
    edge_points: List[Tuple[float, float]],
) -> Dict[str, float]:
    """Create a horizontal boundary line (y = mx + b) from edge points."""
    if len(edge_points) < 2:
        # Not enough points, return a horizontal line at the average y
        avg_y = (
            sum(p[1] for p in edge_points) / len(edge_points)
            if edge_points
            else 0.0
        )
        return {
            "isVertical": False,
            "slope": 0.0,
            "intercept": avg_y,
        }

    # Check if all points have the same y-coordinate (perfect horizontal line)
    y_coords = [p[1] for p in edge_points]
    if max(y_coords) - min(y_coords) < 1e-6:
        # All points have same y -> perfect horizontal line y = constant
        return {
            "isVertical": False,
            "slope": 0.0,
            "intercept": y_coords[0],
        }

    # Check if all points have the same x-coordinate (vertical line - degenerate for horizontal boundary)
    x_coords = [p[0] for p in edge_points]
    if max(x_coords) - min(x_coords) < 1e-6:
        # All points have same x -> vertical line (degenerate)
        # Return a horizontal line at average y
        avg_y = sum(y_coords) / len(y_coords)
        return {
            "isVertical": False,
            "slope": 0.0,
            "intercept": avg_y,
        }

    # Fit y = mx + b directly using least squares
    n = len(edge_points)
    sum_x = sum(p[0] for p in edge_points)
    sum_y = sum(p[1] for p in edge_points)
    sum_xy = sum(p[0] * p[1] for p in edge_points)
    sum_x2 = sum(p[0] * p[0] for p in edge_points)

    # Least squares: slope = (n*sum_xy - sum_x*sum_y) / (n*sum_x2 - sum_x^2)
    denom = n * sum_x2 - sum_x * sum_x
    if abs(denom) < 1e-9:
        # Degenerate case - return horizontal line at average y
        avg_y = sum_y / n
        return {
            "isVertical": False,
            "slope": 0.0,
            "intercept": avg_y,
        }

    slope = (n * sum_xy - sum_x * sum_y) / denom
    intercept = (sum_y - slope * sum_x) / n

    return {
        "isVertical": False,
        "slope": slope,
        "intercept": intercept,
    }


def compute_receipt_box_from_boundaries(
    top_boundary: Dict[str, float],
    bottom_boundary: Dict[str, float],
    left_boundary: Dict[str, float],
    right_boundary: Dict[str, float],
    fallback_centroid: Optional[Tuple[float, float]] = None,
) -> List[Tuple[float, float]]:
    """Compute final receipt quadrilateral from four boundary lines."""

    def find_intersection(
        line1: Dict[str, float], line2: Dict[str, float]
    ) -> Tuple[float, float]:
        if line1.get("isVertical") and line2.get("isVertical"):
            return (
                fallback_centroid
                if fallback_centroid
                else (((line1.get("x", 0) + line2.get("x", 0)) / 2.0, 0.5))
            )

        # Handle vertical lines
        if line1.get("isVertical"):
            x = line1["x"]
            if line2.get("isInverted"):
                # x = slope * y + intercept, solve for y
                if abs(line2["slope"]) < 1e-9:
                    # Inverted horizontal line: x = intercept (this is a vertical line!)
                    # If x != intercept, these are parallel vertical lines - use fallback
                    if abs(x - line2["intercept"]) > 1e-6:
                        return (
                            fallback_centroid
                            if fallback_centroid
                            else (x, 0.5)
                        )
                    # If x == intercept, lines are identical - use a reasonable y
                    y = 0.5
                else:
                    y = (x - line2["intercept"]) / line2["slope"]
            else:
                # y = slope * x + intercept
                y = line2["slope"] * x + line2["intercept"]
            return (x, y)

        if line2.get("isVertical"):
            x = line2["x"]
            if line1.get("isInverted"):
                # x = slope * y + intercept, solve for y
                if abs(line1["slope"]) < 1e-9:
                    # Inverted horizontal line: x = intercept (this is a vertical line!)
                    # If x != intercept, these are parallel vertical lines - use fallback
                    if abs(x - line1["intercept"]) > 1e-6:
                        return (
                            fallback_centroid
                            if fallback_centroid
                            else (x, 0.5)
                        )
                    # If x == intercept, lines are identical - use a reasonable y
                    y = 0.5
                else:
                    y = (x - line1["intercept"]) / line1["slope"]
            else:
                # y = slope * x + intercept
                y = line1["slope"] * x + line1["intercept"]
            return (x, y)

        # Handle intersection of two non-vertical lines
        line1_inverted = line1.get("isInverted", False)
        line2_inverted = line2.get("isInverted", False)

        if line1_inverted and line2_inverted:
            # Both lines: x = slope * y + intercept
            # line1: x = m1*y + b1, line2: x = m2*y + b2
            # m1*y + b1 = m2*y + b2 => (m1-m2)*y = b2-b1
            denom = line1["slope"] - line2["slope"]
            if abs(denom) < 1e-9:
                return fallback_centroid if fallback_centroid else (0.5, 0.5)
            y = (line2["intercept"] - line1["intercept"]) / denom
            x = line1["slope"] * y + line1["intercept"]

        elif line1_inverted and not line2_inverted:
            # line1: x = m1*y + b1, line2: y = m2*x + b2
            # Handle special case where line1 has slope = 0 (horizontal line)
            if abs(line1["slope"]) < 1e-9:
                # line1 is horizontal: x = b1, so substitute into line2
                x = line1["intercept"]
                y = line2["slope"] * x + line2["intercept"]
            else:
                # Substitute line2 into line1: x = m1*(m2*x + b2) + b1
                # x = m1*m2*x + m1*b2 + b1 => x(1 - m1*m2) = m1*b2 + b1
                denom = 1 - line1["slope"] * line2["slope"]
                if abs(denom) < 1e-9:
                    return (
                        fallback_centroid if fallback_centroid else (0.5, 0.5)
                    )
                x = (
                    line1["slope"] * line2["intercept"] + line1["intercept"]
                ) / denom
                y = line2["slope"] * x + line2["intercept"]

        elif not line1_inverted and line2_inverted:
            # line1: y = m1*x + b1, line2: x = m2*y + b2
            # Handle special case where line2 has slope = 0 (horizontal line)
            if abs(line2["slope"]) < 1e-9:
                # line2 is horizontal: x = b2, so substitute into line1
                x = line2["intercept"]
                y = line1["slope"] * x + line1["intercept"]
            else:
                # Substitute line1 into line2: x = m2*(m1*x + b1) + b2
                # x = m2*m1*x + m2*b1 + b2 => x(1 - m2*m1) = m2*b1 + b2
                denom = 1 - line2["slope"] * line1["slope"]
                if abs(denom) < 1e-9:
                    return (
                        fallback_centroid if fallback_centroid else (0.5, 0.5)
                    )
                x = (
                    line2["slope"] * line1["intercept"] + line2["intercept"]
                ) / denom
                y = line1["slope"] * x + line1["intercept"]

        else:
            # Both lines: y = slope * x + intercept (standard case)
            denom = line1["slope"] - line2["slope"]
            if abs(denom) < 1e-6:
                return fallback_centroid if fallback_centroid else (0.5, 0.5)
            x = (line2["intercept"] - line1["intercept"]) / denom
            y = line1["slope"] * x + line1["intercept"]

        # Check for invalid/infinite coordinates before returning
        if not (isfinite(x) and isfinite(y)):
            if fallback_centroid:
                return fallback_centroid
            return (0.0, 0.0)

        return (x, y)

    from math import isfinite

    top_left = find_intersection(top_boundary, left_boundary)
    top_right = find_intersection(top_boundary, right_boundary)
    bottom_left = find_intersection(bottom_boundary, left_boundary)
    bottom_right = find_intersection(bottom_boundary, right_boundary)

    return [top_left, top_right, bottom_right, bottom_left]


def compute_receipt_box_from_refined_segments(
    lines: List[object],
    hull: List[Tuple[float, float]],
    centroid: Tuple[float, float],
    final_angle: float,
    refined_segments: Dict[str, Dict[str, Tuple[float, float]]],
) -> List[Tuple[float, float]]:
    if not lines or len(hull) < 3:
        return []

    edges = find_line_edges_at_secondary_extremes(
        lines, hull, centroid, final_angle
    )

    if len(edges["topEdge"]) < 2 or len(edges["bottomEdge"]) < 2:
        cx, cy = centroid
        return [
            (cx - 0.1, cy + 0.1),
            (cx + 0.1, cy + 0.1),
            (cx + 0.1, cy - 0.1),
            (cx - 0.1, cy - 0.1),
        ]

    top_boundary = create_boundary_line_from_theil_sen(
        theil_sen(edges["topEdge"])
    )
    bottom_boundary = create_boundary_line_from_theil_sen(
        theil_sen(edges["bottomEdge"])
    )
    left_boundary = create_boundary_line_from_points(
        refined_segments["leftSegment"]["extreme"],
        refined_segments["leftSegment"]["optimizedNeighbor"],
    )
    right_boundary = create_boundary_line_from_points(
        refined_segments["rightSegment"]["extreme"],
        refined_segments["rightSegment"]["optimizedNeighbor"],
    )

    return compute_receipt_box_from_boundaries(
        top_boundary, bottom_boundary, left_boundary, right_boundary, centroid
    )
