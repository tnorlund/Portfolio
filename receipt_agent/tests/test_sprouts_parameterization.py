"""Tests for Sprouts-specific receipt parameterization."""

from receipt_agent.agents.label_evaluator.pattern_discovery import (
    generate_synthetic_receipt_candidates,
)
from receipt_agent.agents.label_evaluator.sprouts_parameterization import (
    build_sprouts_receipt_parameters,
    generate_arithmetic_sprouts_candidates,
    generate_parameterized_sprouts_candidates,
)


def _sprouts_receipts():
    return [
        {
            "receipt_id": "img_1_1",
            "image_id": "00000000-0000-0000-0000-000000000001",
            "receipt_num": 1,
            "lines": [
                {
                    "line_id": 1,
                    "y": 0.98,
                    "words": [
                        {
                            "text": "SPROUTS",
                            "bbox": [430, 980, 540, 1000],
                            "labels": ["MERCHANT_NAME"],
                        }
                    ],
                },
                {
                    "line_id": 2,
                    "y": 0.96,
                    "words": [
                        {
                            "text": "FARMERS",
                            "bbox": [410, 955, 510, 975],
                            "labels": ["MERCHANT_NAME"],
                        },
                        {
                            "text": "MARKET",
                            "bbox": [520, 955, 610, 975],
                            "labels": ["MERCHANT_NAME"],
                        },
                    ],
                },
                {
                    "line_id": 3,
                    "y": 0.93,
                    "words": [
                        {
                            "text": "1012",
                            "bbox": [360, 925, 410, 945],
                            "labels": ["ADDRESS_LINE"],
                        },
                        {
                            "text": "WESTLAKE",
                            "bbox": [420, 925, 520, 945],
                            "labels": ["ADDRESS_LINE"],
                        },
                        {
                            "text": "BLVD.",
                            "bbox": [530, 925, 590, 945],
                            "labels": ["ADDRESS_LINE"],
                        },
                    ],
                },
                {
                    "line_id": 4,
                    "y": 0.91,
                    "words": [
                        {
                            "text": "WESTLAKE,",
                            "bbox": [390, 905, 500, 925],
                            "labels": ["ADDRESS_LINE"],
                        },
                        {
                            "text": "CA",
                            "bbox": [510, 905, 540, 925],
                            "labels": ["ADDRESS_LINE"],
                        },
                        {
                            "text": "91361",
                            "bbox": [550, 905, 615, 925],
                            "labels": ["ADDRESS_LINE"],
                        },
                    ],
                },
                {
                    "line_id": 5,
                    "y": 0.88,
                    "words": [
                        {
                            "text": "Store",
                            "bbox": [270, 875, 330, 895],
                            "labels": ["STORE_HOURS"],
                        },
                        {
                            "text": "Hours",
                            "bbox": [340, 875, 405, 895],
                            "labels": ["STORE_HOURS"],
                        },
                        {
                            "text": "MON-SUN",
                            "bbox": [415, 875, 505, 895],
                            "labels": ["STORE_HOURS"],
                        },
                    ],
                },
                {
                    "line_id": 6,
                    "y": 0.70,
                    "words": [
                        {
                            "text": "ORG",
                            "bbox": [90, 690, 135, 715],
                            "labels": ["PRODUCT_NAME"],
                        },
                        {
                            "text": "A2/A2",
                            "bbox": [145, 690, 210, 715],
                            "labels": ["PRODUCT_NAME"],
                        },
                        {
                            "text": "6%",
                            "bbox": [220, 690, 250, 715],
                            "labels": ["PRODUCT_NAME"],
                        },
                        {
                            "text": "FAT",
                            "bbox": [260, 690, 305, 715],
                            "labels": ["PRODUCT_NAME"],
                        },
                        {
                            "text": "MLK",
                            "bbox": [315, 690, 360, 715],
                            "labels": ["PRODUCT_NAME"],
                        },
                        {
                            "text": "7.99",
                            "bbox": [835, 690, 890, 715],
                            "labels": ["LINE_TOTAL"],
                        },
                    ],
                },
                {
                    "line_id": 7,
                    "y": 0.675,
                    "words": [
                        {
                            "text": "SOUR",
                            "bbox": [90, 665, 145, 690],
                            "labels": ["PRODUCT_NAME"],
                        },
                        {
                            "text": "CREAM",
                            "bbox": [155, 665, 220, 690],
                            "labels": ["PRODUCT_NAME"],
                        },
                        {
                            "text": "2.79",
                            "bbox": [835, 665, 890, 690],
                            "labels": ["LINE_TOTAL"],
                        },
                    ],
                },
                {
                    "line_id": 8,
                    "y": 0.615,
                    "words": [
                        {
                            "text": "BALANCE",
                            "bbox": [500, 610, 590, 635],
                            "labels": None,
                        },
                        {
                            "text": "DUE",
                            "bbox": [600, 610, 640, 635],
                            "labels": None,
                        },
                    ],
                },
                {
                    "line_id": 9,
                    "y": 0.59,
                    "words": [
                        {
                            "text": "10.78",
                            "bbox": [835, 585, 900, 610],
                            "labels": ["GRAND_TOTAL"],
                        }
                    ],
                },
            ],
        }
    ]


