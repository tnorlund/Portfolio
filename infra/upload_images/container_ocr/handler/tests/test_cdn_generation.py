"""Tests for CDN crop generation and failure handling in OCRProcessor.

Covers the regression that left receipts persisted with null CDN keys
(blank images) when the pre-uploaded warped crop was missing:
  1. CDN key dict -> entity field mapping
  2. in-process crop fallback when the warped upload is unavailable
  3. graceful None when no fallback source exists
  4. happy path uses the downloaded warped crop
  5. unexpected errors are NOT swallowed
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

_container_ocr_dir = str(Path(__file__).resolve().parents[2])
if _container_ocr_dir not in sys.path:
    sys.path.insert(0, _container_ocr_dir)

import pytest
from botocore.exceptions import ClientError
from handler import ocr_processor as _ocr_mod
from handler.ocr_processor import OCRProcessor
from PIL import Image as PIL_Image
from receipt_dynamo.entities import Receipt

_ACCESS_DENIED = ClientError(
    {"Error": {"Code": "AccessDenied", "Message": "denied"}}, "GetObject"
)


def _make_receipt(width: int = 64, height: int = 128) -> Receipt:
    return Receipt(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=1,
        width=width,
        height=height,
        timestamp_added=datetime(2026, 1, 1, tzinfo=timezone.utc),
        raw_s3_bucket="bucket",
        raw_s3_key="receipts/img/IMG_1-uuid_RECEIPT_00001.png",
        top_left={"x": 0.1, "y": 0.9},
        top_right={"x": 0.9, "y": 0.9},
        bottom_right={"x": 0.9, "y": 0.1},
        bottom_left={"x": 0.1, "y": 0.1},
    )


def _processor() -> OCRProcessor:
    # Skip __init__ (it builds a real DynamoClient); the helpers under test
    # only need site_bucket and the staticmethod perspective solver.
    proc = OCRProcessor.__new__(OCRProcessor)
    proc.site_bucket = "site-bucket"
    return proc


def test_apply_cdn_keys_maps_all_fields() -> None:
    receipt = _make_receipt()
    keys = {
        "jpeg_full": "a.jpg",
        "webp_full": "a.webp",
        "avif_full": "a.avif",
        "jpeg_thumbnail": "t.jpg",
        "webp_thumbnail": "t.webp",
        "avif_thumbnail": "t.avif",
        "jpeg_small": "s.jpg",
        "webp_small": "s.webp",
        "avif_small": "s.avif",
        "jpeg_medium": "m.jpg",
        "webp_medium": "m.webp",
        "avif_medium": "m.avif",
    }
    OCRProcessor._apply_cdn_keys(receipt, keys, "site-bucket")
    assert receipt.cdn_s3_bucket == "site-bucket"
    assert receipt.cdn_s3_key == "a.jpg"
    assert receipt.cdn_avif_s3_key == "a.avif"
    assert receipt.cdn_thumbnail_webp_s3_key == "t.webp"
    assert receipt.cdn_small_s3_key == "s.jpg"
    assert receipt.cdn_medium_avif_s3_key == "m.avif"


def test_apply_cdn_keys_sets_missing_keys_to_none() -> None:
    receipt = _make_receipt()
    OCRProcessor._apply_cdn_keys(
        receipt, {"jpeg_full": "a.jpg"}, "site-bucket"
    )
    assert receipt.cdn_s3_bucket == "site-bucket"
    assert receipt.cdn_s3_key == "a.jpg"
    assert receipt.cdn_avif_s3_key is None
    assert receipt.cdn_medium_webp_s3_key is None


def test_obtain_crop_falls_back_to_in_process_generation() -> None:
    proc = _processor()
    receipt = _make_receipt(width=64, height=128)
    original = PIL_Image.new("RGB", (400, 600), "white")
    with patch.object(
        _ocr_mod, "download_image_from_s3", side_effect=_ACCESS_DENIED
    ):
        crop = proc._obtain_receipt_crop(
            receipt=receipt,
            source_bucket="bucket",
            raw_s3_key="missing.png",
            image_id="img",
            receipt_id=1,
            original_image=original,
            image_width=400,
            image_height=600,
        )
    assert crop is not None
    assert crop.size == (64, 128)


def test_obtain_crop_returns_none_without_original() -> None:
    proc = _processor()
    with patch.object(
        _ocr_mod, "download_image_from_s3", side_effect=_ACCESS_DENIED
    ):
        crop = proc._obtain_receipt_crop(
            receipt=_make_receipt(),
            source_bucket="bucket",
            raw_s3_key="missing.png",
            image_id="img",
            receipt_id=1,
            original_image=None,
            image_width=400,
            image_height=600,
        )
    assert crop is None


def test_obtain_crop_uses_downloaded_warped_when_available(
    tmp_path: Path,
) -> None:
    proc = _processor()
    warped_path = tmp_path / "warped.png"
    PIL_Image.new("RGB", (50, 120), "white").save(warped_path)
    with patch.object(
        _ocr_mod, "download_image_from_s3", return_value=warped_path
    ):
        crop = proc._obtain_receipt_crop(
            receipt=_make_receipt(),
            source_bucket="bucket",
            raw_s3_key="k.png",
            image_id="img",
            receipt_id=1,
            original_image=None,
            image_width=400,
            image_height=600,
        )
    assert crop is not None
    assert crop.size == (50, 120)


def test_obtain_crop_does_not_swallow_unexpected_errors() -> None:
    proc = _processor()
    with patch.object(
        _ocr_mod, "download_image_from_s3", side_effect=KeyError("boom")
    ):
        with pytest.raises(KeyError):
            proc._obtain_receipt_crop(
                receipt=_make_receipt(),
                source_bucket="bucket",
                raw_s3_key="k.png",
                image_id="img",
                receipt_id=1,
                original_image=PIL_Image.new("RGB", (10, 10)),
                image_width=400,
                image_height=600,
            )
