"""Tests for the LayoutLMv3 training-image contract (M4)."""

from __future__ import annotations

from PIL import Image

import pytest

from receipt_agent.agents.label_evaluator.rendering.layoutlm_image import (
    LAYOUTLM_BBOX_SCALE,
    DomainMatchContract,
    LayoutLMv3Example,
    LayoutLMv3ImageContract,
    apply_domain_match,
    normalize_pixels,
    synth_bbox_to_layoutlm,
    to_layoutlmv3_example,
)


def test_bbox_flip_top_of_receipt_maps_to_small_y():
    # A synthesis box near the top (y high ~990) must become a LayoutLM box with
    # a SMALL y (top-left origin, y down).
    box = synth_bbox_to_layoutlm([100, 960, 300, 990], coord_max=1000)
    assert box is not None
    x0, y0, x1, y1 = box
    assert x0 == 100 and x1 == 300
    # top edge (synth 990) -> 1000-990 = 10; bottom edge (synth 960) -> 40.
    assert y0 == 10
    assert y1 == 40
    assert y0 < y1  # y increases downward


def test_bbox_flip_bottom_of_receipt_maps_to_large_y():
    box = synth_bbox_to_layoutlm([100, 10, 300, 40], coord_max=1000)
    assert box == (100, 960, 300, 990)


def test_bbox_rejects_degenerate_and_nan():
    assert synth_bbox_to_layoutlm([1, 2, 3]) is None
    assert synth_bbox_to_layoutlm("nope") is None
    assert synth_bbox_to_layoutlm([float("nan"), 0, 1, 1]) is None
    assert synth_bbox_to_layoutlm([float("inf"), 0, 1, 1]) is None
    # zero width / zero height collapse to zero area -> rejected.
    assert synth_bbox_to_layoutlm([100, 10, 100, 40], coord_max=1000) is None
    assert synth_bbox_to_layoutlm([100, 50, 300, 50], coord_max=1000) is None


def test_contract_from_hf_processor():
    class FakeProcessor:
        size = {"height": 256, "width": 256}
        image_mean = [0.485, 0.456, 0.406]
        image_std = [0.229, 0.224, 0.225]
        rescale_factor = 1.0 / 255.0
        resample = "bilinear"

    contract = LayoutLMv3ImageContract.from_hf_processor(FakeProcessor())
    assert contract.image_size == 256
    assert contract.image_mean == (0.485, 0.456, 0.406)
    assert contract.resample == "bilinear"


def test_bbox_clamped_to_scale():
    box = synth_bbox_to_layoutlm([-50, -50, 1200, 1200], coord_max=1000)
    assert box is not None
    for value in box:
        assert 0 <= value <= LAYOUTLM_BBOX_SCALE


def test_to_layoutlmv3_example_shape_and_alignment():
    example = {
        "tokens": ["VONS", "3.49", "TOTAL"],
        "bboxes": [
            [80, 950, 300, 990],   # header, top
            [820, 950, 900, 990],  # price, top-right
            [80, 60, 240, 100],    # total, bottom
        ],
        "ner_tags": ["B-MERCHANT_NAME", "B-LINE_TOTAL", "B-GRAND_TOTAL"],
    }
    out = to_layoutlmv3_example(example)
    assert isinstance(out, LayoutLMv3Example)
    assert out.image.size == (224, 224)
    assert out.image.mode == "RGB"
    assert len(out.tokens) == len(out.bboxes) == len(out.ner_tags) == 3
    # Header token must have a smaller top-y than the total token after flip.
    header_y0 = out.bboxes[0][1]
    total_y0 = out.bboxes[2][1]
    assert header_y0 < total_y0
    # ner_tags pass through.
    assert out.ner_tags[1] == "B-LINE_TOTAL"


def test_to_layoutlmv3_example_from_receipt_dict():
    receipt = {
        "words": [
            {"text": "MILK", "bbox": [80, 900, 220, 930],
             "labels": ["B-PRODUCT_NAME"]},
        ]
    }
    out = to_layoutlmv3_example(receipt)
    assert out.tokens == ("MILK",)
    assert len(out.bboxes) == 1
    assert out.ner_tags == ("B-PRODUCT_NAME",)