def _word(text, bbox, labels=None):
    return {"text": text, "bbox": bbox, "labels": labels}


def _category_sprouts_receipts():
    return [
        {
            "receipt_id": "category_base_1",
            "image_id": "10000000-0000-0000-0000-000000000001",
            "receipt_num": 1,
            "lines": [
                {
                    "line_id": 1,
                    "y": 0.98,
                    "words": [
                        _word(
                            "SPROUTS",
                            [430, 980, 540, 1000],
                            ["MERCHANT_NAME"],
                        )
                    ],
                },
                {
                    "line_id": 2,
                    "y": 0.74,
                    "words": [_word("PRODUCE", [90, 730, 180, 755])],
                },
                {
                    "line_id": 3,
                    "y": 0.705,
                    "words": [
                        _word(
                            "GREEN",
                            [90, 695, 150, 720],
                            ["PRODUCT_NAME"],
                        ),
                        _word(
                            "BEANS",
                            [160, 695, 230, 720],
                            ["PRODUCT_NAME"],
                        ),
                        _word(
                            "3.49",
                            [835, 695, 890, 720],
                            ["LINE_TOTAL"],
                        ),
                    ],
                },
                {
                    "line_id": 4,
                    "y": 0.665,
                    "words": [_word("DAIRY", [90, 655, 160, 680])],
                },
                {
                    "line_id": 5,
                    "y": 0.63,
                    "words": [
                        _word(
                            "MILK",
                            [90, 620, 145, 645],
                            ["PRODUCT_NAME"],
                        ),
                        _word(
                            "5.00",
                            [835, 620, 890, 645],
                            ["LINE_TOTAL"],
                        ),
                    ],
                },
                {
                    "line_id": 6,
                    "y": 0.58,
                    "words": [
                        _word("BALANCE", [500, 570, 590, 595]),
                        _word("DUE", [600, 570, 640, 595]),
                    ],
                },
                {
                    "line_id": 7,
                    "y": 0.555,
                    "words": [
                        _word("8.49", [835, 545, 900, 570], ["GRAND_TOTAL"])
                    ],
                },
            ],
        },
        {
            "receipt_id": "category_source_1",
            "image_id": "20000000-0000-0000-0000-000000000001",
            "receipt_num": 1,
            "lines": [
                {
                    "line_id": 1,
                    "y": 0.98,
                    "words": [
                        _word(
                            "SPROUTS",
                            [430, 980, 540, 1000],
                            ["MERCHANT_NAME"],
                        )
                    ],
                },
                {
                    "line_id": 2,
                    "y": 0.74,
                    "words": [_word("PRODUCE", [90, 730, 180, 755])],
                },
                {
                    "line_id": 3,
                    "y": 0.705,
                    "words": [
                        _word(
                            "YELLOW",
                            [90, 695, 160, 720],
                            ["PRODUCT_NAME"],
                        ),
                        _word(
                            "BANANAS",
                            [170, 695, 250, 720],
                            ["PRODUCT_NAME"],
                        ),
                        _word(
                            "1.95",
                            [835, 695, 890, 720],
                            ["LINE_TOTAL"],
                        ),
                    ],
                },
                {
                    "line_id": 4,
                    "y": 0.66,
                    "words": [
                        _word("BALANCE", [500, 650, 590, 675]),
                        _word("DUE", [600, 650, 640, 675]),
                    ],
                },
                {
                    "line_id": 5,
                    "y": 0.635,
                    "words": [
                        _word("1.95", [835, 625, 900, 650], ["GRAND_TOTAL"])
                    ],
                },
            ],
        },
    ]


