"""Negative controls for evidence-backed, single-amount OCR loss detection."""

from copy import deepcopy
from itertools import permutations

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
    # A valid fixture includes two explicit surviving prices that establish
    # the amount column. Opt out to exercise insufficient-column evidence.
    anchors = (
        [
            word("$9.00", y=0.04, line_id=1001),
            word("$8.00", y=0.10, line_id=1002),
        ]
        if kwargs.pop("column_anchors", True)
        else []
    )
    return detect_warp_lost_amount(
        source if source is not None else [word("$2.50")],
        (warped if warped is not None else []) + anchors,
        zone if zone is not None else [name()],
        kwargs.pop("bounds", BOUNDS),
        kwargs.pop("others", []),
        kwargs.pop("item_sum", 27.5),
        kwargs.pop("subtotal", 30.0),
        source_image_id=kwargs.pop("source_image_id", "test-image"),
        receipt_image_id=kwargs.pop("receipt_image_id", "test-image"),
        **kwargs,
    )


@pytest.mark.parametrize(
    "label", ["GST", "HST", "PST", "QST", "TVA", "IVA", "MWST"]
)
def test_tax_abbreviations_are_not_product_context(label: str) -> None:
    assert detect(zone=[name(label)]) is None
    assert detect(zone=[name(f"Department {label} Amount")]) is None


@pytest.mark.parametrize(
    "label",
    [
        "Sale Price",
        "Regular Price",
        "Original Price",
        "Comparable Value",
        "PENNE PASTA Sale Price",
        "WAS:",
        "REG.",
        "Unit Price",
        "Unit Cost",
    ],
)
def test_price_comparison_and_column_annotations_abstain(label: str) -> None:
    assert detect(zone=[name(label)]) is None


@pytest.mark.parametrize(
    "label",
    [
        "MILK 2 @",
        "MILK 2 (a)",
        "MILK Unit Price",
        "MILK Unit Cost",
        "MILK per item",
        "2x MILK",
    ],
)
def test_named_rate_context_does_not_prove_a_line_price(label: str) -> None:
    assert detect(zone=[name(label)]) is None


@pytest.mark.parametrize("currency", ["$", "£", "€", "¥", "₹"])
@pytest.mark.parametrize("label", ["WAS:", "REG.", "MILK WAS:"])
def test_reference_prices_abstain_in_every_supported_currency(
    currency: str, label: str
) -> None:
    assert (
        detect(
            source=[word(f"{currency}2.50")],
            warped=[
                word(f"{currency}9.00", y=0.04),
                word(f"{currency}8.00", y=0.10),
            ],
            zone=[name(label)],
            column_anchors=False,
        )
        is None
    )


@pytest.mark.parametrize(
    "token", ["$2.50/lb", "WAS:£2.50", "2/$2.50", "$2.50OFF"]
)
def test_qualified_source_token_cannot_be_loss_evidence(token: str) -> None:
    assert detect(source=[word(token)], zone=[name("MILK")]) is None


@pytest.mark.parametrize(
    "label", ["WAS:", "Weight (oz)", "MILK Unit Price", "MILK 2 @"]
)
def test_source_row_qualifier_survives_loss_of_crop_context(
    label: str,
) -> None:
    assert (
        detect(
            source=[word("$2.50"), word(label, x=0.1, line_id=2)],
            zone=[name("MILK")],
        )
        is None
    )


def test_clear_original_product_context_remains_eligible() -> None:
    assert (
        detect(source=[word("$2.50"), word("MILK", x=0.1, line_id=2)])
        is not None
    )


@pytest.mark.parametrize(
    "confidence", [0.0, 0.5, 0.94, None, True, float("nan")]
)
@pytest.mark.parametrize("reverse", [False, True])
def test_lower_confidence_observation_still_blocks_ambiguity(
    confidence: float | bool | None, reverse: bool
) -> None:
    source = [
        word("$2.50"),
        word("$1.00", y=0.4, line_id=2, confidence=confidence),
    ]
    if reverse:
        source.reverse()
    assert detect(source=source, zone=[name(), name(y=0.41)]) is None
    conflict = [word("$2.50"), word("$7.50", line_id=2, confidence=confidence)]
    assert detect(source=conflict) is None


