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


def compute_rotated_bounding_box_corners(
    hull: List[Tuple[float, float]],
    top_line_corners: List[Tuple[float, float]],
    bottom_line_corners: List[Tuple[float, float]],
) -> List[Tuple[float, float]]:
    """
    Compute receipt corners using the rotated bounding box approach.

    This derives left/right edges from the receipt tilt (average of top/bottom
    edge angles) and projects hull points onto the perpendicular axis to find
    extremes.

    Args:
        hull: Convex hull points of all word corners
        top_line_corners: Corners from top line [TL, TR, BL, BR]
        bottom_line_corners: Corners from bottom line [TL, TR, BL, BR]

    Returns:
        Receipt corners [top_left, top_right, bottom_right, bottom_left]
    """
    # Extract key points from line corners
    top_left_pt = top_line_corners[0]  # (x, y)
    top_right_pt = top_line_corners[1]  # (x, y)
    bottom_left_pt = bottom_line_corners[2]  # (x, y)
    bottom_right_pt = bottom_line_corners[3]  # (x, y)

    # Compute angle of top edge
    top_dx = top_right_pt[0] - top_left_pt[0]
    top_dy = top_right_pt[1] - top_left_pt[1]
    top_angle = math.atan2(top_dy, top_dx)

    # Compute angle of bottom edge
    bottom_dx = bottom_right_pt[0] - bottom_left_pt[0]
    bottom_dy = bottom_right_pt[1] - bottom_left_pt[1]
    bottom_angle = math.atan2(bottom_dy, bottom_dx)

    # Average angle using circular mean (handles wraparound at ±π)
    avg_angle = circular_mean_angle(top_angle, bottom_angle)

    # Left edge direction: perpendicular to horizontal edges
    left_edge_angle = avg_angle + math.pi / 2
    left_dx = math.cos(left_edge_angle)
    left_dy = math.sin(left_edge_angle)

    # Edge directions
    top_dir = (top_dx, top_dy)
    bottom_dir = (bottom_dx, bottom_dy)
    left_dir = (left_dx, left_dy)

    # Project hull points onto perpendicular axis to find left/right extremes
    # Use hull centroid as reference
    centroid = (
        sum(p[0] for p in hull) / len(hull),
        sum(p[1] for p in hull) / len(hull),
    )

    # Horizontal direction (along receipt tilt)
    horiz_dir = (math.cos(avg_angle), math.sin(avg_angle))

    perp_projections = []
    for p in hull:
        rel = (p[0] - centroid[0], p[1] - centroid[1])
        proj = rel[0] * horiz_dir[0] + rel[1] * horiz_dir[1]
        perp_projections.append((proj, p))

    perp_projections.sort(key=lambda x: x[0])
    leftmost_hull_pt = perp_projections[0][1]
    rightmost_hull_pt = perp_projections[-1][1]

    # Final corners: intersect edges
    top_left = line_intersection(top_left_pt, top_dir, leftmost_hull_pt, left_dir)
    top_right = line_intersection(top_left_pt, top_dir, rightmost_hull_pt, left_dir)
    bottom_left = line_intersection(
        bottom_left_pt, bottom_dir, leftmost_hull_pt, left_dir
    )
    bottom_right = line_intersection(
        bottom_left_pt, bottom_dir, rightmost_hull_pt, left_dir
    )

    # Handle intersection failures - fallback to axis-aligned bounds
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
