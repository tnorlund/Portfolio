"""Tests for generic merchant synthesis profiles and candidates."""

import copy
from decimal import Decimal

from receipt_agent.agents.label_evaluator.merchant_synthesis import (
    MerchantAnalysis,
    OnlineCatalogEntry,
    _SYNTHETIC_LINE_ID_BASE,
    _analyze_receipt,
    _apply_taxable_delta,
    _build_template_filled_row,
    _build_remove_item_candidate_from_plan,
    _merchant_online_catalog,
    _normalize_receipt,
    _shift_lines_below_for_insert,
    _stable_tax_rate,
    _verify_template_row_labels,
    _write_composed_totals,
    build_layout_integrity_evidence,
    _summary_block_top_y,
    build_merchant_synthesis_profile,
    build_merchant_synthesis_readiness,
    build_synthesis_candidate_quality,
    generate_merchant_synthesis_candidates,
    select_high_fidelity_synthesis_candidate,
)
from receipt_agent.agents.label_evaluator.pattern_discovery import (
    generate_synthetic_receipt_candidates,
)


def _word(text, bbox, labels=None):
    return {"text": text, "bbox": bbox, "labels": labels}


def _merchant_receipts():
    return [
        {
            "receipt_id": "market_1",
            "image_id": "30000000-0000-0000-0000-000000000001",
            "receipt_num": 1,
            "lines": [
                {
                    "line_id": 1,
                    "y": 0.96,
                    "words": [
                        _word(
                            "MARKET",
                            [410, 950, 500, 975],
                            ["MERCHANT_NAME"],
                        ),
                        _word(
                            "MART",
                            [510, 950, 575, 975],
                            ["MERCHANT_NAME"],
                        ),
                    ],
                },
                {
                    "line_id": 2,
                    "y": 0.72,
                    "words": [_word("PRODUCE", [80, 710, 175, 735])],
                },
                {
                    "line_id": 3,
                    "y": 0.685,
                    "words": [
                        _word(
                            "APPLES",
                            [85, 675, 165, 700],
                            ["PRODUCT_NAME"],
                        ),
                        _word(
                            "3.00",
                            [830, 675, 885, 700],
                            ["LINE_TOTAL"],
                        ),
                    ],
                },
                {
                    "line_id": 4,
                    "y": 0.64,
                    "words": [_word("DAIRY", [80, 630, 155, 655])],
                },
                {
                    "line_id": 5,
                    "y": 0.605,
                    "words": [
                        _word(
                            "MILK",
                            [85, 595, 145, 620],
                            ["PRODUCT_NAME"],
                        ),
                        _word(
                            "5.00",
                            [830, 595, 885, 620],
                            ["LINE_TOTAL"],
                        ),
                    ],
                },
                {
                    "line_id": 6,
                    "y": 0.575,
                    "words": [
                        _word("SUBTOTAL", [500, 565, 600, 590]),
                        _word(
                            "8.00",
                            [830, 565, 885, 590],
                            ["SUBTOTAL"],
                        ),
                    ],
                },
                {
                    "line_id": 7,
                    "y": 0.555,
                    "words": [
                        _word("TAX", [500, 545, 540, 570]),
                        _word("0.00", [830, 545, 885, 570], ["TAX"]),
                    ],
                },
                {
                    "line_id": 8,
                    "y": 0.535,
                    "words": [
                        _word("BALANCE", [500, 525, 595, 550]),
                        _word("DUE", [605, 525, 650, 550]),
                        _word(
                            "8.00",
                            [830, 525, 885, 550],
                            ["GRAND_TOTAL"],
                        ),
                    ],
                },
            ],
        },
        {
            "receipt_id": "market_2",
            "image_id": "30000000-0000-0000-0000-000000000002",
            "receipt_num": 1,
            "lines": [
                {
                    "line_id": 1,
                    "y": 0.96,
                    "words": [
                        _word(
                            "MARKET",
                            [410, 950, 500, 975],
                            ["MERCHANT_NAME"],
                        ),
                        _word(
                            "MART",
                            [510, 950, 575, 975],
                            ["MERCHANT_NAME"],
                        ),
                    ],
                },
                {
                    "line_id": 2,
                    "y": 0.72,
                    "words": [_word("PRODUCE", [80, 710, 175, 735])],
                },
                {
                    "line_id": 3,
                    "y": 0.685,
                    "words": [
                        _word(
                            "BANANAS",
                            [85, 675, 175, 700],
                            ["PRODUCT_NAME"],
                        ),
                        _word(
                            "2.50",
                            [830, 675, 885, 700],
                            ["LINE_TOTAL"],
                        ),
                    ],
                },
                {
                    "line_id": 4,
                    "y": 0.635,
                    "words": [
                        _word("BALANCE", [500, 625, 595, 650]),
                        _word("DUE", [605, 625, 650, 650]),
                        _word(
                            "2.50",
                            [830, 625, 885, 650],
                            ["GRAND_TOTAL"],
                        ),
                    ],
                },
            ],
        },
    ]


def _merchant_receipts_with_removable_category():
    receipts = _merchant_receipts()
    first = receipts[0]
    first["lines"].insert(
        3,
        {
            "line_id": 30,
            "y": 0.665,
            "words": [
                _word("PEARS", [85, 655, 155, 680], ["PRODUCT_NAME"]),
                _word("2.00", [830, 655, 885, 680], ["LINE_TOTAL"]),
            ],
        },
    )
    for line in first["lines"]:
        for word in line["words"]:
            if word["text"] == "8.00":
                word["text"] = "10.00"
    return receipts


def _merchant_receipts_with_datetime():
    receipts = _merchant_receipts()
    for receipt, date_text, time_text in zip(
        receipts,
        ["05/12/2026", "05/13/2026"],
        ["14:32", "15:07"],
        strict=True,
    ):
        receipt["lines"].insert(
            1,
            {
                "line_id": 20,
                "y": 0.9,
                "words": [
                    _word(date_text, [105, 890, 220, 915], ["DATE"]),
                    _word(time_text, [245, 890, 320, 915], ["TIME"]),
                ],
            },
        )
    return receipts


def _merchant_receipts_with_taxable_items():
    def receipt(
        receipt_id,
        image_id,
        *,
        taxable_name,
        taxable_total,
        subtotal,
        tax,
        grand_total,
    ):
        return {
            "receipt_id": receipt_id,
            "image_id": image_id,
            "receipt_num": 1,
            "lines": [
                {
                    "line_id": 1,
                    "y": 0.96,
                    "words": [
                        _word(
                            "TAXABLE", [390, 950, 505, 975], ["MERCHANT_NAME"]
                        ),
                        _word("MART", [515, 950, 575, 975], ["MERCHANT_NAME"]),
                    ],
                },
                {
                    "line_id": 2,
                    "y": 0.72,
                    "words": [_word("GROCERY", [80, 710, 175, 735])],
                },
                {
                    "line_id": 3,
                    "y": 0.685,
                    "words": [
                        _word(
                            taxable_name, [85, 675, 190, 700], ["PRODUCT_NAME"]
                        ),
                        _word(
                            f"{taxable_total}T",
                            [820, 675, 890, 700],
                            ["LINE_TOTAL"],
                        ),
                    ],
                },
                {
                    "line_id": 4,
                    "y": 0.645,
                    "words": [
                        _word("BREAD", [85, 635, 150, 660], ["PRODUCT_NAME"]),
                        _word("3.00", [830, 635, 885, 660], ["LINE_TOTAL"]),
                    ],
                },
                {
                    "line_id": 5,
                    "y": 0.600,
                    "words": [
                        _word("SUBTOTAL", [500, 590, 600, 615]),
                        _word(subtotal, [820, 590, 890, 615], ["SUBTOTAL"]),
                    ],
                },
                {
                    "line_id": 6,
                    "y": 0.575,
                    "words": [
                        _word("TAX", [500, 565, 545, 590]),
                        _word(tax, [830, 565, 890, 590], ["TAX"]),
                    ],
                },
                {
                    "line_id": 7,
                    "y": 0.545,
                    "words": [
                        _word("BALANCE", [500, 535, 595, 560]),
                        _word("DUE", [605, 535, 650, 560]),
                        _word(
                            grand_total, [820, 535, 890, 560], ["GRAND_TOTAL"]
                        ),
                    ],
                },
            ],
        }

    return [
        receipt(
            "taxable_1",
            "40000000-0000-0000-0000-000000000001",
            taxable_name="SOAP",
            taxable_total="10.00",
            subtotal="13.00",
            tax="0.78",
            grand_total="13.78",
        ),
        receipt(
            "taxable_2",
            "40000000-0000-0000-0000-000000000002",
            taxable_name="SHAMPOO",
            taxable_total="20.00",
            subtotal="23.00",
            tax="1.55",
            grand_total="24.55",
        ),
    ]


def _validated_taxable_merchant_receipts():
    """Same shape as ``_merchant_receipts_with_taxable_items`` but with a
    Vons-validated 7.25% taxable-item rate, so the receipt-validated tax config
    clears taxable edits. Used with ``merchant_name="Vons"``.

    receipt 1: taxable 10.00 -> tax 0.73 (7.3%); receipt 2: taxable 20.00 ->
    tax 1.45 (7.25%). Both land inside the config's 0.50pp match tolerance.
    """
    receipts = copy.deepcopy(_merchant_receipts_with_taxable_items())
    fixups = [("0.73", "13.73"), ("1.45", "24.45")]
    for receipt, (tax, grand) in zip(receipts, fixups):
        for line in receipt["lines"]:
            for word in line["words"]:
                labels = word.get("labels") or []
                if "TAX" in labels:
                    word["text"] = tax
                if "GRAND_TOTAL" in labels:
                    word["text"] = grand
    return receipts


