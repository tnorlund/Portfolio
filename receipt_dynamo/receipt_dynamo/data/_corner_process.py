# infra/lambda_layer/python/dynamo/data/_corner_process.py
import math
from typing import Dict, List, Tuple

from PIL.Image import Image, Resampling


def normalize(v: Tuple[float, float]) -> Tuple[float, float]:
    """
    Normalize a 2D vector to unit length.

    This function computes the Euclidean norm (magnitude) of the input vector and
    returns a new vector with the same direction but a length of 1. If the magnitude
    of the input vector is extremely small (less than 1e-8), indicating that the
    vector is effectively zero, the function returns (0.0, 0.0) to avoid division by zero.

    Parameters:
        v (Tuple[float, float]): A tuple representing the 2D vector (x, y).

    Returns:
        Tuple[float, float]: The normalized vector (x/mag, y/mag) or (0.0, 0.0)
        if the input vector is nearly zero.
    """
    mag = math.hypot(v[0], v[1])
    return (v[0] / mag, v[1] / mag) if mag > 1e-8 else (0.0, 0.0)


def offset_corner_inward(
    corner: Tuple[float, float],
    adj1: Tuple[float, float],
    adj2: Tuple[float, float],
    offset_distance: float,
) -> Tuple[float, float]:
    """
    Compute an inwardly offset point for a given corner.

    Given a corner point and its two adjacent points (which define the corner's edges),
    this function calculates a new point by offsetting the original corner inward by a
    distance N along the directions of the edges. The offset is computed as the sum of
    the unit vectors from the corner to each adjacent point, scaled by N.

    Mathematically:
        inner_point = corner + N * (normalize(adj1 - corner) + normalize(adj2 - corner))

    Parameters:
        corner (Tuple[float, float]): The (x, y) coordinates of the original corner.
        adj1 (Tuple[float, float]): The (x, y) coordinates of the first adjacent point.
        adj2 (Tuple[float, float]): The (x, y) coordinates of the second adjacent point.
        offset_distance (float): The distance to offset inward along each edge direction.

    Returns:
        Tuple[float, float]: The (x, y) coordinates of the inward-offset corner.
    """
    v1 = normalize((adj1[0] - corner[0], adj1[1] - corner[1]))
    v2 = normalize((adj2[0] - corner[0], adj2[1] - corner[1]))
    return (
        corner[0] + offset_distance * (v1[0] + v2[0]),
        corner[1] + offset_distance * (v1[1] + v2[1]),
    )


