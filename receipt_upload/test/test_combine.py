from datetime import datetime, timedelta, timezone

import pytest
from receipt_dynamo.constants import MerchantValidationStatus, ValidationStatus
from receipt_dynamo.entities import Receipt, ReceiptPlace, ReceiptWord, ReceiptWordLabel

from receipt_upload.combine import (
    calculate_min_area_rect,
    combine_receipt_words_to_image_coords,
    create_combined_receipt_records,
    get_best_receipt_place,
    migrate_receipt_word_labels,
    transform_point_to_warped_space,
)
from receipt_upload.combine.records_builder import _get_receipt_to_image_transform


TEST_IMAGE_ID = "123e4567-e89b-42d3-a456-426614174000"


class _PlaceClient:
    def __init__(self, places_by_receipt_id):
        self._places_by_receipt_id = places_by_receipt_id

    def get_receipt_place(self, image_id, receipt_id):
        assert image_id == TEST_IMAGE_ID
        value = self._places_by_receipt_id.get(receipt_id)
        if isinstance(value, Exception):
            raise value
        return value


class _LabelClient:
    def __init__(self, pages_by_receipt_id):
        self._pages_by_receipt_id = pages_by_receipt_id
        self._call_count = {}

    def list_receipt_word_labels_for_receipt(
        self, image_id, receipt_id, last_evaluated_key=None
    ):
        assert image_id == TEST_IMAGE_ID
        idx = self._call_count.get(receipt_id, 0)
        self._call_count[receipt_id] = idx + 1
        return self._pages_by_receipt_id[receipt_id][idx]


def _make_place(
    *,
    receipt_id,
    place_id,
    merchant_name,
    formatted_address="",
    phone_number="",
    validation_status="",
    timestamp=None,
):
    return ReceiptPlace(
        image_id=TEST_IMAGE_ID,
        receipt_id=receipt_id,
        place_id=place_id,
        merchant_name=merchant_name,
        formatted_address=formatted_address,
        phone_number=phone_number,
        validation_status=validation_status,
        timestamp=timestamp or datetime.now(timezone.utc),
    )


def _make_label(
    *,
    receipt_id,
    line_id,
    word_id,
    label="TOTAL",
    reasoning="existing reasoning",
    validation_status=ValidationStatus.VALID.value,
    label_proposed_by="model_a",
):
    return ReceiptWordLabel(
        image_id=TEST_IMAGE_ID,
        receipt_id=receipt_id,
        line_id=line_id,
        word_id=word_id,
        label=label,
        reasoning=reasoning,
        timestamp_added=datetime.now(timezone.utc),
        validation_status=validation_status,
        label_proposed_by=label_proposed_by,
    )


@pytest.mark.unit
def test_get_best_receipt_place_prefers_higher_quality_match():
    weak = _make_place(
        receipt_id=1,
        place_id="",
        merchant_name="Cafe",
        validation_status=MerchantValidationStatus.UNSURE.value,
    )
    strong = _make_place(
        receipt_id=2,
        place_id="place_123",
        merchant_name="Cafe",
        formatted_address="123 Main St",
        phone_number="5551234567",
        validation_status=MerchantValidationStatus.MATCHED.value,
    )
    client = _PlaceClient({1: weak, 2: strong})

    best = get_best_receipt_place(client, TEST_IMAGE_ID, [1, 2])

    assert best is strong


@pytest.mark.unit
def test_get_best_receipt_place_uses_newer_timestamp_when_scores_tie():
    old_place = _make_place(
        receipt_id=1,
        place_id="same",
        merchant_name="Market",
        timestamp=datetime.now(timezone.utc) - timedelta(days=1),
    )
    new_place = _make_place(
        receipt_id=2,
        place_id="same",
        merchant_name="Market",
        timestamp=datetime.now(timezone.utc),
    )
    client = _PlaceClient({1: old_place, 2: new_place})

    best = get_best_receipt_place(client, TEST_IMAGE_ID, [1, 2])

    assert best is new_place


