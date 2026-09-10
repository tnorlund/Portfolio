"""Keep OCR geometry aligned when mapping perspective crops to the image."""

from copy import deepcopy
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
from receipt_dynamo import DynamoClient
from receipt_dynamo.entities import Receipt, ReceiptWord

from receipt_upload.combine import combine_receipt_words_to_image_coords
from receipt_upload.geometry.transformations import (
    find_perspective_coeffs,
    invert_warp,
)

IMAGE_ID = "123e4567-e89b-42d3-a456-426614174000"


def _project(
    coefficients: list[float], point: tuple[float, float]
) -> tuple[float, float]:
    a, b, c, d, e, f, g, h = coefficients
    x, y = point
    denominator = 1.0 + g * x + h * y
    return (
        (a * x + b * y + c) / denominator,
        (d * x + e * y + f) / denominator,
    )


@pytest.mark.parametrize(
    "source",
    [
        [(120, 80), (520, 80), (520, 880), (120, 880)],
        [(120, 80), (520, 130), (560, 930), (60, 880)],
    ],
    ids=["affine-crop", "perspective-crop"],
)
def test_inverse_maps_image_corners_back_to_crop(
    source: list[tuple[float, float]],
) -> None:
    destination = [(0.0, 0.0), (399.0, 0.0), (399.0, 799.0), (0.0, 799.0)]
    coefficients = find_perspective_coeffs(source, destination)
    inverse = invert_warp(
        coefficients[0],
        coefficients[1],
        coefficients[2],
        coefficients[3],
        coefficients[4],
        coefficients[5],
        coefficients[6],
        coefficients[7],
    )

    for image_point, crop_point in zip(source, destination):
        assert _project(inverse, image_point) == pytest.approx(crop_point)

    for point in [(40.0, 320.0), (120.0, 400.0), (200.0, 600.0)]:
        image_point = _project(coefficients, point)
        assert _project(inverse, image_point) == pytest.approx(point)


def test_combine_word_coordinates_follow_perspective_crop() -> None:
    # Known receipt -> image homography, with translation and perspective.
    coefficients = [1.2, 0.05, 120.0, 0.1, 1.1, 80.0, 0.0002, 0.0003]
    crop_corners = {
        "top_left": (0.0, 0.0),
        "top_right": (399.0, 0.0),
        "bottom_right": (399.0, 799.0),
        "bottom_left": (0.0, 799.0),
    }
    image_corners = {}
    for name, point in crop_corners.items():
        x, y = _project(coefficients, point)
        image_corners[name] = {"x": x / 1000, "y": 1 - y / 1000}
    receipt = Receipt(
        image_id=IMAGE_ID,
        receipt_id=1,
        width=400,
        height=800,
        timestamp_added=datetime(2026, 1, 1, tzinfo=timezone.utc),
        raw_s3_bucket="test-bucket",
        raw_s3_key="receipt.png",
        top_left=image_corners["top_left"],
        top_right=image_corners["top_right"],
        bottom_right=image_corners["bottom_right"],
        bottom_left=image_corners["bottom_left"],
    )
    word = ReceiptWord(
        image_id=IMAGE_ID,
        receipt_id=1,
        line_id=2,
        word_id=3,
        text="TOTAL",
        bounding_box={"x": 0.1, "y": 0.5, "width": 0.2, "height": 0.1},
        top_left={"x": 0.1, "y": 0.6},
        top_right={"x": 0.3, "y": 0.6},
        bottom_left={"x": 0.1, "y": 0.5},
        bottom_right={"x": 0.3, "y": 0.5},
        angle_degrees=0.0,
        angle_radians=0.0,
        confidence=0.99,
    )
    original = deepcopy(word)
    client = Mock(spec=DynamoClient)
    client.get_receipt.return_value = receipt
    client.list_receipt_words_from_receipt.return_value = [word]

    result = combine_receipt_words_to_image_coords(
        client, IMAGE_ID, [1], image_width=1000, image_height=1000
    )

    assert len(result) == 1
    assert result[0]["text"] == "TOTAL"
    assert (result[0]["line_id"], result[0]["word_id"]) == (2, 3)
    for name in crop_corners:
        point = getattr(original, name)
        x, y = _project(
            coefficients, (point["x"] * 400, (1 - point["y"]) * 800)
        )
        assert result[0][name] == pytest.approx({"x": x, "y": 1000 - y})
    assert word == original


@pytest.mark.parametrize("scale", [1e-13, -1e-13])
def test_inverse_preserves_small_nonzero_homogeneous_scale(
    scale: float,
) -> None:
    coefficients = [1.0, 0.0, 0.0, 0.0, scale, 1.0, 0.0, 1.0]
    inverse = invert_warp(*coefficients)
    image_point = _project(coefficients, (0.0, 0.0))
    assert _project(inverse, image_point) == pytest.approx((0.0, 0.0))


@pytest.mark.parametrize(
    "coefficients",
    [
        [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 1.0],
    ],
    ids=["singular-matrix", "inverse-cannot-use-eight-coefficients"],
)
def test_inverse_rejects_unrepresentable_transform(
    coefficients: list[float],
) -> None:
    with pytest.raises(ValueError):
        invert_warp(*coefficients)