def window_rectangle_for_corner(
    receipt_corner: Tuple[float, float],
    adj1: Tuple[float, float],
    adj2: Tuple[float, float],
    edge_direction: Tuple[float, float],
    offset_distance: float,
    image_size: Tuple[int, int],
    corner_position: str,  # "top" or "bottom"
) -> List[Tuple[float, float]]:
    """
    Compute a rectangular window polygon adjacent to a receipt corner.

    This function generates a quadrilateral that defines a window region for a given receipt
    corner (e.g., top-left, top-right, bottom-right, or bottom-left). Instead of imposing a
    fixed square window, it calculates two distinct distances—one vertical and one horizontal—
    such that the window reaches the appropriate edge of the image.

    The algorithm proceeds as follows:
      1. Compute an "inner" corner point (I) by offsetting the original receipt corner inward
         by a distance offset_distance along the directions toward each of its two adjacent points.
      2. Derive a normalized ray (r) from the edge_direction. For a top corner, r is forced to
         point upward (negative y); for a bottom corner, downward (positive y).
      3. Generate a perpendicular vector (p) by rotating r by 90°. Adjust its sign so that it
         extends toward the left (for left-side corners) or right (for right-side corners).
      4. Determine the vertical distance (d_v) along r required for the point I + d_v·r to meet the
         corresponding horizontal image boundary (top edge for "top" corners or bottom edge for "bottom").
      5. Determine the horizontal distance (d_h) along p required for the point I + d_h·p to meet the
         corresponding vertical image boundary (left edge for left corners or right edge for right corners).
      6. Construct the polygon using the points:
             pt1 = I
             pt2 = I + d_v·r
             pt3 = I + d_v·r + d_h·p
             pt4 = I + d_h·p

    Parameters:
        receipt_corner (Tuple[float, float]): The (x, y) coordinates of the receipt corner.
        adj1 (Tuple[float, float]): The first adjacent point defining one edge of the corner.
        adj2 (Tuple[float, float]): The second adjacent point defining the other edge of the corner.
        edge_direction (Tuple[float, float]): A vector indicating the direction along one edge from the corner.
        offset_distance (float): The distance to offset inward from the receipt corner to compute the inner point I.
        image_size (Tuple[int, int]): The size of the image as (width, height).
        corner_position (str): Indicates which horizontal boundary the window should reach:
                               "top" for reaching the top edge or "bottom" for reaching the bottom edge.

    Returns:
        List[Tuple[float, float]]:
            A list of four (x, y) coordinates representing the window polygon:
                [I, I + d_v·r, I + d_v·r + d_h·p, I + d_h·p].

    Note:
        This function computes the window region based solely on geometric calculations and does
        not perform any perspective warping.
    """
    img_w, img_h = image_size
    # 1. Compute the inner corner I.
    I = offset_corner_inward(receipt_corner, adj1, adj2, offset_distance)

    # 2. Compute ray direction r from edge_direction.
    r = normalize(edge_direction)
    # For top corners, force r_y to be negative; for bottom, positive.
    if corner_position == "top" and r[1] > 0:
        r = (-r[0], -r[1])
    elif corner_position == "bottom" and r[1] < 0:
        r = (-r[0], -r[1])

    # 3. Compute perpendicular vector p = (-r_y, r_x)
    p = (-r[1], r[0])
    # Determine if this is a left or right corner based on the receipt
    # corner's x.
    if receipt_corner[0] < img_w / 2.0:
        # Left corner: ensure p_x is negative.
        if p[0] > 0:
            p = (-p[0], -p[1])
    else:
        # Right corner: ensure p_x is positive.
        if p[0] < 0:
            p = (-p[0], -p[1])

    # 4. Compute vertical distance d_v.
    # For a top corner, we want I + d_v*r to reach y=0.
    # For a bottom corner, to reach y=img_h - 1.
    if corner_position == "top":
        d_v = -I[1] / r[1] if r[1] != 0 else 0
    else:
        d_v = ((img_h - 1) - I[1]) / r[1] if r[1] != 0 else 0

    # 5. Compute horizontal distance d_h.
    # For left corners, we want I + d_h*p to reach x=0.
    # For right corners, to reach x=img_w - 1.
    if receipt_corner[0] < img_w / 2.0:
        d_h = -I[0] / p[0] if p[0] != 0 else 0
    else:
        d_h = ((img_w - 1) - I[0]) / p[0] if p[0] != 0 else 0

    # 6. Define the polygon.
    pt1 = I
    pt2 = (I[0] + d_v * r[0], I[1] + d_v * r[1])
    pt3 = (pt2[0] + d_h * p[0], pt2[1] + d_h * p[1])
    pt4 = (I[0] + d_h * p[0], I[1] + d_h * p[1])

    return [pt1, pt2, pt3, pt4]


def crop_polygon_region(
    image: Image, poly: List[Tuple[float, float]]
) -> Image:
    """
    Given a polygon (a list of (x,y) coordinates), computes its axis-aligned bounding box
    and crops that region from the image.

    The bounding box is computed by finding the minimum and maximum x and y values
    among the polygon points, using floor for the minimums and ceil for the maximums.
    """
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]

    left = int(math.floor(min(xs)))
    top = int(math.floor(min(ys)))
    right = int(math.ceil(max(xs)))
    bottom = int(math.ceil(max(ys)))

    return image.crop((left, top, right, bottom))


