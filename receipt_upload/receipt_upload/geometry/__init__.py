"""
Geometry utilities for receipt processing.

This package provides geometric operations for:
- Affine and perspective transformations
- Convex hull operations
- Edge detection and boundary finding
- Receipt box calculations
"""

# Re-export main functions to maintain backward compatibility
from .edge_detection import (
    compute_edge,
    compute_final_receipt_tilt,
    compute_receipt_box_from_boundaries,
    create_boundary_line_from_points,
    create_boundary_line_from_theil_sen,
    create_horizontal_boundary_line_from_points,
    find_hull_extremes_along_angle,
    find_line_edges_at_secondary_extremes,
    refine_hull_extremes_with_hull_edge_alignment,
)
from .hull_operations import (
    box_points,
    compute_hull_centroid,
    convex_hull,
    min_area_rect,
)
from .receipt_box import (
    compute_receipt_box_from_skewed_extents,
    find_hull_extents_relative_to_centroid,
)
from .transformations import (
    find_perspective_coeffs,
    invert_affine,
    invert_warp,
    pad_corners_opposite,
)
from .utils import (
    circular_mean_angle,
    compute_rotated_bounding_box_corners,
    line_intersection,
    theil_sen,
)

__all__ = [
    # Edge detection
    "compute_edge",
    "compute_final_receipt_tilt",
    "compute_receipt_box_from_boundaries",
    "create_boundary_line_from_points",
    "create_boundary_line_from_theil_sen",
    "create_horizontal_boundary_line_from_points",
    "find_hull_extremes_along_angle",
    "find_line_edges_at_secondary_extremes",
    "refine_hull_extremes_with_hull_edge_alignment",
    # Hull operations
    "box_points",
    "compute_hull_centroid",
    "convex_hull",
    "min_area_rect",
    # Receipt box
    "compute_receipt_box_from_skewed_extents",
    "find_hull_extents_relative_to_centroid",
    # Transformations
    "find_perspective_coeffs",
    "invert_affine",
    "invert_warp",
    "pad_corners_opposite",
    # Utils
    "circular_mean_angle",
    "compute_rotated_bounding_box_corners",
    "line_intersection",
    "theil_sen",
]
