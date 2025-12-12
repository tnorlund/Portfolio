"""
Utilities for transforming receipt coordinates back to image coordinates.

This module provides functions to reverse-transform receipt lines/words
from receipt coordinate space back to the original image coordinate space,
allowing comparison of different receipts in the same image.
"""

from typing import Any, List, Tuple

from receipt_upload.geometry.transformations import find_perspective_coeffs


def get_receipt_to_image_transform(
    receipt: Any,
    image_width: int,
    image_height: int,
) -> Tuple[List[float], int, int]:
    """
    Compute transform coefficients to map receipt coordinates back to image coordinates.

    Args:
        receipt: Receipt entity with top_left, top_right, bottom_left, bottom_right corners
        image_width: Original image width in pixels
        image_height: Original image height in pixels

    Returns:
        Tuple of (transform_coeffs, receipt_width, receipt_height)
        transform_coeffs: 8 perspective coefficients [a, b, c, d, e, f, g, h]
        receipt_width: Receipt width in pixels
        receipt_height: Receipt height in pixels
    """
    # Get receipt corners in image coordinates (normalized 0-1)
    # Note: Receipt stores corners in normalized image coordinates
    # where Y is flipped (top=1, bottom=0)
    src_tl = (
        receipt.top_left["x"] * image_width,
        (1 - receipt.top_left["y"]) * image_height,  # Flip Y
    )
    src_tr = (
        receipt.top_right["x"] * image_width,
        (1 - receipt.top_right["y"]) * image_height,  # Flip Y
    )
    src_br = (
        receipt.bottom_right["x"] * image_width,
        (1 - receipt.bottom_right["y"]) * image_height,  # Flip Y
    )
    src_bl = (
        receipt.bottom_left["x"] * image_width,
        (1 - receipt.bottom_left["y"]) * image_height,  # Flip Y
    )

    # Destination corners in receipt space (normalized 0-1, then to pixels)
    # Receipt is warped to a rectangle: top-left, top-right, bottom-right, bottom-left
    receipt_width = receipt.width
    receipt_height = receipt.height

    dst_tl = (0.0, 0.0)
    dst_tr = (float(receipt_width - 1), 0.0)
    dst_br = (float(receipt_width - 1), float(receipt_height - 1))
    dst_bl = (0.0, float(receipt_height - 1))

    # Compute perspective transform coefficients
    # This maps from receipt space (dst) back to image space (src)
    src_points = [src_tl, src_tr, src_br, src_bl]
    dst_points = [dst_tl, dst_tr, dst_br, dst_bl]

    try:
        # find_perspective_coeffs returns coefficients for PIL's transform
        # which maps dst -> src (inverse of what we want)
        # But we need the inverse of that to map receipt coords -> image coords
        # Actually, find_perspective_coeffs gives us the inverse transform
        # that PIL uses: it maps dst -> src
        # So we can use it directly to transform receipt coords -> image coords
        transform_coeffs = find_perspective_coeffs(
            src_points=src_points,  # Image corners
            dst_points=dst_points,  # Receipt corners
        )
    except ValueError:
        # Fallback to affine if perspective fails
        # For affine, we can compute from 3 points
        # Use top-left, top-right, bottom-left
        from receipt_upload.geometry.transformations import invert_affine

        # Compute affine transform from receipt space to image space
        # Receipt space: (0,0) -> src_tl, (w-1,0) -> src_tr, (0,h-1) -> src_bl
        if receipt_width > 1:
            a = (src_tr[0] - src_tl[0]) / (receipt_width - 1)
            d = (src_tr[1] - src_tl[1]) / (receipt_width - 1)
        else:
            a = d = 0.0

        if receipt_height > 1:
            b = (src_bl[0] - src_tl[0]) / (receipt_height - 1)
            e = (src_bl[1] - src_tl[1]) / (receipt_height - 1)
        else:
            b = e = 0.0

        c = src_tl[0]
        f = src_tl[1]

        # Invert to get receipt -> image transform
        a_inv, b_inv, c_inv, d_inv, e_inv, f_inv = invert_affine(a, b, c, d, e, f)

        # Convert to perspective coefficients (affine is perspective with g=h=0)
        transform_coeffs = [a_inv, b_inv, c_inv, d_inv, e_inv, f_inv, 0.0, 0.0]

    return transform_coeffs, receipt_width, receipt_height