def _taxable_merchant_receipts_at_rates(rate1, rate2):
    """Two taxable receipts whose per-receipt taxable-item rate is ``rate1`` /
    ``rate2`` (tax = taxable_total * rate). Used to exercise the per-receipt
    unanimity gate: equal rates -> one jurisdiction (edits allowed); different
    rates -> mixed jurisdiction (edits rejected)."""
    receipts = copy.deepcopy(_merchant_receipts_with_taxable_items())
    taxables = [Decimal("10.00"), Decimal("20.00")]
    nontax = Decimal("3.00")
    for receipt, taxable, rate in zip(receipts, taxables, (rate1, rate2)):
        tax = (taxable * rate).quantize(Decimal("0.01"))
        grand = taxable + nontax + tax
        for line in receipt["lines"]:
            for word in line["words"]:
                labels = word.get("labels") or []
                if "TAX" in labels:
                    word["text"] = f"{tax:.2f}"
                if "GRAND_TOTAL" in labels:
                    word["text"] = f"{grand:.2f}"
    return receipts


def _validated_merchant_receipts_no_subtotal():
    """Vons receipts that carry NO SUBTOTAL label but a realistic 7.25%-effective
    tax, exercising the taxable-delta reconstruction (subtotal = grand - tax) and
    the single-jurisdiction config-rate path (effective rate stays under the
    config rate so the jurisdiction guard passes)."""
    receipts = copy.deepcopy(_merchant_receipts_with_taxable_items())
    # taxable 10.00 + bread 3.00; tax 0.73 (7.25% on taxable); grand 13.73 / 24.45
    fixups = [("0.73", "13.73"), ("1.45", "24.45")]
    for receipt, (tax, grand) in zip(receipts, fixups):
        for line in receipt["lines"]:
            for word in line["words"]:
                labels = word.get("labels") or []
                if "SUBTOTAL" in labels:
                    word["labels"] = []  # strip the SUBTOTAL anchor entirely
                if "TAX" in labels:
                    word["text"] = tax
                if "GRAND_TOTAL" in labels:
                    word["text"] = grand
    return receipts


def _blind_positive_tax_receipt():
    """A receipt with positive TAX but NO parsed taxable item (its taxable
    LINE_TOTAL lost its flag to OCR). Its jurisdiction is unobservable, so a
    multi-jurisdiction merchant must not let it ride a batch other receipts
    validated. Implied rate here is 9.75% (0.98 / 10.00)."""
    receipt = copy.deepcopy(_merchant_receipts_with_taxable_items()[0])
    receipt["receipt_id"] = "taxable_blind"
    receipt["image_id"] = "40000000-0000-0000-0000-000000000009"
    for line in receipt["lines"]:
        for word in line["words"]:
            labels = word.get("labels") or []
            if "LINE_TOTAL" in labels and word["text"].endswith("T"):
                word["text"] = word["text"][:-1]  # drop the taxable flag
            if "TAX" in labels:
                word["text"] = "0.98"
            if "GRAND_TOTAL" in labels:
                word["text"] = "13.98"
    return receipt


def _plan():
    return {
        "merchant_name": "Market Mart",
        "source_receipt_count": 2,
        "confusion_target_count": 1,
        "recipes": [
            {
                "recipe_id": "market-o-merchant-name",
                "actual_label": "O",
                "predicted_label": "MERCHANT_NAME",
                "error_kind": "false_positive",
                "objective": "Add hard negatives for merchant name.",
                "merchant_scope": "same_merchant",
                "target_zone": {
                    "label": "MERCHANT_NAME",
                    "y_band": "top",
                    "x_zone": "center",
                },
                "source_examples": [],
                "retrieval_queries": [],
                "layout_constraints": [],
                "mutation_steps": [],
                "expected_label_effect": "Improve MERCHANT_NAME precision.",
                "safeguards": [],
            }
        ],
        "synthetic_receipt_guidance": [],
        "similar_merchant_mining": {},
        "metric_guardrails": [],
        "overtraining_guards": [],
    }


def test_build_merchant_synthesis_profile_extracts_catalog_and_categories():
    profile = build_merchant_synthesis_profile(
        "Market Mart",
        _merchant_receipts(),
    )

    assert profile is not None
    assert profile["merchant_name"] == "Market Mart"
    assert profile["receipt_count"] == 2
    assert profile["label_slots"]["MERCHANT_NAME"]["examples"] == [
        "MARKET",
        "MART",
    ]
    assert profile["category_patterns"]["heading_counts"] == {
        "PRODUCE": 2,
        "DAIRY": 1,
    }
    baseline = profile["real_structure_baseline"]
    assert baseline["schema_version"] == "real-structure-baseline-v1"
    assert baseline["receipt_count"] == 2
    assert baseline["pair_count"] == 1
    assert baseline["score_summary"]["count"] == 1
    assert "price_column" in baseline["component_summaries"]
    catalog = profile["observed_item_catalog"]
    assert any(
        row["product_text"] == "BANANAS"
        and row["category"] == "PRODUCE"
        and row["line_total"] == "2.50"
        for row in catalog
    )
    readiness = profile["synthesis_readiness"]
    assert readiness["status"] == "ready"
    assert readiness["score"] >= 0.7
    assert readiness["candidate_capacity"] >= 2
    assert readiness["supported_operations"] == [
        "hard_negative",
        "add_line_item",
    ]
    assert readiness["grounded_add_item_candidate_count"] > 0
    assert readiness["ready_hard_negative_labels"] == []
    assert readiness["blockers"] == []


def test_build_merchant_synthesis_profile_marks_stable_datetime_mutable():
    profile = build_merchant_synthesis_profile(
        "Market Mart",
        _merchant_receipts_with_datetime(),
    )

    assert profile is not None
    mutable = profile["mutable_fields"]
    assert mutable["DATE"]["safe_to_mutate"] is True
    assert mutable["DATE"]["stable_format"] == "MM/DD/YYYY"
    assert mutable["DATE"]["blockers"] == []
    assert mutable["TIME"]["safe_to_mutate"] is True
    assert mutable["TIME"]["stable_format"] == "HH:MM"
    assert mutable["TIME"]["blockers"] == []

    readiness = profile["synthesis_readiness"]
    assert "replace_field" in readiness["supported_operations"]
    assert readiness["mutable_field_count"] == 2
    assert readiness["candidate_capacity"] == 5
    assert readiness["mutable_fields"]["DATE"]["mutation_strategy"] == (
        "replace date text in-place using observed format and bbox"
    )
    assert readiness["mutable_fields"]["TIME"]["mutation_strategy"] == (
        "replace time text in-place using observed format and bbox"
    )


def test_profile_blocks_taxable_edits_for_unvalidated_merchant():
    """A stable observed rate is NOT enough on its own: an unvalidated merchant
    is gated out of taxable edits by the receipt-validated tax config."""
    profile = build_merchant_synthesis_profile(
        "Taxable Mart",
        _merchant_receipts_with_taxable_items(),
    )

    assert profile is not None
    assert profile["tax_policy"] == {
        "supported_policy": "non_taxable_item_delta",
        "taxable_item_count": 2,
        "non_taxable_item_count": 2,
        "receipts_with_tax_total": 2,
        "receipts_with_taxable_items": 2,
        "tax_rate_observation_count": 2,
        "stable_tax_rate": True,
        "tax_changing_synthesis_ready": False,
        "tax_changing_synthesis_blockers": [
            "merchant_not_validated_for_taxable_edits"
        ],
        "avg_tax_rate": "0.0778",
        "min_tax_rate": "0.0775",
        "max_tax_rate": "0.0780",
        "avg_tax_rate_percent": "7.78%",
    }
    readiness = profile["synthesis_readiness"]
    assert readiness["tax_policy"] == profile["tax_policy"]
    assert "tax_changing_synthesis_not_enabled" in readiness["limitations"]


def test_profile_enables_taxable_edits_for_validated_merchant():
    """A receipt-validated merchant (Vons) with a matching observed rate unlocks
    taxable item edits — stable rate AND config clearance both present."""
    profile = build_merchant_synthesis_profile(
        "Vons",
        _validated_taxable_merchant_receipts(),
    )

    assert profile is not None
    assert profile["tax_policy"]["supported_policy"] == "taxable_item_delta"
    assert profile["tax_policy"]["stable_tax_rate"] is True
    assert profile["tax_policy"]["tax_changing_synthesis_ready"] is True
    assert profile["tax_policy"]["tax_changing_synthesis_blockers"] == []
    readiness = profile["synthesis_readiness"]
    assert "tax_changing_synthesis_not_enabled" not in readiness["limitations"]


def test_generate_merchant_synthesis_candidates_replaces_stable_datetime_fields():
    candidates = generate_merchant_synthesis_candidates(
        _plan(),
        _merchant_receipts_with_datetime(),
        max_candidates=5,
    )

    replacements = [
        candidate
        for candidate in candidates
        if candidate["metadata"]["operation"] == "replace_field"
    ]

    assert [
        row["metadata"]["field_replacement"]["label"] for row in replacements
    ] == [
        "DATE",
        "TIME",
    ]
    assert all(
        row["metadata"]["base_receipt_key"].endswith("#00001")
        for row in replacements
    )
    date_replacement = replacements[0]["metadata"]["field_replacement"]
    # The DATE candidate is generated late (index past the receipt count), so
    # _choose_base_receipt rotates back to the CLEANEST base (image ...0001,
    # date 05/12) instead of clamping onto the noisiest receipt.
    assert date_replacement == {
        "label": "DATE",
        "old_text": "05/12/2026",
        "new_text": "05/13/2026",
        "format": "MM/DD/YYYY",
    }
    time_replacement = replacements[1]["metadata"]["field_replacement"]
    assert time_replacement == {
        "label": "TIME",
        "old_text": "15:07",
        "new_text": "15:24",
        "format": "HH:MM",
    }
    assert (
        replacements[0]["metadata"]["mutable_field_evidence"]["safe_to_mutate"]
        is True
    )
    assert "05/13/2026" in replacements[0]["tokens"]
    assert "15:24" in replacements[1]["tokens"]
    assert "B-DATE" in replacements[0]["ner_tags"]
    assert "B-TIME" in replacements[1]["ner_tags"]
    quality = replacements[0]["metadata"]["candidate_quality"]
    assert quality["schema_version"] == "synthetic-candidate-quality-v1"
    assert quality["high_fidelity"] is True
    assert quality["components"]["safe_mutable_field"] == 1.0
    assert quality["components"]["stable_field_format"] == 1.0
    assert quality["components"]["stable_field_geometry"] == 1.0
    preview_lines = replacements[0]["metadata"]["synthetic_receipt_preview"][
        "lines"
    ]
    assert any("DATE" in line["modified_labels"] for line in preview_lines)
    evidence = replacements[0]["metadata"]["synthesis_accuracy_evidence"]
    assert evidence["label"] == "DATE"
    assert evidence["new_text"] == "05/13/2026"
    assert {
        "field_marked_safe_to_mutate",
        "stable_field_geometry",
        "stable_field_format",
    }.issubset(evidence["checks"])


