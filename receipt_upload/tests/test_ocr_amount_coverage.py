"""Negative controls for evidence-backed, single-amount OCR loss detection."""

from copy import deepcopy

import pytest

from receipt_upload.ocr_amount_coverage import detect_warp_lost_amount

BOUNDS = {
    "top_left": {"x": 0.0, "y": 1.0},
    "top_right": {"x": 1.0, "y": 1.0},
    "bottom_right": {"x": 1.0, "y": 0.0},
    "bottom_left": {"x": 0.0, "y": 0.0},
}


def word(text, x=0.8, y=0.6, line_id=1, confidence=1.0):
    return {
        "line_id": line_id,
        "word_id": 1,
        "text": text,
        "confidence": confidence,
        "bounding_box": {"x": x, "y": y, "width": 0.1, "height": 0.02},
    }


def name(text="SIDE", y=0.61):
    return {"text": text, "x": 0.1, "y_mid": y, "h": 0.02}


def detect(source=None, warped=None, zone=None, **kwargs):
    return detect_warp_lost_amount(
        source if source is not None else [word("$2.50")],
        warped if warped is not None else [],
        zone if zone is not None else [name()],
        kwargs.pop("bounds", BOUNDS),
        kwargs.pop("others", []),
        kwargs.pop("item_sum", 27.5),
        kwargs.pop("subtotal", 30.0),
        source_image_id=kwargs.pop("source_image_id", "test-image"),
        receipt_image_id=kwargs.pop("receipt_image_id", "test-image"),
        **kwargs,
    )


def test_exact_single_price_loss_produces_evidence_not_an_item():
    found = detect()
    assert found is not None
    assert found.amount == 2.5
    assert found.source_line_id == found.source_word_id == 1
    assert found.x == pytest.approx(0.8)
    assert found.y == pytest.approx(0.6)
    assert not hasattr(found, "name")


def test_equal_price_elsewhere_does_not_hide_missing_occurrence():
    found = detect(
        source=[word("$2.50"), word("$2.50", y=0.4, line_id=2)],
        warped=[word("$2.50", y=0.4)],
    )
    assert found is not None and found.source_line_id == 1


def test_all_source_prices_survive():
    assert detect(warped=[word("$2.50")]) is None


def test_different_warped_reading_at_same_location_is_not_an_omission():
    assert detect(warped=[word("$7.50")]) is None


@pytest.mark.parametrize(
    "source",
    [
        [word("$2.49")],
        [word("$2.51")],
        [word("2.500")],
        [word("-2.50")],
        [word("$0.00")],
        [word("$2.50", confidence=0.94)],
    ],
)
def test_non_exact_or_unreliable_source_amounts_abstain(source):
    assert detect(source=source) is None


@pytest.mark.parametrize(
    "item_sum,subtotal",
    [
        (30.0, 30.0),
        (30.01, 30.0),
        (29.99, 30.0),
        (29.8, 30.0),
        (27.5, None),
        (27.5, 0),
        (27.5, -30),
        (10, 30),
        (float("nan"), 30),
        (27.5, float("inf")),
    ],
)
def test_only_near_shortfalls_are_eligible(item_sum, subtotal):
    assert detect(item_sum=item_sum, subtotal=subtotal) is None


@pytest.mark.parametrize(
    "text",
    [
        "Subtotal",
        "Tax",
        "VISA DEBIT",
        "1",
        "2 @ 1.25",
        "TIP",
        "Gratuity",
        "DISCOUNT",
        "COUPON",
        "Tax 8.25%",
        "Sales Tax 8.25%",
        "Suggested Tip",
        "Savings",
        "18% Tip =",
        "Tip (18%)",
        "18%: (Tip Total)",
        "Suggested gratuity (18%)",
        "Service Charge",
    ],
)
def test_missing_amount_needs_unpriced_product_context(text):
    assert detect(zone=[name(text)]) is None