def _plan():
    def recipe(label):
        return {
            "recipe_id": f"sprouts-o-{label.lower()}",
            "actual_label": "O",
            "predicted_label": label,
            "error_kind": "false_positive",
            "objective": f"Add hard negatives for {label}.",
            "merchant_scope": "same_merchant",
            "target_zone": {
                "label": label,
                "y_band": "observed",
                "x_zone": "observed",
            },
            "source_examples": [],
            "retrieval_queries": [],
            "layout_constraints": [],
            "mutation_steps": [],
            "expected_label_effect": f"Improve {label} precision.",
            "safeguards": [],
        }

    return {
        "merchant_name": "Sprouts Farmers Market",
        "source_receipt_count": 1,
        "confusion_target_count": 3,
        "recipes": [
            recipe("ADDRESS_LINE"),
            recipe("DISCOUNT"),
            recipe("GRAND_TOTAL"),
        ],
        "synthetic_receipt_guidance": [],
        "similar_merchant_mining": {},
        "metric_guardrails": [],
        "overtraining_guards": [],
    }


def test_build_sprouts_receipt_parameters_finds_stable_slots():
    parameters = build_sprouts_receipt_parameters(_sprouts_receipts())

    assert parameters is not None
    assert parameters.receipt_count == 1
    assert parameters.generation_limits["max_candidates_per_training_run"] == 5
    assert (
        parameters.generation_limits[
            "max_arithmetic_candidates_per_training_run"
        ]
        == 2
    )
    assert parameters.slots["address_block"].examples[:3] == [
        "1012",
        "WESTLAKE",
        "BLVD.",
    ]
    assert parameters.slots["product_column"].x.p50 < 260
    assert parameters.slots["line_total_column"].x.p50 > 850
    assert any(
        "Percent-bearing" in pattern
        for pattern in parameters.product_name_patterns
    )
    assert parameters.real_structure_baseline == {
        "schema_version": "real-structure-baseline-v1",
        "receipt_count": 1,
        "pair_count": 0,
        "score_summary": {"count": 0},
        "component_summaries": {},
    }


def test_generate_parameterized_sprouts_candidates_clones_receipt_geometry():
    candidates = generate_parameterized_sprouts_candidates(
        _plan(),
        _sprouts_receipts(),
    )

    assert len(candidates) == 3
    assert {
        candidate["metadata"]["predicted_label"] for candidate in candidates
    } == {
        "ADDRESS_LINE",
        "DISCOUNT",
        "GRAND_TOTAL",
    }
    first = candidates[0]
    assert first["train_only"] is True
    assert first["metadata"]["source"] == "sprouts_parameterized_geometry"
    assert first["metadata"]["operation"] == "hard_negative"
    assert first["metadata"]["structure_similarity"]["score"] > 0
    assert (
        "nearest_real_receipt_key" in first["metadata"]["structure_similarity"]
    )
    assert "shape_deltas" in first["metadata"]["structure_similarity"]
    assert "match_summary" in first["metadata"]["structure_similarity"]
    assert (
        "price_column_aligned"
        in first["metadata"]["structure_similarity"]["match_summary"][
            "shape_checks"
        ]
    )
    quality = first["metadata"]["candidate_quality"]
    assert quality["schema_version"] == "synthetic-candidate-quality-v1"
    assert quality["high_fidelity"] is True
    assert quality["components"]["local_distractor"] == 1.0
    assert quality["components"]["target_label_slot"] == 1.0
    preview = first["metadata"]["synthetic_receipt_preview"]
    assert (
        preview["coordinate_system"]
        == "normalized_receipt_0_1000_y_high_is_top"
    )
    assert "LOCAL FAVORITES" in preview["text"]
    assert any(
        line["synthetic_insert"] and line["text"] == "LOCAL FAVORITES"
        for line in preview["lines"]
    )
    evidence = first["metadata"]["synthesis_accuracy_evidence"]
    assert evidence["operation"] == "hard_negative"
    assert evidence["predicted_label"] == "ADDRESS_LINE"
    assert "inserted_o_label_distractor" in evidence["checks"]
    assert "SPROUTS" in first["tokens"]
    assert "LOCAL" in first["tokens"]
    local_index = first["tokens"].index("LOCAL")
    assert first["ner_tags"][local_index] == "O"
    assert "B-GRAND_TOTAL" in first["ner_tags"]
    assert all(
        len(box) == 4 and all(0 <= coord <= 1000 for coord in box)
        for box in first["bboxes"]
    )