@pytest.mark.unit
def test_migrate_receipt_word_labels_maps_and_paginates():
    labels_page_1 = [
        _make_label(receipt_id=2, line_id=4, word_id=10),
        _make_label(receipt_id=2, line_id=4, word_id=99),  # unmapped
    ]
    labels_page_2 = [
        _make_label(
            receipt_id=2,
            line_id=5,
            word_id=11,
            reasoning=None,
            label_proposed_by=None,
        )
    ]
    labels_page_3 = [
        _make_label(receipt_id=3, line_id=7, word_id=20, label="TAX")
    ]
    client = _LabelClient(
        {
            2: [
                (labels_page_1, {"next": "page2"}),
                (labels_page_2, None),
            ],
            3: [(labels_page_3, None)],
        }
    )

    word_id_map = {
        (10, 4, 2): 100,
        (11, 5, 2): 101,
        (20, 7, 3): 200,
    }
    line_id_map = {
        (4, 2): 400,
        (5, 2): 500,
        (7, 3): 700,
    }

    migrated = migrate_receipt_word_labels(
        client=client,
        image_id=TEST_IMAGE_ID,
        original_receipt_ids=[2, 3],
        word_id_map=word_id_map,
        line_id_map=line_id_map,
        new_receipt_id=9,
    )

    assert len(migrated) == 3
    assert {(l.line_id, l.word_id, l.label) for l in migrated} == {
        (400, 100, "TOTAL"),
        (500, 101, "TOTAL"),
        (700, 200, "TAX"),
    }
    assert all(label.receipt_id == 9 for label in migrated)
    # For labels with missing metadata, fallback values should be populated.
    fallback_label = [l for l in migrated if l.word_id == 101][0]
    assert "Migrated from receipt 2, word 11" in fallback_label.reasoning
    assert fallback_label.label_proposed_by == "receipt_combination"
    assert fallback_label.label_consolidated_from == "receipt_2_word_11"


@pytest.mark.unit
def test_transform_point_to_warped_space_identity_mapping():
    src_corners = [
        (0.0, 0.0),
        (99.0, 0.0),
        (99.0, 99.0),
        (0.0, 99.0),
    ]

    x_out, y_out = transform_point_to_warped_space(
        x=25.0,
        y=75.0,
        src_corners=src_corners,
        warped_width=100,
        warped_height=100,
    )

    assert x_out == pytest.approx(25.0, abs=1e-6)
    assert y_out == pytest.approx(75.0, abs=1e-6)


@pytest.mark.unit
def test_calculate_min_area_rect_returns_portrait_warp():
    words = [
        {
            "top_left": {"x": 10.0, "y": 90.0},
            "top_right": {"x": 30.0, "y": 90.0},
            "bottom_left": {"x": 10.0, "y": 20.0},
            "bottom_right": {"x": 30.0, "y": 20.0},
        }
    ]

    result = calculate_min_area_rect(words, image_width=100, image_height=100)

    assert result["warped_width"] > 0
    assert result["warped_height"] > 0
    assert result["warped_height"] >= result["warped_width"]
    assert len(result["src_corners"]) == 4
    assert set(result["bounds"].keys()) == {
        "top_left",
        "top_right",
        "bottom_right",
        "bottom_left",
    }