@pytest.mark.parametrize(
    "text",
    [
        "Tax Amount",
        "TAX TOTAL",
        "Tax Included",
        "Sales Tax Amount",
        "Sales Tax Total",
        "Sales Tax Included",
        "Tax Amount (8.25%)",
        "Sales Tax Included: 8.25%",
    ],
)
def test_tax_summary_labels_are_not_lost_product_amounts(text: str) -> None:
    # Constructed safety counterexamples: a coincidental exact subtotal gap
    # must not turn a missing tax amount into product-price loss evidence.
    assert detect(zone=[name(text)]) is None


@pytest.mark.parametrize(
    "text",
    [
        "TAX PREP SOFTWARE",
        "SALES TAX GUIDE",
        "TAX AMOUNT WORKBOOK",
        "TAX INCLUDED COFFEE",
        "TAX FREE BAG",
        "STEAK TIPS",
    ],
)
def test_product_names_containing_summary_words_remain_eligible(
    text: str,
) -> None:
    found = detect(zone=[name(text)])
    assert found is not None
    assert found.amount == 2.5


@pytest.mark.parametrize(
    "tokens,is_product",
    [
        (["Sales", "Tax", "Included:", "8.25%"], False),
        (["SALES", "TAX", "GUIDE"], True),
    ],
)
def test_split_tax_labels_use_full_row_context(
    tokens: list[str], is_product: bool
) -> None:
    zone = [
        {**name(text), "x": 0.1 + index * 0.1}
        for index, text in enumerate(tokens)
    ]
    # Input order may differ from the left-to-right order on the receipt.
    found = detect(zone=list(reversed(zone)))
    assert (found is not None) is is_product


def test_no_product_context_is_not_enough_evidence():
    assert detect(zone=[]) is None
    assert detect(zone=[name(y=0.4)]) is None


def test_multiple_missing_prices_that_each_explain_gap_are_ambiguous():
    assert (
        detect(
            source=[word("$2.50"), word("$2.50", y=0.4, line_id=2)],
            zone=[name(), name(y=0.41)],
        )
        is None
    )


def test_duplicate_source_observations_are_not_double_counted():
    found = detect(source=[word("$2.50"), word("$2.50", line_id=2)])
    assert found is not None


def test_conflicting_source_observations_at_same_position_abstain():
    assert detect(source=[word("$2.50"), word("$7.50")]) is None
    assert detect(source=[word("$2.50"), word("-2.50")]) is None
    assert detect(source=[word("$2.50"), word("2.50-")]) is None


def test_two_missing_amounts_are_not_a_single_loss_even_if_one_fits_gap():
    assert (
        detect(
            source=[word("$2.50"), word("$1.00", y=0.4, line_id=2)],
            zone=[name(), name(y=0.41)],
        )
        is None
    )


@pytest.mark.parametrize("confidence", [float("nan"), float("inf"), True])
def test_invalid_confidence_is_not_high_confidence(confidence):
    assert detect(source=[word("$2.50", confidence=confidence)]) is None


def test_reflected_and_top_origin_bounds_are_not_accepted():
    reflected = {
        key: {"x": 1 - p["x"], "y": p["y"]} for key, p in BOUNDS.items()
    }
    assert detect(bounds=reflected, source=[word("$2.50", x=0.1)]) is None
    top_origin = {
        key: {"x": p["x"], "y": 1 - p["y"]} for key, p in BOUNDS.items()
    }
    assert detect(bounds=top_origin) is None


def test_source_image_identity_must_match():
    assert detect(source_image_id="other-image") is None
    assert detect(source_image_id="", receipt_image_id="") is None


def test_real_product_containing_tip_word_is_not_a_tip_suggestion():
    assert detect(zone=[name("STEAK TIPS")]) is not None


def test_source_word_partly_overlapping_other_receipt_abstains():
    other = deepcopy(BOUNDS)
    other["top_right"]["x"] = other["bottom_right"]["x"] = 0.85
    assert detect(others=[other]) is None


