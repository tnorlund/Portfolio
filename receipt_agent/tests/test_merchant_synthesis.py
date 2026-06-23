"""Tests for generic merchant synthesis profiles and candidates."""

from receipt_agent.agents.label_evaluator.merchant_synthesis import (
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


def test_build_merchant_synthesis_profile_records_tax_policy_without_enabling_tax_edits():
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
            "tax_changing_loader_gate_not_enabled"
        ],
        "avg_tax_rate": "0.0778",
        "min_tax_rate": "0.0775",
        "max_tax_rate": "0.0780",
        "avg_tax_rate_percent": "7.78%",
    }
    readiness = profile["synthesis_readiness"]
    assert readiness["tax_policy"] == profile["tax_policy"]
    assert "tax_changing_synthesis_not_enabled" in readiness["limitations"]
    assert all(
        row["taxable"] is False for row in profile["observed_item_catalog"]
    )


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
    date_replacement = replacements[0]["metadata"]["field_replacement"]
    assert date_replacement == {
        "label": "DATE",
        "old_text": "05/13/2026",
        "new_text": "05/14/2026",
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
    assert "05/14/2026" in replacements[0]["tokens"]
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
    assert evidence["new_text"] == "05/14/2026"
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
    candidates = generate_merchant_synthesis_candidates(
        _plan(),
        _merchant_receipts(),
    )

    assert [candidate["metadata"]["source"] for candidate in candidates] == [
        "merchant_parameterized_geometry",
        "merchant_arithmetic_geometry",
    ]

    hard_negative = candidates[0]
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

    arithmetic = candidates[1]
    metadata = arithmetic["metadata"]
    assert metadata["operation"] == "add_line_item"
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
        "out_of_bounds_word_count": 0,
        "invalid_word_box_count": 0,
        "line_order_valid": True,
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
        "insert_y": 661.5,
        "shifted_lower_lines_by": 26,
        "shifted_line_count": 1,
        "line_step": 26,
        "selection_reason": (
            "observed item from another receipt inserted into the same "
            "category block on the base receipt"
        ),
        "base_receipt_has_category": True,
        "category_seen_count": 2,
        "category_heading_seen_count": 2,
        "category_alignment": "same_category_as_base",
    }
    assert metadata["category_insertion"] == {
        "category": "PRODUCE",
        "y_center": 661.5,
        "line_step": 26,
        "shifted_lower_lines_by": 26,
        "shifted_line_count": 1,
        "selection_reason": (
            "observed item from another receipt inserted into the same "
            "category block on the base receipt"
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
    assert metadata["removed_item"]["product_text"] == "PEARS"
    assert metadata["removed_item"]["category"] == "PRODUCE"
    assert metadata["old_grand_total"] == "10.00"
    assert metadata["new_grand_total"] == "8.00"
    assert metadata["old_subtotal"] == "10.00"
    assert metadata["new_subtotal"] == "8.00"
    assert metadata["arithmetic_reconciliation"]["tax_delta"] == "0.00"
    assert "PEARS" not in removed["tokens"]
    assert "APPLES" in removed["tokens"]
    assert "8.00" in removed["tokens"]


def test_generic_entry_point_uses_merchant_synthesis_for_non_sprouts():
    candidates = generate_synthetic_receipt_candidates(
        _plan(),
        receipts_data=_merchant_receipts(),
    )

    assert len(candidates) == 2
    assert (
        candidates[0].metadata["source"] == "merchant_parameterized_geometry"
    )
    assert candidates[1].metadata["source"] == "merchant_arithmetic_geometry"
