# infra/lambda_layer/python/test/integration/test__corner_process.py
import math

import pytest
from PIL import Image

from receipt_dynamo.data._corner_process import (
    crop_polygon_region,
    extract_and_save_corner_windows,
    normalize,
    offset_corner_inward,
    window_rectangle_for_corner,
)


@pytest.mark.integration
def test_normalize():
    """
    Checks the normalize function with typical and edge-case vectors.
    """
    # A normal vector
    v = (3.0, 4.0)
    result = normalize(v)
    expected_mag = 1.0
    # Magnitude should be ~1
    mag = math.hypot(result[0], result[1])
    assert (
        abs(mag - expected_mag) < 1e-8
    ), f"Expected unit vector, got magnitude {mag}"
    # Direction check
    # (3,4) normalized => (0.6, 0.8)
    assert abs(result[0] - 0.6) < 1e-8, "X part incorrect"
    assert abs(result[1] - 0.8) < 1e-8, "Y part incorrect"

    # A near-zero vector
    tiny = (1e-12, -1e-12)
    result2 = normalize(tiny)
    # Because magnitude ~ 1.4142e-12 < 1e-8 => we expect (0, 0)
    assert result2 == (
        0.0,
        0.0,
    ), f"Expected (0,0) for near-zero vector, got {result2}"


@pytest.mark.integration
def test_offset_corner_inward():
    """
    Tests offset_corner_inward by giving a corner with two adjacent corners
    and verifying the result is inward by 'offset_distance' along sum of
    directions.
    """
    # Suppose we have a corner at (10,10), and two adjacent corners at (0,10)
    # and (10,0).
    corner = (10.0, 10.0)
    adj1 = (0.0, 10.0)
    adj2 = (10.0, 0.0)
    offset_distance = 5.0
    # We'll compute the inward offset
    inward_pt = offset_corner_inward(corner, adj1, adj2, offset_distance)
    # The direction from corner to adj1 is (-10,0), normalized => approx (-1,0)
    # The direction from corner to adj2 is (0,-10), normalized => approx (0,-1)
    # Summation => (-1,0)+(0,-1) = (-1,-1), magnitude sqrt(2).
    # Then we scale by offset_distance => 5 * (-1, -1) => (-5, -5)
    # and add to corner => (10,10)+(-5,-5) => (5,5).
    # So we expect roughly (5,5).
    assert abs(inward_pt[0] - 5.0) < 1e-6, f"X mismatch, got {inward_pt[0]}"
    assert abs(inward_pt[1] - 5.0) < 1e-6, f"Y mismatch, got {inward_pt[1]}"


@pytest.mark.integration
def test_window_rectangle_for_corner():
    """
    Tests window_rectangle_for_corner by creating a known scenario with
    an image size, a corner, and verifying the polygon extends to edges.
    We'll do TWO sub-scenarios:
      1) corner_position="top" (already tested)
      2) corner_position="bottom" (to ensure coverage of the bottom-flip
         branch).
    """
    # ---------------- Scenario 1: Top corner ----------------
    img_w, img_h = 100, 50
    corner_top = (10.0, 0.0)
    adj1_top = (0.0, 0.0)
    adj2_top = (10.0, 10.0)
    edge_dir_top = (
        adj2_top[0] - corner_top[0],
        adj2_top[1] - corner_top[1],
    )  # => (0, 10)
    offset_dist = 5.0

    poly_top = window_rectangle_for_corner(
        receipt_corner=corner_top,
        adj1=adj1_top,
        adj2=adj2_top,
        edge_direction=edge_dir_top,
        offset_distance=offset_dist,
        image_size=(img_w, img_h),
        corner_position="top",
    )
    xs_top = [p[0] for p in poly_top]
    ys_top = [p[1] for p in poly_top]
    assert (
        len(poly_top) == 4
    ), "Should return 4 corner points for 'top' scenario."
    # Since corner_position="top", we expect some point near y=0
    assert (
        min(ys_top) >= -1e-6
    ), f"Polygon extends above top edge? min y={min(ys_top)}"
    # No point should exceed y=img_h
    assert (
        max(ys_top) <= img_h + 1e-6
    ), f"Polygon extends below bottom edge? max y={max(ys_top)}"
    # Similarly check x-bounds
    assert (
        min(xs_top) >= -1e-6 and max(xs_top) <= img_w + 1e-6
    ), "Polygon out of bounds in x for 'top' scenario."

    # ---------------- Scenario 2: Bottom corner ----------------
    # We'll pick a corner near the bottom of the image. We want edge_direction
    # to have negative y, so that the code triggers: "elif corner_position ==
    # 'bottom' and r[1] < 0: r = (-r[0], -r[1])".
    corner_bottom = (10.0, 49.0)
    # Adjacents that make 'edge_direction' negative in y
    # e.g. adj2 below the corner => (10, 40)
    adj1_bottom = (0.0, 49.0)
    adj2_bottom = (10.0, 40.0)  # lower than corner => negative y
    edge_dir_bottom = (
        adj2_bottom[0] - corner_bottom[0],
        adj2_bottom[1] - corner_bottom[1],
    )
    # => (0, -9)

    poly_bottom = window_rectangle_for_corner(
        receipt_corner=corner_bottom,
        adj1=adj1_bottom,
        adj2=adj2_bottom,
        edge_direction=edge_dir_bottom,
        offset_distance=offset_dist,
        image_size=(img_w, img_h),
        corner_position="bottom",
    )
    xs_bot = [p[0] for p in poly_bottom]
    ys_bot = [p[1] for p in poly_bottom]
    assert (
        len(poly_bottom) == 4
    ), "Should return 4 corner points for 'bottom' scenario."
    # For corner_position="bottom", we expect points near y=img_h (i.e. 50)
    # but definitely not going beyond. Let's allow slight float tolerance.
    assert (
        max(ys_bot) <= img_h + 1e-6
    ), f"Polygon extends below bottom edge? max y={max(ys_bot)}"
    # And it shouldn't go above y=0 by a large margin
    assert (
        min(ys_bot) >= -1e-6
    ), f"Polygon extends above top edge? min y={min(ys_bot)}"
    # Check x-bounds
    assert (
        min(xs_bot) >= -1e-6 and max(xs_bot) <= img_w + 1e-6
    ), "Polygon out of bounds in x for 'bottom' scenario."


