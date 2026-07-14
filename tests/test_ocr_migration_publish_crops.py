"""Unit tests for the post-migration crop publisher (pure parts only).

No AWS, no network: warp-coefficient math against a known square, the CDN
key -> Receipt field mapping, orphan detection from S3 listings, the
--allow-aws guardrail, and invalidation chunking.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PIL import Image as PIL_Image

from scripts import ocr_migration_publish_crops as pub

IMG = "0e2f8a2c-1111-4222-8333-944445555666"


def make_receipt(width: int, height: int, corners: dict) -> SimpleNamespace:
    """Duck-typed receipt: normalized corners (bottom-left origin) + dims."""
    return SimpleNamespace(width=width, height=height, **corners)


def apply_coeffs(coeffs, x: float, y: float) -> tuple[float, float]:
    """PIL PERSPECTIVE forward map: receipt (dst) pixel -> image (src) pixel."""
    a, b, c, d, e, f, g, h = coeffs
    den = 1.0 + g * x + h * y
    return (a * x + b * y + c) / den, (d * x + e * y + f) / den


def test_perspective_coeffs_known_square():
    # A receipt occupying the axis-aligned square x in [0.25, 0.75],
    # y in [0.25, 0.75] (normalized, bottom-left origin) of a 200x400 image.
    receipt = make_receipt(
        101,
        201,
        {
            "top_left": {"x": 0.25, "y": 0.75},
            "top_right": {"x": 0.75, "y": 0.75},
            "bottom_left": {"x": 0.25, "y": 0.25},
            "bottom_right": {"x": 0.75, "y": 0.25},
        },
    )
    coeffs = pub.perspective_coeffs(receipt, 200, 400)
    # Receipt corners (PIL pixel space) must land on the square's corners in
    # image PIL space; note the y-flip: normalized y=0.75 -> pixel y=100.
    assert apply_coeffs(coeffs, 0, 0) == pytest.approx((50.0, 100.0))
    assert apply_coeffs(coeffs, 100, 0) == pytest.approx((150.0, 100.0))
    assert apply_coeffs(coeffs, 100, 200) == pytest.approx((150.0, 300.0))
    assert apply_coeffs(coeffs, 0, 200) == pytest.approx((50.0, 300.0))


def test_warp_receipt_crop_extracts_known_region():
    # White image with a solid red rectangle covering the receipt region
    # (with margin, since the crop's edge pixels sample AT the receipt
    # corners); the warped crop must be entirely red (and the right size).
    image = PIL_Image.new("RGB", (100, 100), "white")
    image.paste((255, 0, 0), (15, 5, 90, 95))  # PIL box, top-left origin
    receipt = make_receipt(
        60,
        80,
        {
            # bottom-left-origin normalized: pixel y=10 -> 0.9, y=90 -> 0.1
            "top_left": {"x": 0.20, "y": 0.90},
            "top_right": {"x": 0.80, "y": 0.90},
            "bottom_left": {"x": 0.20, "y": 0.10},
            "bottom_right": {"x": 0.80, "y": 0.10},
        },
    )
    crop = pub.warp_receipt_crop(image, receipt)
    assert crop.size == (60, 80)
    for xy in ((0, 0), (59, 0), (59, 79), (0, 79), (30, 40)):
        assert crop.getpixel(xy) == (255, 0, 0)


def test_cdn_key_field_mapping_matches_ingestion():
    base = f"assets/{IMG}/3"
    keys = pub.expected_cdn_keys(base)
    assert len(keys) == 12
    expression, values = pub.build_cdn_update(keys, "site-bucket")
    field_to_value = {
        name.strip(): values[placeholder.strip()]
        for name, placeholder in (
            part.split("=") for part in expression.removeprefix("SET ").split(",")
        )
    }
    assert field_to_value["cdn_s3_bucket"] == {"S": "site-bucket"}
    assert field_to_value["cdn_s3_key"] == {"S": f"{base}.jpg"}
    assert field_to_value["cdn_webp_s3_key"] == {"S": f"{base}.webp"}
    assert field_to_value["cdn_avif_s3_key"] == {"S": f"{base}.avif"}
    assert field_to_value["cdn_thumbnail_s3_key"] == {"S": f"{base}_thumbnail.jpg"}
    assert field_to_value["cdn_small_webp_s3_key"] == {"S": f"{base}_small.webp"}
    assert field_to_value["cdn_medium_avif_s3_key"] == {"S": f"{base}_medium.avif"}
    assert len(field_to_value) == 13  # bucket + 12 keys, nothing else

    # A missing format (e.g. AVIF encoder unavailable) serializes as NULL,
    # exactly like CDNFieldsMixin.cdn_fields_to_dynamodb_item.
    partial = dict(keys)
    partial["avif_full"] = None
    _, null_values = pub.build_cdn_update(partial, "site-bucket")
    assert {"NULL": True} in null_values.values()


def test_orphan_detection_from_listing():
    listed = [
        f"assets/{IMG}/1.jpg",
        f"assets/{IMG}/1_thumbnail.webp",
        f"assets/{IMG}/2.avif",  # receipt 2 no longer exists -> orphan
        f"assets/{IMG}/2_medium.jpg",  # orphan
        f"assets/{IMG}/12.jpg",  # receipt 12 gone; must not match "1"
        f"assets/{IMG}/notes.txt",  # unrecognized: reported, never deleted
        "assets/other-image/2.jpg",  # different image: ignored entirely
    ]
    orphans, unrecognized = pub.find_orphan_asset_keys(listed, IMG, {1})
    assert orphans == sorted(
        [
            f"assets/{IMG}/2.avif",
            f"assets/{IMG}/2_medium.jpg",
            f"assets/{IMG}/12.jpg",
        ]
    )
    assert unrecognized == [f"assets/{IMG}/notes.txt"]


def test_guardrail_refuses_real_write_without_allow_aws(tmp_path):
    with pytest.raises(SystemExit, match="REFUSING"):
        pub.ensure_write_allowed(dry_run=False, allow_aws=False)
    # Dry-run and explicit opt-in are both fine.
    pub.ensure_write_allowed(dry_run=True, allow_aws=False)
    pub.ensure_write_allowed(dry_run=False, allow_aws=True)

    # The CLI refuses BEFORE touching AWS or pulumi.
    images = tmp_path / "images.txt"
    images.write_text(f"{IMG}\n")
    with pytest.raises(SystemExit, match="REFUSING"):
        pub.main(
            [
                "--table-name",
                "ReceiptsTable-dev",
                "--images",
                str(images),
                "--raw-dir",
                str(tmp_path),
                "--report",
                str(tmp_path / "report.json"),
                "--site-bucket",
                "site-bucket",
            ]
        )


def test_invalidation_paths_chunked_at_cloudfront_limit():
    paths = [f"/assets/img-{i}/*" for i in range(6500)]
    chunks = list(pub.chunk_paths(paths))
    assert [len(c) for c in chunks] == [3000, 3000, 500]
    assert [p for c in chunks for p in c] == paths
