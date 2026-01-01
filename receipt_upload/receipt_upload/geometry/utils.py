"""
Utility functions for geometric operations.
"""

import math
from typing import Dict, List, Optional, Tuple


def line_intersection(
    p1: Tuple[float, float],
    d1: Tuple[float, float],
    p2: Tuple[float, float],
    d2: Tuple[float, float],
) -> Optional[Tuple[float, float]]:
    """
    Find intersection of two lines defined by point + direction.

    Args:
        p1: Point on first line
        d1: Direction vector of first line
        p2: Point on second line
        d2: Direction vector of second line

    Returns:
        Intersection point, or None if lines are parallel
    """
    cross = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(cross) < 1e-9:
        return None  # Parallel
    dp = (p2[0] - p1[0], p2[1] - p1[1])
    t = (dp[0] * d2[1] - dp[1] * d2[0]) / cross
    return (p1[0] + t * d1[0], p1[1] + t * d1[1])


def circular_mean_angle(angle1: float, angle2: float) -> float:
    """
    Compute the circular mean of two angles (in radians).

    Handles wraparound at ±π correctly. For example, averaging
    +179° and -179° gives ±180° instead of 0°.

    Args:
        angle1: First angle in radians
        angle2: Second angle in radians

    Returns:
        Mean angle in radians
    """
    # Convert to unit vectors, average, convert back to angle
    sin_sum = math.sin(angle1) + math.sin(angle2)
    cos_sum = math.cos(angle1) + math.cos(angle2)
    return math.atan2(sin_sum, cos_sum)


def _find_hull_extremes(
    hull: List[Tuple[float, float]],
    angle_rad: float,
) -> Tuple[Tuple[float, float], Tuple[float, float]]:
    """Find leftmost and rightmost hull points projected along an angle."""
    centroid = (
        sum(p[0] for p in hull) / len(hull),
        sum(p[1] for p in hull) / len(hull),
    )
    cos_a = math.cos(angle_rad)
    sin_a = math.sin(angle_rad)

    min_proj = float("inf")
    max_proj = float("-inf")
    left_pt = hull[0]
    right_pt = hull[0]

    for p in hull:
        rel = (p[0] - centroid[0], p[1] - centroid[1])
        proj = rel[0] * cos_a + rel[1] * sin_a
        if proj < min_proj:
            min_proj = proj
            left_pt = p
        if proj > max_proj:
            max_proj = proj
            right_pt = p

    return left_pt, right_pt


def compute_rotated_bounding_box_corners(
    hull: List[Tuple[float, float]],
    top_line_corners: List[Tuple[float, float]],
    bottom_line_corners: List[Tuple[float, float]],
) -> List[Tuple[float, float]]:
    """
    Compute receipt corners using perspective-aware transformation.

    This creates a quadrilateral by:
    - Top edge: follows top line's natural angle
    - Bottom edge: follows bottom line's natural angle
    - Left/Right edges: perpendicular to average angle, positioned at hull extremes

    The top/bottom edges can have different angles (perspective from text lines),
    while left/right edges are parallel to each other (perpendicular to average).

    Args:
        hull: Convex hull points of all word corners
        top_line_corners: Corners from top line [TL, TR, BL, BR]
        bottom_line_corners: Corners from bottom line [TL, TR, BL, BR]

    Returns:
        Receipt corners [top_left, top_right, bottom_right, bottom_left]
    """
    # Extract key points from line corners
    top_left_pt = top_line_corners[0]
    top_right_pt = top_line_corners[1]
    bottom_left_pt = bottom_line_corners[2]
    bottom_right_pt = bottom_line_corners[3]

    # Compute angles of top and bottom edges
    top_dx = top_right_pt[0] - top_left_pt[0]
    top_dy = top_right_pt[1] - top_left_pt[1]
    top_angle = math.atan2(top_dy, top_dx)

    bottom_dx = bottom_right_pt[0] - bottom_left_pt[0]
    bottom_dy = bottom_right_pt[1] - bottom_left_pt[1]
    bottom_angle = math.atan2(bottom_dy, bottom_dx)

    # Edge directions from the lines (these preserve perspective)
    top_dir = (top_dx, top_dy)
    bottom_dir = (bottom_dx, bottom_dy)

    # Average angle for left/right edge direction
    avg_angle = circular_mean_angle(top_angle, bottom_angle)

    # Left/right edges are perpendicular to the average horizontal angle
    side_angle = avg_angle + math.pi / 2
    side_dir = (math.cos(side_angle), math.sin(side_angle))

    # Find extreme hull points along the tilt direction
    left_extreme, right_extreme = _find_hull_extremes(hull, avg_angle)

    # Intersect edges to get corners
    top_left = line_intersection(top_left_pt, top_dir, left_extreme, side_dir)
    top_right = line_intersection(top_left_pt, top_dir, right_extreme, side_dir)
    bottom_left = line_intersection(bottom_left_pt, bottom_dir, left_extreme, side_dir)
    bottom_right = line_intersection(bottom_left_pt, bottom_dir, right_extreme, side_dir)

    # Fallback for intersection failures - axis-aligned bounds
    if any(p is None for p in [top_left, top_right, bottom_left, bottom_right]):
        hull_xs = [p[0] for p in hull]
        min_hull_x = min(hull_xs)
        max_hull_x = max(hull_xs)
        top_left = (min_hull_x, top_left_pt[1])
        top_right = (max_hull_x, top_right_pt[1])
        bottom_left = (min_hull_x, bottom_left_pt[1])
        bottom_right = (max_hull_x, bottom_right_pt[1])

    return [top_left, top_right, bottom_right, bottom_left]


def theil_sen(pts: List[Tuple[float, float]]) -> Dict[str, float]:
    """Perform Theil–Sen regression to estimate a line."""
    if len(pts) < 2:
        return {"slope": 0.0, "intercept": pts[0][1] if pts else 0.0}

    slopes: List[float] = []
    for i, pt_i in enumerate(pts):
        for j in range(i + 1, len(pts)):
            if pt_i[1] == pts[j][1]:
                continue
            slopes.append((pts[j][0] - pt_i[0]) / (pts[j][1] - pt_i[1]))

    if not slopes:
        return {"slope": 0.0, "intercept": pts[0][1]}

    slopes.sort()
    slope = slopes[len(slopes) // 2]

    intercepts = sorted(p[0] - slope * p[1] for p in pts)
    intercept = intercepts[len(intercepts) // 2]

    return {"slope": slope, "intercept": intercept}