@pytest.mark.integration
def test_crop_polygon_region():
    """
    Tests that crop_polygon_region returns the bounding box of a polygon
    within the given image.
    """
    # Create a small 10x10 image (solid color) for testing.
    img = Image.new("RGB", (10, 10), color="blue")
    # Define a polygon covering from (2,2) to (7,5)
    poly = [(2, 2), (7, 2), (7, 5), (2, 5)]
    cropped = crop_polygon_region(img, poly)
    # The bounding box => left=2, top=2, right=7, bottom=5 => width=5, height=3
    assert cropped.size == (5, 3), f"Expected 5x3, got {cropped.size}"
    # Check a pixel inside => (0,0) in cropped => (2,2) in original =>
    # color=blue
    pix = cropped.getpixel((0, 0))
    assert pix == (0, 0, 255), f"Should be blue pixel, got {pix}"


@pytest.mark.integration
def test_extract_and_save_corner_windows():
    """
    An end-to-end test for extract_and_save_corner_windows that:
      1) Creates a test image
      2) Defines a "receipt" bounding box
      3) Calls extract_and_save_corner_windows
      4) Verifies the returned dictionary images are downscaled,
         and corners are inside the image.
    """
    # 1) Create a test image, say 200x100
    img_w, img_h = 200, 100
    test_img = Image.new("RGB", (img_w, img_h), color="white")

    # 2) Suppose the receipt corners are basically near each corner of the
    # image:
    top_left = (0.0, 0.0)
    top_right = (199.0, 0.0)
    bottom_right = (199.0, 99.0)
    bottom_left = (0.0, 99.0)
    receipt_corners = [top_left, top_right, bottom_right, bottom_left]

    # We'll define offset_distance=10, max_dim=50
    offset_distance = 10
    max_dim = 50

    # 3) Call extract_and_save_corner_windows
    windows = extract_and_save_corner_windows(
        image=test_img,
        receipt_box_corners=receipt_corners,
        offset_distance=offset_distance,
        max_dim=max_dim,
    )

    # 4) Check structure => keys = top_left, top_right, bottom_right,
    # bottom_left
    for k in ["top_left", "top_right", "bottom_right", "bottom_left"]:
        assert k in windows, f"Missing corner key {k}"
        corner_data = windows[k]
        # corner_data => {'image': PIL.Image, 'width': int, 'height': int,
        # 'inner_corner': (x,y)}
        assert "image" in corner_data
        assert "width" in corner_data
        assert "height" in corner_data
        assert "inner_corner" in corner_data

        # Check that largest dimension <= max_dim
        w = corner_data["width"]
        h = corner_data["height"]
        assert (
            w <= max_dim and h <= max_dim
        ), f"{k} window not downscaled properly"

        # inner_corner must be within image bounds
        ix, iy = corner_data["inner_corner"]
        assert 0 <= ix < img_w, f"Inner corner {ix} not in [0,{img_w})"
        assert 0 <= iy < img_h, f"Inner corner {iy} not in [0,{img_h})"

    # Optionally, you can also check if windows['top_left']['image'] is
    # not empty, etc.
    # This test ensures your corner-window extraction logic runs end-to-end.
