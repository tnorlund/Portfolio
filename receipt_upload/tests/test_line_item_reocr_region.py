"""Unit tests for the items-zone re-OCR region computation.

The region math mirrors the MCP server's compute_reocr_region: full
width, vertical span of zone words plus padding, receipt-relative ->
full-image Vision coordinates via the four-corner homography.
"""

from types import SimpleNamespace

from receipt_upload.line_items.reocr import items_zone_reocr_region


def _identity_receipt(width: int = 100, height: int = 200):
    """A receipt occupying the whole image (identity homography)."""
    return SimpleNamespace(
        top_left={"x": 0.0, "y": 1.0},
        top_right={"x": 1.0, "y": 1.0},
        bottom_right={"x": 1.0, "y": 0.0},
        bottom_left={"x": 0.0, "y": 0.0},
        width=width,
        height=height,
    )


def test_empty_zone_returns_none() -> None:
    assert items_zone_reocr_region([], _identity_receipt(), 100, 200) is None


def test_identity_receipt_region_covers_zone_span() -> None:
    words = [
        {"y_mid": 0.60, "h": 0.02},
        {"y_mid": 0.40, "h": 0.02},
    ]
    region = items_zone_reocr_region(
        words, _identity_receipt(), 100, 200, padding=0.05
    )
    assert region is not None
    # Full width
    assert region["x"] == 0.0
    assert region["width"] == 1.0
    # Vertical span 0.39..0.61 plus 0.05 padding = 0.34..0.66 in receipt
    # space; identity mapping keeps it there (Vision y-flip cancels).
    assert 0.30 <= region["y"] <= 0.38
    assert 0.28 <= region["height"] <= 0.38


def test_region_clamped_to_unit_square() -> None:
    words = [{"y_mid": 0.02, "h": 0.06}, {"y_mid": 0.99, "h": 0.06}]
    region = items_zone_reocr_region(
        words, _identity_receipt(), 100, 200, padding=0.10
    )
    assert region is not None
    assert 0.0 <= region["y"]
    assert region["y"] + region["height"] <= 1.0 + 1e-9
    assert region["x"] >= 0.0
    assert region["x"] + region["width"] <= 1.0 + 1e-9
