"""
Geometry utilities for receipt combination.

This module contains geometry transformation and warping functions
used when combining receipts.
"""

from typing import Any, Dict, List, Optional, Tuple

from PIL import Image as PIL_Image
from receipt_upload.cluster import reorder_box_points
from receipt_upload.geometry.hull_operations import box_points, min_area_rect
from receipt_upload.geometry.transformations import (
    find_perspective_coeffs,
    invert_warp,
)

IMAGE_PROCESSING_AVAILABLE = True


def calculate_min_area_rect(
    words: List[Dict[str, Any]], image_width: int, image_height: int
) -> Dict[str, Any]:
    """
    Calculate the minimum-area rectangle covering all words and return bounds + warping info.

    Args:
        words: List of word dictionaries with corner coordinates
        image_width: Width of the original image
        image_height: Height of the original image

    Returns:
        Dict with:
            - bounds: OCR-space bounds dict (for Receipt entity)
            - src_corners: Source corners in PIL image space (y=0 at top) for warping
            - warped_width: Width of warped image
            - warped_height: Height of warped image

    Raises:
        ValueError: If no words are provided
    """
    if not words:
        raise ValueError("No words provided to calculate bounds")

    # Collect all corner points in PIL image space (y=0 at top)
    # Words are currently in OCR space (y=0 at bottom), so we need to flip Y
    all_points = []
    for word in words:
        # Convert OCR space (y=0 at bottom) to PIL space (y=0 at top)
        for corner_name in [
            "top_left",
            "top_right",
            "bottom_left",
            "bottom_right",
        ]:
            corner = word.get(corner_name, {})
            x = corner.get("x", 0)
            y_ocr = corner.get("y", 0)
            y_pil = image_height - y_ocr  # Flip Y: PIL space has y=0 at top
            all_points.append((x, y_pil))

    # Compute minimum-area rectangle
    (cx, cy), (rw, rh), angle_deg = min_area_rect(all_points)

    # Ensure portrait orientation (width < height)
    if rw > rh:
        angle_deg -= 90.0
        rw, rh = rh, rw

    # Get the 4 corners of the min-area rect
    box_4 = box_points((cx, cy), (rw, rh), angle_deg)
    src_corners_ordered = reorder_box_points(
        box_4
    )  # [top-left, top-right, bottom-right, bottom-left]

    # Calculate warped dimensions
    warped_width = int(round(rw))
    warped_height = int(round(rh))

    # Ensure minimum dimensions
    if warped_width < 1:
        warped_width = 1
    if warped_height < 1:
        warped_height = 1

    # Convert src_corners back to OCR space for bounds dict (y=0 at bottom)
    # Bounds are used for Receipt entity coordinates
    bounds = {
        "top_left": {
            "x": src_corners_ordered[0][0],
            "y": image_height
            - src_corners_ordered[0][1],  # Convert back to OCR space
        },
        "top_right": {
            "x": src_corners_ordered[1][0],
            "y": image_height - src_corners_ordered[1][1],
        },
        "bottom_right": {
            "x": src_corners_ordered[2][0],
            "y": image_height - src_corners_ordered[2][1],
        },
        "bottom_left": {
            "x": src_corners_ordered[3][0],
            "y": image_height - src_corners_ordered[3][1],
        },
    }

    return {
        "bounds": bounds,
        "src_corners": src_corners_ordered,  # In PIL space for warping
        "warped_width": warped_width,
        "warped_height": warped_height,
    }


def create_warped_receipt_image(
    image: Any,
    src_corners: List[Tuple[float, float]],
    warped_width: int,
    warped_height: int,
) -> Optional[Any]:
    """
    Create a warped/rectified image using perspective transform.

    Args:
        image: PIL Image in original space
        src_corners: Source corners in PIL image space
            [top-left, top-right, bottom-right, bottom-left]
        warped_width: Width of destination rectified image
        warped_height: Height of destination rectified image

    Returns:
        Warped PIL Image or None if warping fails or image processing unavailable
    """
    # Destination corners for rectified rectangle
    # (in order: top-left, top-right, bottom-right, bottom-left)
    dst_corners = [
        (0.0, 0.0),
        (float(warped_width - 1), 0.0),
        (float(warped_width - 1), float(warped_height - 1)),
        (0.0, float(warped_height - 1)),
    ]

    # Compute perspective transform coefficients
    transform_coeffs = find_perspective_coeffs(
        src_points=src_corners,
        dst_points=dst_corners,
    )

    # Warp the image
    warped_img = image.transform(
        (warped_width, warped_height),
        PIL_Image.Transform.PERSPECTIVE,
        transform_coeffs,
        resample=PIL_Image.Resampling.BICUBIC,
    )
    return warped_img


def transform_point_to_warped_space(
    x: float,
    y: float,
    src_corners: List[Tuple[float, float]],
    warped_width: int,
    warped_height: int,
) -> Tuple[float, float]:
    """
    Transform a point from original image space to warped/rectified space.

    Uses the perspective transform defined by src_corners -> dst_corners.
    Since PIL uses backward mapping (dst -> src), we need to solve for
    the forward mapping.

    Args:
        x, y: Point in original image space (PIL space, y=0 at top)
        src_corners: Source corners [top-left, top-right, bottom-right, bottom-left]
        warped_width, warped_height: Dimensions of warped image

    Returns:
        (x_warped, y_warped) in rectified space
    """
    # Destination corners for rectified rectangle
    dst_corners = [
        (0.0, 0.0),
        (float(warped_width - 1), 0.0),
        (float(warped_width - 1), float(warped_height - 1)),
        (0.0, float(warped_height - 1)),
    ]

    # Get backward transform coefficients (dst -> src)
    backward_coeffs = find_perspective_coeffs(
        src_points=dst_corners,  # Note: swapped for backward mapping
        dst_points=src_corners,
    )
    if len(backward_coeffs) != 8:
        raise ValueError(
            f"Expected 8 warp coefficients, got {len(backward_coeffs)}"
        )
    a, b, c, d, e, f, g, h = backward_coeffs

    # Invert to get forward transform (src -> dst)
    forward_coeffs = invert_warp(a, b, c, d, e, f, g, h)

    # Apply forward transform: x_dst = (a*x_src + b*y_src + c) / (1 + g*x_src + h*y_src)
    a, b, c, d, e, f, g, h = forward_coeffs
    denom = 1.0 + g * x + h * y
    if abs(denom) < 1e-10:
        # Degenerate case, return original coordinates
        return (x, y)

    x_warped = (a * x + b * y + c) / denom
    y_warped = (d * x + e * y + f) / denom

    return (x_warped, y_warped)
