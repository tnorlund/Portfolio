"""
Geometry helper functions for Label Evaluator.

Provides functions for calculating angles, distances, and coordinate
conversions used in pattern computation and issue detection.
"""

import math


def calculate_angle_degrees(
    from_point: tuple[float, float],
    to_point: tuple[float, float],
) -> float:
    """
    Calculate angle in degrees from one point to another.

    Args:
        from_point: (x, y) starting point
        to_point: (x, y) ending point

    Returns:
        Angle in degrees (0-360), where:
        - 0° = directly right
        - 90° = directly down
        - 180° = directly left
        - 270° = directly up
    """
    dx = to_point[0] - from_point[0]
    dy = to_point[1] - from_point[1]

    # Note: y increases downward (0=bottom, 1=top in receipt coords)
    # atan2(dy, dx) gives angle with x=right as 0°
    radians = math.atan2(dy, dx)
    degrees = math.degrees(radians)

    # Normalize to 0-360 range
    if degrees < 0:
        degrees += 360

    return degrees


def calculate_distance(
    point1: tuple[float, float],
    point2: tuple[float, float],
) -> float:
    """
    Calculate Euclidean distance between two points.

    Args:
        point1: (x, y) first point
        point2: (x, y) second point

    Returns:
        Distance (in normalized 0-1 coordinate space, diagonal ≈ 1.41)
    """
    dx = point2[0] - point1[0]
    dy = point2[1] - point1[1]
    return math.sqrt(dx * dx + dy * dy)


def angle_difference(angle1: float, angle2: float) -> float:
    """
    Calculate shortest angular distance between two angles.

    Args:
        angle1, angle2: Angles in degrees (0-360)

    Returns:
        Shortest angular distance (0-180 degrees)
    """
    diff = abs(angle1 - angle2)
    if diff > 180:
        diff = 360 - diff
    return diff


def convert_polar_to_cartesian(
    angle_degrees: float,
    distance: float,
) -> tuple[float, float]:
    """
    Convert polar coordinates (angle, distance) to Cartesian (dx, dy).

    Args:
        angle_degrees: Angle in degrees (0-360)
        distance: Euclidean distance (0-1 normalized)

    Returns:
        Tuple of (dx, dy) in Cartesian space
    """
    angle_radians = math.radians(angle_degrees)
    dx = distance * math.cos(angle_radians)
    dy = distance * math.sin(angle_radians)
    return (dx, dy)