def test_generic_candidate_generator_uses_sprouts_parameterization():
    candidates = generate_synthetic_receipt_candidates(
        _plan(),
        receipts_data=_sprouts_receipts(),
    )

    assert len(candidates) == 4
    assert candidates[0].metadata["source"] == "sprouts_parameterized_geometry"
    assert len(candidates[0].tokens) > 20
    assert "ORG" in candidates[0].tokens
    assert candidates[-1].metadata["source"] == "sprouts_arithmetic_geometry"
    assert candidates[-1].metadata["operation"] == "remove_line_item"


def test_generate_arithmetic_sprouts_candidates_requires_observed_add_items():
    candidates = generate_arithmetic_sprouts_candidates(_sprouts_receipts())

    assert [
        candidate["metadata"]["operation"] for candidate in candidates
    ] == ["remove_line_item"]

    removed = candidates[0]
    assert removed["metadata"]["old_grand_total"] == "10.78"
    assert removed["metadata"]["new_grand_total"] == "7.99"
    assert removed["metadata"]["old_subtotal"] == "10.78"
    assert removed["metadata"]["new_subtotal"] == "7.99"
    assert (
        removed["metadata"]["arithmetic_reconciliation"]["grand_total_delta"]
        == "-2.79"
    )
    assert (
        removed["metadata"]["arithmetic_reconciliation"]["tax_delta"] == "0.00"
    )
    assert removed["metadata"]["removed_item"]["product_text"] == "SOUR CREAM"
    assert removed["metadata"]["candidate_quality"]["high_fidelity"] is True
    assert (
        removed["metadata"]["candidate_quality"]["components"][
            "arithmetic_reconciliation"
        ]
        >= 0.8
    )
    assert (
        removed["metadata"]["candidate_quality"]["components"][
            "removed_item_safe"
        ]
        == 1.0
    )
    assert "SOUR" not in removed["tokens"]
    assert "7.99" in removed["tokens"]

    for candidate in candidates:
        assert candidate["train_only"] is True
        assert len(candidate["tokens"]) == len(candidate["bboxes"])
        assert len(candidate["tokens"]) == len(candidate["ner_tags"])
        assert all(
            len(box) == 4 and all(0 <= coord <= 1000 for coord in box)
            for box in candidate["bboxes"]
        )