def test_outside_source_words_are_not_clamped_into_receipt():
    assert detect(source=[word("$2.50", x=1.01)]) is None
    assert detect(source=[word("$2.50", x=-0.01)]) is None


def test_overlap_with_another_receipt_abstains():
    assert detect(others=[deepcopy(BOUNDS)]) is None


def test_other_receipt_cannot_donate_its_price():
    left = deepcopy(BOUNDS)
    left["top_right"]["x"] = left["bottom_right"]["x"] = 0.4
    assert detect(bounds=left, others=[BOUNDS]) is None


def test_non_overlapping_other_receipt_does_not_block_target():
    other = deepcopy(BOUNDS)
    other["top_right"]["x"] = other["bottom_right"]["x"] = 0.3
    assert detect(others=[other]) is not None


def test_degenerate_receipt_geometry_abstains():
    bad = {key: {"x": 0.0, "y": 0.0} for key in BOUNDS}
    assert detect(bounds=bad) is None
    assert detect(others=[bad]) is None


def test_cropped_normalized_mapping_preserves_location():
    inset = {
        "top_left": {"x": 0.1, "y": 0.9},
        "top_right": {"x": 0.9, "y": 0.9},
        "bottom_right": {"x": 0.9, "y": 0.1},
        "bottom_left": {"x": 0.1, "y": 0.1},
    }
    source = word("$2.50", x=0.74, y=0.58)
    source["bounding_box"].update(width=0.08, height=0.016)
    found = detect(source=[source], bounds=inset)
    assert found is not None
    assert found.x == pytest.approx(0.8)
    assert found.y == pytest.approx(0.6)
    assert found.height == pytest.approx(0.02)


def test_sheared_receipt_projects_amount_into_the_same_product_row():
    # Analytic affine map: original x=.1+.7*x+.08*y, y=.15+.7*y.
    bounds = {
        "top_left": {"x": 0.18, "y": 0.85},
        "top_right": {"x": 0.88, "y": 0.85},
        "bottom_right": {"x": 0.80, "y": 0.15},
        "bottom_left": {"x": 0.10, "y": 0.15},
    }
    source = word("$2.50", x=0.708, y=0.57)
    source["bounding_box"].update(width=0.0716, height=0.014)
    found = detect(source=[source], bounds=bounds)
    assert found is not None
    assert found.x == pytest.approx(0.8, abs=0.003)
    assert found.y == pytest.approx(0.6)


def test_nonfinite_source_geometry_abstains():
    source = word("$2.50", x=float("nan"))
    assert detect(source=[source]) is None


def test_keystone_mapping_uses_projective_not_axis_aligned_coordinates():
    # Independent analytic homography, with a nonconstant denominator.
    def original(x, y):
        return (
            (0.1 + 0.7 * x + 0.05 * y) / (1 + 0.1 * y),
            (0.1 + 0.8 * y) / (1 + 0.1 * y),
        )

    bounds = {
        label: dict(zip(("x", "y"), original(x, y)))
        for label, x, y in (
            ("top_left", 0, 1),
            ("top_right", 1, 1),
            ("bottom_right", 1, 0),
            ("bottom_left", 0, 0),
        )
    }
    points = [original(x, y) for x in (0.8, 0.9) for y in (0.6, 0.62)]
    left, bottom = min(p[0] for p in points), min(p[1] for p in points)
    source = word("$2.50", x=left, y=bottom)
    source["bounding_box"].update(
        width=max(p[0] for p in points) - left,
        height=max(p[1] for p in points) - bottom,
    )
    found = detect(source=[source], bounds=bounds)
    assert found is not None
    assert found.x == pytest.approx(0.8, abs=0.003)
    assert found.y == pytest.approx(0.6)
    assert found.height == pytest.approx(0.02)
