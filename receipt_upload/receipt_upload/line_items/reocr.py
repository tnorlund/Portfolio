"""Items-zone regional re-OCR region computation (pure math, bundleable).

Ports the MCP server's compute_reocr_region logic for the ingest-stage
Lambda: the region is the vertical span of the ITEMS-zone words (plus
padding) at full width, transformed from receipt-relative space to
full-image Vision coordinates via the same four-corner homography the
FIRST_PASS warp used. The math mirrors
scripts/receipt_mcp_server.py::_receipt_point_to_image verbatim; depends
only on receipt_upload.geometry.transformations (math-only module).
"""

from __future__ import annotations

from typing import Any

from receipt_upload.geometry.transformations import find_perspective_coeffs


def _receipt_point_to_image(
    rx: float,
    ry: float,
    coeffs: list[float],
    receipt_width: int,
    receipt_height: int,
    image_width: int,
    image_height: int,
) -> tuple[float, float]:
    """Projective forward transform: receipt-relative -> full-image Vision."""
    x_rct = rx * (receipt_width - 1)
    y_rct = (1.0 - ry) * (receipt_height - 1)

    a, b, c, d, e, f, g, h = coeffs
    denom = 1.0 + g * x_rct + h * y_rct
    if abs(denom) < 1e-12:
        return 0.5, 0.5

    x_img = (a * x_rct + b * y_rct + c) / denom
    y_img = (d * x_rct + e * y_rct + f) / denom

    ix = x_img / image_width
    iy = 1.0 - (y_img / image_height)
    return max(0.0, min(1.0, ix)), max(0.0, min(1.0, iy))


def items_zone_reocr_region(
    zone_words: list[dict],
    receipt: Any,
    image_width: int,
    image_height: int,
    padding: float = 0.05,
) -> dict | None:
    """Full-image Vision region covering the ITEMS zone.

    ``zone_words`` carry receipt-relative geometry as produced by the
    line-item processor: {"y_mid": float, "h": float}. ``receipt`` is the
    Receipt entity (four corner attributes in image-normalised space,
    width/height in pixels). Returns {"x","y","width","height"} in
    full-image Vision normalised coordinates, or None when the span
    cannot be computed.
    """
    if not zone_words:
        return None

    min_y = min(w["y_mid"] - w["h"] / 2 for w in zone_words)
    max_y = max(w["y_mid"] + w["h"] / 2 for w in zone_words)
    # Full width: Vision reads better with horizontal context; constrain
    # only the vertical range (same policy as the MCP tool).
    padded_x = 0.0
    padded_right = 1.0
    padded_y = max(0.0, min_y - padding)
    padded_top = min(1.0, max_y + padding)

    src_points = [
        (
            receipt.top_left["x"] * image_width,
            (1.0 - receipt.top_left["y"]) * image_height,
        ),
        (
            receipt.top_right["x"] * image_width,
            (1.0 - receipt.top_right["y"]) * image_height,
        ),
        (
            receipt.bottom_right["x"] * image_width,
            (1.0 - receipt.bottom_right["y"]) * image_height,
        ),
        (
            receipt.bottom_left["x"] * image_width,
            (1.0 - receipt.bottom_left["y"]) * image_height,
        ),
    ]
    dst_points = [
        (0.0, 0.0),
        (float(receipt.width - 1), 0.0),
        (float(receipt.width - 1), float(receipt.height - 1)),
        (0.0, float(receipt.height - 1)),
    ]
    coeffs = find_perspective_coeffs(src_points, dst_points)

    img_corners = [
        _receipt_point_to_image(
            rx,
            ry,
            coeffs,
            receipt.width,
            receipt.height,
            image_width,
            image_height,
        )
        for rx, ry in (
            (padded_x, padded_y),
            (padded_right, padded_y),
            (padded_x, padded_top),
            (padded_right, padded_top),
        )
    ]

    img_min_x = max(0.0, min(c[0] for c in img_corners))
    img_min_y = max(0.0, min(c[1] for c in img_corners))
    img_max_x = min(1.0, max(c[0] for c in img_corners))
    img_max_y = min(1.0, max(c[1] for c in img_corners))
    if img_max_x <= img_min_x or img_max_y <= img_min_y:
        return None
    return {
        "x": round(img_min_x, 6),
        "y": round(img_min_y, 6),
        "width": round(img_max_x - img_min_x, 6),
        "height": round(img_max_y - img_min_y, 6),
    }
