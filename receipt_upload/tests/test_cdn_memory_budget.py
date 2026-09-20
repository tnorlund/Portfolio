"""Large phone exports retain JPEG/WebP without exhausting the AVIF encoder."""

from unittest.mock import Mock

import pytest
from PIL import Image

from receipt_upload import utils


def test_large_export_retains_full_jpeg_webp_and_responsive_avif(monkeypatch):
    image = Image.new("RGB", (2001, 2000))
    saved = []
    uploaded = Mock()
    monkeypatch.setattr(utils, "client", lambda service: uploaded)
    monkeypatch.setattr(
        Image, "registered_extensions", lambda: {".avif": "AVIF"}
    )

    def save(self, buffer, format, **options):
        saved.append((self.size, format, options))
        buffer.write(b"encoded")

    monkeypatch.setattr(Image.Image, "save", save)
    keys = utils.upload_all_cdn_formats(image, "site", "assets/test")
    assert keys["jpeg"] == "assets/test.jpg"
    assert keys["webp"] == "assets/test.webp"
    assert keys["avif"] is None
    assert all(
        keys[f"avif_{size}"] for size in ("thumbnail", "small", "medium")
    )
    assert not any(
        size == image.size and fmt == "AVIF" for size, fmt, _ in saved
    )
    assert all(
        opts["max_threads"] == 1 for _, fmt, opts in saved if fmt == "AVIF"
    )


def test_oversized_avif_fails_before_creating_an_aws_client(monkeypatch):
    client = Mock()
    monkeypatch.setattr(utils, "client", client)
    with pytest.raises(utils.AVIFError, match="4 megapixels"):
        utils.upload_avif_to_s3(
            Mock(width=2001, height=2000), "site", "a.avif"
        )
    client.assert_not_called()


def test_basic_avif_fallback_also_bounds_threads(monkeypatch):
    uploaded = Mock()
    monkeypatch.setattr(utils, "client", lambda service: uploaded)
    monkeypatch.setattr(
        Image, "registered_extensions", lambda: {".avif": "AVIF"}
    )
    options_seen = []

    def save(self, buffer, **options):
        options_seen.append(options)
        if "codec" in options:
            raise ValueError("advanced options unavailable")
        buffer.write(b"avif")

    monkeypatch.setattr(Image.Image, "save", save)
    utils.upload_avif_to_s3(Image.new("RGB", (20, 20)), "site", "a.avif")
    assert len(options_seen) == 2
    assert all(options["max_threads"] == 1 for options in options_seen)
    uploaded.put_object.assert_called_once()