def test_build_merchant_synthesis_readiness_uses_plan_slots():
    readiness = build_merchant_synthesis_readiness(
        "Market Mart",
        _merchant_receipts(),
        plan=_plan(),
    )

    assert readiness is not None
    assert readiness["status"] == "ready"
    assert readiness["ready_hard_negative_labels"] == ["MERCHANT_NAME"]
    assert readiness["supported_operations"] == [
        "hard_negative",
        "add_line_item",
    ]
    assert readiness["grounded_add_item_examples"][0]["product_text"] in {
        "APPLES",
        "BANANAS",
    }


def test_build_merchant_synthesis_readiness_blocks_unstructured_receipts():
    readiness = build_merchant_synthesis_readiness(
        "Thin Merchant",
        [
            {
                "receipt_id": "thin_1",
                "image_id": "30000000-0000-0000-0000-000000000099",
                "receipt_num": 1,
                "lines": [
                    {
                        "line_id": 1,
                        "y": 0.95,
                        "words": [
                            _word(
                                "THIN",
                                [420, 940, 470, 965],
                                ["MERCHANT_NAME"],
                            )
                        ],
                    }
                ],
            }
        ],
    )

    assert readiness is not None
    assert readiness["status"] == "blocked"
    assert readiness["candidate_capacity"] == 1
    assert readiness["blockers"] == [
        "no_line_items",
        "no_observed_item_catalog",
    ]


def test_candidate_quality_requires_loader_structure_thresholds():
    quality = build_synthesis_candidate_quality(
        "add_line_item",
        {
            "structure_similarity": {
                "score": 0.82,
                "components": {
                    "category_sequence": 0.7,
                    "category_set": 0.7,
                    "token_count": 0.7,
                    "price_column": 0.2,
                    "line_step": 0.7,
                },
            },
            "observed_item_evidence": {
                "product_seen_outside_base": ["source#00001"],
                "category": "PRODUCE",
                "base_receipt_has_category": True,
                "category_seen_count": 2,
                "category_heading_seen_count": 1,
            },
            "added_item": {"seen_in_other_receipt": True},
            "arithmetic_reconciliation": {
                "summary_update_policy": "non_taxable_item_delta",
                "tax_delta": "0.00",
                "updated_summary_labels": {
                    "grand_total": 1,
                    "subtotal": 1,
                    "payment_or_balance": 1,
                },
            },
            "layout_integrity": {
                "score": 1.0,
                "passed": True,
                "overlap_pair_count": 0,
                "out_of_bounds_word_count": 0,
                "invalid_word_box_count": 0,
                "line_order_valid": True,
            },
        },
        token_count=48,
    )

    assert quality["components"]["structure_component_pass_rate"] == 0.8
    assert quality["structure_gate"]["passed"] is False
    assert quality["structure_gate"]["failed_components"] == {
        "price_column": {"value": 0.2, "threshold": 0.75}
    }
    assert quality["high_fidelity"] is False


def test_candidate_quality_rejects_overlapping_layout():
    quality = build_synthesis_candidate_quality(
        "add_line_item",
        {
            "structure_similarity": {
                "score": 0.92,
                "components": {
                    "category_sequence": 0.9,
                    "category_set": 0.9,
                    "token_count": 0.9,
                    "price_column": 0.9,
                    "line_step": 0.9,
                },
            },
            "observed_item_evidence": {
                "product_seen_outside_base": ["source#00001"],
                "category": "PRODUCE",
                "base_receipt_has_category": True,
                "category_seen_count": 2,
                "category_heading_seen_count": 2,
            },
            "added_item": {"seen_in_other_receipt": True},
            "arithmetic_reconciliation": {
                "summary_update_policy": "non_taxable_item_delta",
                "tax_delta": "0.00",
                "updated_summary_labels": {
                    "grand_total": 1,
                    "subtotal": 1,
                    "payment_or_balance": 1,
                },
            },
            "layout_integrity": {
                "score": 0.4,
                "passed": False,
                "overlap_pair_count": 3,
                "out_of_bounds_word_count": 0,
                "invalid_word_box_count": 0,
                "line_order_valid": True,
            },
        },
        token_count=48,
    )

    assert quality["components"]["layout_integrity"] == 0.4
    assert quality["structure_gate"]["passed"] is True
    assert quality["high_fidelity"] is False


def test_layout_integrity_only_fails_on_synthesis_introduced_defects():
    """layout_integrity measures geometry the SYNTHESIS introduced, not the base
    receipt's inherited rotated-OCR noise. Base overlaps/inversions do not fail a
    clean edit; only a synthetic-line collision, a malformed/off-canvas box, or a
    catastrophic splice does."""
    from receipt_agent.agents.label_evaluator.merchant_synthesis import (
        _layout_integrity_score_from_counts as score,
    )

    common = dict(invalid_count=0, out_of_bounds_count=0)
    # Mild base-OCR overlaps + inversions on a clean edit no longer penalize.
    assert (
        score(
            overlap_count=11,
            line_order_valid=False,
            word_count=126,
            line_count=55,
            line_inversion_count=8,
            **common,
        )
        == 1.0
    )
    # A collision involving an inserted synthetic line is a hard failure.
    assert (
        score(
            overlap_count=1,
            line_order_valid=True,
            word_count=126,
            line_count=55,
            synthetic_overlap_count=1,
            **common,
        )
        == 0.0
    )
    # A catastrophic base splice (overlaps ~ word count) still fails.
    assert (
        score(
            overlap_count=80,
            line_order_valid=True,
            word_count=100,
            line_count=40,
            **common,
        )
        == 0.0
    )
    # A single invalid (malformed/zero-area) box is always a hard failure.
    assert (
        score(
            overlap_count=0,
            line_order_valid=True,
            word_count=100,
            line_count=40,
            invalid_count=1,
            out_of_bounds_count=0,
        )
        == 0.0
    )


def test_line_step_falls_back_to_labeled_rows_for_thin_merchants():
    """When PRODUCT_NAME / LINE_TOTAL pairing yields fewer than two MATCHED items
    (common for under-labeled "thin" merchants), the row pitch must still be
    measured from the receipt's labeled item-region rows instead of collapsing to
    the flat default that no real or synthetic receipt actually matches. The flat
    default is reached only when no row geometry exists at all.
    """
    from receipt_agent.agents.label_evaluator.merchant_synthesis import (
        MerchantLineItem,
        _DEFAULT_LINE_STEP,
        _line_step,
    )

    # No matched items and no receipt -> historical constant, unchanged.
    assert _line_step([]) == _DEFAULT_LINE_STEP

    # A single matched item cannot yield a measurable gap; without the receipt
    # the constant is still returned (backwards compatible).
    one_item = [
        MerchantLineItem(
            line_index=3,
            line_indices=[3],
            amount=Decimal("1.00"),
            product_text="EGGS",
            center_y=658.0,
            taxable=False,
        )
    ]
    assert _line_step(one_item) == _DEFAULT_LINE_STEP

    # Same single matched item, but the receipt carries SIX labeled LINE_TOTAL
    # rows on a real, evenly-spaced grid. The pitch is now measured from those
    # rows (~20 px), not the flat default.
    receipt = {
        "lines": [
            {
                "line_id": idx,
                "words": [
                    _word(
                        f"{idx}.00",
                        [830, top, 885, top + 20],
                        ["LINE_TOTAL"],
                    )
                ],
            }
            # 6 rows stepping down the receipt by 20 px each.
            for idx, top in enumerate(range(800, 800 - 6 * 20, -20), start=1)
        ]
    }
    measured = _line_step(one_item, receipt)
    assert measured != _DEFAULT_LINE_STEP
    assert 18 <= measured <= 22

    # When neither matched items nor labeled item-region rows exist, the
    # constant remains the floor (no geometry to measure).
    empty_receipt = {"lines": [{"line_id": 1, "words": [_word("THANKS", [80, 50, 200, 75])]}]}
    assert _line_step([], empty_receipt) == _DEFAULT_LINE_STEP


def _selection_candidate(
    candidate_id,
    *,
    quality,
    structure,
    high_fidelity=True,
    within_real_range=True,
    delta_from_min=0.05,
    token_count=48,
):
    return {
        "candidate_id": candidate_id,
        "tokens": ["TOK"] * token_count,
        "metadata": {
            "candidate_quality": {
                "score": quality,
                "high_fidelity": high_fidelity,
                "components": {
                    "structure_component_pass_rate": 1.0,
                    "token_budget": 1.0,
                },
            },
            "structure_similarity": {
                "score": structure,
                "real_baseline_comparison": {
                    "baseline_pair_count": 6,
                    "candidate_score": structure,
                    "baseline_min": structure - delta_from_min,
                    "within_real_score_range": within_real_range,
                    "delta_from_min": delta_from_min,
                },
            },
        },
    }


def test_select_high_fidelity_candidate_prefers_real_baseline_fit():
    selected = select_high_fidelity_synthesis_candidate(
        [
            _selection_candidate(
                "below-baseline",
                quality=0.96,
                structure=0.86,
                within_real_range=False,
                delta_from_min=-0.03,
            ),
            _selection_candidate(
                "inside-baseline",
                quality=0.91,
                structure=0.84,
                within_real_range=True,
                delta_from_min=0.02,
            ),
            _selection_candidate(
                "not-high-fidelity",
                quality=0.99,
                structure=0.95,
                high_fidelity=False,
                within_real_range=True,
                delta_from_min=0.20,
            ),
        ]
    )

    assert selected is not None
    assert selected["candidate_id"] == "inside-baseline"
    selection = selected["metadata"]["selection_evidence"]
    assert selection["schema_version"] == "synthetic-candidate-selection-v1"
    assert selection["selected_from_candidate_count"] == 3
    assert selection["selected_score"]["within_real_score_range"] is True
    assert selection["selected_score"]["candidate_quality"] == 0.91


