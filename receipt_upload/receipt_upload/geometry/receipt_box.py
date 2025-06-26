"""
Receipt-specific box calculation functions.

This module provides functions for computing receipt bounding boxes
from convex hulls and handling skewed/rotated receipts.
"""

from math import cos, degrees, radians, sin
from typing import Dict, List, Optional, Tuple


def compute_receipt_box_from_skewed_extents(
    hull_pts: List[Tuple[float, float]],
    cx: float,
    cy: float,
    rotation_deg: float,
    use_radians: bool = False,
) -> Optional[List[List[int]]]:
    """
    Compute a perspective-correct quadrilateral ("receipt box") from a set of
    convex hull points.

    This function first deskews the input points by translating them so that
    (cx, cy) becomes the origin and then rotating them by -rotation_deg (or
    the equivalent in radians if use_radians is True). In the deskewed
    coordinate system, the points are split into "top" (y < 0) and "bottom"
    (y >= 0) groups. If either group is empty, all deskewed points are used.
    The function then determines the extreme left and right vertices in both
    the top and bottom groups, and computes the overall vertical boundaries.

    Using linear interpolation along the segments connecting the extreme
    vertices, four boundary points are determined at the top and bottom
    y-values. These points form a quadrilateral in the deskewed space.
    Finally, the quadrilateral is transformed back to the original coordinate
    system by applying the inverse rotation and translation, and the resulting
    coordinates are rounded to the nearest integers.

    Parameters:
        hull_pts (List[Tuple[float, float]]): A list of (x, y) coordinates
            representing the convex hull.
        cx (float): X-coordinate of the translation center (deskew origin).
        cy (float): Y-coordinate of the translation center (deskew origin).
        rotation_deg (float): The skew angle. Interpreted as degrees by
            default (or as radians if use_radians is True).
        use_radians (bool, optional): If True, rotation_deg is treated as
            radians. Defaults to False.

    Returns:
        Optional[List[List[int]]]: A list of four [x, y] integer pairs
            representing the corners of the quadrilateral, ordered as
            [top-left, top-right, bottom-right, bottom-left]. Returns None if
            hull_pts is empty.
    """
    # If the user specifies use_radians=True, interpret rotation_deg as
    # radians; otherwise, treat rotation_deg as degrees and convert to radians.
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
        Linear interpolation along the segment [v_top, v_bottom] in deskewed
        space.
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

    left_top_point = interpolate_vertex(left_top_vertex, left_bottom_vertex, top_y)
    left_bottom_point = interpolate_vertex(
        left_top_vertex, left_bottom_vertex, bottom_y
    )
    right_top_point = interpolate_vertex(right_top_vertex, right_bottom_vertex, top_y)
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
    Compute the intersection points between a convex hull and four rays
    emanating from a centroid, in a rotated coordinate system.

    The function defines a rotated coordinate system by rotating the standard
    axes by `rotation_deg` (interpreted as degrees by default; if
    `use_radians` is True, the value is treated as radians and converted to
    degrees). In this rotated system:
      - The positive X-axis is defined by the unit vector
        u = (cos(theta), sin(theta)).
      - The positive Y-axis is defined by the unit vector
        v = (-sin(theta), cos(theta)).
    The rays originate at (cx, cy) and extend in the following directions:
      - "left":   opposite to u,
      - "right":  along u,
      - "top":    opposite to v,
      - "bottom": along v.

    For each of these directions, the function finds the intersection point of
    the ray with the convex hull (provided as `hull_pts`, typically in
    counter-clockwise order) using the helper function
    `_intersection_point_for_direction`. If an intersection is found, its
    coordinates are rounded to the nearest integer; otherwise, the
    corresponding value is set to None.

    Parameters:
        hull_pts (List[Tuple[float, float]]): A list of (x, y) vertices
            defining the convex hull.
        cx (float): The x-coordinate of the centroid (ray origin).
        cy (float): The y-coordinate of the centroid (ray origin).
        rotation_deg (float, optional): The angle to rotate the coordinate
            system. Defaults to 0.0. If `use_radians` is False, this is
            interpreted in degrees.
        use_radians (bool, optional): If True, `rotation_deg` is interpreted
            as radians. Defaults to False.

    Returns:
        Dict[str, Optional[Tuple[int, int]]]: A dictionary with keys "left",
            "right", "top", and "bottom". Each key maps to an (x, y) tuple of
            integers representing the intersection point of the corresponding
            ray with the convex hull, or None if no valid intersection is
            found.
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
        pt = _intersection_point_for_direction(hull_pts, cx, cy, direction_vector)
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
    Compute the intersection point between a ray and the edges of a convex
    polygon.

    The ray originates at (cx, cy) and extends in the specified direction
    vector. The polygon is defined by its vertices in `hull_pts` (order may be
    clockwise or counter-clockwise, though typically CCW). For each edge of
    the polygon, the function computes the intersection with the ray,
    parameterized as:

        intersection = (cx, cy) + t * direction

    where t >= 0. It only considers intersections that occur on the edge
    segment (i.e. where the edge parameter s is between 0 and 1). If multiple
    valid intersections are found, the one corresponding to the smallest
    nonnegative t is returned.

    Parameters:
        hull_pts (List[Tuple[float, float]]): List of (x, y) vertices defining
            the convex polygon.
        cx (float): X-coordinate of the ray's origin.
        cy (float): Y-coordinate of the ray's origin.
        direction (Tuple[float, float]): Direction vector (dx, dy) of the ray.

    Returns:
        Optional[Tuple[float, float]]: The intersection point (x, y) with the
            smallest nonnegative parameter t, or None if no valid intersection
            exists.
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