@pytest.mark.unit
def test_create_combined_receipt_records_keeps_noise_and_mappings():
    combined_words = [
        {
            "receipt_id": 2,
            "word_id": 7,
            "line_id": 1,
            "text": "HELLO",
            "centroid_x": 15.0,
            "centroid_y": 85.0,
            "bounding_box": {
                "x": 10.0,
                "y": 80.0,
                "width": 10.0,
                "height": 10.0,
            },
            "top_left": {"x": 10.0, "y": 90.0},
            "top_right": {"x": 20.0, "y": 90.0},
            "bottom_left": {"x": 10.0, "y": 80.0},
            "bottom_right": {"x": 20.0, "y": 80.0},
            "angle_degrees": 0.0,
            "confidence": 0.99,
            "is_noise": False,
        },
        {
            "receipt_id": 2,
            "word_id": 8,
            "line_id": 1,
            "text": "NOISE",
            "centroid_x": 30.0,
            "centroid_y": 85.0,
            "bounding_box": {
                "x": 25.0,
                "y": 80.0,
                "width": 10.0,
                "height": 10.0,
            },
            "top_left": {"x": 25.0, "y": 90.0},
            "top_right": {"x": 35.0, "y": 90.0},
            "bottom_left": {"x": 25.0, "y": 80.0},
            "bottom_right": {"x": 35.0, "y": 80.0},
            "angle_degrees": 0.0,
            "confidence": 0.90,
            "is_noise": True,
        },
    ]
    bounds = {
        "top_left": {"x": 10.0, "y": 90.0},
        "top_right": {"x": 35.0, "y": 90.0},
        "bottom_right": {"x": 35.0, "y": 80.0},
        "bottom_left": {"x": 10.0, "y": 80.0},
    }
    src_corners = [
        (0.0, 0.0),
        (99.0, 0.0),
        (99.0, 99.0),
        (0.0, 99.0),
    ]

    records = create_combined_receipt_records(
        image_id=TEST_IMAGE_ID,
        new_receipt_id=3,
        combined_words=combined_words,
        bounds=bounds,
        raw_bucket="raw-bucket",
        site_bucket="site-bucket",
        image_width=100,
        image_height=100,
        warped_width=100,
        warped_height=100,
        src_corners=src_corners,
    )

    assert len(records["receipt_lines"]) == 1
    assert [w.word_id for w in records["receipt_words"]] == [1, 2]
    assert [w.is_noise for w in records["receipt_words"]] == [False, True]
    assert records["line_id_map"] == {(1, 2): 1}
    assert records["word_id_map"] == {(7, 1, 2): 1, (8, 1, 2): 2}


@pytest.mark.unit
def test_get_receipt_to_image_transform_identity_like():
    """Receipt covering the full image should produce near-identity transform."""
    receipt = Receipt(
        image_id=TEST_IMAGE_ID,
        receipt_id=1,
        width=100,
        height=200,
        timestamp_added=datetime.now(timezone.utc),
        raw_s3_bucket="bucket",
        raw_s3_key="key",
        # Corners in OCR space (y=0 at bottom, normalised 0-1)
        top_left={"x": 0.0, "y": 1.0},
        top_right={"x": 1.0, "y": 1.0},
        bottom_left={"x": 0.0, "y": 0.0},
        bottom_right={"x": 1.0, "y": 0.0},
    )

    coeffs, rw, rh = _get_receipt_to_image_transform(receipt, 100, 200)

    assert rw == 100
    assert rh == 200
    assert len(coeffs) == 8


@pytest.mark.unit
def test_combine_receipt_words_to_image_coords_produces_words():
    """End-to-end: words should be returned (not silently dropped)."""
    receipt = Receipt(
        image_id=TEST_IMAGE_ID,
        receipt_id=1,
        width=100,
        height=200,
        timestamp_added=datetime.now(timezone.utc),
        raw_s3_bucket="bucket",
        raw_s3_key="key",
        top_left={"x": 0.0, "y": 1.0},
        top_right={"x": 1.0, "y": 1.0},
        bottom_left={"x": 0.0, "y": 0.0},
        bottom_right={"x": 1.0, "y": 0.0},
    )
    word = ReceiptWord(
        receipt_id=1,
        image_id=TEST_IMAGE_ID,
        line_id=1,
        word_id=1,
        text="HELLO",
        bounding_box={"x": 0.1, "y": 0.4, "width": 0.2, "height": 0.1},
        top_left={"x": 0.1, "y": 0.5},
        top_right={"x": 0.3, "y": 0.5},
        bottom_left={"x": 0.1, "y": 0.4},
        bottom_right={"x": 0.3, "y": 0.4},
        angle_degrees=0.0,
        angle_radians=0.0,
        confidence=0.99,
    )

    class _WordClient:
        def get_receipt(self, image_id, receipt_id):
            return receipt

        def list_receipt_words_from_receipt(self, image_id, receipt_id):
            return [word]

    result = combine_receipt_words_to_image_coords(
        client=_WordClient(),
        image_id=TEST_IMAGE_ID,
        receipt_ids=[1],
        image_width=100,
        image_height=200,
    )

    assert len(result) == 1
    assert result[0]["text"] == "HELLO"