def test_generate_merchant_synthesis_candidates_uses_real_geometry_and_items():
    receipts = _merchant_receipts()
    receipt_keys = {
        f"{receipt['image_id']}#{receipt['receipt_num']:05d}"
        for receipt in receipts
    }
    candidates = generate_merchant_synthesis_candidates(
        _plan(),
        receipts,
    )

    # Grounded add-item augmentations are generated first (grounded-dominant
    # batch), then the hard-negative fills the remaining budget.
    sources = [candidate["metadata"]["source"] for candidate in candidates]
    assert sources[0] == "merchant_arithmetic_geometry"
    assert "merchant_parameterized_geometry" in sources

    hard_negative = next(
        candidate
        for candidate in candidates
        if candidate["metadata"]["operation"] == "hard_negative"
    )
    assert hard_negative["metadata"]["base_receipt_key"] in receipt_keys
    assert "REWARDS" in hard_negative["tokens"]
    rewards_index = hard_negative["tokens"].index("REWARDS")
    assert hard_negative["ner_tags"][rewards_index] == "O"
    assert hard_negative["metadata"]["structure_similarity"]["score"] > 0
    hard_negative_preview = hard_negative["metadata"][
        "synthetic_receipt_preview"
    ]
    assert hard_negative_preview["line_count"] > 0
    assert "REWARDS CLUB" in hard_negative_preview["text"]
    assert any(
        line["synthetic_insert"] and line["text"] == "REWARDS CLUB"
        for line in hard_negative_preview["lines"]
    )
    assert (
        "inserted_o_label_distractor"
        in hard_negative["metadata"]["synthesis_accuracy_evidence"]["checks"]
    )
    hard_negative_quality = hard_negative["metadata"]["candidate_quality"]
    assert hard_negative_quality["high_fidelity"] is True
    assert hard_negative["metadata"]["layout_integrity"]["passed"] is True
    assert hard_negative_quality["components"]["layout_integrity"] == 1.0
    assert hard_negative_quality["components"]["local_distractor"] == 1.0
    assert hard_negative_quality["components"]["target_label_slot"] == 1.0
    assert (
        hard_negative_quality["components"]["structure_component_pass_rate"]
        == 1.0
    )
    assert hard_negative_quality["structure_gate"]["passed"] is True

    arithmetic = next(
        candidate
        for candidate in candidates
        if candidate["metadata"]["operation"] == "add_line_item"
    )
    metadata = arithmetic["metadata"]
    assert metadata["operation"] == "add_line_item"
    assert (
        metadata["base_receipt_key"]
        == "30000000-0000-0000-0000-000000000002#00001"
    )
    assert metadata["old_grand_total"] == "2.50"
    assert metadata["new_grand_total"] == "5.50"
    assert metadata["old_subtotal"] is None
    assert metadata["new_subtotal"] == "5.50"
    assert metadata["arithmetic_reconciliation"] == {
        "summary_update_policy": "non_taxable_item_delta",
        "old_subtotal": "2.50",
        "new_subtotal": "5.50",
        "old_grand_total": "2.50",
        "new_grand_total": "5.50",
        "subtotal_delta": "3.00",
        "grand_total_delta": "3.00",
        "tax_delta": "0.00",
        "tax_policy": "left unchanged because synthesized item is non-taxable",
        "updated_summary_labels": {
            "subtotal": 0,
            "grand_total": 1,
            "payment_or_balance": 0,
        },
    }
    assert metadata["added_item"]["product_text"] == "APPLES"
    assert metadata["added_item"]["category"] == "PRODUCE"
    assert metadata["added_item"]["seen_in_other_receipt"] is True
    assert (
        metadata["observed_item_evidence"]["base_receipt_has_category"] is True
    )
    assert metadata["observed_item_evidence"]["product_seen_outside_base"] == [
        "30000000-0000-0000-0000-000000000001#00001"
    ]
    assert metadata["structure_similarity"]["score"] > 0
    assert "category_set" in metadata["structure_similarity"]["components"]
    assert "candidate_signature" in metadata["structure_similarity"]
    baseline_comparison = metadata["structure_similarity"][
        "real_baseline_comparison"
    ]
    assert baseline_comparison["baseline_pair_count"] == 1
    assert (
        baseline_comparison["candidate_score"]
        == metadata["structure_similarity"]["score"]
    )
    assert "within_real_score_range" in baseline_comparison
    shape_deltas = metadata["structure_similarity"]["shape_deltas"]
    assert abs(shape_deltas["line_total_x_delta"]) <= 1.0
    assert shape_deltas["token_count_delta"] < 0
    assert shape_deltas["line_count_delta"] < 0
    assert shape_deltas["line_item_count_delta"] == 0
    assert {
        "price_column_aligned",
        "line_spacing_close",
        "category_order_close",
        "category_set_close",
    }.issubset(
        set(metadata["structure_similarity"]["match_summary"]["shape_checks"])
    )
    assert metadata["candidate_quality"]["high_fidelity"] is True
    assert metadata["layout_integrity"] == {
        "schema_version": "synthetic-layout-integrity-v1",
        "score": 1.0,
        "passed": True,
        "line_count": 5,
        "word_count": 10,
        "overlap_pair_count": 0,
        "synthetic_overlap_pair_count": 0,
        "base_overlap_pair_count": 0,
        "edit_introduced_overlap_pair_count": 0,
        "out_of_bounds_word_count": 0,
        "invalid_word_box_count": 0,
        "line_order_valid": True,
        "line_inversion_count": 0,
        "overlap_examples": [],
        "out_of_bounds_examples": [],
        "invalid_word_examples": [],
    }
    assert (
        metadata["candidate_quality"]["components"]["layout_integrity"] == 1.0
    )
    assert metadata["candidate_quality"]["structure_gate"]["passed"] is True
    assert (
        metadata["candidate_quality"]["components"]["cross_receipt_grounding"]
        == 1.0
    )
    assert (
        metadata["candidate_quality"]["components"][
            "arithmetic_reconciliation"
        ]
        >= 0.8
    )
    assert (
        metadata["candidate_quality"]["components"]["category_alignment"]
        >= 0.8
    )
    selection = metadata["selection_evidence"]
    assert selection["schema_version"] == "synthetic-candidate-selection-v1"
    assert selection["selected_from_candidate_count"] >= 2
    assert (
        selection["selected_score"]["candidate_quality"]
        == metadata["candidate_quality"]["score"]
    )
    assert (
        "real_baseline_comparison.within_real_score_range"
        in selection["ranked_by"]
    )
    preview = metadata["synthetic_receipt_preview"]
    assert (
        preview["coordinate_system"]
        == "normalized_receipt_0_1000_y_high_is_top"
    )
    assert "BANANAS 2.50" in preview["text"]
    assert "APPLES 3.00" in preview["text"]
    inserted_lines = [
        line for line in preview["lines"] if line["synthetic_insert"]
    ]
    assert inserted_lines[0]["role"] == "line_item"
    assert inserted_lines[0]["labels"] == ["LINE_TOTAL", "PRODUCT_NAME"]
    balance_line = [
        line for line in preview["lines"] if line["text"].startswith("BALANCE")
    ][0]
    assert inserted_lines[0]["bbox"][1] > balance_line["bbox"][3]
    evidence = metadata["synthesis_accuracy_evidence"]
    assert evidence["changed_text"] == "APPLES"
    assert evidence["category"] == "PRODUCE"
    assert evidence["old_grand_total"] == "2.50"
    assert evidence["new_grand_total"] == "5.50"
    assert evidence["layout_integrity"] == {
        "score": 1.0,
        "passed": True,
        "line_count": 5,
        "word_count": 10,
        "overlap_pair_count": 0,
        "out_of_bounds_word_count": 0,
        "invalid_word_box_count": 0,
        "line_order_valid": True,
    }
    assert evidence["structure_similarity"]["nearest_real_receipt_key"] == (
        "30000000-0000-0000-0000-000000000001#00001"
    )
    assert evidence["structure_similarity"]["real_baseline_comparison"] == (
        metadata["structure_similarity"]["real_baseline_comparison"]
    )
    assert (
        evidence["structure_similarity"]["shape_deltas"][
            "line_item_count_delta"
        ]
        == 0
    )
    assert evidence["catalog_grounding"] == {
        "product_observed_count": 1,
        "product_seen_receipt_count": 1,
        "product_seen_outside_base_count": 1,
        "product_seen_outside_base": [
            "30000000-0000-0000-0000-000000000001#00001"
        ],
        "category": "PRODUCE",
        "category_seen_count": 2,
        "category_heading_seen_count": 2,
        "category_seen_in_receipts": [
            "30000000-0000-0000-0000-000000000001#00001",
            "30000000-0000-0000-0000-000000000002#00001",
        ],
    }
    assert evidence["category_placement"] == {
        "category": "PRODUCE",
        "insert_y": 662.0,
        "shifted_lower_lines_by": 41,
        "shifted_line_count": 1,
        "shifted_lower_line_shift_min": 41,
        "shifted_lower_line_shift_max": 41,
        "line_step": 26,
        "category_item_count_before": 1,
        "nearest_category_item_y": 687.5,
        "nearest_lower_line_y": 637.5,
        "same_category_section": True,
        "selection_reason": (
            "observed item from another receipt inserted under the same "
            "category on the base receipt"
        ),
        "base_receipt_has_category": True,
        "category_seen_count": 2,
        "category_heading_seen_count": 2,
        "category_alignment": "same_category_as_base",
    }
    assert metadata["category_insertion"] == {
        "category": "PRODUCE",
        "y_center": 662.0,
        "line_step": 26,
        "shifted_lower_lines_by": 41,
        "shifted_line_count": 1,
        "shifted_lower_line_shift_min": 41,
        "shifted_lower_line_shift_max": 41,
        "category_item_count_before": 1,
        "nearest_category_item_y": 687.5,
        "nearest_lower_line_y": 637.5,
        "same_category_section": True,
        "selection_reason": (
            "observed item from another receipt inserted under the same "
            "category on the base receipt"
        ),
    }
    assert {
        "item_seen_in_other_receipt",
        "base_receipt_has_category",
        "category_heading_seen_in_real_receipts",
        "non_taxable_arithmetic_reconciled",
        "layout_integrity_checked",
        "no_overlapping_or_out_of_bounds_boxes",
        "nearest_real_structure_similarity",
    }.issubset(evidence["checks"])
    assert "5.50" in arithmetic["tokens"]


