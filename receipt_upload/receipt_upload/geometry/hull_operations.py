"""
Convex hull and related geometric operations.

This module provides functions for computing convex hulls,
minimum area rectangles, and centroids.
"""

from math import atan2, cos, degrees, radians, sin
from typing import List, Tuple


def compute_hull_centroid(
    hull_vertices: List[Tuple[float, float]],
) -> Tuple[float, float]:
    """
    Compute the centroid (geometric center) of a polygon defined by its convex
    hull vertices.

    The convex hull is assumed to be provided as a list of points in
    counter-clockwise (CCW) order. The centroid is calculated as follows,
    based on the number of vertices:

      - If the hull is empty, returns (0.0, 0.0).
      - If the hull contains a single point, returns that point.
      - If the hull consists of two points, returns the midpoint of the
        segment connecting them.
      - If the hull contains three or more points, computes the area-based
        centroid using the standard shoelace formula:

          * Compute the cross product for each edge:
                cross_i = x_i * y_{i+1} - x_{i+1} * y_i
          * The polygon's area is given by:
                A = 0.5 * Σ(cross_i)
          * The centroid (C_x, C_y) is then:
                C_x = (1 / (6A)) * Σ((x_i + x_{i+1}) * cross_i)
                C_y = (1 / (6A)) * Σ((y_i + y_{i+1}) * cross_i)

    If the computed area is nearly zero (indicating a degenerate or very thin
    polygon), the function avoids numerical instability by returning the
    arithmetic mean of the hull vertices instead.

    Parameters:
        hull_vertices (List[Tuple[float, float]]): A list of (x, y)
            coordinates representing the vertices of the convex hull in
            counter-clockwise order.

    Returns:
        Tuple[float, float]: The (x, y) coordinates of the centroid.
    """
    n = len(hull_vertices)

    if n == 0:
        return (0.0, 0.0)
    if n == 1:
        # Single point
        return (hull_vertices[0][0], hull_vertices[0][1])
    if n == 2:
        # Midpoint of the two points
        x0, y0 = hull_vertices[0]
        x1, y1 = hull_vertices[1]
        return ((x0 + x1) / 2.0, (y0 + y1) / 2.0)

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

    # Centroid is (1/(6A)) * sum((x_i + x_{i+1}) * cross,
    # (y_i + y_{i+1}) * cross)
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