def test_generate_arithmetic_sprouts_candidates_uses_category_catalog():
    candidates = generate_arithmetic_sprouts_candidates(
        _category_sprouts_receipts(),
        max_candidates=1,
    )

    assert len(candidates) == 1
    added = candidates[0]
    metadata = added["metadata"]
    assert metadata["operation"] == "add_line_item"
    assert metadata["old_grand_total"] == "8.49"
    assert metadata["new_grand_total"] == "10.44"
    assert metadata["old_subtotal"] == "8.49"
    assert metadata["new_subtotal"] == "10.44"
    assert metadata["arithmetic_reconciliation"]["subtotal_delta"] == "1.95"
    assert metadata["arithmetic_reconciliation"]["tax_delta"] == "0.00"
    assert (
        metadata["arithmetic_reconciliation"]["updated_summary_labels"][
            "grand_total"
        ]
        == 1
    )
    assert metadata["added_item"]["product_text"] == "YELLOW BANANAS"
    assert metadata["added_item"]["category"] == "PRODUCE"
    assert metadata["added_item"]["seen_in_other_receipt"] is True
    assert metadata["category_insertion"]["category"] == "PRODUCE"
    assert metadata["category_insertion"]["shifted_lower_lines_by"] > 0
    assert (
        metadata["observed_item_evidence"]["base_receipt_has_category"] is True
    )
    assert metadata["observed_item_evidence"]["product_seen_outside_base"] == [
        "20000000-0000-0000-0000-000000000001#00001"
    ]
    assert metadata["structure_similarity"]["score"] > 0
    baseline_comparison = metadata["structure_similarity"][
        "real_baseline_comparison"
    ]
    assert baseline_comparison["baseline_pair_count"] == 1
    assert (
        baseline_comparison["candidate_score"]
        == metadata["structure_similarity"]["score"]
    )
    assert "within_real_score_range" in baseline_comparison
    assert (
        metadata["structure_similarity"]["shape_deltas"][
            "line_item_count_delta"
        ]
        >= 0
    )
    assert (
        "category_set_close"
        in metadata["structure_similarity"]["match_summary"]["shape_checks"]
    )
    assert metadata["candidate_quality"]["high_fidelity"] is True
    assert metadata["layout_integrity"]["passed"] is True
    assert (
        metadata["candidate_quality"]["components"]["layout_integrity"] == 1.0
    )
    assert (
        metadata["candidate_quality"]["components"]["cross_receipt_grounding"]
        == 1.0
    )
    assert (
        metadata["candidate_quality"]["components"]["category_alignment"]
        == 1.0
    )
    assert (
        metadata["candidate_quality"]["components"][
            "arithmetic_reconciliation"
        ]
        >= 0.8
    )
    preview = metadata["synthetic_receipt_preview"]
    assert "YELLOW BANANAS 1.95" in preview["text"]
    assert any(
        line["synthetic_insert"] and line["role"] == "line_item"
        for line in preview["lines"]
    )
    evidence = metadata["synthesis_accuracy_evidence"]
    assert evidence["changed_text"] == "YELLOW BANANAS"
    assert evidence["category"] == "PRODUCE"
    assert evidence["tax_delta"] == "0.00"
    assert evidence["layout_integrity"]["passed"] is True
    assert evidence["layout_integrity"]["overlap_pair_count"] == 0
    assert evidence["structure_similarity"]["shape_deltas"] == (
        metadata["structure_similarity"]["shape_deltas"]
    )
    assert evidence["structure_similarity"]["real_baseline_comparison"] == (
        metadata["structure_similarity"]["real_baseline_comparison"]
    )
    assert evidence["catalog_grounding"] == {
        "product_observed_count": 1,
        "product_seen_receipt_count": 1,
        "product_seen_outside_base_count": 1,
        "product_seen_outside_base": [
            "20000000-0000-0000-0000-000000000001#00001"
        ],
        "category": "PRODUCE",
        "category_seen_count": 2,
        "category_heading_seen_count": 2,
        "category_seen_in_receipts": [
            "10000000-0000-0000-0000-000000000001#00001",
            "20000000-0000-0000-0000-000000000001#00001",
        ],
    }
    assert evidence["category_placement"] == {
        "category": "PRODUCE",
        "insert_y": 665.5,
        "shifted_lower_lines_by": 42,
        "selection_reason": (
            "observed in another Sprouts receipt with the same category"
        ),
        "base_receipt_has_category": True,
        "category_seen_count": 2,
        "category_heading_seen_count": 2,
        "category_alignment": "same_category_as_base",
    }
    assert {
        "item_seen_in_other_receipt",
        "base_receipt_has_category",
        "category_heading_seen_in_real_receipts",
        "non_taxable_arithmetic_reconciled",
        "layout_integrity_checked",
        "no_overlapping_or_out_of_bounds_boxes",
    }.issubset(evidence["checks"])

    produce_index = added["tokens"].index("PRODUCE")
    yellow_index = added["tokens"].index("YELLOW")
    dairy_index = added["tokens"].index("DAIRY")
    assert produce_index < yellow_index < dairy_index


def test_generated_arithmetic_sprouts_candidates_pass_layoutlm_gate():
    import sys
    from pathlib import Path

    layoutlm_source = Path(__file__).resolve().parents[2] / "receipt_layoutlm"
    sys.path.insert(0, str(layoutlm_source))
    from receipt_layoutlm.data_loader import (
        _select_synthetic_training_examples,
    )

    candidates = generate_arithmetic_sprouts_candidates(
        _sprouts_receipts()
    ) + generate_arithmetic_sprouts_candidates(
        _category_sprouts_receipts(),
        max_candidates=1,
    )

    selected = _select_synthetic_training_examples(
        candidates,
        max_per_merchant=5,
        max_per_merchant_operation=2,
        min_structure_similarity=0.6,
    )

    assert selected.candidates_seen == 2
    assert selected.candidates_accepted == 2
    assert selected.rejection_reasons == {}
    assert selected.accepted_operation_counts == {
        "add_line_item": 1,
        "remove_line_item": 1,
    }
    assert selected.accepted_arithmetic_count == 2
    assert selected.accepted_grounded_count == 1