@pytest.mark.parametrize("token", ["-$1.00", "$-1.00", "(1.00)", "1.00-"])
@pytest.mark.parametrize("reverse", [False, True])
def test_missing_discount_blocks_single_positive_loss(
    token: str, reverse: bool
) -> None:
    source = [word("$2.50"), word(token, y=0.4, line_id=2)]
    if reverse:
        source.reverse()
    assert detect(source=source, zone=[name(), name("COUPON", y=0.41)]) is None


@pytest.mark.parametrize("x", [float("nan"), -0.01, 0.99])
def test_unattributable_source_amount_blocks_other_loss(x: float) -> None:
    assert (
        detect(source=[word("$2.50"), word("$1.00", x=x, y=0.4, line_id=2)])
        is None
    )


def test_entirely_outside_source_amount_does_not_block_target() -> None:
    found = detect(
        source=[word("$2.50"), word("$1.00", x=1.01, y=0.4, line_id=2)]
    )
    assert found is not None and found.amount == 2.5


@pytest.mark.parametrize("token", ["$1", "£1", "$1.000"])
@pytest.mark.parametrize("confidence", [0.94, 1.0])
def test_other_explicit_currency_shapes_remain_ambiguity_blockers(
    token: str, confidence: float
) -> None:
    assert (
        detect(
            source=[
                word("$2.50"),
                word(token, y=0.4, line_id=2, confidence=confidence),
            ],
            zone=[name(), name(y=0.41)],
        )
        is None
    )


@pytest.mark.parametrize(
    "marker", ["-", "−", "–", "(", "-$", "-USD$", "$-", "£-", "USD-"]
)
@pytest.mark.parametrize("reverse", [False, True])
def test_split_negative_or_accounting_prefix_blocks_positive_evidence(
    marker: str, reverse: bool
) -> None:
    source = [
        word("$2.50"),
        word("MILK", x=0.1, line_id=2),
        word(marker, x=0.7, line_id=3),
    ]
    if reverse:
        source.reverse()
    assert detect(source=source, zone=[name("MILK")]) is None


@pytest.mark.parametrize(
    "label,x", [("/LB", 0.86), ("/LB", 0.89), ("REG.", 0.08)]
)
def test_partly_cropped_source_qualifiers_remain_blockers(
    label: str, x: float
) -> None:
    bounds = {
        "top_left": {"x": 0.1, "y": 0.9},
        "top_right": {"x": 0.9, "y": 0.9},
        "bottom_right": {"x": 0.9, "y": 0.1},
        "bottom_left": {"x": 0.1, "y": 0.1},
    }
    price = word("$2.50", x=0.74, y=0.58)
    price["bounding_box"].update(width=0.08, height=0.016)
    qualifier = word(label, x=x, y=0.58, line_id=2)
    qualifier["bounding_box"].update(width=0.03, height=0.016)
    assert (
        detect(source=[price, qualifier], bounds=bounds, zone=[name("MILK")])
        is None
    )


@pytest.mark.parametrize(
    "label", ["MILK 2 FOR", "MILK 2 /", "MILK 2/$", "MILK 2/USD$"]
)
def test_multiples_deal_prefix_does_not_prove_a_charged_total(
    label: str,
) -> None:
    assert (
        detect(
            source=[word("$2.50"), word(label, x=0.1, line_id=2)],
            zone=[name(label)],
        )
        is None
    )


def test_covered_currency_deal_prefix_remains_context_not_an_amount() -> None:
    prefix = word("2/$", x=0.5, line_id=2)
    assert (
        detect(
            source=[word("2.50"), prefix], warped=[prefix], zone=[name("MILK")]
        )
        is None
    )