def test_insert_shift_summary_reports_realized_clamped_movement():
    receipt = {
        "lines": [
            {
                "line_id": 1,
                "y": 0.005,
                "words": [_word("BOTTOM", [80, 0, 160, 10])],
            },
            {
                "line_id": 2,
                "y": 0.200,
                "words": [_word("MIDDLE", [80, 188, 160, 212])],
            },
            {
                "line_id": 3,
                "y": 0.400,
                "words": [_word("ABOVE", [80, 388, 160, 412])],
            },
        ]
    }

    summary = _shift_lines_below_for_insert(
        receipt,
        inserted_center_y=250,
        delta=26,
    )

    assert summary == {
        "line_count": 2,
        "median_shift": 16,
        "min_shift": 5,
        "max_shift": 26,
    }
    assert receipt["lines"][0]["words"][0]["bbox"] == [80, 0, 160, 0]
    assert receipt["lines"][1]["words"][0]["bbox"] == [80, 162, 160, 186]
    assert receipt["lines"][2]["words"][0]["bbox"] == [80, 388, 160, 412]


def test_summary_block_top_y_ignores_stray_total_above_items():
    """A receipt may carry a duplicate/stray summary-labeled word ABOVE the
    items (a header balance or duplicate-OCR'd grand total). y-high-is-top, so
    the genuine footer block sits at LOWER y than the items. The boundary must
    be the genuine footer's top edge, not the stray top total — otherwise every
    item reads as sitting "below" the summary and valid inserts are rejected.
    """
    receipt = {
        "lines": [
            # Stray grand total near the TOP of the receipt (high y).
            {
                "line_id": 1,
                "y": 0.640,
                "words": [_word("29.40", [400, 626, 460, 638], ["GRAND_TOTAL"])],
            },
            # Real item rows in the middle.
            {
                "line_id": 2,
                "y": 0.400,
                "words": [
                    _word("SHALLOTS", [80, 388, 240, 412], ["PRODUCT_NAME"]),
                    _word("1.99", [400, 388, 460, 412], ["LINE_TOTAL"]),
                ],
            },
            {
                "line_id": 3,
                "y": 0.300,
                "words": [
                    _word("GARLIC", [80, 288, 240, 312], ["PRODUCT_NAME"]),
                    _word("2.49", [400, 288, 460, 312], ["LINE_TOTAL"]),
                ],
            },
            # Genuine footer summary BELOW the items (low y).
            {
                "line_id": 4,
                "y": 0.240,
                "words": [_word("29.40", [400, 235, 460, 246], ["GRAND_TOTAL"])],
            },
        ]
    }

    # The boundary is the genuine footer's top edge (246), not the stray 638.
    assert _summary_block_top_y(receipt) == 246.0


def test_summary_block_top_y_falls_back_without_item_anchor():
    """With no item-labeled words to anchor the floor, every summary word is a
    candidate (no stray to filter), so the highest summary edge is returned."""
    receipt = {
        "lines": [
            {
                "line_id": 1,
                "y": 0.200,
                "words": [_word("10.00", [400, 188, 460, 212], ["GRAND_TOTAL"])],
            }
        ]
    }

    assert _summary_block_top_y(receipt) == 212.0


def test_generate_merchant_synthesis_candidates_can_remove_supported_item():
    candidates = generate_merchant_synthesis_candidates(
        _plan(),
        _merchant_receipts_with_removable_category(),
    )

    removed = [
        candidate
        for candidate in candidates
        if candidate["metadata"]["operation"] == "remove_line_item"
    ][0]
    metadata = removed["metadata"]
    assert (
        metadata["base_receipt_key"]
        == "30000000-0000-0000-0000-000000000001#00001"
    )
    assert metadata["removed_item"]["product_text"] == "PEARS"
    assert metadata["removed_item"]["category"] == "PRODUCE"
    assert metadata["old_grand_total"] == "10.00"
    assert metadata["new_grand_total"] == "8.00"
    assert metadata["old_subtotal"] == "10.00"
    assert metadata["new_subtotal"] == "8.00"
    assert metadata["arithmetic_reconciliation"]["tax_delta"] == "0.00"
    assert metadata["removal_context"] == {
        "category": "PRODUCE",
        "removed_y": 667.5,
        "line_step": 40,
        "shifted_lower_lines_by": 40,
        "shifted_line_count": 5,
        "shifted_lower_line_shift_min": 40,
        "shifted_lower_line_shift_max": 40,
        "category_item_count_before": 2,
        "category_item_count_after": 1,
        "selection_reason": (
            "removed non-taxable item from a multi-item category "
            "and shifted lower receipt lines to close the gap"
        ),
    }
    assert "PEARS" not in removed["tokens"]
    assert "APPLES" in removed["tokens"]
    assert "8.00" in removed["tokens"]
    evidence = metadata["synthesis_accuracy_evidence"]
    assert evidence["removal_context"] == metadata["removal_context"]
    assert "removed_from_multi_item_category" in evidence["checks"]
    assert "lower_lines_shifted_to_close_gap" in evidence["checks"]


def test_remove_candidate_does_not_claim_unknown_category_block():
    receipts = [
        {
            "receipt_id": "uncategorized_1",
            "image_id": "31000000-0000-0000-0000-000000000001",
            "receipt_num": 1,
            "lines": [
                {
                    "line_id": 1,
                    "y": 0.96,
                    "words": [
                        _word("MARKET", [410, 950, 500, 975], ["MERCHANT_NAME"]),
                        _word("MART", [510, 950, 575, 975], ["MERCHANT_NAME"]),
                    ],
                },
                {
                    "line_id": 2,
                    "y": 0.685,
                    "words": [
                        _word("APPLES", [85, 675, 165, 700], ["PRODUCT_NAME"]),
                        _word("3.00", [830, 675, 885, 700], ["LINE_TOTAL"]),
                    ],
                },
                {
                    "line_id": 3,
                    "y": 0.645,
                    "words": [
                        _word("PEARS", [85, 635, 155, 660], ["PRODUCT_NAME"]),
                        _word("2.00", [830, 635, 885, 660], ["LINE_TOTAL"]),
                    ],
                },
                {
                    "line_id": 4,
                    "y": 0.600,
                    "words": [
                        _word("SUBTOTAL", [500, 590, 600, 615]),
                        _word("5.00", [830, 590, 885, 615], ["SUBTOTAL"]),
                    ],
                },
                {
                    "line_id": 5,
                    "y": 0.555,
                    "words": [
                        _word("BALANCE", [500, 545, 595, 570]),
                        _word("DUE", [605, 545, 650, 570]),
                        _word("5.00", [830, 545, 885, 570], ["GRAND_TOTAL"]),
                    ],
                },
            ],
        }
    ]

    candidates = generate_merchant_synthesis_candidates(_plan(), receipts)

    removed = [
        candidate
        for candidate in candidates
        if candidate["metadata"]["operation"] == "remove_line_item"
    ][0]
    metadata = removed["metadata"]
    context = metadata["removal_context"]
    assert metadata["removed_item"]["category"] == "UNCATEGORIZED"
    assert context["category"] == "UNCATEGORIZED"
    assert context["category_item_count_before"] is None
    assert context["category_item_count_after"] is None
    assert context["selection_reason"] == (
        "removed non-taxable item and shifted lower receipt lines to close the gap"
    )
    evidence = metadata["synthesis_accuracy_evidence"]
    assert "removed_from_multi_item_category" not in evidence["checks"]
    assert "lower_lines_shifted_to_close_gap" in evidence["checks"]


def test_remove_candidate_does_not_claim_single_item_category_is_multi_item():
    receipts = _merchant_receipts()
    profile = build_merchant_synthesis_profile("Market Mart", receipts)
    assert profile is not None
    receipt = _normalize_receipt(receipts[0])
    analysis = _analyze_receipt(receipt)
    removed = [
        item for item in analysis.line_items if item.product_text == "APPLES"
    ][0]

    candidate = _build_remove_item_candidate_from_plan(
        "Market Mart",
        profile,
        [analysis],
        analysis,
        removed,
        index=1,
        plan_rank=1,
        plan_count=1,
        plan_score=1.0,
    )

    assert candidate is not None
    context = candidate["metadata"]["removal_context"]
    assert context["category"] == "PRODUCE"
    assert context["category_item_count_before"] == 1
    assert context["category_item_count_after"] == 0
    assert "multi-item category" not in context["selection_reason"]
    evidence = candidate["metadata"]["synthesis_accuracy_evidence"]
    assert "removed_from_multi_item_category" not in evidence["checks"]


def test_generic_entry_point_uses_merchant_synthesis_for_non_sprouts():
    candidates = generate_synthetic_receipt_candidates(
        _plan(),
        receipts_data=_merchant_receipts(),
    )

    # Grounded-dominant batch: grounded add-item augmentations lead, then the
    # hard-negative fills the remaining budget.
    operations = [candidate.metadata["operation"] for candidate in candidates]
    assert operations.count("add_line_item") >= 2
    assert "hard_negative" in operations
    assert operations.index("add_line_item") < operations.index("hard_negative")
    assert {candidate.metadata["source"] for candidate in candidates} == {
        "merchant_arithmetic_geometry",
        "merchant_parameterized_geometry",
    }