def test_contract_describe_documents_axes_and_norm():
    contract = LayoutLMv3ImageContract()
    described = contract.describe()
    assert described["image_size"] == 224
    assert described["bbox_scale"] == 1000
    assert described["apply_ocr"] is False
    assert described["image_axis"] == "top_left_y_down"
    assert "flipped" in described["notes"]


def test_normalize_pixels_matches_contract_stats():
    contract = LayoutLMv3ImageContract()
    image = Image.new("RGB", (4, 4), (255, 255, 255))
    arr = normalize_pixels(image, contract)
    # White (255) -> rescale 1.0 -> (1-0.5)/0.5 = 1.0 on every channel.
    try:
        import numpy as np

        assert arr.shape == (3, 4, 4)
        assert np.allclose(arr, 1.0)
    except ImportError:
        assert len(arr) == 3 and len(arr[0]) == 4 and len(arr[0][0]) == 4
        assert all(
            abs(arr[c][y][x] - 1.0) < 1e-6
            for c in range(3)
            for y in range(4)
            for x in range(4)
        )


def test_to_dict_excludes_image_by_default():
    out = to_layoutlmv3_example(
        {"tokens": ["A"], "bboxes": [[10, 10, 50, 40]], "ner_tags": ["O"]}
    )
    payload = out.to_dict()
    assert "image" not in payload
    assert payload["image_size"] == [224, 224]
    assert "image" in out.to_dict(include_image=True)


def _example():
    return {"tokens": ["A"], "bboxes": [[10, 10, 50, 40]], "ner_tags": ["O"]}


def test_clean_render_is_qa_only_not_training_ready():
    # The default (no domain match) image is a clean render: QA-only.
    out = to_layoutlmv3_example(_example())
    assert out.domain_matched is False
    assert out.image_training_ready is False
    assert out.to_dict()["image_role"] == "qa_only"


def test_domain_match_transform_marks_training_ready_and_moves_boxes():
    # A box-aware transform stands in for the future degradation pipeline: it
    # degrades the image AND shifts the boxes so they stay aligned.
    def fake_degrade(image, boxes):
        shifted = [(x0 + 1, y0, x1 + 1, y1) for (x0, y0, x1, y1) in boxes]
        return image.rotate(2), shifted

    out = to_layoutlmv3_example(_example(), domain_match=fake_degrade)
    assert out.domain_matched is True
    assert out.image_training_ready is True
    assert out.to_dict()["image_role"] == "training"
    # Boxes were moved by the transform (x shifted by +1).
    assert out.bboxes[0][0] == 11


def test_apply_domain_match_refuses_without_transform():
    image = Image.new("RGB", (8, 8), (255, 255, 255))
    with pytest.raises(NotImplementedError):
        apply_domain_match(image, [(1, 1, 5, 5)])
    # With a box-aware transform it applies and returns (image, boxes).
    result_image, result_boxes = apply_domain_match(
        image, [(1, 1, 5, 5)], transform=lambda im, bx: (im.rotate(1), bx)
    )
    assert isinstance(result_image, Image.Image)
    assert result_boxes == [(1, 1, 5, 5)]


def test_domain_match_contract_lists_required_transforms():
    contract = DomainMatchContract()
    required = contract.required_transforms()
    for expected in (
        "skew_rotation", "thermal_fade", "lighting_gradient",
        "sensor_noise", "background_composite",
    ):
        assert expected in required
    described = contract.describe()
    assert "domain" in described["rationale"].lower()
    # The structured artifact is called out as canonical / domain-independent.
    assert "canonical" in described["note"].lower()


def test_structured_columns_are_canonical_regardless_of_image():
    # Even with no domain match, the structured columns are valid LayoutLMv3
    # inputs (the canonical, domain-independent artifact).
    out = to_layoutlmv3_example(
        {"tokens": ["VONS", "9.99"], "bboxes": [[80, 950, 300, 990],
         [820, 950, 900, 990]], "ner_tags": ["B-MERCHANT_NAME", "B-LINE_TOTAL"]}
    )
    assert len(out.tokens) == len(out.bboxes) == len(out.ner_tags) == 2
    assert all(0 <= v <= 1000 for box in out.bboxes for v in box)
    assert all(box[2] > box[0] and box[3] > box[1] for box in out.bboxes)