@pytest.mark.parametrize("minus", ["−", "‒", "–", "—", "﹣", "－", "‐", "‑"])
@pytest.mark.parametrize("suffix", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_unicode_negative_observations_remain_ambiguity_blockers(
    minus: str, suffix: bool, reverse: bool
) -> None:
    token = f"1.00{minus}" if suffix else f"{minus}1.00"
    source = [word("$2.50"), word(token, y=0.4, line_id=2)]
    if reverse:
        source.reverse()
    assert detect(source=source, zone=[name(), name("COUPON", y=0.41)]) is None


def test_low_confidence_duplicate_cannot_be_selected_as_evidence() -> None:
    found = detect(
        source=[
            word("$2.50", line_id=1, confidence=0.94),
            word("$2.50", line_id=2),
        ]
    )
    assert found is not None and found.source_line_id == 2


@pytest.mark.parametrize("currency", ["$", "£", "€", "USD $", "CAD $"])
def test_split_source_currency_cannot_hide_reference_keyword(
    currency: str,
) -> None:
    assert (
        detect(
            source=[
                word("2.50"),
                word("MILK", x=0.1, line_id=2),
                word("WAS:", x=0.3, line_id=3),
                word(currency, x=0.6, line_id=4),
            ],
            zone=[name("MILK")],
        )
        is None
    )


def test_split_source_multiplication_sign_is_rate_context() -> None:
    assert (
        detect(
            source=[
                word("$2.50"),
                word("MILK", x=0.1, line_id=2),
                word("2", x=0.3, line_id=3),
                word("×", x=0.4, line_id=4),
            ],
            zone=[name("MILK")],
        )
        is None
    )


def test_product_prefixed_price_header_abstains() -> None:
    assert detect(zone=[name("MILK REGUL R PRICE")]) is None
    assert detect(zone=[name("HOUSE REGULAR COFFEE")]) is not None


def test_sale_word_in_product_name_remains_eligible() -> None:
    assert detect(zone=[name("BAG SALE PAPER EA")]) is not None


@pytest.mark.parametrize("subtotal", [50_000.01, 60_000.0, 1_000_000.0])
def test_implausible_subtotal_cannot_anchor_loss(subtotal: float) -> None:
    assert (
        detect(
            source=[word("$1,000.00")],
            item_sum=subtotal - 1_000.0,
            subtotal=subtotal,
        )
        is None
    )


def test_plausibility_ceiling_itself_remains_eligible() -> None:
    found = detect(
        source=[word("$1,000.00")], item_sum=49_000.0, subtotal=50_000.0
    )
    assert found is not None and found.amount == 1_000.0


@pytest.mark.parametrize("x", [0.2, 0.4, 0.6])
def test_decimal_measurement_outside_price_column_abstains(x: float) -> None:
    assert (
        detect(
            source=[word("12.50", x=x)],
            zone=[name("CHEESE"), {**name("OZ"), "x": x + 0.12}],
            item_sum=187.5,
            subtotal=200.0,
        )
        is None
    )


@pytest.mark.parametrize(
    "label",
    [
        "Weight (oz):",
        "NET WT LB",
        "Tare",
        "Volume ml",
        "LENGTH CM",
        "CHEESE OZ",
    ],
)
def test_measurement_prefix_is_not_product_context(label: str) -> None:
    assert (
        detect(
            source=[word("12.50")],
            zone=[name(label)],
            item_sum=187.5,
            subtotal=200.0,
        )
        is None
    )


def test_measurement_substring_in_product_name_remains_eligible() -> None:
    assert detect(zone=[name("WEIGHTED BLANKET")]) is not None


def test_bare_decimal_price_aligned_with_established_column_is_eligible() -> (
    None
):
    found = detect(source=[word("2.50")])
    assert found is not None and found.amount == 2.5


def test_measurement_suffix_blocks_even_column_aligned_decimal() -> None:
    assert (
        detect(
            source=[word("12.50")],
            zone=[name("CHEESE"), {**name("OZ"), "x": 0.95}],
            item_sum=187.5,
            subtotal=200.0,
        )
        is None
    )


@pytest.mark.parametrize(
    "anchors",
    [
        [],
        [word("$9.00", y=0.04)],
        [word("9.00", y=0.04), word("8.00", y=0.10)],
        [word("$9.00", y=0.04), word("$9.00", y=0.04, line_id=2)],
        [word("$9.00", y=0.04), word("$8.00", y=0.10, confidence=0.94)],
        [word("$9.00", y=0.04, x=0.3), word("$8.00", y=0.10)],
    ],
)
def test_insufficient_price_column_evidence_abstains(
    anchors: list[dict],
) -> None:
    assert detect(warped=anchors, column_anchors=False) is None


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


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("matching_reading_also_present", [False, True])
def test_conflicting_crop_row_blocks_other_loss_evidence(
    reverse: bool, matching_reading_also_present: bool
) -> None:
    source = [word("$1.00"), word("$2.50", y=0.4, line_id=2)]
    warped = [word("$7.50", y=0.4, line_id=2)]
    if matching_reading_also_present:
        warped.append(word("$2.50", y=0.4, line_id=3))
    if reverse:
        source.reverse()
        warped.reverse()
    assert (
        detect(
            source=source,
            warped=warped,
            zone=[name(), name(y=0.41)],
            item_sum=29.0,
            subtotal=30.0,
        )
        is None
    )


@pytest.mark.parametrize("reverse_source", [False, True])
@pytest.mark.parametrize("reverse_crop", [False, True])
def test_duplicate_source_cannot_hide_crop_conflict(
    reverse_source: bool, reverse_crop: bool
) -> None:
    source = [
        word("$1.00"),
        word("$2.50", x=0.8, y=0.4, line_id=2),
        word("$2.50", x=0.82, y=0.4, line_id=3),
    ]
    warped = [
        word("$2.50", x=0.8, y=0.4, line_id=2),
        word("$7.50", x=0.86, y=0.4, line_id=3),
    ]
    if reverse_source:
        source.reverse()
    if reverse_crop:
        warped.reverse()
    assert (
        detect(
            source=source,
            warped=warped,
            zone=[name(), name(y=0.41)],
            item_sum=29.0,
            subtotal=30.0,
        )
        is None
    )


@pytest.mark.parametrize("reverse_source", [False, True])
def test_duplicate_source_cannot_hide_later_source_conflict(
    reverse_source: bool,
) -> None:
    source = [
        word("$1.00"),
        word("$2.50", x=0.8, y=0.4, line_id=2),
        word("$2.50", x=0.82, y=0.4, line_id=3),
        word("$7.50", x=0.86, y=0.4, line_id=4),
    ]
    if reverse_source:
        source.reverse()
    assert (
        detect(
            source=source,
            warped=[word("$7.50", x=0.88, y=0.4, line_id=4)],
            zone=[name(), name(y=0.41), {**name("$7.50", y=0.41), "x": 0.88}],
            item_sum=29.0,
            subtotal=30.0,
        )
        is None
    )


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
        "Produce Subtotal",
        "Produce Sub-total",
        "Grocery Sub Total Amount",
        "VAT",
        "VAT Amount",
        "VAT Included (8.25%)",
        "State Tax Total",
        "State Sales Tax Amount",
        "VAT Total Amount",
        "VAT Amount Included",
        "CA Sales Tax",
        "New York State Sales Tax",
        "Tax Due",
        "Tax Total Amount Due",
        "Produce Total",
        "Grocery Total Amount",
        "Produce Amount Due",
        "Department Balance",
        "Department Total to Pay",
        "Department Balance to Pay",
        "Department Total Paid",
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
        "VAT GUIDE",
        "SUBTOTAL MATH WORKBOOK",
        "GUIDE TO SALES TAX",
        "GUIDE TO VAT",
        "HANDBOOK ON VAT",
        "GUIDE TO SUBTOTAL",
        "TOTAL BODY LOTION",
        "BALANCE BAR",
        "GUIDE TO TOTAL",
        "AUTHORIZED BIOGRAPHY",
    ],
)
def test_product_names_with_summary_vocabulary_abstain_conservatively(
    text: str,
) -> None:
    # This evidence-only helper has no reliable way to distinguish product
    # names from all summary phrasing. Abstain without changing decoded items.
    assert detect(zone=[name(text)]) is None