def test_nearest_open_y_skips_when_target_zone_is_crowded():
    """A distractor that cannot be placed near its target zone without
    overlapping real words returns None (caller skips) rather than colliding."""
    from receipt_agent.agents.label_evaluator.merchant_synthesis import (
        _nearest_open_y,
    )

    # A wall of words filling every row around the target band.
    receipt = {
        "lines": [
            {
                "line_id": i,
                "words": [
                    {"text": "X", "bbox": [0, y, 1000, y + 24], "word_id": 1}
                ],
            }
            for i, y in enumerate(range(300, 700, 18), start=1)
        ]
    }
    assert _nearest_open_y(receipt, x0=100, desired_y=500, tokens=["FOO"]) is None
    # An empty receipt always has room.
    assert _nearest_open_y({"lines": []}, x0=100, desired_y=500, tokens=["FOO"]) == 500


def test_choose_base_receipt_prefers_clean_geometry():
    """A receipt whose own OCR has overlapping word boxes is ranked behind a
    clean receipt of the same merchant."""
    from receipt_agent.agents.label_evaluator.merchant_synthesis import (
        _choose_base_receipt,
    )

    clean = {
        "receipt_id": 1,
        "image_id": "img-clean",
        "lines": [
            {"line_id": 1, "words": [{"text": "A", "bbox": [10, 900, 90, 924]}]},
            {"line_id": 2, "words": [{"text": "B", "bbox": [10, 850, 90, 874]}]},
        ],
    }
    dirty = {
        "receipt_id": 2,
        "image_id": "img-dirty",
        "lines": [
            {
                "line_id": 1,
                "words": [
                    {"text": "COSTCO", "bbox": [10, 900, 400, 950]},
                    {"text": "CO", "bbox": [12, 902, 398, 948]},  # overlaps
                ],
            },
        ],
    }
    # Regardless of input order, the clean receipt is chosen first.
    assert _choose_base_receipt([dirty, clean], used=0)["image_id"] == "img-clean"
    assert _choose_base_receipt([clean, dirty], used=0)["image_id"] == "img-clean"
    # A late candidate (used past the receipt count) must still get the CLEAN
    # base, not spill onto the noisy one — it rotates back among clean bases.
    assert _choose_base_receipt([clean, dirty], used=1)["image_id"] == "img-clean"
    assert _choose_base_receipt([clean, dirty], used=5)["image_id"] == "img-clean"


def _online_catalog():
    """A small online product catalog (name + price + UPC) for template fill."""
    return [
        OnlineCatalogEntry("ACME 2 IN ROLL", Decimal("12.50"), "012345678905"),
        OnlineCatalogEntry("ACME BOLT KIT 50PC", Decimal("8.25"), "098765432105"),
        OnlineCatalogEntry("ACME TAPE 2IN ROLL", Decimal("4.75"), "076543210982"),
    ]


def _compose_candidate(max_candidates=4):
    receipts = _merchant_receipts_with_taxable_items()
    candidates = generate_merchant_synthesis_candidates(
        {"merchant_name": "Taxable Mart", "recipes": []},
        receipts,
        max_candidates=max_candidates,
        online_catalog=_online_catalog(),
    )
    composed = [
        candidate
        for candidate in candidates
        if candidate["metadata"]["operation"] == "compose_online_catalog"
    ]
    return composed


def _all_taxable_receipts():
    receipts = copy.deepcopy(_merchant_receipts_with_taxable_items())
    for receipt in receipts:
        for line in receipt["lines"]:
            for word in line["words"]:
                if word["text"] == "3.00":
                    word["text"] = "3.00T"
    return receipts


def _numeric_product_receipts():
    receipts = copy.deepcopy(_merchant_receipts_with_taxable_items())
    code = 10000
    for receipt in receipts:
        for line in receipt["lines"]:
            for word in line["words"]:
                if "PRODUCT_NAME" in (word.get("labels") or []):
                    word["text"] = str(code)
                    code += 1
    return receipts


def _summary_only_receipt(receipt_id, image_id, *, subtotal, tax, grand_total):
    return {
        "receipt_id": receipt_id,
        "image_id": image_id,
        "receipt_num": 1,
        "lines": [
            {
                "line_id": 1,
                "y": 0.96,
                "words": [_word("TAXABLE", [390, 950, 505, 975], ["MERCHANT_NAME"])],
            },
            {
                "line_id": 2,
                "y": 0.60,
                "words": [
                    _word("SUBTOTAL", [500, 590, 600, 615]),
                    _word(subtotal, [820, 590, 890, 615], ["SUBTOTAL"]),
                ],
            },
            {
                "line_id": 3,
                "y": 0.575,
                "words": [
                    _word("TAX", [500, 565, 545, 590]),
                    _word(tax, [830, 565, 890, 590], ["TAX"]),
                ],
            },
            {
                "line_id": 4,
                "y": 0.545,
                "words": [
                    _word("BALANCE", [500, 535, 595, 560]),
                    _word("DUE", [605, 535, 650, 560]),
                    _word(grand_total, [820, 535, 890, 560], ["GRAND_TOTAL"]),
                ],
            },
        ],
    }


def test_compose_online_catalog_assigns_clean_item_labels():
    """Template fill gives the item region the labels WE assign (clean
    supervision a transplanted real row cannot guarantee)."""
    composed = _compose_candidate()
    assert composed, "expected at least one compose_online_catalog candidate"
    candidate = composed[0]
    metadata = candidate["metadata"]

    # Exhaustive: every composed item-row token carries its expected label.
    assert metadata["label_control"]["all_correct"] is True
    assert metadata["label_control"]["item_token_count"] > 0
    assert (
        metadata["label_control"]["correctly_labeled"]
        == metadata["label_control"]["item_token_count"]
    )

    # Independent token/tag spot check on the flattened sequence: composed
    # prices ($-prefixed) are the only LINE_TOTAL tokens, and tax flags are O.
    price_tokens = 0
    name_tokens = 0
    for token, tag in zip(candidate["tokens"], candidate["ner_tags"]):
        if token.startswith("$"):
            assert tag.endswith("LINE_TOTAL"), (token, tag)
            price_tokens += 1
        if token == "<A>":
            assert tag == "O", (token, tag)
        if tag.endswith("PRODUCT_NAME"):
            name_tokens += 1
    assert price_tokens >= 2
    assert name_tokens >= 2

    row = _build_template_filled_row(
        OnlineCatalogEntry("ACME 50PC BOLT KIT", Decimal("12.50"), "012345678905"),
        y0=100,
        geo={"char_w": 10, "name_x0": 140, "price_x1": 420, "height": 20},
        line_id=_SYNTHETIC_LINE_ID_BASE,
    )
    assert row is not None
    assert any(
        any(char.isdigit() for char in str(word["text"]))
        and word.get("labels") == ["PRODUCT_NAME"]
        for word in row["words"]
    )
    assert _verify_template_row_labels({"lines": [row]})["all_correct"] is True

    # High fidelity + valid layout (the LayoutLM-loader gate conditions).
    quality = metadata["candidate_quality"]
    assert quality["high_fidelity"] is True
    assert metadata["layout_integrity"]["score"] == 1.0
    assert candidate["train_only"] is True


def test_compose_online_catalog_recomputes_tax_at_stable_observed_rate():
    """Totals are recomputed: subtotal from composed items, tax from rendered
    taxable items at the merchant's stable rate, grand total consistent."""
    composed = _compose_candidate()
    assert composed
    arithmetic = composed[0]["metadata"]["arithmetic_reconciliation"]

    subtotal = Decimal(arithmetic["new_subtotal"])
    taxable_subtotal = Decimal(arithmetic["new_taxable_subtotal"])
    tax = Decimal(arithmetic["new_tax"])
    total = Decimal(arithmetic["new_grand_total"])
    rate = Decimal(arithmetic["tax_rate"])

    assert arithmetic["tax_rate_stable"] is True
    assert arithmetic["subtotal_consistent"] is True
    assert arithmetic["tax_basis"] == "taxable_catalog_subtotal"
    # tax = round(taxable subtotal * rate); grand total reconciles.
    assert tax == (taxable_subtotal * rate).quantize(Decimal("0.01"))
    assert total == subtotal + tax
    # A realistic sales-tax rate, not a per-item-detection artifact.
    assert Decimal("0.001") <= rate <= Decimal("0.20")


def test_compose_online_catalog_does_not_tax_exempt_entries():
    exempt_catalog = [
        OnlineCatalogEntry("ACME GROCERY EXEMPT", Decimal("12.50"), taxable=False),
        OnlineCatalogEntry("ACME BREAD EXEMPT", Decimal("8.25"), taxable=False),
    ]

    candidates = generate_merchant_synthesis_candidates(
        {"merchant_name": "Taxable Mart", "recipes": []},
        _merchant_receipts_with_taxable_items(),
        max_candidates=4,
        online_catalog=exempt_catalog,
    )
    composed = [
        candidate
        for candidate in candidates
        if candidate["metadata"]["operation"] == "compose_online_catalog"
    ]

    assert composed
    arithmetic = composed[0]["metadata"]["arithmetic_reconciliation"]
    assert arithmetic["new_taxable_subtotal"] == "0.00"
    assert arithmetic["new_tax"] == "0.00"
    assert Decimal(arithmetic["new_grand_total"]) == Decimal(arithmetic["new_subtotal"])


def test_taxable_add_items_blocked_for_unvalidated_merchant():
    """Even with a stable observed rate, an unvalidated merchant generates NO
    taxable adds — the per-merchant tax config is the gate, not rate stability."""
    candidates = generate_merchant_synthesis_candidates(
        {"merchant_name": "Taxable Mart", "recipes": []},
        _merchant_receipts_with_taxable_items(),
        max_candidates=6,
    )

    taxable_adds = [
        candidate
        for candidate in candidates
        if candidate["metadata"]["operation"] == "add_line_item"
        and candidate["metadata"]["added_item"]["taxable"] is True
    ]

    assert taxable_adds == []