def extract_and_save_corner_windows(
    image: Image,
    receipt_box_corners: List[Tuple[float, float]],
    offset_distance: float,
    max_dim: int,
) -> Dict[str, Dict[str, object]]:
    """
    Extract and downscale window regions from each receipt corner.

    Given an input image and the four corners of a receipt (ordered as
    [top_left, top_right, bottom_right, bottom_left]), this function computes a window
    region adjacent to each corner using predefined geometric rules. For each corner, it:
      1. Determines an inner offset point by moving the receipt corner inward by N pixels.
      2. Computes a quadrilateral (window polygon) by extending this inner point vertically
         and horizontally until it reaches the corresponding image boundaries.
      3. Crops the resulting polygon region from the original image.
      4. Down-scales the cropped window so that its largest dimension does not exceed max_dim pixels,
         while preserving the aspect ratio.

    Parameters:
        image (PIL.Image): The source image containing the receipt.
        receipt_box_corners (List[Tuple[float, float]]): A list of four (x, y) coordinates representing
            the corners of the receipt. The order should be:
            [top_left, top_right, bottom_right, bottom_left].
        offset_distance (float): The offset distance (in pixels) used to move each corner inward when computing the window.
        max_dim (int): The maximum allowed dimension (width or height) for the downscaled window images.

    Returns:
        Dict[str, Dict[str, object]]:
            A dictionary with keys "top_left", "top_right", "bottom_right", and "bottom_left".
            Each key maps to another dictionary containing:
                - 'image': The downscaled window as a PIL Image object.
                - 'width': The width (in pixels) of the downscaled window.
                - 'height': The height (in pixels) of the downscaled window.

    Example:
        >>> windows = extract_and_save_corner_windows(image, receipt_corners, 200, 512)
        >>> top_left_window = windows["top_left"]
        >>> top_left_window["image"].show()
    """
    img_size = image.size
    c0, c1, c2, c3 = receipt_box_corners

    # Compute window polygons for each corner
    poly_tl = window_rectangle_for_corner(
        receipt_corner=c0,
        adj1=c1,
        adj2=c3,
        edge_direction=(c3[0] - c0[0], c3[1] - c0[1]),
        offset_distance=offset_distance,
        image_size=img_size,
        corner_position="top",
    )
    poly_tr = window_rectangle_for_corner(
        receipt_corner=c1,
        adj1=c0,
        adj2=c2,
        edge_direction=(c2[0] - c1[0], c2[1] - c1[1]),
        offset_distance=offset_distance,
        image_size=img_size,
        corner_position="top",
    )
    poly_br = window_rectangle_for_corner(
        receipt_corner=c2,
        adj1=c1,
        adj2=c3,
        edge_direction=(c2[0] - c1[0], c2[1] - c1[1]),
        offset_distance=offset_distance,
        image_size=img_size,
        corner_position="bottom",
    )
    poly_bl = window_rectangle_for_corner(
        receipt_corner=c3,
        adj1=c0,
        adj2=c2,
        edge_direction=(c3[0] - c0[0], c3[1] - c0[1]),
        offset_distance=offset_distance,
        image_size=img_size,
        corner_position="bottom",
    )

    # Crop each region from the original image.
    cropped_tl = crop_polygon_region(image, poly_tl)
    cropped_tr = crop_polygon_region(image, poly_tr)
    cropped_br = crop_polygon_region(image, poly_br)
    cropped_bl = crop_polygon_region(image, poly_bl)

    # Helper function: down_sample image so that its largest dimension is
    # max_dim.
    def down_sample(image: Image, max_dim: int) -> Tuple[Image, int, int]:
        width, height = image.size
        scale = min(max_dim / width, max_dim / height, 1.0)
        new_width = int(round(width * scale))
        new_height = int(round(height * scale))
        resized = image.resize((new_width, new_height), Resampling.LANCZOS)
        return resized, new_width, new_height

    image_tl, width_tl, height_tl = down_sample(cropped_tl, max_dim)
    image_tr, width_tr, height_tr = down_sample(cropped_tr, max_dim)
    image_br, width_br, height_br = down_sample(cropped_br, max_dim)
    image_bl, width_bl, height_bl = down_sample(cropped_bl, max_dim)

    # Since the inner corner I is the first element of each polygon, include
    # it in the output.
    return {
        "top_left": {
            "image": image_tl,
            "width": width_tl,
            "height": height_tl,
            "inner_corner": poly_tl[0],
        },
        "top_right": {
            "image": image_tr,
            "width": width_tr,
            "height": height_tr,
            "inner_corner": poly_tr[0],
        },
        "bottom_right": {
            "image": image_br,
            "width": width_br,
            "height": height_br,
            "inner_corner": poly_br[0],
        },
        "bottom_left": {
            "image": image_bl,
            "width": width_bl,
            "height": height_bl,
            "inner_corner": poly_bl[0],
        },
    }
