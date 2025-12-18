"""
Edge detection and boundary computation for receipt processing.

This module provides functions for detecting edges, computing boundaries,
and creating receipt boxes from text lines and hull points.
"""

from math import atan2, cos, degrees, isfinite, radians, sin, sqrt
from typing import Dict, List, Optional, Tuple

from .utils import theil_sen


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
            pick == "left" and x < current[0] or pick == "right" and x > current[0]
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
    lines: List[object],  # pylint: disable=unused-argument
    hull: List[Tuple[float, float]],
    centroid: Tuple[float, float],
    avg_angle: float,
) -> Dict[str, List[Tuple[float, float]]]:
    """Locate points along text edges at extreme secondary-axis positions."""

    angle_rad = radians(avg_angle)
    secondary_axis_angle = angle_rad + radians(90)

    hull_projections = []
    for point in hull:
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

    edges = find_line_edges_at_secondary_extremes(lines, hull, centroid, avg_angle)
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

        edge1_angle = atan2(extreme[1] - prev_point[1], extreme[0] - prev_point[0])
        edge2_angle = atan2(next_point[1] - extreme[1], next_point[0] - extreme[0])

        line_angle = atan2(dy, dx)

        alignment_score1 = abs(cos(line_angle - edge1_angle))
        alignment_score2 = abs(cos(line_angle - edge2_angle))
        hull_edge_alignment = (alignment_score1 + alignment_score2) / 2.0

        target_alignment = abs(cos(line_angle - target_rad))

        return boundary_score * 0.3 + hull_edge_alignment * 0.6 + target_alignment * 0.1

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
    # Check for degenerate case where slope is 0
    # (horizontal line in inverted form = vertical line)
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
            sum(p[1] for p in edge_points) / len(edge_points) if edge_points else 0.0
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

    # Check if all points have the same x-coordinate
    # (vertical line - degenerate for horizontal boundary)
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


def _find_line_intersection(
    line1: Dict[str, float],
    line2: Dict[str, float],
    fallback_centroid: Optional[Tuple[float, float]] = None,
) -> Tuple[float, float]:
    """Find intersection of two lines with various representations."""
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
                # Inverted horizontal line: x = intercept
                # (this is a vertical line!)
                # If x != intercept, these are parallel vertical lines
                if abs(x - line2["intercept"]) > 1e-6:
                    return fallback_centroid if fallback_centroid else (x, 0.5)
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
                # Inverted horizontal line: x = intercept
                # (this is a vertical line!)
                # If x != intercept, these are parallel vertical lines
                if abs(x - line1["intercept"]) > 1e-6:
                    return fallback_centroid if fallback_centroid else (x, 0.5)
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
                return fallback_centroid if fallback_centroid else (0.5, 0.5)
            x = (line1["slope"] * line2["intercept"] + line1["intercept"]) / denom
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
                return fallback_centroid if fallback_centroid else (0.5, 0.5)
            x = (line2["slope"] * line1["intercept"] + line2["intercept"]) / denom
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


def compute_receipt_box_from_boundaries(
    top_boundary: Dict[str, float],
    bottom_boundary: Dict[str, float],
    left_boundary: Dict[str, float],
    right_boundary: Dict[str, float],
    fallback_centroid: Optional[Tuple[float, float]] = None,
) -> List[Tuple[float, float]]:
    """Compute final receipt quadrilateral from four boundary lines."""

    top_left = _find_line_intersection(top_boundary, left_boundary, fallback_centroid)
    top_right = _find_line_intersection(top_boundary, right_boundary, fallback_centroid)
    bottom_left = _find_line_intersection(
        bottom_boundary, left_boundary, fallback_centroid
    )
    bottom_right = _find_line_intersection(
        bottom_boundary, right_boundary, fallback_centroid
    )

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

    edges = find_line_edges_at_secondary_extremes(lines, hull, centroid, final_angle)

    if len(edges["topEdge"]) < 2 or len(edges["bottomEdge"]) < 2:
        cx, cy = centroid
        return [
            (cx - 0.1, cy + 0.1),
            (cx + 0.1, cy + 0.1),
            (cx + 0.1, cy - 0.1),
            (cx - 0.1, cy - 0.1),
        ]

    top_boundary = create_boundary_line_from_theil_sen(theil_sen(edges["topEdge"]))
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