def test_taxable_add_items_generated_for_validated_merchant():
    """A receipt-validated merchant (Vons) at its validated rate generates
    taxable adds, and TAX is recomputed at the snapped validated rate."""
    candidates = generate_merchant_synthesis_candidates(
        {"merchant_name": "Vons", "recipes": []},
        _validated_taxable_merchant_receipts(),
        max_candidates=6,
    )

    taxable_adds = [
        candidate
        for candidate in candidates
        if candidate["metadata"]["operation"] == "add_line_item"
        and candidate["metadata"]["added_item"]["taxable"] is True
    ]

    assert taxable_adds
    arithmetic = taxable_adds[0]["metadata"]["arithmetic_reconciliation"]
    assert arithmetic["summary_update_policy"] == "taxable_item_delta"
    # Snapped to Vons' receipt-validated 7.25%, not the noisy observed rate.
    assert arithmetic["tax_rate"] == "0.0725"


def test_taxable_edits_rejected_for_mixed_jurisdiction_batch():
    """A Target batch mixing NV (8.375%) and CA (9.50%) receipts has receipts
    that disagree on the validated rate, so NO taxable edit is emitted — the
    batch median must not be allowed to snap the off-jurisdiction receipt."""
    receipts = _taxable_merchant_receipts_at_rates(
        Decimal("0.08375"), Decimal("0.0950")
    )
    candidates = generate_merchant_synthesis_candidates(
        {"merchant_name": "Target", "recipes": []},
        receipts,
        max_candidates=6,
    )
    taxable_edits = [
        candidate
        for candidate in candidates
        if candidate["metadata"]["operation"] in ("add_line_item", "remove_line_item")
        and (
            candidate["metadata"].get("added_item")
            or candidate["metadata"].get("removed_item")
        ).get("taxable")
        is True
    ]
    assert taxable_edits == []


def test_single_jurisdiction_config_rate_used_without_subtotal_label():
    """A single-jurisdiction validated merchant (Vons) generates taxable adds at
    its config rate (7.25%) even when the receipts carry no SUBTOTAL label — the
    config rate is ground truth and the delta reconciles via grand_total - tax."""
    candidates = generate_merchant_synthesis_candidates(
        {"merchant_name": "Vons", "recipes": []},
        _validated_merchant_receipts_no_subtotal(),
        max_candidates=80,
    )
    taxable_adds = [
        c
        for c in candidates
        if c["metadata"]["operation"] == "add_line_item"
        and c["metadata"]["added_item"]["taxable"] is True
    ]
    assert taxable_adds
    assert all(
        c["metadata"]["arithmetic_reconciliation"]["tax_rate"] == "0.0725"
        for c in taxable_adds
    )


def test_single_jurisdiction_rejected_when_effective_rate_exceeds_config():
    """A same-brand receipt from a HIGHER-tax jurisdiction (effective rate above
    the config rate) is refused — the brand-matched single-rate config must not
    be applied to a different jurisdiction. Effective rate uses reliable summary
    anchors, so it can't exceed the taxable-item rate for a correct receipt."""
    from receipt_agent.agents.label_evaluator.merchant_synthesis import (
        _taxable_edit_rate_for_receipt,
    )

    # taxable 10.00 + bread 3.00, tax 1.90 -> effective 1.90/13.00 = 14.6% (a
    # ~9.5% jurisdiction on a taxable-heavy basket), well above 7.25%.
    receipts = copy.deepcopy(_merchant_receipts_with_taxable_items())
    for receipt in receipts:
        for line in receipt["lines"]:
            for word in line["words"]:
                labels = word.get("labels") or []
                if "TAX" in labels:
                    word["text"] = "1.90"
                if "GRAND_TOTAL" in labels:
                    word["text"] = "14.90"
    analyses = [_analyze_receipt(_normalize_receipt(r)) for r in receipts]
    assert (
        _taxable_edit_rate_for_receipt("Vons", analyses, analyses[0]) is None
    )


def test_taxable_delta_reconciles_without_a_subtotal_label():
    """_apply_taxable_delta reconstructs the subtotal anchor from grand - tax
    when no SUBTOTAL label exists, so it reconciles instead of bailing."""
    receipts = _validated_merchant_receipts_no_subtotal()
    analysis = _analyze_receipt(_normalize_receipt(receipts[0]))
    assert analysis.subtotal is None  # no SUBTOTAL label present
    assert analysis.tax_total is not None and analysis.grand_total is not None
    result = _apply_taxable_delta(
        copy.deepcopy(analysis.receipt),
        analysis,
        delta=Decimal("-10.00"),
        rate=Decimal("0.0725"),
    )
    assert result is not None
    # subtotal reconstructed as grand(13.73) - tax(0.73) = 13.00, then -10.00.
    assert result["new_subtotal"] == "3.00"
    assert result["summary_update_policy"] == "taxable_item_delta"


def test_taxable_removal_larger_than_subtotal_is_rejected():
    """A taxable removal larger than the (reconstructed) subtotal must bail, not
    clamp the subtotal to zero (which would emit a wrong total movement)."""
    receipts = _validated_merchant_receipts_no_subtotal()
    analysis = _analyze_receipt(_normalize_receipt(receipts[0]))  # subtotal 13.00
    result = _apply_taxable_delta(
        copy.deepcopy(analysis.receipt),
        analysis,
        delta=Decimal("-99.00"),  # exceeds the 13.00 reconstructed subtotal
        rate=Decimal("0.0725"),
    )
    assert result is None


def test_taxable_edits_rejected_when_a_blind_positive_tax_receipt_is_present():
    """Regression: a positive-TAX receipt whose taxable flag didn't parse is
    invisible to the rate-observation set. For a multi-jurisdiction merchant it
    could be a different jurisdiction than the unanimous batch rate, so its
    presence must block ALL taxable edits — no edit may be applied at a rate the
    blind receipt can't confirm."""
    receipts = _taxable_merchant_receipts_at_rates(
        Decimal("0.0950"), Decimal("0.0950")
    )
    receipts.append(_blind_positive_tax_receipt())
    candidates = generate_merchant_synthesis_candidates(
        {"merchant_name": "Target", "recipes": []},
        receipts,
        max_candidates=8,
    )
    taxable_edits = [
        candidate
        for candidate in candidates
        if candidate["metadata"]["operation"] in ("add_line_item", "remove_line_item")
        and (
            candidate["metadata"].get("added_item")
            or candidate["metadata"].get("removed_item")
        ).get("taxable")
        is True
    ]
    assert taxable_edits == []


def test_blind_positive_tax_receipt_is_tolerated_for_single_jurisdiction():
    """A single-rate merchant (Vons) has only one possible jurisdiction, so a
    blind positive-TAX receipt cannot be a different rate — taxable edits on the
    observed receipts stay enabled."""
    receipts = _validated_taxable_merchant_receipts()
    receipts.append(_blind_positive_tax_receipt())
    candidates = generate_merchant_synthesis_candidates(
        {"merchant_name": "Vons", "recipes": []},
        receipts,
        max_candidates=8,
    )
    taxable_adds = [
        candidate
        for candidate in candidates
        if candidate["metadata"]["operation"] == "add_line_item"
        and candidate["metadata"]["added_item"]["taxable"] is True
    ]
    assert taxable_adds


def test_taxable_edits_use_one_rate_for_single_jurisdiction_batch():
    """A single-jurisdiction Target batch (all CA 9.50%) unlocks taxable edits
    at that one validated rate."""
    receipts = _taxable_merchant_receipts_at_rates(
        Decimal("0.0950"), Decimal("0.0950")
    )
    candidates = generate_merchant_synthesis_candidates(
        {"merchant_name": "Target", "recipes": []},
        receipts,
        max_candidates=6,
    )
    taxable_adds = [
        candidate
        for candidate in candidates
        if candidate["metadata"]["operation"] == "add_line_item"
        and candidate["metadata"]["added_item"]["taxable"] is True
    ]
    assert taxable_adds
    assert (
        taxable_adds[0]["metadata"]["arithmetic_reconciliation"]["tax_rate"]
        == "0.0950"
    )


def test_taxable_delta_splits_tax_across_multiple_tax_rows():
    receipt = {
        "lines": [
            {
                "line_id": 1,
                "words": [
                    _word("SUBTOTAL", [500, 700, 600, 725], ["SUBTOTAL"]),
                    _word("100.00", [820, 700, 900, 725], ["SUBTOTAL"]),
                ],
            },
            {
                "line_id": 2,
                "words": [
                    _word("STATE", [500, 670, 570, 695]),
                    _word("TAX", [580, 670, 620, 695], ["TAX"]),
                    _word("2.00", [820, 670, 900, 695], ["TAX"]),
                ],
            },
            {
                "line_id": 3,
                "words": [
                    _word("CITY", [500, 640, 550, 665]),
                    _word("TAX", [560, 640, 600, 665], ["TAX"]),
                    _word("3.00", [820, 640, 900, 665], ["TAX"]),
                ],
            },
            {
                "line_id": 4,
                "words": [
                    _word("TOTAL", [500, 610, 570, 635], ["GRAND_TOTAL"]),
                    _word("105.00", [820, 610, 900, 635], ["GRAND_TOTAL"]),
                ],
            },
        ]
    }
    analysis = MerchantAnalysis(
        receipt=receipt,
        line_items=[],
        subtotal=Decimal("100.00"),
        tax_total=Decimal("5.00"),
        grand_total=Decimal("105.00"),
        grand_total_line_indices=[3],
    )

    arithmetic = _apply_taxable_delta(
        receipt,
        analysis,
        delta=Decimal("-10.00"),
        rate=Decimal("0.1000"),
    )

    assert arithmetic is not None
    assert arithmetic["new_tax"] == "4.00"
    tax_amounts = [
        word["text"]
        for line in receipt["lines"]
        for word in line["words"]
        if "TAX" in (word.get("labels") or []) and word["text"] not in {"TAX"}
    ]
    assert tax_amounts == ["1.60", "2.40"]
    assert sum(Decimal(value) for value in tax_amounts) == Decimal("4.00")