def transform_receipt_line_to_image_coords(
    receipt_line: Any,
    receipt: Any,
    image_width: int,
    image_height: int,
) -> dict:
    """
    Transform a receipt line from receipt coordinates to image coordinates.

    Args:
        receipt_line: ReceiptLine entity with coordinates in receipt space
        receipt: Receipt entity with corner information
        image_width: Original image width
        image_height: Original image height

    Returns:
        Dictionary with transformed coordinates in image space
    """
    # Get transform coefficients
    transform_coeffs, receipt_width, receipt_height = get_receipt_to_image_transform(
        receipt, image_width, image_height
    )

    # Create a copy of the line to transform (don't modify original)
    from receipt_dynamo.entities.receipt_line import ReceiptLine
    import copy

    line_copy = copy.deepcopy(receipt_line)

    # Apply inverse perspective transform
    # The warp_transform method expects the forward transform coefficients
    # but we computed the inverse, so we need to use the inverse of those
    # Actually, warp_transform does inverse_perspective_transform internally
    # which expects coefficients that map image -> receipt, and it inverts them
    # So we need to provide the forward transform coefficients (image -> receipt)
    # and it will invert them for us

    # But wait - we computed receipt -> image, so we need to invert again
    from receipt_upload.geometry.transformations import invert_warp

    # Invert the transform to get image -> receipt (which warp_transform expects)
    forward_coeffs = invert_warp(*transform_coeffs)

    # Now apply the transform (warp_transform will invert it back)
    line_copy.warp_transform(
        *forward_coeffs,
        src_width=image_width,
        src_height=image_height,
        dst_width=receipt_width,
        dst_height=receipt_height,
        flip_y=True,  # Receipt space typically has Y flipped
    )

    # Return transformed coordinates
    centroid = line_copy.calculate_centroid()
    return {
        "line_id": receipt_line.line_id,
        "text": receipt_line.text,
        "centroid_x": centroid[0],
        "centroid_y": centroid[1],
        "top_left": line_copy.top_left,
        "top_right": line_copy.top_right,
        "bottom_left": line_copy.bottom_left,
        "bottom_right": line_copy.bottom_right,
    }


def get_receipt_lines_in_image_coords(
    receipt_lines: List[Any],
    receipt: Any,
    image_width: int,
    image_height: int,
) -> List[dict]:
    """
    Transform all receipt lines from receipt coordinates to image coordinates.

    Args:
        receipt_lines: List of ReceiptLine entities
        receipt: Receipt entity
        image_width: Original image width
        image_height: Original image height

    Returns:
        List of dictionaries with transformed line coordinates
    """
    transformed = []
    for line in receipt_lines:
        try:
            transformed.append(
                transform_receipt_line_to_image_coords(
                    line, receipt, image_width, image_height
                )
            )
        except Exception as e:
            # Skip lines that fail to transform
            print(f"Warning: Failed to transform line {line.line_id}: {e}")
            continue
    return transformed


def sort_lines_by_reading_order(lines: List[dict]) -> List[dict]:
    """
    Sort lines by reading order: top to bottom, then left to right.

    Args:
        lines: List of line dictionaries with centroid_x and centroid_y

    Returns:
        Sorted list of lines
    """
    # Sort by Y (top to bottom), then by X (left to right)
    # Use a small tolerance for Y to group lines that are roughly on the same row
    TOLERANCE = 0.01  # Normalized coordinate tolerance

    # First, group by approximate Y position
    sorted_by_y = sorted(lines, key=lambda l: l["centroid_y"])

    # Then sort within each group by X
    result = []
    current_group = []
    current_y = None

    for line in sorted_by_y:
        y = line["centroid_y"]
        if current_y is None or abs(y - current_y) < TOLERANCE:
            # Same row
            current_group.append(line)
            current_y = y
        else:
            # New row - sort current group by X and add to result
            current_group.sort(key=lambda l: l["centroid_x"])
            result.extend(current_group)
            current_group = [line]
            current_y = y

    # Don't forget the last group
    if current_group:
        current_group.sort(key=lambda l: l["centroid_x"])
        result.extend(current_group)

    return result









