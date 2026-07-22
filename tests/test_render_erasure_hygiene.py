"""Adversarial tests for render evidence erasure regressions (#1216)."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

from PIL import Image

from scripts import render_synthetic_receipts as rsr
from synthesis_loop import glyph_review


def test_phrase_logo_placement_drops_only_matched_span() -> None:
    phrase = {
        "text": "COSTCO",
        "bbox": [100.0, 920.0, 350.0, 960.0],
        "labels": ["MERCHANT_NAME"],
    }
    address_number = {
        "text": "123",
        "bbox": [370.0, 920.0, 450.0, 960.0],
        "labels": ["ADDRESS_LINE"],
    }
    address_street = {
        "text": "MAIN",
        "bbox": [470.0, 920.0, 650.0, 960.0],
        "labels": ["ADDRESS_LINE"],
    }
    receipt = {"words": [phrase, address_number, address_street]}
    config = SimpleNamespace(width=760, height=1800, margin=24)

    placed = rsr._phrase_logo_placement(
        receipt,
        ["COSTCO"],
        config=config,
        logo=Image.new("L", (240, 60), 0),
        coord_max=1000.0,
        extend_left=False,
    )

    assert placed is not None
    dropped, _bbox = placed
    assert [word["text"] for word in dropped] == ["COSTCO"]


def test_glyph_review_preserves_all_word_labels_in_stable_order(
    monkeypatch, tmp_path
) -> None:
    captured: dict = {}

    fake_renderer = ModuleType("render_synthetic_receipts")
    fake_renderer.cached_font_profile = lambda *_args, **_kwargs: None
    fake_renderer.section_scale_for_merchant = lambda *_args: {}
    fake_renderer.merchant_typography = lambda *_args: {
        "bitmap_font": "regular.npz",
        "bitmap_thin": "regular.npz",
    }

    def capture_render(receipt, _atlas, *, path, **_kwargs) -> None:
        captured.update(receipt)
        Image.new("RGB", (20, 40), "white").save(path)

    fake_renderer._render_cached_hybrid = capture_render
    monkeypatch.setitem(sys.modules, "render_synthetic_receipts", fake_renderer)

    word = SimpleNamespace(
        receipt_id=1,
        line_id=2,
        word_id=3,
        text="MAIN",
        top_left={"x": 0.1, "y": 0.2},
        bottom_right={"x": 0.3, "y": 0.25},
    )
    labels = [
        SimpleNamespace(
            receipt_id=1,
            line_id=2,
            word_id=3,
            label="MERCHANT_NAME",
            validation_status="PENDING",
        ),
        SimpleNamespace(
            receipt_id=1,
            line_id=2,
            word_id=3,
            label="ADDRESS_LINE",
            validation_status="VALID",
        ),
    ]
    receipt = SimpleNamespace(
        receipt_id=1,
        width=100,
        height=200,
        cdn_s3_bucket=None,
        cdn_s3_key=None,
        raw_s3_bucket=None,
        raw_s3_key=None,
    )
    details = SimpleNamespace(
        receipts=[receipt],
        receipt_words=[word],
        receipt_word_labels=labels,
    )

    class FakeDynamoClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def get_image_details(self, _image_id):
            return details

    import receipt_dynamo.data.dynamo_client as dynamo_client

    monkeypatch.setattr(dynamo_client, "DynamoClient", FakeDynamoClient)
    monkeypatch.setattr(
        glyph_review,
        "_save_zoom_crops",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "boto3.client",
        lambda *_args, **_kwargs: SimpleNamespace(),
    )

    result = glyph_review.receipt(
        "Costco Wholesale", "image-id", 1, str(tmp_path / "review.png")
    )

    assert result == 0
    assert captured["words"][0]["labels"] == [
        "ADDRESS_LINE",
        "MERCHANT_NAME",
    ]