def test_taxable_removal_dedupes_by_removed_item_identity():
    receipts = copy.deepcopy(_merchant_receipts_with_taxable_items())
    replacements = {
        "3.00": "30.00",
        "13.00": "40.00",
        "13.78": "40.78",
        "23.00": "50.00",
        "24.55": "51.55",
    }
    for receipt in receipts:
        for line in receipt["lines"]:
            for word in line["words"]:
                if word["text"] in replacements:
                    word["text"] = replacements[word["text"]]

    candidates = generate_merchant_synthesis_candidates(
        {"merchant_name": "Taxable Mart", "recipes": []},
        receipts,
        max_candidates=6,
    )
    removals = [
        candidate
        for candidate in candidates
        if candidate["metadata"]["operation"] == "remove_line_item"
    ]
    identities = {
        (
            candidate["metadata"]["base_receipt_key"],
            candidate["metadata"]["removed_item"]["product_text"],
            candidate["metadata"]["removed_item"]["line_total"],
            candidate["metadata"]["removed_item"]["taxable"],
        )
        for candidate in removals
    }

    assert removals
    assert len(removals) == len(identities)


def test_compose_online_catalog_absent_without_a_catalog():
    """Merchants with no registered/injected online catalog are unaffected."""
    receipts = _merchant_receipts_with_taxable_items()
    candidates = generate_merchant_synthesis_candidates(
        {"merchant_name": "Taxable Mart", "recipes": []},
        receipts,
        max_candidates=4,
    )
    assert all(
        candidate["metadata"]["operation"] != "compose_online_catalog"
        for candidate in candidates
    )


def test_compose_online_catalog_skips_without_stable_tax_rate():
    """A single receipt yields one tax observation, which is not enough to
    establish a stable rate, so no taxable receipt is composed."""
    receipts = _merchant_receipts_with_taxable_items()[:1]
    rate, stable, observations = _stable_tax_rate(
        [_analyze_receipt(_normalize_receipt(r)) for r in receipts]
    )
    assert rate is None and stable is False and len(observations) < 2

    candidates = generate_merchant_synthesis_candidates(
        {"merchant_name": "Taxable Mart", "recipes": []},
        receipts,
        max_candidates=4,
        online_catalog=_online_catalog(),
    )
    assert all(
        candidate["metadata"]["operation"] != "compose_online_catalog"
        for candidate in candidates
    )


def test_compose_quality_requires_compose_specific_guarantees():
    quality = build_synthesis_candidate_quality(
        "compose_online_catalog",
        {
            "layout_integrity": {"score": 1.0},
            "structure_similarity": {
                "score": 0.95,
                "components": {
                    "category_sequence": 1.0,
                    "category_set": 1.0,
                    "item_count": 1.0,
                    "token_count": 1.0,
                    "price_column": 1.0,
                    "line_step": 1.0,
                },
            },
            "online_catalog_grounding": {
                "all_priced": True,
                "all_named": True,
            },
            "label_control": {"all_correct": False},
            "arithmetic_reconciliation": {
                "subtotal_consistent": True,
                "tax_rate_stable": True,
            },
        },
        token_count=48,
    )

    assert quality["components"]["label_control"] == 0.0
    assert quality["score"] >= 0.75
    assert quality["high_fidelity"] is False


def test_injected_catalog_is_included_in_compose_readiness_contract():
    readiness = build_merchant_synthesis_readiness(
        "Injected Catalog Mart",
        _merchant_receipts_with_taxable_items(),
        online_catalog=_online_catalog(),
    )

    assert readiness is not None
    assert "compose_online_catalog" in readiness["supported_operations"]
    assert readiness["compose_online_catalog_candidate_count"] == len(_online_catalog())


def test_compose_only_merchant_is_not_blocked_by_empty_edit_catalog():
    readiness = build_merchant_synthesis_readiness(
        "Taxable Only Mart",
        _numeric_product_receipts(),
        online_catalog=_online_catalog(),
    )

    assert readiness is not None
    assert "compose_online_catalog" in readiness["supported_operations"]
    assert "no_observed_item_catalog" not in readiness["blockers"]
    assert "no_observed_item_catalog" in readiness["limitations"]


def test_compose_readiness_uses_same_tax_population_as_generation():
    receipts = _all_taxable_receipts() + [
        _summary_only_receipt(
            "summary_1",
            "40000000-0000-0000-0000-000000000011",
            subtotal="10.00",
            tax="1.20",
            grand_total="11.20",
        ),
        _summary_only_receipt(
            "summary_2",
            "40000000-0000-0000-0000-000000000012",
            subtotal="10.00",
            tax="1.40",
            grand_total="11.40",
        ),
    ]

    readiness = build_merchant_synthesis_readiness(
        "Mixed Tax Mart",
        receipts,
        online_catalog=_online_catalog(),
    )

    assert readiness is not None
    assert "compose_online_catalog" not in readiness["supported_operations"]
    assert readiness["compose_online_catalog_candidate_count"] == 0


def test_template_fill_requires_at_least_one_rendered_product_name():
    row = _build_template_filled_row(
        OnlineCatalogEntry("EXTREMELY LONG PRODUCT NAME", Decimal("1.00"), "123"),
        y0=500,
        geo={"char_w": 10, "name_x0": 200, "price_x1": 240, "height": 20},
        line_id=20_000,
    )

    assert row is None


def test_composed_totals_update_unlabeled_amounts_on_labeled_lines():
    receipt = {
        "lines": [
            {
                "line_id": 1,
                "words": [
                    _word("SUBTOTAL", [500, 590, 600, 615], ["SUBTOTAL"]),
                    _word("8.00", [830, 590, 890, 615]),
                ],
            },
            {
                "line_id": 2,
                "words": [
                    _word("TAX", [500, 565, 545, 590], ["TAX"]),
                    _word("0.78", [830, 565, 890, 590]),
                ],
            },
            {
                "line_id": 3,
                "words": [
                    _word("TOTAL", [500, 535, 595, 560], ["GRAND_TOTAL"]),
                    _word("8.78", [820, 535, 890, 560]),
                ],
            },
        ]
    }

    counts = _write_composed_totals(
        receipt,
        Decimal("10.00"),
        Decimal("0.80"),
        Decimal("10.80"),
        old_grand_total=Decimal("8.78"),
    )

    assert [word["text"] for line in receipt["lines"] for word in line["words"]] == [
        "SUBTOTAL",
        "10.00",
        "TAX",
        "0.80",
        "TOTAL",
        "10.80",
    ]
    assert counts == {
        "subtotal": 1,
        "tax": 1,
        "grand_total": 1,
        "payment_or_balance": 0,
    }


def test_layout_integrity_counts_lower_synthetic_line_id_collisions():
    receipt = {
        "lines": [
            {
                "line_id": 1,
                "words": [_word("APPLES", [100, 500, 220, 530], ["PRODUCT_NAME"])],
            },
            {
                "line_id": 10_000,
                "words": [_word("SURVEY", [120, 505, 240, 535])],
            },
        ]
    }

    evidence = build_layout_integrity_evidence(receipt)

    assert evidence["synthetic_overlap_pair_count"] == 1
    assert evidence["score"] == 0.0


def test_home_depot_online_catalog_is_registered():
    """The seeded Home Depot catalog is available via case-insensitive lookup."""
    entries = _merchant_online_catalog("The Home Depot")
    assert len(entries) >= 3
    assert all(entry.price > Decimal("0.00") and entry.name for entry in entries)
    assert _merchant_online_catalog("the home depot")
    assert _merchant_online_catalog("Unknown Merchant") == []


def test_store_merchant_match_uses_significant_prefix_not_first_token():
    from receipt_agent.agents.label_evaluator.merchant_synthesis import (
        _store_merchant_match,
    )

    assert _store_merchant_match("The Home Depot", "Home Depot #123")
    assert _store_merchant_match("Gelson's", "Gelson's Westlake Village")
    assert _store_merchant_match("VONS", "Vons")
    assert not _store_merchant_match("The Home Depot", "The Coffee Bean")
    assert not _store_merchant_match("Amazon Fresh", "Amazon Go")


def test_store_header_swap_includes_website_lines():
    from receipt_agent.agents.label_evaluator.merchant_synthesis import (
        _apply_store_header_swap,
    )
    from receipt_agent.agents.label_evaluator.store_profile import StoreProfile

    receipt = {
        "lines": [
            {
                "line_id": 1,
                "y": 0.90,
                "words": [
                    _word("2725", [100, 900, 170, 920], ["ADDRESS_LINE"]),
                    _word("Agoura", [175, 900, 280, 920], ["ADDRESS_LINE"]),
                    _word("Road", [285, 900, 360, 920], ["ADDRESS_LINE"]),
                ],
            },
            {
                "line_id": 2,
                "y": 0.87,
                "words": [
                    _word("Agoura", [100, 870, 210, 890], ["ADDRESS_LINE"]),
                    _word("Hills,", [215, 870, 300, 890], ["ADDRESS_LINE"]),
                    _word("CA", [305, 870, 340, 890], ["ADDRESS_LINE"]),
                    _word("91301", [345, 870, 430, 890], ["ADDRESS_LINE"]),
                ],
            },
            {
                "line_id": 3,
                "y": 0.84,
                "words": [
                    _word("(805) 497-1921", [100, 840, 310, 860], ["PHONE_NUMBER"])
                ],
            },
            {
                "line_id": 4,
                "y": 0.81,
                "words": [
                    _word("old.example.com", [100, 810, 320, 830], ["WEBSITE"])
                ],
            },
        ]
    }
    alt = StoreProfile(
        "place-2",
        "Vons",
        street="1790 N Moorpark Rd",
        city_state_zip="Thousand Oaks, CA 91360",
        phone="877-276-9637",
        website="local.vons.com",
    )

    swaps = _apply_store_header_swap(receipt, alt)

    assert swaps is not None
    assert sorted({swap["label"] for swap in swaps}) == [
        "ADDRESS_LINE",
        "PHONE_NUMBER",
        "WEBSITE",
    ]
    assert any(
        word["text"] == "local.vons.com" and word["labels"] == ["WEBSITE"]
        for line in receipt["lines"]
        for word in line["words"]
    )