@pytest.mark.parametrize("text", ["MILK", "STEAK STRIPS", "BODY LOTION"])
def test_unambiguous_product_names_remain_eligible(text: str) -> None:
    found = detect(zone=[name(text)])
    assert found is not None and found.amount == 2.5


@pytest.mark.parametrize(
    "text",
    [
        "TOOTHPASTE COLGATE TOTAL",
        "RUNNING SHOES NEW BALANCE",
        "Produce Total",
        "Department Balance",
        "Produce Subtotal",
        "CA Sales Tax",
    ],
)
@pytest.mark.parametrize("reverse", [False, True])
def test_summary_shaped_context_cannot_hide_another_missing_price(
    text: str, reverse: bool
) -> None:
    source = [word("$2.50"), word("$1.00", y=0.4, line_id=2)]
    if reverse:
        source.reverse()
    assert detect(source=source, zone=[name(), name(text, y=0.41)]) is None
    assert detect(zone=[name(text)]) is None


@pytest.mark.parametrize(
    "tokens,is_product",
    [
        (["Sales", "Tax", "Included:", "8.25%"], False),
        (["SALES", "TAX", "GUIDE"], False),
        (["Produce", "Sub", "Total"], False),
        (["VAT", "Amount"], False),
        (["VAT", "GUIDE"], False),
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


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("label", ["TOOTHPASTE COLGATE TOTAL", "MILK"])
def test_surviving_unit_price_cannot_hide_a_second_missing_price(
    reverse: bool, label: str
) -> None:
    source = [
        word("$2.50"),
        word("$1.00", y=0.4, line_id=2),
        word("$0.50", x=0.55, y=0.4, line_id=3),
    ]
    if reverse:
        source.reverse()
    assert (
        detect(
            source=source,
            warped=[word("$0.50", x=0.55, y=0.4, line_id=3)],
            zone=[
                name(),
                name(label, y=0.41),
                {**name("2 @", y=0.41), "x": 0.4},
                {**name("$0.50", y=0.41), "x": 0.55},
            ],
        )
        is None
    )


def test_no_product_context_is_not_enough_evidence():
    assert detect(zone=[]) is None
    assert detect(zone=[name(y=0.4)]) is None


@pytest.mark.parametrize("context", [None, "1", "VISA DEBIT", "DISCOUNT"])
@pytest.mark.parametrize("reverse", [False, True])
def test_uncovered_price_without_product_context_blocks_single_loss(
    context: str | None, reverse: bool
) -> None:
    source = [
        word("$2.50"),
        word("$1.00", y=0.4, line_id=2),
        word("$3.00", y=0.2, line_id=3),
    ]
    if reverse:
        source.reverse()
    zone = [name(), name("MILK", y=0.21)]
    if context is not None:
        zone.append(name(context, y=0.41))
    assert (
        detect(
            source=source,
            warped=[word("$3.00", y=0.2, line_id=3)],
            zone=zone,
        )
        is None
    )


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


@pytest.mark.parametrize("ys", list(permutations([0.600, 0.604, 0.608])))
def test_ambiguous_duplicate_chains_abstain_in_every_order(
    ys: tuple[float, float, float],
) -> None:
    source = [word("$2.50", y=y, line_id=i) for i, y in enumerate(ys, 1)]
    assert detect(source=source) is None


@pytest.mark.parametrize("ys", list(permutations([0.600, 0.601, 0.602])))
def test_tight_duplicate_group_counts_once_in_every_order(
    ys: tuple[float, float, float],
) -> None:
    source = [word("$2.50", y=y, line_id=i) for i, y in enumerate(ys, 1)]
    found = detect(source=source)
    assert found is not None and found.amount == 2.5


@pytest.mark.parametrize("reverse", [False, True])
def test_duplicate_geometry_threshold_is_symmetric(reverse: bool) -> None:
    source = [word("$2.50", x=0.8), word("$2.50", x=0.823, line_id=2)]
    source[0]["bounding_box"]["width"] = 0.08
    if reverse:
        source.reverse()
    assert detect(source=source) is None


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("separate_loss", [False, True])
def test_surviving_crop_amount_covers_every_source_duplicate(
    reverse: bool, separate_loss: bool
) -> None:
    source = [word("$2.50", x=0.8), word("$2.50", x=0.82, line_id=2)]
    if separate_loss:
        source.append(word("$1.00", y=0.4, line_id=3))
    if reverse:
        source.reverse()
    found = detect(
        source=source,
        warped=[word("$2.50", x=0.86)],
        zone=[name(), name(y=0.41)],
        item_sum=29.0 if separate_loss else 27.5,
    )
    if separate_loss:
        assert found is not None
        assert found.amount == 1.0 and found.source_line_id == 3
    else:
        assert found is None


@pytest.mark.parametrize("reverse_source", [False, True])
@pytest.mark.parametrize("reverse_crop", [False, True])
def test_duplicate_group_cannot_claim_two_crop_occurrences(
    reverse_source: bool, reverse_crop: bool
) -> None:
    source = [word("$2.50", x=0.8), word("$2.50", x=0.82, line_id=2)]
    source.append(word("$1.00", y=0.4, line_id=3))
    warped = [word("$2.50", x=0.76), word("$2.50", x=0.86, line_id=2)]
    if reverse_source:
        source.reverse()
    if reverse_crop:
        warped.reverse()
    assert (
        detect(
            source=source,
            warped=warped,
            zone=[name(), name(y=0.41)],
            item_sum=29.0,
        )
        is None
    )


@pytest.mark.parametrize("reverse", [False, True])
def test_duplicate_group_with_mixed_product_context_abstains(
    reverse: bool,
) -> None:
    source = [word("$2.50", y=0.600), word("$2.50", y=0.604, line_id=2)]
    if reverse:
        source.reverse()
    assert detect(source=source, zone=[name(y=0.628)]) is None


@pytest.mark.parametrize("reverse", [False, True])
def test_duplicate_evidence_uses_a_stable_existing_observation(
    reverse: bool,
) -> None:
    source = [word("$2.50", x=0.8), word("$2.50", x=0.82, line_id=2)]
    if reverse:
        source.reverse()
    found = detect(source=source)
    assert found is not None
    assert found.source_line_id == 1 and found.x == pytest.approx(0.8)


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


def test_product_containing_summary_vocabulary_conservatively_abstains():
    assert detect(zone=[name("STEAK TIPS")]) is None


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


@pytest.mark.parametrize(
    "label",
    [
        "EA",
        "EACH",
        "CT",
        "PK",
        "ITEM",
        "UNIT",
        "1B",
        "IB",
        "ea",
        "ct",
    ],
)
@pytest.mark.parametrize("source_context", [False, True])
def test_count_unit_context_cannot_establish_a_product(
    label: str, source_context: bool
) -> None:
    source = [word("$2.50")]
    zone = [name(label)]
    if source_context:
        source.append(word(label, x=0.1, line_id=2))
        zone = [name("MILK")]
    assert detect(source=source, zone=zone) is None


@pytest.mark.parametrize("other_right", [0.15, 0.3])
@pytest.mark.parametrize("reverse", [False, True])
def test_foreign_source_context_cannot_support_target_price(
    other_right: float, reverse: bool
) -> None:
    other = deepcopy(BOUNDS)
    other["top_right"]["x"] = other["bottom_right"]["x"] = other_right
    source = [word("$2.50"), word("MILK", x=0.1, line_id=2)]
    if reverse:
        source.reverse()
    assert detect(source=source, zone=[name("MILK")], others=[other]) is None


def test_disjoint_receipt_does_not_block_owned_product_context() -> None:
    other = deepcopy(BOUNDS)
    other["top_right"]["x"] = other["bottom_right"]["x"] = 0.05
    assert (
        detect(
            source=[word("$2.50"), word("PEACH", x=0.1, line_id=2)],
            zone=[name("PEACH")],
            others=[other],
        )
        is not None
    )


@pytest.mark.parametrize("label", ["Tax", "Total", "Subtotal"])
@pytest.mark.parametrize("direction", [-1, 1])
@pytest.mark.parametrize("original_label", [False, True])
def test_skewed_summary_cannot_borrow_neighboring_product_context(
    label: str, direction: int, original_label: bool
) -> None:
    drift = direction * 0.03
    source = [word("$2.50")]
    if original_label:
        source.extend(
            [
                word(label, x=0.1, y=0.6 - drift),
                word("MILK", x=0.1, y=0.6, line_id=2),
            ]
        )
    warped = []
    for i, y in enumerate([0.2, 0.4], 10):
        warped.extend(
            [
                word("PRODUCT", x=0.1, y=y - drift, line_id=i),
                word("$9.00", y=y, line_id=i),
            ]
        )
    assert (
        detect(
            source=source,
            warped=warped,
            zone=[name(label, y=0.61 - drift), name("MILK")],
            column_anchors=False,
        )
        is None
    )


@pytest.mark.parametrize("direction", [-1, 1])
def test_skewed_product_context_keeps_its_own_price(direction: int) -> None:
    drift = direction * 0.03
    source = [word("$2.50"), word("MILK", x=0.1, y=0.6 - drift)]
    warped = []
    for i, y in enumerate([0.2, 0.4], 10):
        warped.extend(
            [
                word("PRODUCT", x=0.1, y=y - drift, line_id=i),
                word("$9.00", y=y, line_id=i),
            ]
        )
    assert (
        detect(
            source=source,
            warped=warped,
            zone=[name("MILK", y=0.61 - drift)],
            column_anchors=False,
        )
        is not None
    )


@pytest.mark.parametrize(
    "label",
    [
        "Automatic Gratuity",
        "Banquet Service Charge",
        "Service Charges Included",
        "You Saved",
        "SAVE",
        "Member Saved",
        "Banquet Tip",
        "Optional Tips",
    ],
)
@pytest.mark.parametrize("original_context", [False, True])
def test_qualified_charges_and_past_tense_savings_abstain(
    label: str, original_context: bool
) -> None:
    source = [word("$2.50")]
    zone = [name(label)]
    if original_context:
        source.append(word(label, x=0.1))
        zone = [name("MILK")]
    assert detect(source=source, zone=zone) is None


@pytest.mark.parametrize("offset", [-0.019, -0.011, 0.011, 0.019])
@pytest.mark.parametrize("text", ["$2.50", "$3.50"])
def test_overlapping_crop_amount_outside_strict_match_window_abstains(
    offset: float, text: str
) -> None:
    assert (
        detect(
            warped=[word(text, y=0.6 + offset)], zone=[name(y=0.61 + offset)]
        )
        is None
    )


@pytest.mark.parametrize(
    "axis,source_shift,crop_shift", [("y", 0.011, 0.02), ("x", 0.051, 0.1)]
)
@pytest.mark.parametrize("direction", [-1, 1])
@pytest.mark.parametrize("reverse", [False, True])
def test_conflicting_source_overlap_outside_strict_window_abstains(
    axis: str,
    source_shift: float,
    crop_shift: float,
    direction: int,
    reverse: bool,
) -> None:
    first = word("$2.50")
    conflicting = word("$7.50", line_id=2)
    crop = word("$7.50", line_id=2)
    conflicting["bounding_box"][axis] += direction * source_shift
    crop["bounding_box"][axis] += direction * crop_shift
    source = [first, conflicting]
    if reverse:
        source.reverse()
    assert detect(source=source, warped=[crop]) is None


@pytest.mark.parametrize(
    "label", ["Taxes", "Sales Taxes", "TAXES INCLUDED", "Local Taxes"]
)
@pytest.mark.parametrize("original_context", [False, True])
def test_plural_tax_context_cannot_be_product_evidence(
    label: str, original_context: bool
) -> None:
    source = [word("$2.50")]
    zone = [name(label)]
    if original_context:
        source.append(word(label, x=0.1))
        zone = [name("MILK")]
    assert detect(source=source, zone=zone) is None
