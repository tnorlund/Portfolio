"""Tests for offline synthetic replay preflight checks."""

import importlib.util
import json
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / ("verify_synthetic_replay.py")
)
STRUCTURE_COMPONENT_THRESHOLDS = {
    "price_column": 0.75,
    "line_step": 0.45,
    "category_sequence": 0.4,
    "category_set": 0.4,
    "token_count": 0.35,
}


def _blocked_source_quality(merchant_name="Market Mart"):
    return {
        "merchant_name": merchant_name,
        "status": "blocked",
        "receipt_count": 2,
        "receipts_with_lines": 2,
        "receipts_with_words": 2,
        "receipts_with_labels": 0,
        "receipts_with_merchant_name_label": 0,
        "receipts_with_line_item_labels": 0,
        "receipts_with_grand_total_label": 0,
        "receipts_with_date_or_time_label": 0,
        "line_count": 24,
        "word_count": 120,
        "labeled_word_count": 0,
        "top_labels": {},
        "blockers": ["no_word_labels"],
        "limitations": ["no_labeled_line_items", "no_grand_total_labels"],
    }


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "verify_synthetic_replay_for_test",
        MODULE_PATH,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _artifact(
    merchant_name="Market Mart",
    *,
    status="ready",
    score=0.86,
    candidates=True,
    candidate_quality=True,
    source_receipt_quality=None,
):
    artifact = {
        "merchant_name": merchant_name,
        "_trace_metadata": {
            "llm_execution": {
                "mode": "deterministic_fallback",
                "paid_llm_disabled": True,
                "api_call_allowed": False,
                "configured_model": "openai/gpt-5.5",
                "model_profile": "cheap",
                "latest_openai_model": "gpt-5.5",
                "latest_model_source": (
                    "https://developers.openai.com/api/docs/guides/latest-model"
                ),
                "latest_model_verified_at": "2026-06-23",
            }
        },
        "merchant_receipt_parameterization": {
            "synthesis_readiness": {
                "merchant_name": merchant_name,
                "status": status,
                "score": score,
                "supported_operations": [
                    "hard_negative",
                    "add_line_item",
                ],
                "candidate_capacity": 4,
                "hard_negative_label_count": 1,
                "grounded_add_item_candidate_count": 1,
                "blockers": [] if status != "blocked" else ["no_line_items"],
                "limitations": [],
            }
        },
        "synthetic_receipt_candidates": (
            [
                {
                    "candidate_id": "grounded-add-item",
                    "merchant_name": merchant_name,
                    "image_id": f"synthetic-{merchant_name}-add",
                    "receipt_key": f"synthetic-{merchant_name}-add#00001",
                    "train_only": True,
                    "tokens": ["BANANAS", "1.95"],
                    "bboxes": [[90, 695, 180, 720], [835, 695, 890, 720]],
                    "ner_tags": ["B-PRODUCT_NAME", "B-LINE_TOTAL"],
                    "metadata": {
                        "operation": "add_line_item",
                        "added_item": {
                            "category": "PRODUCE",
                            "seen_in_other_receipt": True,
                        },
                        "observed_item_evidence": {
                            "product_seen_outside_base": ["source#00001"],
                            "category": "PRODUCE",
                            "category_seen_in_receipts": [
                                "base#00001",
                                "source#00001",
                            ],
                            "category_seen_count": 2,
                            "base_receipt_has_category": True,
                        },
                        "arithmetic_reconciliation": {
                            "summary_update_policy": "non_taxable_item_delta",
                            "tax_delta": "0.00",
                        },
                        "structure_similarity": {
                            "score": 0.92,
                            "components": {
                                "category_sequence": 0.67,
                                "category_set": 0.50,
                                "item_count": 0.33,
                                "token_count": 0.47,
                                "price_column": 1.00,
                                "line_step": 0.65,
                            },
                        },
                    },
                },
                {
                    "candidate_id": "hard-negative",
                    "merchant_name": merchant_name,
                    "image_id": f"synthetic-{merchant_name}-hard-negative",
                    "receipt_key": (f"synthetic-{merchant_name}-hard-negative#00001"),
                    "train_only": True,
                    "tokens": ["REWARDS", "4.49"],
                    "bboxes": [[390, 60, 470, 88], [720, 520, 780, 548]],
                    "ner_tags": ["O", "O"],
                    "metadata": {
                        "operation": "hard_negative",
                        "error_kind": "false_positive",
                        "actual_label": "O",
                        "predicted_label": "LINE_TOTAL",
                        "structure_similarity": {
                            "score": 0.88,
                            "components": {
                                "category_sequence": 0.67,
                                "category_set": 0.50,
                                "item_count": 0.50,
                                "token_count": 0.47,
                                "price_column": 1.00,
                                "line_step": 0.55,
                            },
                        },
                    },
                },
            ]
            if candidates
            else []
        ),
    }
    if candidates and candidate_quality:
        add_item, hard_negative = artifact["synthetic_receipt_candidates"]
        add_item["metadata"]["candidate_quality"] = {
            "schema_version": "synthetic-candidate-quality-v1",
            "score": 0.93,
            "high_fidelity": True,
            "components": {
                "arithmetic_reconciliation": 0.9,
                "category_alignment": 1.0,
                "cross_receipt_grounding": 1.0,
                "structure_similarity": 0.92,
                "token_budget": 1.0,
            },
        }
        hard_negative["metadata"]["candidate_quality"] = {
            "schema_version": "synthetic-candidate-quality-v1",
            "score": 0.9,
            "high_fidelity": True,
            "components": {
                "structure_similarity": 0.88,
                "token_budget": 1.0,
            },
        }
    if source_receipt_quality is not None:
        artifact["source_receipt_quality"] = source_receipt_quality
    return artifact


def _word(text, bbox, labels=None):
    return {"text": text, "bbox": bbox, "labels": labels}


def _receipt_group_payload(merchant_name="Market Mart"):
    return {
        "merchant_name": merchant_name,
        "receipts": [
            {
                "receipt_id": "market_1",
                "image_id": "30000000-0000-0000-0000-000000000001",
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
                        "y": 0.72,
                        "words": [_word("PRODUCE", [80, 710, 175, 735])],
                    },
                    {
                        "line_id": 3,
                        "y": 0.685,
                        "words": [
                            _word("APPLES", [85, 675, 165, 700], ["PRODUCT_NAME"]),
                            _word("3.00", [830, 675, 885, 700], ["LINE_TOTAL"]),
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
                            _word("MILK", [85, 595, 145, 620], ["PRODUCT_NAME"]),
                            _word("5.00", [830, 595, 885, 620], ["LINE_TOTAL"]),
                        ],
                    },
                    {
                        "line_id": 6,
                        "y": 0.575,
                        "words": [
                            _word("SUBTOTAL", [500, 565, 600, 590]),
                            _word("8.00", [830, 565, 885, 590], ["SUBTOTAL"]),
                        ],
                    },
                    {
                        "line_id": 7,
                        "y": 0.535,
                        "words": [
                            _word("BALANCE", [500, 525, 595, 550]),
                            _word("DUE", [605, 525, 650, 550]),
                            _word("8.00", [830, 525, 885, 550], ["GRAND_TOTAL"]),
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
                            _word("MARKET", [410, 950, 500, 975], ["MERCHANT_NAME"]),
                            _word("MART", [510, 950, 575, 975], ["MERCHANT_NAME"]),
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
                            _word("BANANAS", [85, 675, 175, 700], ["PRODUCT_NAME"]),
                            _word("2.50", [830, 675, 885, 700], ["LINE_TOTAL"]),
                        ],
                    },
                    {
                        "line_id": 4,
                        "y": 0.635,
                        "words": [
                            _word("BALANCE", [500, 625, 595, 650]),
                            _word("DUE", [605, 625, 650, 650]),
                            _word("2.50", [830, 625, 885, 650], ["GRAND_TOTAL"]),
                        ],
                    },
                ],
            },
        ],
    }


def _receipt_group_payload_with_datetime():
    payload = json.loads(json.dumps(_receipt_group_payload("Date Time Mart")))
    for receipt, date_text, time_text in zip(
        payload["receipts"],
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
    return payload


def test_load_local_receipt_groups_infers_merchants_from_ungrouped_export(tmp_path):
    module = _load_module()
    receipt_path = tmp_path / "ungrouped.json"
    payload = {
        "receipts": [
            {"image_id": "market-image", "receipt_id": "market-receipt"},
            {"image_id": "corner-image", "receipt_id": "corner-receipt"},
        ],
        "lines": [
            {
                "image_id": "market-image",
                "receipt_id": "market-receipt",
                "line_id": 1,
                "words": [
                    _word("MARKET", [410, 950, 500, 975], ["MERCHANT_NAME"]),
                    _word("MART", [510, 950, 575, 975], ["MERCHANT_NAME"]),
                ],
            },
            {
                "image_id": "market-image",
                "receipt_id": "market-receipt",
                "line_id": 2,
                "words": [
                    _word("APPLES", [85, 675, 165, 700], ["PRODUCT_NAME"]),
                    _word("3.00", [830, 675, 885, 700], ["LINE_TOTAL"]),
                ],
            },
            {
                "image_id": "corner-image",
                "receipt_id": "corner-receipt",
                "line_id": 1,
                "words": [
                    _word("CORNER", [410, 950, 500, 975], ["MERCHANT_NAME"]),
                    _word("SHOP", [510, 950, 575, 975], ["MERCHANT_NAME"]),
                ],
            },
            {
                "image_id": "corner-image",
                "receipt_id": "corner-receipt",
                "line_id": 2,
                "words": [
                    _word("MILK", [85, 675, 145, 700], ["PRODUCT_NAME"]),
                    _word("5.00", [830, 675, 885, 700], ["LINE_TOTAL"]),
                ],
            },
        ],
    }
    receipt_path.write_text(json.dumps(payload), encoding="utf-8")

    groups = module.load_local_receipt_groups(receipt_files=[str(receipt_path)])

    assert [group["merchant_name"] for group in groups] == [
        "CORNER SHOP",
        "MARKET MART",
    ]
    assert [len(group["receipts"]) for group in groups] == [1, 1]
    assert all(group["receipts"][0]["lines"] for group in groups)


def test_load_local_receipt_groups_normalizes_line_only_ocr(tmp_path):
    module = _load_module()
    receipt_path = tmp_path / "line_only.json"
    receipt_path.write_text(
        json.dumps(
            {
                "receipts": [{"image_id": "cafe-image", "receipt_id": "1"}],
                "lines": [
                    {
                        "image_id": "cafe-image",
                        "receipt_id": "1",
                        "text": "Cafe Rio",
                        "labels": ["MERCHANT_NAME"],
                        "bounding_box": {
                            "x": 0.1,
                            "y": 0.9,
                            "width": 0.3,
                            "height": 0.04,
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    groups = module.load_local_receipt_groups(receipt_files=[str(receipt_path)])

    assert [group["merchant_name"] for group in groups] == ["Cafe Rio"]
    words = groups[0]["receipts"][0]["lines"][0]["words"]
    assert [word["text"] for word in words] == ["Cafe", "Rio"]
    assert all(word["labels"] == ["MERCHANT_NAME"] for word in words)
    assert words[0]["bbox"] == [100, 900, 250, 940]
    assert words[1]["bbox"] == [250, 900, 400, 940]


def test_load_local_receipt_groups_attaches_top_level_words_and_labels(tmp_path):
    module = _load_module()
    receipt_path = tmp_path / "word_export.json"
    receipt_path.write_text(
        json.dumps(
            {
                "receipts": [{"image_id": "grocer-image", "receipt_id": 1}],
                "lines": [
                    {"image_id": "grocer-image", "receipt_id": 1, "line_id": 1},
                    {"image_id": "grocer-image", "receipt_id": 1, "line_id": 2},
                ],
                "words": [
                    {
                        "image_id": "grocer-image",
                        "receipt_id": 1,
                        "line_id": 1,
                        "word_id": 1,
                        "text": "Green",
                        "bounding_box": {
                            "x": 0.1,
                            "y": 0.9,
                            "width": 0.1,
                            "height": 0.03,
                        },
                    },
                    {
                        "image_id": "grocer-image",
                        "receipt_id": 1,
                        "line_id": 1,
                        "word_id": 2,
                        "text": "Grocer",
                        "bounding_box": {
                            "x": 0.22,
                            "y": 0.9,
                            "width": 0.14,
                            "height": 0.03,
                        },
                    },
                    {
                        "image_id": "grocer-image",
                        "receipt_id": 1,
                        "line_id": 2,
                        "word_id": 1,
                        "text": "APPLES",
                        "bounding_box": {
                            "x": 0.1,
                            "y": 0.7,
                            "width": 0.12,
                            "height": 0.03,
                        },
                    },
                    {
                        "image_id": "grocer-image",
                        "receipt_id": 1,
                        "line_id": 2,
                        "word_id": 2,
                        "text": "3.00",
                        "bounding_box": {
                            "x": 0.84,
                            "y": 0.7,
                            "width": 0.06,
                            "height": 0.03,
                        },
                    },
                ],
                "word_labels": [
                    {
                        "image_id": "grocer-image",
                        "receipt_id": 1,
                        "line_id": 1,
                        "word_id": 1,
                        "label": "MERCHANT_NAME",
                        "validation_status": "VALID",
                    },
                    {
                        "image_id": "grocer-image",
                        "receipt_id": 1,
                        "line_id": 1,
                        "word_id": 2,
                        "label": "MERCHANT_NAME",
                        "validation_status": "VALID",
                    },
                    {
                        "image_id": "grocer-image",
                        "receipt_id": 1,
                        "line_id": 2,
                        "word_id": 1,
                        "label": "PRODUCT_NAME",
                        "validation_status": "VALID",
                    },
                    {
                        "image_id": "grocer-image",
                        "receipt_id": 1,
                        "line_id": 2,
                        "word_id": 2,
                        "label": "LINE_TOTAL",
                        "validation_status": "VALID",
                    },
                    {
                        "image_id": "grocer-image",
                        "receipt_id": 1,
                        "line_id": 2,
                        "word_id": 2,
                        "label": "GRAND_TOTAL",
                        "validation_status": "INVALID",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    groups = module.load_local_receipt_groups(receipt_files=[str(receipt_path)])

    assert [group["merchant_name"] for group in groups] == ["Green Grocer"]
    lines = groups[0]["receipts"][0]["lines"]
    assert [word["text"] for word in lines[0]["words"]] == ["Green", "Grocer"]
    assert [word["labels"] for word in lines[0]["words"]] == [
        ["MERCHANT_NAME"],
        ["MERCHANT_NAME"],
    ]
    assert [word["labels"] for word in lines[1]["words"]] == [
        ["PRODUCT_NAME"],
        ["LINE_TOTAL"],
    ]
    assert lines[1]["words"][1]["bbox"] == [840, 700, 900, 730]


def _category_sprouts_group_payload():
    return {
        "merchant_name": "Sprouts Farmers Market",
        "receipts": [
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
                            _word(
                                "8.49",
                                [835, 545, 900, 570],
                                ["GRAND_TOTAL"],
                            )
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
                            _word(
                                "1.95",
                                [835, 625, 900, 650],
                                ["GRAND_TOTAL"],
                            )
                        ],
                    },
                ],
            },
        ],
    }


def _thin_group_payload():
    return {
        "merchant_name": "Thin Merchant",
        "receipts": [
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
    }


def _multi_merchant_receipt_payload():
    return {
        "merchants": [
            _receipt_group_payload(),
            _category_sprouts_group_payload(),
            _thin_group_payload(),
        ]
    }


def test_summarize_local_synthesis_preflight_passes_ready_artifacts():
    module = _load_module()

    summary = module.summarize_local_synthesis_preflight(
        [
            _artifact("Market Mart", score=0.86),
            _artifact("Sprouts Farmers Market", score=0.92),
        ],
        min_ready_share=0.75,
        min_avg_readiness_score=0.7,
        min_grounded_candidate_share=0.4,
    )

    assert summary["ready"] is True
    assert summary["reasons"] == []
    assert summary["merchant_count"] == 2
    assert summary["ready_merchant_count"] == 2
    assert summary["avg_readiness_score"] == 0.89
    assert summary["candidate_count"] == 4
    assert summary["grounded_candidate_count"] == 2
    assert summary["arithmetic_candidate_count"] == 2
    assert summary["llm_execution"] == {
        "mode_counts": {"deterministic_fallback": 2},
        "paid_llm_disabled_count": 2,
        "api_call_allowed_count": 0,
        "configured_models": ["openai/gpt-5.5"],
        "latest_model_sources": [
            "https://developers.openai.com/api/docs/guides/latest-model"
        ],
        "latest_model_verified_at": "2026-06-23",
    }
    assert summary["merchant_gap_summary"]["blocked_merchant_count"] == 0
    assert summary["merchant_gap_summary"]["merchant_gap_count"] == 2
    assert summary["merchant_gap_summary"]["merchants"][0] == {
        "merchant_name": "Market Mart",
        "status": "ready",
        "score": 0.86,
        "candidate_count": 2,
        "accepted_count": 0,
        "ready_operation_count": 2,
        "missing_operations": ["remove_line_item", "replace_field"],
        "operation_gap_reasons": {
            "remove_line_item": ["no_removable_non_taxable_items"],
            "replace_field": ["no_safe_mutable_fields"],
        },
        "blockers": [],
        "limitations": [],
    }
    assert summary["merchants"][0]["candidate_count"] == 2
    assert summary["merchants"][0]["accepted_count"] == 0
    assert summary["merchants"][0]["llm_execution"] == {
        "mode": "deterministic_fallback",
        "paid_llm_disabled": True,
        "api_call_allowed": False,
        "configured_model": "openai/gpt-5.5",
        "model_profile": "cheap",
        "latest_openai_model": "gpt-5.5",
        "latest_model_source": (
            "https://developers.openai.com/api/docs/guides/latest-model"
        ),
        "latest_model_verified_at": "2026-06-23",
    }
    coverage = summary["operation_coverage"]
    assert coverage["ready_operation_count"] == 2
    assert coverage["ready_operation_share"] == 0.5
    assert coverage["operations"]["hard_negative"]["ready_merchant_count"] == 2
    assert coverage["operations"]["hard_negative"]["candidate_count"] == 2
    assert coverage["operations"]["add_line_item"]["ready_merchant_count"] == 2
    assert coverage["operations"]["add_line_item"]["candidate_count"] == 2
    assert coverage["operations"]["remove_line_item"]["ready_merchant_count"] == 0
    assert coverage["operations"]["replace_field"]["ready_merchant_count"] == 0
    assert coverage["recommendations"] == [
        "collect_multi_item_receipts_for_remove_item_synthesis",
        "collect_stable_date_time_examples_for_field_replacement",
    ]


def test_summarize_local_synthesis_preflight_fails_weak_artifacts():
    module = _load_module()

    summary = module.summarize_local_synthesis_preflight(
        [
            _artifact("Thin Merchant", status="blocked", score=0.2, candidates=False),
            {"merchant_name": "Missing Readiness"},
        ],
    )

    assert summary["ready"] is False
    assert summary["readiness_status_counts"] == {
        "blocked": 1,
        "missing": 1,
    }
    assert summary["llm_execution"]["mode_counts"] == {
        "deterministic_fallback": 1,
        "unknown": 1,
    }
    assert summary["llm_execution"]["paid_llm_disabled_count"] == 1
    assert summary["llm_execution"]["api_call_allowed_count"] == 0
    assert summary["merchant_gap_summary"]["blocked_merchant_count"] == 2
    assert summary["merchant_gap_summary"]["top_blockers"] == {"no_line_items": 1}
    assert summary["reasons"] == [
        "missing_synthesis_readiness",
        "ready_merchant_share_below_threshold",
        "avg_readiness_score_below_threshold",
        "no_synthetic_candidates",
    ]
    coverage = summary["operation_coverage"]
    assert coverage["ready_operation_count"] == 0
    assert coverage["operations"]["hard_negative"]["ready_merchant_count"] == 0
    assert coverage["operations"]["add_line_item"]["ready_merchant_count"] == 0
    assert coverage["recommendations"] == [
        "collect_label_geometry_for_hard_negative_synthesis",
        "collect_cross_receipt_item_catalog_for_add_item_synthesis",
        "collect_multi_item_receipts_for_remove_item_synthesis",
        "collect_stable_date_time_examples_for_field_replacement",
    ]


def test_merchant_gap_summary_keeps_candidates_separate_from_accepted_counts():
    module = _load_module()

    summary = module._merchant_gap_summary(
        [
            {
                "merchant_name": "Candidate Only",
                "readiness_status": "ready",
                "readiness_score": 0.9,
                "candidate_count": 4,
                "candidate_operation_counts": {"add_line_item": 4},
                "supported_operations": ["add_line_item"],
            },
            {
                "merchant_name": "Accepted Operation",
                "readiness_status": "ready",
                "readiness_score": 0.9,
                "candidate_count": 4,
                "candidate_operation_counts": {"add_line_item": 4},
                "accepted_operation_counts": {"add_line_item": 1},
                "supported_operations": ["add_line_item"],
            },
            {
                "merchant_name": "Legacy Operation Counts",
                "readiness_status": "ready",
                "readiness_score": 0.9,
                "candidate_count": 4,
                "operation_counts": {"add_line_item": 4},
                "supported_operations": ["add_line_item"],
            },
        ]
    )

    merchants = {row["merchant_name"]: row for row in summary["merchants"]}
    assert merchants["Candidate Only"]["candidate_count"] == 4
    assert merchants["Candidate Only"]["accepted_count"] == 0
    assert merchants["Accepted Operation"]["candidate_count"] == 4
    assert merchants["Accepted Operation"]["accepted_count"] == 1
    assert merchants["Legacy Operation Counts"]["candidate_count"] == 4
    assert merchants["Legacy Operation Counts"]["accepted_count"] == 0


def test_preflight_compact_artifact_can_preserve_explicit_accepted_operations():
    module = _load_module()

    artifact = _artifact("Market Mart")
    artifact["accepted_operation_counts"] = {"add_line_item": 1}
    summary = module.summarize_local_synthesis_preflight(
        [artifact],
        min_ready_share=0.0,
        min_avg_readiness_score=0.0,
        min_grounded_candidate_share=0.0,
    )

    merchant = summary["merchants"][0]
    assert merchant["candidate_count"] == 2
    assert merchant["accepted_operation_counts"] == {"add_line_item": 1}
    assert merchant["accepted_count"] == 1
    assert summary["merchant_gap_summary"]["merchants"][0]["accepted_count"] == 1


def test_source_quality_blocked_artifact_cannot_feed_training_bundle():
    module = _load_module()

    artifact = _artifact(
        "Market Mart",
        status="ready",
        score=0.92,
        source_receipt_quality=_blocked_source_quality("Market Mart"),
    )
    summary = module.summarize_local_synthesis_preflight(
        [artifact],
        min_ready_share=0.0,
        min_avg_readiness_score=0.0,
        min_grounded_candidate_share=0.0,
    )

    assert summary["ready"] is False
    assert summary["reasons"] == ["source_receipt_quality_blocked"]
    assert summary["readiness_status_counts"] == {"blocked": 1}
    assert summary["source_quality_status_counts"] == {"blocked": 1}
    assert summary["blocked_source_quality_merchant_count"] == 1
    assert summary["operation_coverage"]["ready_operation_count"] == 0
    assert summary["operation_coverage"]["operations"]["add_line_item"][
        "blocked_merchants"
    ] == [
        {
            "merchant_name": "Market Mart",
            "reasons": [
                "source_receipt_quality_blocked",
                "readiness_status_blocked",
            ],
        }
    ]

    bundle = module.build_local_synthetic_training_bundle(
        [artifact],
        min_ready_share=0.0,
        min_avg_readiness_score=0.0,
        min_grounded_candidate_share=0.0,
    )

    assert bundle["ready"] is False
    assert bundle["reasons"] == [
        "source_receipt_quality_blocked",
        "no_layoutlm_accepted_candidates",
    ]
    assert bundle["merchant_synthesis_contracts"][0]["status"] == "blocked"
    assert bundle["merchant_synthesis_contracts"][0]["source_receipt_quality"] == {
        "status": "blocked",
        "receipt_count": 2,
        "receipts_with_labels": 0,
        "receipts_with_line_item_labels": 0,
        "receipts_with_grand_total_label": 0,
        "receipts_with_date_or_time_label": 0,
        "labeled_word_count": 0,
        "blockers": ["source_receipt_quality_blocked", "no_word_labels"],
        "limitations": ["no_labeled_line_items", "no_grand_total_labels"],
    }
    assert (
        bundle["merchant_synthesis_contracts"][0]["operation_contracts"][
            "add_line_item"
        ]["source_quality_blocker"]
        == "source_receipt_quality_blocked"
    )
    assert bundle["selection"]["candidates_accepted"] == 0
    assert bundle["selection"]["rejection_reasons"] == {
        "merchant_contract_not_ready": 2
    }
    assert bundle["candidate_mix"]["accepted_count"] == 0
    report = bundle["synthesis_quality_report"]
    assert report["summary"]["source_quality_status_counts"] == {"blocked": 1}
    assert report["summary"]["blocked_source_quality_merchant_count"] == 1
    assert report["recommendations"] == [
        "resolve_bundle_reasons_before_training",
        "collect_more_receipts_or_fix_parameterization",
        "collect_more_receipts_for_not_ready_merchants",
        "fix_source_receipt_quality_before_synthesis",
    ]
    assert report["merchants"][0]["source_quality_status"] == "blocked"
    assert report["merchants"][0]["source_quality_operation_blockers"] == {
        "hard_negative": "source_receipt_quality_blocked",
        "add_line_item": "source_receipt_quality_blocked",
        "remove_line_item": "source_receipt_quality_blocked",
        "replace_field": "source_receipt_quality_blocked",
    }


def test_source_quality_reports_recoverable_unlabeled_text_structure():
    module = _load_module()

    receipt = module._normalize_local_receipt(
        {
            "lines": [
                {"line_id": 1, "text": "Market Mart"},
                {"line_id": 2, "text": "BANANAS 1.95"},
                {"line_id": 3, "text": "TAX 0.00"},
                {"line_id": 4, "text": "TOTAL 1.95"},
                {"line_id": 5, "text": "06/23/26 12:45 PM"},
            ],
        }
    )

    quality = module._source_receipt_quality("Market Mart", [receipt])

    assert quality["status"] == "blocked"
    assert quality["blockers"] == ["no_word_labels"]
    assert "unlabeled_text_requires_label_validation" in quality["limitations"]
    assert quality["text_structure_status"] == "recoverable_unlabeled_text"
    assert quality["receipts_with_merchant_header_like_text"] == 1
    assert quality["receipts_with_line_item_like_text"] == 1
    assert quality["receipts_with_total_like_text"] == 1
    assert quality["receipts_with_date_time_like_text"] == 1
    assert quality["line_item_like_text_line_count"] == 1
    assert quality["total_like_text_line_count"] == 1
    summary = module.summarize_source_receipt_quality(
        [{"source_receipt_quality": quality}]
    )
    assert summary["text_structure_status_counts"] == {"recoverable_unlabeled_text": 1}
    assert summary["line_item_like_text_line_count"] == 1
    assert summary["total_like_text_line_count"] == 1

    artifact = _artifact(
        "Market Mart",
        status="ready",
        score=0.92,
        source_receipt_quality=quality,
    )
    bundle = module.build_local_synthetic_training_bundle(
        [artifact],
        min_ready_share=0.0,
        min_avg_readiness_score=0.0,
        min_grounded_candidate_share=0.0,
    )
    assert bundle["selection"]["candidates_accepted"] == 0
    assert bundle["candidate_mix"]["accepted_count"] == 0
    assert bundle["synthesis_quality_report"]["training_ready"] is False
    assert (
        "validate_recoverable_unlabeled_receipts"
        in bundle["synthesis_quality_report"]["recommendations"]
    )
    assert (
        "validate_recoverable_unlabeled_receipts"
        in bundle["synthesis_quality_report"]["training_ready_reasons"]
    )
    report_merchant = bundle["synthesis_quality_report"]["merchants"][0]
    assert (
        report_merchant["source_quality_text_structure_status"]
        == "recoverable_unlabeled_text"
    )
    assert report_merchant["source_quality_line_item_like_text_line_count"] == 1
    assert report_merchant["source_quality_total_like_text_line_count"] == 1
    assert report_merchant["source_quality_limitations"] == [
        "unlabeled_text_requires_label_validation",
        "no_labeled_line_items",
        "no_grand_total_labels",
        "single_receipt_limits_cross_receipt_grounding",
    ]
    assert report_merchant["source_quality_requires_label_validation"] is True
    blocked_artifact = _artifact(
        "Market Mart",
        status="blocked",
        score=0.2,
        source_receipt_quality=quality,
    )
    audit = module.summarize_merchant_synthesis_audit([blocked_artifact])[0]
    assert audit["next_synthesis_actions"] == [
        "validate_recoverable_unlabeled_receipts",
        "resolve_merchant_synthesis_blockers",
    ]

    ready_audit = module.summarize_merchant_synthesis_audit([artifact])[0]
    assert ready_audit["next_synthesis_actions"][:3] == [
        "validate_recoverable_unlabeled_receipts",
        "synthesize_hard_negative_from_existing_evidence",
        "synthesize_add_line_item_from_existing_evidence",
    ]
    assert (
        ready_audit["next_synthesis_actions"].count(
            "validate_recoverable_unlabeled_receipts"
        )
        == 1
    )

    weak_receipt = module._normalize_local_receipt(
        {
            "lines": [
                {"line_id": 1, "text": "Market Mart"},
                {"line_id": 2, "text": "Order ABC123"},
                {"line_id": 3, "text": "06/23/26 12:45 PM"},
            ],
        }
    )
    weak_quality = module._source_receipt_quality("Market Mart", [weak_receipt])
    assert (
        weak_quality["text_structure_status"]
        == "unlabeled_text_without_receipt_structure"
    )
    assert "unlabeled_text_requires_label_validation" not in weak_quality["limitations"]
    weak_artifact = _artifact(
        "Market Mart",
        status="ready",
        score=0.92,
        source_receipt_quality=weak_quality,
    )
    weak_audit = module.summarize_merchant_synthesis_audit([weak_artifact])[0]
    assert (
        "validate_recoverable_unlabeled_receipts"
        not in weak_audit["next_synthesis_actions"]
    )


def test_preflight_cli_loads_local_artifacts_without_deployment_lookup(
    tmp_path,
    monkeypatch,
    capsys,
):
    module = _load_module()
    artifact_path = tmp_path / "merchant.json"
    artifact_path.write_text(json.dumps(_artifact()), encoding="utf-8")
    monkeypatch.setattr(
        module,
        "load_outputs",
        lambda _env: (_ for _ in ()).throw(AssertionError("should not call AWS")),
    )
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "verify_synthetic_replay.py",
            "preflight",
            "--artifact-dir",
            str(tmp_path),
            "--min-grounded-candidate-share",
            "0.4",
        ],
    )

    assert module.main() == 0
    output = json.loads(capsys.readouterr().out)
    assert output["ready"] is True
    assert output["merchant_count"] == 1


def test_build_local_synthetic_training_bundle_writes_loader_ready_rows():
    module = _load_module()

    bundle = module.build_local_synthetic_training_bundle(
        [_artifact("Market Mart")],
        min_grounded_candidate_share=0.4,
    )

    assert bundle["ready"] is True
    assert bundle["schema_version"] == "layoutlm-synthetic-training-bundle-v1"
    assert bundle["validation_policy"] == "real_receipts_only"
    assert bundle["selection"]["candidates_seen"] == 2
    assert bundle["selection"]["candidates_accepted"] == 2
    assert bundle["selection"]["rejection_reasons"] == {}
    assert bundle["candidate_mix"]["merchant_count"] == 1
    assert bundle["candidate_mix"]["accepted_merchant_count"] == 1
    assert bundle["candidate_mix"]["accepted_operation_counts"] == {
        "add_line_item": 1,
        "hard_negative": 1,
    }
    assert bundle["candidate_mix"]["accepted_category_counts"] == {"PRODUCE": 1}
    assert bundle["candidate_mix"]["accepted_structure_similarity"] == {
        "count": 2,
        "avg": 0.9,
        "min": 0.88,
        "max": 0.92,
    }
    assert bundle["candidate_mix"]["accepted_structure_components"] == {
        "category_sequence": {"count": 2, "avg": 0.67, "min": 0.67, "max": 0.67},
        "category_set": {"count": 2, "avg": 0.5, "min": 0.5, "max": 0.5},
        "item_count": {"count": 2, "avg": 0.415, "min": 0.33, "max": 0.5},
        "line_step": {"count": 2, "avg": 0.6, "min": 0.55, "max": 0.65},
        "price_column": {"count": 2, "avg": 1.0, "min": 1.0, "max": 1.0},
        "token_count": {"count": 2, "avg": 0.47, "min": 0.47, "max": 0.47},
    }
    assert bundle["merchant_synthesis_contracts"] == [
        {
            "merchant_name": "Market Mart",
            "status": "ready",
            "score": 0.86,
            "source_receipt_count": 0,
            "source_receipt_keys": [],
            "supported_operations": [
                "hard_negative",
                "add_line_item",
            ],
            "operation_contracts": {
                "hard_negative": {
                    "ready": True,
                    "labels": [],
                },
                "add_line_item": {
                    "ready": True,
                    "candidate_count": 1,
                    "requires": [
                        "item_seen_in_other_receipt",
                        "base_receipt_has_category",
                        "non_taxable_arithmetic_reconciliation",
                    ],
                },
                "remove_line_item": {
                    "ready": False,
                    "candidate_count": 0,
                    "requires": [
                        "removable_non_taxable_item",
                        "summary_total_reconciliation",
                    ],
                },
                "replace_field": {
                    "ready": False,
                    "candidate_count": 0,
                    "fields": {},
                    "requires": [
                        "stable_format",
                        "stable_geometry",
                        "multiple_observed_values",
                    ],
                },
            },
            "category_contract": {
                "heading_counts": {},
                "top_items_by_category": {},
            },
            "catalog_contract": {
                "catalog_item_count": 0,
                "cross_receipt_catalog_item_count": None,
                "grounded_add_item_examples": [],
                "top_catalog_items": [],
            },
            "bundle_acceptance": {
                "candidate_count": 2,
                "accepted_count": 2,
                "rejected_count": 0,
                "accepted_operation_counts": {
                    "add_line_item": 1,
                    "hard_negative": 1,
                },
                "accepted_category_counts": {"PRODUCE": 1},
                "accepted_field_replacement_counts": {},
                "rejection_reasons": {},
            },
            "quality_gates": {
                "validation_policy": "real_receipts_only",
                "candidate_requires_train_only": True,
                "min_structure_similarity": 0.6,
                "max_per_merchant": 5,
                "max_per_merchant_operation": 2,
                "max_candidates_per_training_run": None,
                "structure_component_thresholds": STRUCTURE_COMPONENT_THRESHOLDS,
            },
            "blockers": [],
            "limitations": [],
        }
    ]
    assert (
        bundle["selection"]["structure_component_thresholds"]
        == STRUCTURE_COMPONENT_THRESHOLDS
    )
    assert bundle["candidate_mix"]["merchants"][0]["merchant_name"] == "Market Mart"
    assert [row["candidate_id"] for row in bundle["synthetic_training_examples"]] == [
        "grounded-add-item",
        "hard-negative",
    ]
    assert [
        row["candidate_id"]
        for row in bundle["selection"]["accepted_candidate_examples"]
    ] == [
        "grounded-add-item",
        "hard-negative",
    ]
    expected_source_lineage = {
        "schema_version": "synthetic-candidate-lineage-v1",
        "source_receipt_key_count": 2,
        "source_receipt_keys": ["base#00001", "source#00001"],
        "product_source_receipt_keys": ["source#00001"],
        "category_source_receipt_keys": ["base#00001", "source#00001"],
        "evidence_flags": {
            "has_base_receipt": False,
            "has_cross_receipt_item": True,
            "has_category_evidence": True,
            "has_nearest_real_structure": False,
            "has_layout_integrity": False,
            "has_arithmetic_reconciliation": True,
            "has_selection_evidence": False,
        },
    }
    expected_accepted_source_lineage = {
        "schema_version": "accepted-source-lineage-v1",
        "coverage_status": "complete",
        "authoritative": True,
        "candidate_count": 2,
        "observed_candidate_count": 2,
        "expected_candidate_count": 2,
        "with_base_receipt_count": 0,
        "with_cross_receipt_item_count": 1,
        "with_category_evidence_count": 1,
        "with_nearest_real_structure_count": 0,
        "with_layout_integrity_count": 0,
        "with_arithmetic_reconciliation_count": 1,
        "with_selection_evidence_count": 0,
        "source_receipt_key_count": 2,
        "source_receipt_keys": ["base#00001", "source#00001"],
        "source_receipt_keys_truncated": False,
    }
    assert bundle["candidate_mix"]["accepted_source_lineage"] == (
        expected_accepted_source_lineage
    )
    expected_add_candidate_quality = {
        "score": 0.93,
        "high_fidelity": True,
        "components": {
            "arithmetic_reconciliation": 0.9,
            "category_alignment": 1.0,
            "cross_receipt_grounding": 1.0,
            "structure_similarity": 0.92,
            "token_budget": 1.0,
        },
    }
    expected_hard_negative_candidate_quality = {
        "score": 0.9,
        "high_fidelity": True,
        "components": {
            "structure_similarity": 0.88,
            "token_budget": 1.0,
        },
    }
    assert bundle["selection"]["accepted_candidate_examples"][0] == {
        "rank": 1,
        "candidate_id": "grounded-add-item",
        "receipt_key": "synthetic-Market Mart-add#00001",
        "operation": "add_line_item",
        "category": "PRODUCE",
        "structure_similarity": 0.92,
        "accuracy_checks": [],
        "merchant_name": "Market Mart",
        "image_id": "synthetic-Market Mart-add",
        "source_lineage": expected_source_lineage,
        "selection_reason": ("declared_candidate_quality_ranked_after_layoutlm_gates"),
        "candidate_quality": expected_add_candidate_quality,
    }
    assert bundle["selection"]["accepted_candidate_examples"][1] == {
        "rank": 2,
        "candidate_id": "hard-negative",
        "receipt_key": "synthetic-Market Mart-hard-negative#00001",
        "operation": "hard_negative",
        "category": None,
        "structure_similarity": 0.88,
        "accuracy_checks": [],
        "merchant_name": "Market Mart",
        "image_id": "synthetic-Market Mart-hard-negative",
        "selection_reason": ("declared_candidate_quality_ranked_after_layoutlm_gates"),
        "candidate_quality": expected_hard_negative_candidate_quality,
    }
    assert all(row.get("metadata") for row in bundle["synthetic_training_examples"])
    assert bundle["synthesis_quality_report"]["schema_version"] == (
        "local-synthesis-quality-report-v1"
    )
    assert bundle["synthesis_quality_report"]["ready"] is True
    assert bundle["synthesis_quality_report"]["summary"]["accepted_count"] == 2
    assert bundle["synthesis_quality_report"]["summary"]["accepted_source_lineage"] == (
        expected_accepted_source_lineage
    )
    assert bundle["synthesis_quality_report"]["summary"]["llm_execution"] == {
        "mode_counts": {"deterministic_fallback": 1},
        "paid_llm_disabled_count": 1,
        "api_call_allowed_count": 0,
        "configured_models": ["openai/gpt-5.5"],
        "latest_model_sources": [
            "https://developers.openai.com/api/docs/guides/latest-model"
        ],
        "latest_model_verified_at": "2026-06-23",
    }
    assert (
        bundle["synthesis_quality_report"]["summary"]["accepted_structure_components"]
        == bundle["candidate_mix"]["accepted_structure_components"]
    )
    assert (
        bundle["synthesis_quality_report"]["merchant_gap_summary"]["merchant_gap_count"]
        == 1
    )
    assert bundle["synthesis_quality_report"]["merchant_gap_summary"]["merchants"][0][
        "missing_operations"
    ] == ["remove_line_item", "replace_field"]
    assert bundle["synthesis_quality_report"]["quality_gates"]["contract_gate"] == {
        "enabled": True,
        "merchant_contract_count": 1,
        "ready_merchant_contract_count": 1,
    }
    assert (
        bundle["synthesis_quality_report"]["quality_gates"][
            "structure_component_thresholds"
        ]
        == STRUCTURE_COMPONENT_THRESHOLDS
    )
    coverage = bundle["synthesis_quality_report"]["operation_coverage"]
    assert coverage["operations"]["hard_negative"]["ready_merchant_count"] == 1
    assert coverage["operations"]["hard_negative"]["candidate_count"] == 1
    assert coverage["operations"]["add_line_item"]["ready_merchant_count"] == 1
    assert coverage["operations"]["add_line_item"]["candidate_count"] == 1
    assert coverage["operations"]["remove_line_item"]["ready_merchant_count"] == 0
    assert coverage["operations"]["replace_field"]["ready_merchant_count"] == 0


def test_build_local_synthetic_training_bundle_enforces_contract_before_writing():
    module = _load_module()
    artifact = _artifact("Market Mart")
    artifact["merchant_receipt_parameterization"]["synthesis_readiness"][
        "supported_operations"
    ] = ["hard_negative"]

    bundle = module.build_local_synthetic_training_bundle(
        [artifact],
        min_grounded_candidate_share=0.4,
    )

    assert bundle["ready"] is True
    assert bundle["selection"]["candidates_seen"] == 2
    assert bundle["selection"]["candidates_accepted"] == 1
    assert bundle["selection"]["contract_gate"] == {
        "enabled": True,
        "merchant_contract_count": 1,
        "ready_merchant_contract_count": 1,
    }
    assert bundle["selection"]["rejection_reasons"] == {
        "operation_not_supported_by_contract": 1
    }
    assert bundle["selection"]["rejected_candidate_examples"] == [
        {
            "candidate_id": "grounded-add-item",
            "receipt_key": "synthetic-Market Mart-add#00001",
            "image_id": "synthetic-Market Mart-add",
            "merchant_name": "Market Mart",
            "operation": "add_line_item",
            "reason": "operation_not_supported_by_contract",
            "idx": 0,
            "category": "PRODUCE",
            "structure_similarity": 0.92,
            "candidate_quality": 0.93,
        }
    ]
    assert [row["candidate_id"] for row in bundle["synthetic_training_examples"]] == [
        "hard-negative"
    ]
    assert bundle["candidate_mix"]["accepted_operation_counts"] == {"hard_negative": 1}
    assert bundle["candidate_mix"]["rejection_reasons"] == {
        "operation_not_supported_by_contract": 1
    }
    contract = bundle["merchant_synthesis_contracts"][0]
    assert contract["supported_operations"] == ["hard_negative"]
    assert contract["operation_contracts"]["add_line_item"]["ready"] is False
    assert contract["bundle_acceptance"]["accepted_operation_counts"] == {
        "hard_negative": 1
    }
    assert contract["bundle_acceptance"]["rejection_reasons"] == {
        "operation_not_supported_by_contract": 1
    }


def test_build_local_synthetic_training_bundle_requires_contract_capacity():
    module = _load_module()
    artifact = _artifact("Market Mart")
    readiness = artifact["merchant_receipt_parameterization"]["synthesis_readiness"]
    readiness["supported_operations"] = ["add_line_item"]
    readiness["grounded_add_item_candidate_count"] = 0
    readiness["hard_negative_label_count"] = 0

    bundle = module.build_local_synthetic_training_bundle(
        [artifact],
        min_grounded_candidate_share=0.4,
    )

    assert bundle["ready"] is False
    assert bundle["selection"]["candidates_seen"] == 2
    assert bundle["selection"]["candidates_accepted"] == 0
    assert bundle["selection"]["rejection_reasons"] == {
        "operation_not_supported_by_contract": 2
    }
    assert bundle["selection"]["rejected_candidate_examples"] == [
        {
            "candidate_id": "grounded-add-item",
            "receipt_key": "synthetic-Market Mart-add#00001",
            "image_id": "synthetic-Market Mart-add",
            "merchant_name": "Market Mart",
            "operation": "add_line_item",
            "reason": "operation_not_supported_by_contract",
            "idx": 0,
            "category": "PRODUCE",
            "structure_similarity": 0.92,
            "candidate_quality": 0.93,
        },
        {
            "candidate_id": "hard-negative",
            "receipt_key": "synthetic-Market Mart-hard-negative#00001",
            "image_id": "synthetic-Market Mart-hard-negative",
            "merchant_name": "Market Mart",
            "operation": "hard_negative",
            "reason": "operation_not_supported_by_contract",
            "idx": 1,
            "structure_similarity": 0.88,
            "candidate_quality": 0.9,
        },
    ]
    assert bundle["candidate_mix"]["accepted_count"] == 0
    assert bundle["candidate_mix"]["rejection_reasons"] == {
        "operation_not_supported_by_contract": 2
    }
    contract = bundle["merchant_synthesis_contracts"][0]
    assert contract["supported_operations"] == ["add_line_item"]
    assert contract["operation_contracts"]["add_line_item"]["ready"] is False
    assert contract["operation_contracts"]["add_line_item"]["candidate_count"] == 0
    assert contract["operation_contracts"]["hard_negative"]["ready"] is False
    assert bundle["synthesis_quality_report"]["training_ready"] is False
    audit = module.summarize_merchant_synthesis_audit([artifact])[0]
    audit_operations = {row["operation"]: row for row in audit["operation_readiness"]}
    assert audit_operations["add_line_item"]["supported"] is True
    assert audit_operations["add_line_item"]["ready"] is False
    assert audit_operations["add_line_item"]["blockers"] == [
        "no_cross_receipt_grounded_add_items"
    ]


def test_merchant_contract_readiness_requires_support_and_capacity():
    module = _load_module()
    artifact = _artifact("Capacity Mart", candidates=False)
    readiness = artifact["merchant_receipt_parameterization"]["synthesis_readiness"]
    readiness["supported_operations"] = ["remove_line_item", "replace_field"]
    readiness["hard_negative_label_count"] = 1
    readiness["grounded_add_item_candidate_count"] = 1
    readiness["removable_item_candidate_count"] = 1
    readiness["mutable_field_count"] = 2
    readiness["mutable_fields"] = {
        "DATE": {
            "label": "DATE",
            "safe_to_mutate": True,
            "stable_format": "MM/DD/YYYY",
            "stable_geometry": True,
            "observed_count": 2,
        },
        "TIME": {
            "label": "TIME",
            "safe_to_mutate": True,
            "stable_format": "HH:MM",
            "stable_geometry": True,
            "observed_count": 2,
        },
    }

    contract = module.build_merchant_synthesis_contracts([artifact])[0]

    operations = contract["operation_contracts"]
    assert operations["hard_negative"]["ready"] is False
    assert operations["add_line_item"]["ready"] is False
    assert operations["add_line_item"]["candidate_count"] == 1
    assert operations["remove_line_item"]["ready"] is True
    assert operations["remove_line_item"]["candidate_count"] == 1
    assert operations["replace_field"]["ready"] is True
    assert operations["replace_field"]["candidate_count"] == 2
    assert sorted(operations["replace_field"]["fields"]) == ["DATE", "TIME"]

    audit = module.summarize_merchant_synthesis_audit([artifact])[0]
    audit_operations = {row["operation"]: row for row in audit["operation_readiness"]}
    assert audit_operations["hard_negative"]["ready"] is False
    assert audit_operations["hard_negative"]["blockers"] == [
        "operation_not_supported_by_contract"
    ]
    assert audit_operations["add_line_item"]["ready"] is False
    assert audit_operations["add_line_item"]["blockers"] == [
        "operation_not_supported_by_contract"
    ]
    assert audit_operations["remove_line_item"]["ready"] is True
    assert audit_operations["replace_field"]["ready"] is True
    assert audit["next_synthesis_actions"] == [
        "mine_confusion_targets_for_hard_negative_slots",
        "collect_cross_receipt_item_and_category_evidence",
        "generate_remove_line_item_candidate_from_ready_contract",
        "generate_replace_field_candidate_from_ready_contract",
    ]


def test_build_local_synthetic_training_bundle_includes_tax_contract():
    module = _load_module()
    artifact = _artifact("Taxable Mart")
    readiness = artifact["merchant_receipt_parameterization"]["synthesis_readiness"]
    readiness["limitations"] = ["tax_changing_synthesis_not_enabled"]
    readiness["tax_policy"] = {
        "supported_policy": "non_taxable_item_delta",
        "taxable_item_count": 2,
        "non_taxable_item_count": 2,
        "receipts_with_tax_total": 2,
        "receipts_with_taxable_items": 2,
        "tax_rate_observation_count": 2,
        "stable_tax_rate": True,
        "avg_tax_rate": "0.0778",
        "avg_tax_rate_percent": "7.78%",
        "tax_changing_synthesis_ready": False,
        "tax_changing_synthesis_blockers": ["tax_changing_loader_gate_not_enabled"],
    }

    bundle = module.build_local_synthetic_training_bundle(
        [artifact],
        min_grounded_candidate_share=0.4,
    )

    contract = bundle["merchant_synthesis_contracts"][0]
    assert contract["tax_contract"] == {
        "supported_policy": "non_taxable_item_delta",
        "taxable_item_count": 2,
        "non_taxable_item_count": 2,
        "receipts_with_tax_total": 2,
        "receipts_with_taxable_items": 2,
        "tax_rate_observation_count": 2,
        "stable_tax_rate": True,
        "avg_tax_rate": "0.0778",
        "avg_tax_rate_percent": "7.78%",
        "tax_changing_synthesis_ready": False,
        "tax_changing_synthesis_blockers": ["tax_changing_loader_gate_not_enabled"],
    }
    assert "tax_changing_synthesis_not_enabled" in contract["limitations"]


def test_build_local_synthetic_training_bundle_reloads_without_contract_drops(
    tmp_path,
):
    from receipt_layoutlm.data_loader import (
        _load_synthetic_training_examples_with_summary,
    )

    module = _load_module()
    bundle = module.build_local_synthetic_training_bundle(
        [_artifact("Market Mart")],
        min_grounded_candidate_share=0.4,
    )
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")

    loaded = _load_synthetic_training_examples_with_summary(str(bundle_path))

    assert loaded.candidates_seen == bundle["selection"]["candidates_accepted"]
    assert loaded.candidates_accepted == bundle["selection"]["candidates_accepted"]
    assert loaded.rejection_reasons == {}
    assert [row["candidate_id"] for row in loaded.accepted_rows] == bundle["selection"][
        "accepted_candidate_ids"
    ]


def test_build_local_synthesis_quality_report_summarizes_evidence():
    module = _load_module()
    artifact = _artifact("Market Mart")
    add_item = artifact["synthetic_receipt_candidates"][0]
    add_item["metadata"]["synthetic_receipt_preview"] = {
        "coordinate_system": "normalized_receipt_0_1000_y_high_is_top",
        "line_count": 8,
        "token_count": 28,
        "truncated": False,
        "lines": [
            {
                "line_number": 3,
                "text": "BANANAS 1.95",
                "role": "line_item",
                "y": 0.695,
                "bbox": [90, 695, 890, 720],
                "synthetic_insert": True,
                "modified_labels": [],
            }
        ],
    }
    add_item["metadata"]["synthesis_accuracy_evidence"] = {
        "operation": "add_line_item",
        "checks": [
            "train_only_real_validation_policy",
            "item_seen_in_other_receipt",
            "base_receipt_has_category",
            "non_taxable_arithmetic_reconciled",
        ],
        "changed_text": "BANANAS",
        "category": "PRODUCE",
        "old_grand_total": "8.49",
        "new_grand_total": "10.44",
        "tax_delta": "0.00",
    }
    add_item["metadata"]["candidate_quality"] = {
        "schema_version": "synthetic-candidate-quality-v1",
        "score": 0.93,
        "high_fidelity": True,
        "components": {
            "arithmetic_reconciliation": 0.9,
            "category_alignment": 1.0,
            "cross_receipt_grounding": 1.0,
            "structure_similarity": 0.86,
            "token_budget": 1.0,
        },
    }
    add_item["metadata"]["selection_evidence"] = {
        "schema_version": "synthetic-candidate-selection-v1",
        "selected_from_candidate_count": 3,
        "selected_input_index": 1,
        "ranked_by": [
            "candidate_quality.high_fidelity",
            "real_baseline_comparison.within_real_score_range",
            "candidate_quality.score",
        ],
        "selected_score": {
            "candidate_quality": 0.93,
            "high_fidelity": True,
            "structure_similarity": 0.86,
            "structure_component_pass_rate": 1.0,
            "token_budget": 1.0,
            "within_real_score_range": True,
            "delta_from_min": 0.04,
            "baseline_pair_count": 6,
            "token_count": 28,
        },
        "selection_policy": (
            "Generate feasible merchant-local mutations, then keep the highest "
            "fidelity option instead of maximizing synthetic volume."
        ),
    }
    bundle = module.build_local_synthetic_training_bundle(
        [artifact],
        min_grounded_candidate_share=0.4,
    )
    accepted_examples = bundle["selection"]["accepted_candidate_examples"]
    assert accepted_examples[0]["candidate_id"] == "grounded-add-item"
    assert accepted_examples[0]["selection_reason"] == (
        "declared_candidate_quality_ranked_after_layoutlm_gates"
    )
    assert accepted_examples[0]["candidate_quality"] == {
        "score": 0.93,
        "high_fidelity": True,
        "components": {
            "arithmetic_reconciliation": 0.9,
            "category_alignment": 1.0,
            "cross_receipt_grounding": 1.0,
            "structure_similarity": 0.86,
            "token_budget": 1.0,
        },
    }
    expected_selection_evidence = {
        "schema_version": "synthetic-candidate-selection-v1",
        "selected_from_candidate_count": 3,
        "selected_input_index": 1,
        "ranked_by": [
            "candidate_quality.high_fidelity",
            "real_baseline_comparison.within_real_score_range",
            "candidate_quality.score",
        ],
        "selected_score": {
            "candidate_quality": 0.93,
            "high_fidelity": True,
            "structure_similarity": 0.86,
            "structure_component_pass_rate": 1.0,
            "token_budget": 1.0,
            "within_real_score_range": True,
            "delta_from_min": 0.04,
            "baseline_pair_count": 6,
            "token_count": 28,
        },
        "selection_policy": (
            "Generate feasible merchant-local mutations, then keep the highest "
            "fidelity option instead of maximizing synthetic volume."
        ),
    }
    assert accepted_examples[0]["selection_evidence"] == expected_selection_evidence
    expected_source_lineage = {
        "schema_version": "synthetic-candidate-lineage-v1",
        "source_receipt_key_count": 2,
        "source_receipt_keys": ["base#00001", "source#00001"],
        "product_source_receipt_keys": ["source#00001"],
        "category_source_receipt_keys": ["base#00001", "source#00001"],
        "evidence_flags": {
            "has_base_receipt": False,
            "has_cross_receipt_item": True,
            "has_category_evidence": True,
            "has_nearest_real_structure": False,
            "has_layout_integrity": False,
            "has_arithmetic_reconciliation": True,
            "has_selection_evidence": True,
        },
    }
    expected_accepted_source_lineage = {
        "schema_version": "accepted-source-lineage-v1",
        "coverage_status": "complete",
        "authoritative": True,
        "candidate_count": 2,
        "observed_candidate_count": 2,
        "expected_candidate_count": 2,
        "with_base_receipt_count": 0,
        "with_cross_receipt_item_count": 1,
        "with_category_evidence_count": 1,
        "with_nearest_real_structure_count": 0,
        "with_layout_integrity_count": 0,
        "with_arithmetic_reconciliation_count": 1,
        "with_selection_evidence_count": 1,
        "source_receipt_key_count": 2,
        "source_receipt_keys": ["base#00001", "source#00001"],
        "source_receipt_keys_truncated": False,
    }
    assert accepted_examples[0]["source_lineage"] == expected_source_lineage
    assert accepted_examples[0]["preview_lines"] == [
        {
            "line_number": 3,
            "text": "BANANAS 1.95",
            "role": "line_item",
            "y": 0.695,
            "bbox": [90, 695, 890, 720],
            "synthetic_insert": True,
            "modified_labels": [],
        }
    ]

    report = module.build_local_synthesis_quality_report(
        bundle,
        artifacts=[artifact],
    )

    assert report["schema_version"] == "local-synthesis-quality-report-v1"
    assert report["ready"] is True
    assert report["training_ready"] is True
    assert report["training_ready_reasons"] == []
    assert report["summary"]["merchant_count"] == 1
    assert report["summary"]["accepted_count"] == 2
    assert report["summary"]["acceptance_rate"] == 1.0
    assert report["summary"]["accepted_operation_counts"] == {
        "add_line_item": 1,
        "hard_negative": 1,
    }
    expected_training_batch_policy = {
        "schema_version": "synthetic-training-batch-policy-v1",
        "status": "smoke_test_only",
        "recommended_example_count": 2,
        "accepted_candidate_count": 2,
        "selected_candidate_count": 2,
        "candidate_quality_count": 2,
        "high_fidelity_candidate_count": 2,
        "max_synthetic_train_share": 0.01,
        "max_per_merchant": 5,
        "max_per_merchant_operation": 2,
        "overtraining_risk_level": "low",
        "risk_reasons": ["too_few_examples_for_balance_assessment"],
        "hold_reasons": [],
        "requires_real_validation_split": True,
        "review_required": True,
    }
    assert bundle["synthetic_training_batch_policy"] == expected_training_batch_policy
    assert report["training_batch_policy"] == expected_training_batch_policy
    assert report["summary"]["accepted_source_lineage"] == (
        expected_accepted_source_lineage
    )
    assert (
        report["summary"]["accepted_structure_components"]
        == bundle["candidate_mix"]["accepted_structure_components"]
    )
    assert report["summary"]["accepted_candidate_quality"] == {
        "count": 2,
        "avg": 0.915,
        "min": 0.9,
        "max": 0.93,
    }
    assert report["summary"]["accepted_candidate_quality_components"] == {
        "arithmetic_reconciliation": {
            "count": 1,
            "avg": 0.9,
            "min": 0.9,
            "max": 0.9,
        },
        "category_alignment": {"count": 1, "avg": 1.0, "min": 1.0, "max": 1.0},
        "cross_receipt_grounding": {
            "count": 1,
            "avg": 1.0,
            "min": 1.0,
            "max": 1.0,
        },
        "structure_similarity": {
            "count": 2,
            "avg": 0.87,
            "min": 0.86,
            "max": 0.88,
        },
        "token_budget": {"count": 2, "avg": 1.0, "min": 1.0, "max": 1.0},
    }
    assert report["quality_gates"]["contract_gate"] == {
        "enabled": True,
        "merchant_contract_count": 1,
        "ready_merchant_contract_count": 1,
    }
    assert (
        report["quality_gates"]["structure_component_thresholds"]
        == STRUCTURE_COMPONENT_THRESHOLDS
    )
    assert report["quality_gates"]["accepted_operation_coverage_gate"] == {
        "enabled": True,
        "passed": True,
        "ready_operation_count": 2,
        "accepted_ready_operation_count": 2,
        "uncovered_ready_operations": [],
    }
    assert report["quality_gates"]["llm_model_freshness_gate"] == {
        "enabled": True,
        "passed": True,
        "requires_current_model_guidance": False,
        "api_call_allowed_count": 0,
        "latest_model_verified_at": "2026-06-23",
        "max_age_days": 30,
        "latest_model_sources": [
            "https://developers.openai.com/api/docs/guides/latest-model"
        ],
    }
    assert report["recommendations"] == [
        "verify_total_and_tax_reconciliation_in_preview",
        "prefer_cross_receipt_grounded_item_mutations",
    ]
    merchant = report["merchants"][0]
    assert merchant["merchant_name"] == "Market Mart"
    assert merchant["readiness_status"] == "ready"
    assert merchant["acceptance_rate"] == 1.0
    assert merchant["contract_ready_operations"] == [
        "add_line_item",
        "hard_negative",
    ]
    example = merchant["accepted_examples"][0]
    assert example["candidate_id"] == "grounded-add-item"
    assert example["changed_text"] == "BANANAS"
    assert example["candidate_quality"] == {
        "score": 0.93,
        "high_fidelity": True,
        "components": {
            "arithmetic_reconciliation": 0.9,
            "category_alignment": 1.0,
            "cross_receipt_grounding": 1.0,
            "structure_similarity": 0.86,
            "token_budget": 1.0,
        },
    }
    assert example["selection_evidence"] == expected_selection_evidence
    assert example["source_lineage"] == expected_source_lineage
    assert example["total_change"] == {
        "old_grand_total": "8.49",
        "new_grand_total": "10.44",
        "tax_delta": "0.00",
    }
    assert example["receipt_shape"] == {
        "line_count": 8,
        "token_count": 28,
        "truncated": False,
        "coordinate_system": "normalized_receipt_0_1000_y_high_is_top",
    }
    assert example["preview_lines"] == [
        {
            "line_number": 3,
            "text": "BANANAS 1.95",
            "role": "line_item",
            "y": 0.695,
            "bbox": [90, 695, 890, 720],
            "synthetic_insert": True,
            "modified_labels": [],
        }
    ]


def test_build_local_synthesis_quality_report_blocks_stale_paid_llm_guidance(
    monkeypatch,
):
    module = _load_module()
    monkeypatch.setenv("RECEIPT_AGENT_MODEL_FRESHNESS_CHECK_DATE", "2026-06-23")
    artifact = _artifact("Market Mart")
    bundle = module.build_local_synthetic_training_bundle(
        [artifact],
        min_grounded_candidate_share=0.4,
    )
    bundle["preflight"]["llm_execution"] = {
        "mode_counts": {"llm_assisted": 1},
        "paid_llm_disabled_count": 0,
        "api_call_allowed_count": 1,
        "configured_models": ["openai/gpt-5.5"],
        "latest_model_sources": [
            "https://developers.openai.com/api/docs/guides/latest-model"
        ],
        "latest_model_verified_at": "2026-04-01",
    }

    report = module.build_local_synthesis_quality_report(
        bundle,
        artifacts=[artifact],
    )

    assert report["ready"] is True
    assert report["training_ready"] is False
    assert report["training_ready_reasons"] == [
        "refresh_latest_model_guidance_before_synthesis"
    ]
    assert report["quality_gates"]["llm_model_freshness_gate"] == {
        "enabled": True,
        "passed": False,
        "requires_current_model_guidance": True,
        "api_call_allowed_count": 1,
        "llm_assisted_mode_count": 1,
        "latest_model_verified_at": "2026-04-01",
        "latest_model_age_days": 83,
        "max_age_days": 30,
        "latest_model_sources": [
            "https://developers.openai.com/api/docs/guides/latest-model"
        ],
        "reason": "latest_model_guidance_stale_or_missing",
    }


def test_build_local_synthesis_quality_report_blocks_missing_paid_llm_guidance(
    monkeypatch,
):
    module = _load_module()
    monkeypatch.setenv("RECEIPT_AGENT_MODEL_FRESHNESS_CHECK_DATE", "2026-06-23")
    artifact = _artifact("Market Mart")
    bundle = module.build_local_synthetic_training_bundle(
        [artifact],
        min_grounded_candidate_share=0.4,
    )
    bundle["preflight"]["llm_execution"] = {
        "mode_counts": {"llm_assisted": 1},
        "paid_llm_disabled_count": 0,
        "api_call_allowed_count": 0,
        "configured_models": ["openai/gpt-5.5"],
    }

    report = module.build_local_synthesis_quality_report(
        bundle,
        artifacts=[artifact],
    )

    assert report["training_ready"] is False
    assert report["training_ready_reasons"] == [
        "refresh_latest_model_guidance_before_synthesis"
    ]
    assert report["quality_gates"]["llm_model_freshness_gate"] == {
        "enabled": True,
        "passed": False,
        "requires_current_model_guidance": True,
        "api_call_allowed_count": 0,
        "llm_assisted_mode_count": 1,
        "max_age_days": 30,
        "reason": "latest_model_guidance_stale_or_missing",
    }


def test_build_local_synthesis_quality_report_allows_stale_no_spend_guidance(
    monkeypatch,
):
    module = _load_module()
    monkeypatch.setenv("RECEIPT_AGENT_MODEL_FRESHNESS_CHECK_DATE", "2026-06-23")
    artifact = _artifact("Market Mart")
    bundle = module.build_local_synthetic_training_bundle(
        [artifact],
        min_grounded_candidate_share=0.4,
    )
    bundle["preflight"]["llm_execution"] = {
        "mode_counts": {"deterministic_fallback": 1},
        "paid_llm_disabled_count": 1,
        "api_call_allowed_count": 0,
        "configured_models": ["openai/gpt-5.5"],
        "latest_model_sources": [
            "https://developers.openai.com/api/docs/guides/latest-model"
        ],
        "latest_model_verified_at": "2026-04-01",
    }

    report = module.build_local_synthesis_quality_report(
        bundle,
        artifacts=[artifact],
    )

    assert report["training_ready"] is True
    assert report["training_ready_reasons"] == []
    assert report["quality_gates"]["llm_model_freshness_gate"] == {
        "enabled": True,
        "passed": True,
        "requires_current_model_guidance": False,
        "api_call_allowed_count": 0,
        "latest_model_verified_at": "2026-04-01",
        "max_age_days": 30,
        "latest_model_sources": [
            "https://developers.openai.com/api/docs/guides/latest-model"
        ],
    }


def test_build_local_synthetic_training_bundle_reports_caps():
    module = _load_module()
    artifact = _artifact("Market Mart")
    duplicate = json.loads(json.dumps(artifact["synthetic_receipt_candidates"][-1]))
    duplicate["candidate_id"] = "duplicate-hard-negative"
    duplicate["image_id"] = "synthetic-duplicate-hard-negative"
    duplicate["receipt_key"] = "synthetic-duplicate-hard-negative#00001"
    duplicate["metadata"]["structure_similarity"]["score"] = 0.86
    artifact["synthetic_receipt_candidates"].append(duplicate)

    bundle = module.build_local_synthetic_training_bundle(
        [artifact],
        min_grounded_candidate_share=0.3,
        max_per_merchant_operation=1,
    )

    assert bundle["ready"] is True
    assert bundle["selection"]["candidates_seen"] == 3
    assert bundle["selection"]["candidates_accepted"] == 2
    assert bundle["selection"]["rejection_reasons"] == {
        "merchant_operation_synthetic_cap": 1
    }
    assert bundle["selection"]["rejected_candidate_examples"] == [
        {
            "candidate_id": "duplicate-hard-negative",
            "receipt_key": "synthetic-duplicate-hard-negative#00001",
            "image_id": "synthetic-duplicate-hard-negative",
            "merchant_name": "Market Mart",
            "operation": "hard_negative",
            "reason": "merchant_operation_synthetic_cap",
            "idx": 2,
            "structure_similarity": 0.86,
            "candidate_quality": 0.9,
        }
    ]
    assert bundle["candidate_mix"]["rejected_count"] == 1
    assert bundle["candidate_mix"]["rejection_reasons"] == {
        "merchant_operation_synthetic_cap": 1
    }
    assert bundle["candidate_mix"]["merchants"][0]["rejected_count"] == 1
    assert bundle["candidate_mix"]["merchants"][0]["rejection_reasons"] == {
        "merchant_operation_synthetic_cap": 1
    }
    assert [row["candidate_id"] for row in bundle["synthetic_training_examples"]] == [
        "grounded-add-item",
        "hard-negative",
    ]


def test_build_local_synthetic_training_bundle_reports_multi_merchant_mix():
    module = _load_module()

    bundle = module.build_local_synthetic_training_bundle(
        [
            _artifact("Market Mart"),
            _artifact("Sprouts Farmers Market", score=0.92),
        ],
        min_ready_share=1.0,
        min_grounded_candidate_share=0.4,
    )

    assert bundle["ready"] is True
    assert bundle["candidate_mix"]["merchant_count"] == 2
    assert bundle["candidate_mix"]["accepted_merchant_count"] == 2
    assert bundle["candidate_mix"]["rejected_count"] == 0
    assert bundle["candidate_mix"]["rejection_reasons"] == {}
    assert bundle["candidate_mix"]["accepted_operation_counts"] == {
        "add_line_item": 2,
        "hard_negative": 2,
    }
    assert [
        merchant["merchant_name"] for merchant in bundle["candidate_mix"]["merchants"]
    ] == ["Market Mart", "Sprouts Farmers Market"]
    assert [
        merchant["accepted_operation_counts"]
        for merchant in bundle["candidate_mix"]["merchants"]
    ] == [
        {"add_line_item": 1, "hard_negative": 1},
        {"add_line_item": 1, "hard_negative": 1},
    ]
    assert [
        merchant["accepted_category_counts"]
        for merchant in bundle["candidate_mix"]["merchants"]
    ] == [{"PRODUCE": 1}, {"PRODUCE": 1}]


def test_build_local_synthetic_training_bundle_blocks_high_risk_single_merchant_mix():
    module = _load_module()
    dominant = _artifact("Market Mart", candidate_quality=False)
    extra_add = json.loads(json.dumps(dominant["synthetic_receipt_candidates"][0]))
    extra_add.update(
        {
            "candidate_id": "second-grounded-add-item",
            "image_id": "synthetic-market-mart-second-add",
            "receipt_key": "synthetic-market-mart-second-add#00001",
        }
    )
    extra_add["metadata"]["structure_similarity"]["score"] = 0.91
    dominant["synthetic_receipt_candidates"].append(extra_add)

    bundle = module.build_local_synthetic_training_bundle(
        [
            dominant,
            _artifact("Ready But No Accepted Merchant", candidates=False),
        ],
        min_ready_share=0.0,
        min_avg_readiness_score=0.0,
        min_grounded_candidate_share=0.0,
    )

    assert bundle["ready"] is False
    assert bundle["reasons"] == [
        "accepted_synthetic_mix_single_merchant_high_risk",
        "no_high_fidelity_candidate_quality",
    ]
    assert bundle["selection"]["candidates_accepted"] == 3
    balance = bundle["candidate_mix"]["accepted_mix_balance"]
    assert balance["risk_level"] == "high"
    assert balance["risk_reasons"] == ["single_merchant_accepted"]
    assert bundle["candidate_mix"]["accepted_merchant_count"] == 1
    assert bundle["preflight"]["ready_merchant_count"] == 2
    assert bundle["synthetic_training_batch_policy"] == {
        "schema_version": "synthetic-training-batch-policy-v1",
        "status": "hold",
        "recommended_example_count": 0,
        "accepted_candidate_count": 3,
        "selected_candidate_count": 3,
        "candidate_quality_count": 3,
        "high_fidelity_candidate_count": 0,
        "max_synthetic_train_share": 0.0,
        "max_per_merchant": 5,
        "max_per_merchant_operation": 2,
        "overtraining_risk_level": "high",
        "risk_reasons": ["single_merchant_accepted"],
        "hold_reasons": [
            "accepted_synthetic_mix_single_merchant_high_risk",
            "rebalance_synthetic_mix_before_training",
            "no_high_fidelity_candidate_quality",
        ],
        "requires_real_validation_split": True,
        "review_required": True,
    }
    assert bundle["synthesis_quality_report"]["training_batch_policy"]["status"] == (
        "hold"
    )
    assert (
        "rebalance_synthetic_mix_before_training"
        in bundle["synthesis_quality_report"]["recommendations"]
    )


def test_build_local_synthetic_training_bundle_derives_candidate_quality():
    module = _load_module()
    artifact = _artifact("Market Mart", candidate_quality=False)
    for candidate in artifact["synthetic_receipt_candidates"]:
        metadata = candidate["metadata"]
        structure = metadata["structure_similarity"]
        score = structure["score"]
        structure["real_baseline_comparison"] = {
            "baseline_pair_count": 6,
            "candidate_score": score,
            "baseline_min": 0.82,
            "baseline_max": 0.98,
            "within_real_score_range": True,
        }
        metadata["layout_integrity"] = {
            "score": 1.0,
            "passed": True,
            "line_count": 5,
            "word_count": len(candidate.get("tokens") or []),
            "overlap_pair_count": 0,
            "out_of_bounds_word_count": 0,
        }
    bundle = module.build_local_synthetic_training_bundle(
        [artifact],
        min_grounded_candidate_share=0.4,
    )

    assert bundle["ready"] is True
    assert bundle["reasons"] == []
    assert bundle["selection"]["candidates_accepted"] == 2
    assert bundle["synthetic_training_batch_policy"] == {
        "schema_version": "synthetic-training-batch-policy-v1",
        "status": "smoke_test_only",
        "recommended_example_count": 2,
        "accepted_candidate_count": 2,
        "selected_candidate_count": 2,
        "candidate_quality_count": 2,
        "high_fidelity_candidate_count": 2,
        "max_synthetic_train_share": 0.01,
        "max_per_merchant": 5,
        "max_per_merchant_operation": 2,
        "overtraining_risk_level": "low",
        "risk_reasons": ["too_few_examples_for_balance_assessment"],
        "hold_reasons": [],
        "requires_real_validation_split": True,
        "review_required": True,
    }
    assert [
        row["metadata"]["candidate_quality"]["source"]
        for row in bundle["synthetic_training_examples"]
    ] == [
        "deterministic_layoutlm_gate_evidence",
        "deterministic_layoutlm_gate_evidence",
    ]
    assert all(
        row["metadata"]["candidate_quality"]["high_fidelity"] is True
        for row in bundle["synthetic_training_examples"]
    )


def test_build_local_synthetic_training_bundle_holds_derived_quality_without_independent_evidence():
    module = _load_module()
    bundle = module.build_local_synthetic_training_bundle(
        [_artifact("Market Mart", candidate_quality=False)],
        min_grounded_candidate_share=0.4,
    )

    assert bundle["ready"] is False
    assert bundle["reasons"] == ["no_high_fidelity_candidate_quality"]
    assert bundle["selection"]["candidates_accepted"] == 2
    assert bundle["synthetic_training_batch_policy"]["status"] == "hold"
    assert bundle["synthetic_training_batch_policy"]["candidate_quality_count"] == 2
    assert (
        bundle["synthetic_training_batch_policy"]["high_fidelity_candidate_count"] == 0
    )
    assert bundle["synthetic_training_batch_policy"]["hold_reasons"] == [
        "no_high_fidelity_candidate_quality"
    ]


def test_derived_candidate_quality_rejects_above_range_real_baseline():
    module = _load_module()
    candidate = _artifact("Market Mart", candidate_quality=False)[
        "synthetic_receipt_candidates"
    ][0]
    candidate["metadata"]["structure_similarity"]["real_baseline_comparison"] = {
        "baseline_pair_count": 6,
        "candidate_score": 1.0,
        "baseline_min": 0.82,
        "baseline_max": 0.95,
    }

    assert module._candidate_real_baseline_alignment_score(candidate) == 0.0

    candidate["metadata"]["structure_similarity"]["real_baseline_comparison"][
        "within_real_score_range"
    ] = True
    assert module._candidate_real_baseline_alignment_score(candidate) == 0.0


def test_derived_candidate_quality_requires_robust_independent_evidence():
    module = _load_module()
    candidate = _artifact("Market Mart", candidate_quality=False)[
        "synthetic_receipt_candidates"
    ][0]
    candidate["metadata"]["layout_integrity"] = {"passed": True, "score": 0.7}
    candidate["metadata"]["structure_similarity"]["real_baseline_comparison"] = {
        "baseline_pair_count": 1,
        "candidate_score": 0.92,
        "baseline_min": 0.82,
        "baseline_max": 0.98,
        "within_real_score_range": True,
    }

    assert module._candidate_layout_integrity_score(candidate) == 0.7
    assert module._candidate_real_baseline_alignment_score(candidate) is None
    candidate["metadata"]["layout_integrity"] = {"passed": True}
    assert module._candidate_layout_integrity_score(candidate) is None
    quality = module._derived_candidate_quality(
        candidate,
        min_structure_similarity=0.6,
        structure_component_thresholds=STRUCTURE_COMPONENT_THRESHOLDS,
        quality_failure=None,
    )
    assert quality["high_fidelity"] is False


def test_synthetic_training_batch_policy_holds_unscored_larger_batch():
    module = _load_module()
    policy = module._synthetic_training_batch_policy(
        candidate_mix={
            "accepted_count": 4,
            "accepted_mix_balance": {"risk_level": "low", "risk_reasons": []},
        },
        selected_rows=[{"metadata": {}} for _ in range(4)],
        max_per_merchant=5,
        max_per_merchant_operation=2,
        bundle_reasons=[],
    )

    assert policy["status"] == "hold"
    assert policy["recommended_example_count"] == 0
    assert policy["candidate_quality_count"] == 0
    assert policy["high_fidelity_candidate_count"] == 0
    assert policy["hold_reasons"] == ["missing_candidate_quality_assessment"]


def test_synthetic_training_batch_policy_holds_unscored_smoke_batch():
    module = _load_module()
    policy = module._synthetic_training_batch_policy(
        candidate_mix={
            "accepted_count": 2,
            "accepted_mix_balance": {
                "risk_level": "low",
                "risk_reasons": ["too_few_examples_for_balance_assessment"],
            },
        },
        selected_rows=[{"metadata": {}} for _ in range(2)],
        max_per_merchant=5,
        max_per_merchant_operation=2,
        bundle_reasons=[],
    )

    assert policy["status"] == "hold"
    assert policy["recommended_example_count"] == 0
    assert policy["hold_reasons"] == ["missing_candidate_quality_assessment"]


def test_inventory_local_artifacts_classifies_pattern_and_bundle(tmp_path):
    module = _load_module()
    pattern_path = tmp_path / "pattern.json"
    bundle_path = tmp_path / "bundle.json"
    pattern_path.write_text(json.dumps(_artifact("Market Mart")), encoding="utf-8")
    bundle = module.build_local_synthetic_training_bundle(
        [_artifact("Sprouts Farmers Market", score=0.92)],
        min_grounded_candidate_share=0.4,
    )
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")

    inventory = module.inventory_local_artifacts(artifact_dirs=[str(tmp_path)])

    assert inventory["ready"] is True
    assert inventory["file_count"] == 2
    assert inventory["kind_counts"] == {
        "pattern_artifact": 1,
        "training_bundle": 1,
    }
    assert inventory["preflightable_file_count"] == 1
    assert inventory["layoutlm_ready_file_count"] == 1
    assert inventory["quality_report_file_count"] == 1
    assert inventory["quality_report_ready_file_count"] == 1
    assert inventory["candidate_count"] == 4
    assert inventory["merchant_count"] == 1
    assert inventory["recommendations"] == [
        "run_bundle_on_pattern_artifacts",
        "use_training_bundle_for_layoutlm",
        "review_synthesis_quality_report",
    ]
    rows_by_kind = {row["kind"]: row for row in inventory["files"]}
    assert rows_by_kind["pattern_artifact"]["operation_counts"] == {
        "add_line_item": 1,
        "hard_negative": 1,
    }
    assert rows_by_kind["training_bundle"]["accepted_merchant_count"] == 1
    assert rows_by_kind["training_bundle"]["quality_report_present"] is True
    assert rows_by_kind["training_bundle"]["quality_report_ready"] is True
    assert rows_by_kind["training_bundle"]["quality_report_merchant_count"] == 1
    assert rows_by_kind["training_bundle"]["quality_report_accepted_count"] == 2
    assert rows_by_kind["training_bundle"]["quality_report_recommendations"] == [
        "verify_total_and_tax_reconciliation_in_preview",
        "prefer_cross_receipt_grounded_item_mutations",
    ]


def test_inventory_local_artifacts_flags_visual_summaries_not_ready(tmp_path):
    module = _load_module()
    visual_path = tmp_path / "visual.json"
    visual_path.write_text(
        json.dumps(
            {
                "image_path": "/tmp/synthetic.png",
                "candidate_id": "visual-only",
                "metadata": {"operation": "hard_negative"},
                "lines": [{"text": "LOCAL FAVORITES"}],
            }
        ),
        encoding="utf-8",
    )

    inventory = module.inventory_local_artifacts(artifact_dirs=[str(tmp_path)])

    assert inventory["ready"] is False
    assert inventory["kind_counts"] == {"visual_receipt_summary": 1}
    assert inventory["preflightable_file_count"] == 0
    assert inventory["layoutlm_ready_file_count"] == 0
    assert inventory["candidate_count"] == 0
    assert inventory["recommendations"] == [
        "regenerate_pattern_artifacts_from_receipt_data",
        "visual_summaries_are_not_training_bundles",
    ]


def test_inventory_cli_does_not_load_deployment(tmp_path, monkeypatch, capsys):
    module = _load_module()
    pattern_path = tmp_path / "pattern.json"
    pattern_path.write_text(json.dumps(_artifact()), encoding="utf-8")
    monkeypatch.setattr(
        module,
        "load_outputs",
        lambda _env: (_ for _ in ()).throw(AssertionError("should not call AWS")),
    )
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "verify_synthetic_replay.py",
            "inventory",
            "--artifact-dir",
            str(tmp_path),
        ],
    )

    assert module.main() == 0
    output = json.loads(capsys.readouterr().out)
    assert output["ready"] is True
    assert output["kind_counts"] == {"pattern_artifact": 1}


def test_build_local_pattern_artifacts_from_receipt_json(tmp_path):
    module = _load_module()
    receipt_path = tmp_path / "receipts.json"
    output_dir = tmp_path / "artifacts"
    receipt_path.write_text(json.dumps(_receipt_group_payload()), encoding="utf-8")

    groups = module.load_local_receipt_groups(receipt_files=[str(receipt_path)])
    artifacts = module.build_local_pattern_artifacts_from_receipts(groups)
    output_files = module.write_local_pattern_artifacts(
        artifacts,
        output_dir=str(output_dir),
    )
    summary = module.summarize_built_pattern_artifacts(
        artifacts,
        output_files=output_files,
    )

    assert summary["ready"] is True
    assert summary["artifact_count"] == 1
    assert summary["merchant_count"] == 1
    assert summary["candidate_count"] >= 2
    assert summary["readiness_status_counts"] in (
        {"ready": 1},
        {"partial": 1},
    )
    assert summary["source_receipt_quality"]["status_counts"] == {"usable": 1}
    assert summary["source_receipt_quality"]["usable_merchant_count"] == 1
    source_quality = summary["source_receipt_quality"]["merchants"][0]
    assert source_quality["merchant_name"] == "Market Mart"
    assert source_quality["receipt_count"] == 2
    assert source_quality["text_structure_status"] == "labeled"
    assert source_quality["receipts_with_line_item_labels"] == 2
    assert source_quality["receipts_with_grand_total_label"] == 2
    assert source_quality["blockers"] == []
    assert source_quality["limitations"] == []
    written = json.loads(Path(output_files[0]).read_text(encoding="utf-8"))
    assert written["schema_version"] == "offline-pattern-artifact-v1"
    assert written["merchant_name"] == "Market Mart"
    assert written["source_receipt_count"] == 2
    assert written["source_receipt_quality"]["status"] == "usable"
    assert written["merchant_receipt_parameterization"]["synthesis_readiness"]
    assert written["synthetic_receipt_candidates"]

    inventory = module.inventory_local_artifacts(artifact_dirs=[str(output_dir)])
    assert inventory["ready"] is True
    assert inventory["kind_counts"] == {"pattern_artifact": 1}

    bundle = module.build_local_synthetic_training_bundle(
        module.load_local_pattern_artifacts(artifact_dirs=[str(output_dir)]),
        min_ready_share=0.0,
        min_avg_readiness_score=0.0,
        min_grounded_candidate_share=0.0,
    )
    assert bundle["selection"]["candidates_accepted"] > 0
    assert bundle["source_receipt_quality"]["status_counts"] == {"usable": 1}
    assert bundle["source_receipt_quality"]["usable_merchant_count"] == 1
    assert bundle["source_receipt_quality"]["merchants"][0]["merchant_name"] == (
        "Market Mart"
    )


def test_build_artifacts_cli_writes_without_deployment_lookup(
    tmp_path,
    monkeypatch,
    capsys,
):
    module = _load_module()
    receipt_path = tmp_path / "receipts.json"
    output_dir = tmp_path / "artifacts"
    receipt_path.write_text(json.dumps(_receipt_group_payload()), encoding="utf-8")
    monkeypatch.setattr(
        module,
        "load_outputs",
        lambda _env: (_ for _ in ()).throw(AssertionError("should not call AWS")),
    )
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "verify_synthetic_replay.py",
            "build-artifacts",
            "--receipt-file",
            str(receipt_path),
            "--output-dir",
            str(output_dir),
        ],
    )

    assert module.main() == 0
    output = json.loads(capsys.readouterr().out)
    assert output["ready"] is True
    assert output["artifact_count"] == 1
    assert output["candidate_count"] >= 2
    assert output["output_files"] == [str(output_dir / "market-mart.json")]


def test_run_local_synthetic_pipeline_builds_artifacts_and_bundle(tmp_path):
    module = _load_module()
    receipt_path = tmp_path / "receipts.json"
    artifact_dir = tmp_path / "artifacts"
    bundle_path = tmp_path / "bundle.json"
    receipt_path.write_text(json.dumps(_receipt_group_payload()), encoding="utf-8")

    result = module.run_local_synthetic_pipeline(
        receipt_files=[str(receipt_path)],
        artifact_output_dir=str(artifact_dir),
        bundle_output=str(bundle_path),
        min_ready_share=0.0,
        min_avg_readiness_score=0.0,
        min_grounded_candidate_share=0.0,
    )

    assert result["ready"] is True
    assert result["build"]["artifact_count"] == 1
    assert result["inventory"]["kind_counts"] == {"pattern_artifact": 1}
    assert result["preflight"]["ready"] is True
    assert result["bundle"]["ready"] is True
    assert result["bundle"]["selection"]["candidates_accepted"] > 0
    assert (artifact_dir / "market-mart.json").exists()
    written_bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    assert written_bundle["schema_version"] == "layoutlm-synthetic-training-bundle-v1"
    assert written_bundle["selection"]["candidates_accepted"] > 0


def test_run_local_synthetic_pipeline_handles_multi_merchant_receipts_without_paid_lookup(
    tmp_path,
    monkeypatch,
):
    module = _load_module()
    receipt_path = tmp_path / "multi_merchant_receipts.json"
    artifact_dir = tmp_path / "artifacts"
    bundle_path = tmp_path / "bundle.json"
    receipt_path.write_text(
        json.dumps(_multi_merchant_receipt_payload()),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        module,
        "load_outputs",
        lambda _env: (_ for _ in ()).throw(AssertionError("should not call AWS")),
    )

    result = module.run_local_synthetic_pipeline(
        receipt_files=[str(receipt_path)],
        artifact_output_dir=str(artifact_dir),
        bundle_output=str(bundle_path),
        min_ready_share=0.0,
        min_avg_readiness_score=0.0,
        min_grounded_candidate_share=0.0,
    )

    assert result["ready"] is True
    assert result["build"]["artifact_count"] == 3
    assert result["build"]["readiness_status_counts"].get("blocked") == 1
    audits = {
        row["merchant_name"]: row for row in result["build"]["merchant_synthesis_audit"]
    }
    assert audits["Sprouts Farmers Market"]["readiness_status"] == "ready"
    assert audits["Sprouts Farmers Market"]["grounded_candidate_count"] >= 1
    assert audits["Sprouts Farmers Market"]["arithmetic_candidate_count"] >= 2
    assert audits["Sprouts Farmers Market"]["mutation_inventory"][
        "candidate_operation_counts"
    ] == {
        "add_line_item": 1,
        "hard_negative": 3,
        "remove_line_item": 1,
    }
    assert audits["Sprouts Farmers Market"]["mutation_inventory"][
        "candidate_category_counts"
    ] == {"PRODUCE": 2}
    assert (
        audits["Sprouts Farmers Market"]["mutation_inventory"]["mutable_field_count"]
        == 0
    )
    sprouts_operations = {
        row["operation"]: row
        for row in audits["Sprouts Farmers Market"]["operation_readiness"]
    }
    assert audits["Sprouts Farmers Market"]["missing_operations"] == [
        "remove_line_item",
        "replace_field",
    ]
    assert sprouts_operations["add_line_item"]["ready"] is True
    assert sprouts_operations["add_line_item"]["candidate_count"] == 1
    assert sprouts_operations["add_line_item"]["evidence_candidate_count"] == 1
    assert (
        sprouts_operations["add_line_item"]["evidence"]["grounded_candidate_count"] == 2
    )
    assert sprouts_operations["remove_line_item"]["ready"] is False
    assert sprouts_operations["remove_line_item"]["evidence_candidate_count"] == 1
    assert sprouts_operations["remove_line_item"]["blockers"] == [
        "no_removable_non_taxable_items"
    ]
    assert sprouts_operations["replace_field"]["blockers"] == [
        "no_stable_mutable_fields"
    ]
    assert audits["Sprouts Farmers Market"]["next_synthesis_actions"] == [
        "synthesize_hard_negative_from_existing_evidence",
        "synthesize_add_line_item_from_existing_evidence",
        "collect_multi_item_non_taxable_receipts_with_totals",
        "collect_stable_date_time_examples_for_field_replacement",
    ]
    assert audits["Thin Merchant"]["readiness_status"] == "blocked"
    assert audits["Thin Merchant"]["blockers"] == [
        "no_line_items",
        "no_observed_item_catalog",
    ]
    assert audits["Thin Merchant"]["grounded_candidate_count"] == 0
    assert audits["Thin Merchant"]["arithmetic_candidate_count"] == 0
    thin_operations = {
        row["operation"]: row for row in audits["Thin Merchant"]["operation_readiness"]
    }
    assert audits["Thin Merchant"]["missing_operations"] == [
        "hard_negative",
        "add_line_item",
        "remove_line_item",
        "replace_field",
    ]
    assert audits["Thin Merchant"]["next_synthesis_actions"] == [
        "resolve_merchant_synthesis_blockers"
    ]
    assert thin_operations["hard_negative"]["ready"] is False
    assert thin_operations["hard_negative"]["candidate_count"] == 1
    assert thin_operations["hard_negative"]["evidence_candidate_count"] == 1
    assert "readiness_status_blocked" in thin_operations["hard_negative"]["blockers"]
    assert thin_operations["add_line_item"]["blockers"] == [
        "readiness_status_blocked",
        "no_line_items",
        "no_observed_item_catalog",
        "no_cross_receipt_grounded_add_items",
    ]
    assert thin_operations["remove_line_item"]["blockers"] == [
        "readiness_status_blocked",
        "no_line_items",
        "no_observed_item_catalog",
        "no_removable_non_taxable_items",
    ]
    assert result["preflight"]["merchant_count"] == 3
    assert result["preflight"]["readiness_status_counts"].get("blocked") == 1
    coverage = result["preflight"]["operation_coverage"]
    assert coverage["operations"]["hard_negative"]["ready_merchant_count"] >= 2
    assert coverage["operations"]["add_line_item"]["ready_merchant_count"] >= 2
    assert coverage["operations"]["remove_line_item"]["ready_merchant_count"] >= 1
    assert coverage["operations"]["replace_field"]["ready_merchant_count"] == 0
    assert (
        "collect_stable_date_time_examples_for_field_replacement"
        in coverage["recommendations"]
    )
    assert result["bundle"]["candidate_mix"]["accepted_merchant_count"] >= 2
    assert (
        result["bundle"]["candidate_mix"]["accepted_category_counts"].get(
            "PRODUCE",
            0,
        )
        >= 2
    )
    pipeline_merchants = {
        row["merchant_name"]: row
        for row in result["bundle"]["candidate_mix"]["merchants"]
    }
    assert pipeline_merchants["Market Mart"]["accepted_count"] > 0
    assert pipeline_merchants["Sprouts Farmers Market"]["accepted_count"] > 0
    assert pipeline_merchants["Thin Merchant"]["accepted_count"] == 0
    assert pipeline_merchants["Thin Merchant"]["rejection_reasons"] == {
        "merchant_synthesis_not_ready": 1
    }
    assert (artifact_dir / "market-mart.json").exists()
    assert (artifact_dir / "sprouts-farmers-market.json").exists()
    assert (artifact_dir / "thin-merchant.json").exists()

    written_bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    assert written_bundle["validation_policy"] == "real_receipts_only"
    report_coverage = written_bundle["synthesis_quality_report"]["operation_coverage"]
    assert report_coverage["operations"]["hard_negative"]["ready_merchant_count"] >= 2
    assert report_coverage["operations"]["add_line_item"]["ready_merchant_count"] >= 2
    report_merchants = {
        row["merchant_name"]: row
        for row in written_bundle["synthesis_quality_report"]["merchants"]
    }
    report_sprouts_ops = {
        row["operation"]: row
        for row in report_merchants["Sprouts Farmers Market"]["operation_readiness"]
    }
    assert report_sprouts_ops["add_line_item"]["ready"] is True
    assert report_sprouts_ops["add_line_item"]["evidence_candidate_count"] == 1
    assert report_sprouts_ops["remove_line_item"]["ready"] is False
    assert report_sprouts_ops["remove_line_item"]["evidence_candidate_count"] == 1
    assert report_merchants["Sprouts Farmers Market"]["missing_operations"] == [
        "remove_line_item",
        "replace_field",
    ]
    assert report_merchants["Sprouts Farmers Market"]["next_synthesis_actions"] == [
        "synthesize_hard_negative_from_existing_evidence",
        "synthesize_add_line_item_from_existing_evidence",
        "collect_multi_item_non_taxable_receipts_with_totals",
        "collect_stable_date_time_examples_for_field_replacement",
    ]
    merchants = {
        row["merchant_name"]: row
        for row in written_bundle["candidate_mix"]["merchants"]
    }
    assert merchants["Market Mart"]["accepted_count"] > 0
    assert merchants["Sprouts Farmers Market"]["accepted_count"] > 0
    assert merchants["Thin Merchant"]["accepted_count"] == 0
    assert merchants["Thin Merchant"]["rejection_reasons"] == {
        "merchant_synthesis_not_ready": 1
    }

    sprouts_adds = [
        row
        for row in written_bundle["synthetic_training_examples"]
        if row["merchant_name"] == "Sprouts Farmers Market"
        and row["metadata"]["operation"] == "add_line_item"
        and row["metadata"]["added_item"]["product_text"] == "YELLOW BANANAS"
    ]
    assert len(sprouts_adds) == 1
    sprouts_add = sprouts_adds[0]
    metadata = sprouts_add["metadata"]
    assert metadata["old_grand_total"] == "8.49"
    assert metadata["new_grand_total"] == "10.44"
    assert metadata["arithmetic_reconciliation"]["tax_delta"] == "0.00"
    assert metadata["added_item"]["category"] == "PRODUCE"
    assert metadata["added_item"]["seen_in_other_receipt"] is True
    assert metadata["observed_item_evidence"]["base_receipt_has_category"] is True
    assert metadata["observed_item_evidence"]["product_seen_outside_base"] == [
        "20000000-0000-0000-0000-000000000001#00001"
    ]

    tokens = sprouts_add["tokens"]
    produce_index = tokens.index("PRODUCE")
    yellow_index = tokens.index("YELLOW")
    dairy_index = tokens.index("DAIRY")
    assert produce_index < yellow_index < dairy_index
    produce_y = sprouts_add["bboxes"][produce_index][1]
    yellow_y = sprouts_add["bboxes"][yellow_index][1]
    dairy_y = sprouts_add["bboxes"][dairy_index][1]
    assert produce_y > yellow_y > dairy_y


def test_merchant_synthesis_audit_does_not_treat_raw_candidate_count_as_ready():
    module = _load_module()

    audit = module.summarize_merchant_synthesis_audit(
        [
            {
                "merchant_name": "Candidate Count Mart",
                "source_receipt_count": 2,
                "merchant_receipt_parameterization": {
                    "merchant_name": "Candidate Count Mart",
                    "receipt_count": 2,
                    "synthesis_readiness": {
                        "status": "ready",
                        "score": 0.78,
                        "supported_operations": [],
                        "grounded_add_item_candidate_count": 0,
                        "removable_item_candidate_count": 0,
                        "mutable_field_count": 0,
                        "blockers": [],
                        "limitations": [],
                    },
                },
                "synthetic_receipt_candidates": [
                    {
                        "candidate_id": "ungrounded-add-item",
                        "merchant_name": "Candidate Count Mart",
                        "metadata": {"operation": "add_line_item"},
                    }
                ],
            }
        ]
    )[0]

    operations = {row["operation"]: row for row in audit["operation_readiness"]}
    assert operations["add_line_item"]["candidate_count"] == 1
    assert operations["add_line_item"]["evidence_candidate_count"] == 0
    assert operations["add_line_item"]["ready"] is False
    assert operations["add_line_item"]["blockers"] == [
        "no_cross_receipt_grounded_add_items"
    ]
    assert (
        "collect_cross_receipt_item_and_category_evidence"
        in audit["next_synthesis_actions"]
    )


def test_merchant_synthesis_audit_requires_confused_hard_negative_evidence():
    module = _load_module()

    audit = module.summarize_merchant_synthesis_audit(
        [
            {
                "merchant_name": "Easy Negative Mart",
                "source_receipt_count": 2,
                "merchant_receipt_parameterization": {
                    "merchant_name": "Easy Negative Mart",
                    "receipt_count": 2,
                    "synthesis_readiness": {
                        "status": "ready",
                        "score": 0.84,
                        "supported_operations": [],
                        "ready_hard_negative_labels": [],
                        "blockers": [],
                        "limitations": [],
                    },
                },
                "synthetic_receipt_candidates": [
                    {
                        "candidate_id": "not-a-confusion",
                        "merchant_name": "Easy Negative Mart",
                        "metadata": {
                            "operation": "hard_negative",
                            "actual_label": "O",
                            "predicted_label": "O",
                        },
                    }
                ],
            }
        ]
    )[0]

    operations = {row["operation"]: row for row in audit["operation_readiness"]}
    assert operations["hard_negative"]["candidate_count"] == 1
    assert operations["hard_negative"]["evidence_candidate_count"] == 0
    assert operations["hard_negative"]["ready"] is False
    assert operations["hard_negative"]["blockers"] == [
        "no_supported_hard_negative_slots"
    ]
    assert (
        "mine_confusion_targets_for_hard_negative_slots"
        in audit["next_synthesis_actions"]
    )


def test_merchant_synthesis_audit_requires_ready_status_for_synthesis_actions():
    module = _load_module()

    audit = module.summarize_merchant_synthesis_audit(
        [
            {
                "merchant_name": "Partial Evidence Mart",
                "source_receipt_count": 2,
                "merchant_receipt_parameterization": {
                    "merchant_name": "Partial Evidence Mart",
                    "receipt_count": 2,
                    "synthesis_readiness": {
                        "status": "partial",
                        "score": 0.72,
                        "supported_operations": ["add_line_item"],
                        "grounded_add_item_candidate_count": 1,
                        "blockers": [],
                        "limitations": [],
                    },
                },
                "synthetic_receipt_candidates": [
                    {
                        "candidate_id": "grounded-but-partial",
                        "merchant_name": "Partial Evidence Mart",
                        "metadata": {
                            "operation": "add_line_item",
                            "added_item": {"seen_in_other_receipt": True},
                            "observed_item_evidence": {
                                "product_seen_outside_base": ["source#00001"]
                            },
                            "arithmetic_reconciliation": {"tax_delta": "0.00"},
                        },
                    }
                ],
            }
        ]
    )[0]

    operations = {row["operation"]: row for row in audit["operation_readiness"]}
    assert operations["add_line_item"]["candidate_count"] == 1
    assert operations["add_line_item"]["evidence_candidate_count"] == 1
    assert operations["add_line_item"]["supported"] is True
    assert operations["add_line_item"]["ready"] is False
    assert operations["add_line_item"]["blockers"] == ["readiness_status_partial"]
    assert all(
        not action.startswith(("synthesize_", "generate_"))
        for action in audit["next_synthesis_actions"]
    )


def test_merchant_synthesis_audit_does_not_let_candidate_metadata_bypass_contract():
    module = _load_module()

    audit = module.summarize_merchant_synthesis_audit(
        [
            {
                "merchant_name": "Self Assert Mart",
                "source_receipt_count": 2,
                "merchant_receipt_parameterization": {
                    "merchant_name": "Self Assert Mart",
                    "receipt_count": 2,
                    "synthesis_readiness": {
                        "status": "ready",
                        "score": 0.8,
                        "supported_operations": [],
                        "grounded_add_item_candidate_count": 0,
                        "blockers": [],
                        "limitations": [],
                    },
                },
                "synthetic_receipt_candidates": [
                    {
                        "candidate_id": "self-asserted-add",
                        "merchant_name": "Self Assert Mart",
                        "metadata": {
                            "operation": "add_line_item",
                            "added_item": {"seen_in_other_receipt": True},
                            "observed_item_evidence": {
                                "product_seen_outside_base": ["source#00001"]
                            },
                            "arithmetic_reconciliation": {"tax_delta": "0.00"},
                        },
                    }
                ],
            }
        ]
    )[0]

    operations = {row["operation"]: row for row in audit["operation_readiness"]}
    assert operations["add_line_item"]["candidate_count"] == 1
    assert operations["add_line_item"]["evidence_candidate_count"] == 1
    assert operations["add_line_item"]["ready"] is False
    assert operations["add_line_item"]["blockers"] == [
        "no_cross_receipt_grounded_add_items"
    ]
    assert (
        "collect_cross_receipt_item_and_category_evidence"
        in audit["next_synthesis_actions"]
    )


def test_merchant_synthesis_audit_reuses_only_evidence_backed_candidates():
    module = _load_module()

    audit = module.summarize_merchant_synthesis_audit(
        [
            {
                "merchant_name": "Contract Ready Mart",
                "source_receipt_count": 2,
                "merchant_receipt_parameterization": {
                    "merchant_name": "Contract Ready Mart",
                    "receipt_count": 2,
                    "synthesis_readiness": {
                        "status": "ready",
                        "score": 0.82,
                        "supported_operations": ["add_line_item"],
                        "grounded_add_item_candidate_count": 1,
                        "blockers": [],
                        "limitations": [],
                    },
                },
                "synthetic_receipt_candidates": [
                    {
                        "candidate_id": "raw-add",
                        "merchant_name": "Contract Ready Mart",
                        "metadata": {"operation": "add_line_item"},
                    }
                ],
            }
        ]
    )[0]

    operations = {row["operation"]: row for row in audit["operation_readiness"]}
    assert operations["add_line_item"]["candidate_count"] == 1
    assert operations["add_line_item"]["evidence_candidate_count"] == 0
    assert operations["add_line_item"]["ready"] is True
    assert (
        "generate_add_line_item_candidate_from_ready_contract"
        in audit["next_synthesis_actions"]
    )
    assert (
        "synthesize_add_line_item_from_existing_evidence"
        not in audit["next_synthesis_actions"]
    )


def test_merchant_synthesis_audit_requires_supported_operation_for_generation():
    module = _load_module()

    audit = module.summarize_merchant_synthesis_audit(
        [
            {
                "merchant_name": "Unsupported Count Mart",
                "source_receipt_count": 2,
                "merchant_receipt_parameterization": {
                    "merchant_name": "Unsupported Count Mart",
                    "receipt_count": 2,
                    "synthesis_readiness": {
                        "status": "ready",
                        "score": 0.82,
                        "supported_operations": [],
                        "grounded_add_item_candidate_count": 1,
                        "blockers": [],
                        "limitations": [],
                    },
                },
                "synthetic_receipt_candidates": [],
            }
        ]
    )[0]

    operations = {row["operation"]: row for row in audit["operation_readiness"]}
    assert operations["add_line_item"]["ready"] is False
    assert operations["add_line_item"]["blockers"] == [
        "operation_not_supported_by_contract"
    ]
    assert (
        "generate_add_line_item_candidate_from_ready_contract"
        not in audit["next_synthesis_actions"]
    )


def test_run_local_synthetic_pipeline_accepts_datetime_replacements(tmp_path):
    module = _load_module()
    receipt_path = tmp_path / "datetime_receipts.json"
    artifact_dir = tmp_path / "artifacts"
    bundle_path = tmp_path / "bundle.json"
    receipt_path.write_text(
        json.dumps(_receipt_group_payload_with_datetime()),
        encoding="utf-8",
    )

    result = module.run_local_synthetic_pipeline(
        receipt_files=[str(receipt_path)],
        artifact_output_dir=str(artifact_dir),
        bundle_output=str(bundle_path),
        max_candidates=6,
        min_ready_share=0.0,
        min_avg_readiness_score=0.0,
        min_grounded_candidate_share=0.0,
    )

    assert result["ready"] is True
    audit = result["build"]["merchant_synthesis_audit"][0]
    assert audit["merchant_name"] == "Date Time Mart"
    assert audit["mutation_inventory"]["mutable_field_count"] == 2
    assert sorted(audit["mutation_inventory"]["mutable_fields"]) == [
        "DATE",
        "TIME",
    ]
    operations = {row["operation"]: row for row in audit["operation_readiness"]}
    assert audit["missing_operations"] == ["remove_line_item"]
    assert operations["replace_field"]["ready"] is True
    assert operations["replace_field"]["candidate_count"] == 2
    assert operations["replace_field"]["evidence_candidate_count"] == 2
    assert operations["replace_field"]["evidence"]["mutable_field_count"] == 2
    assert sorted(operations["replace_field"]["evidence"]["mutable_fields"]) == [
        "DATE",
        "TIME",
    ]
    assert (
        "synthesize_replace_field_from_existing_evidence"
        in audit["next_synthesis_actions"]
    )
    assert (
        audit["mutation_inventory"]["candidate_operation_counts"]["replace_field"] == 2
    )
    assert (
        result["bundle"]["candidate_mix"]["accepted_operation_counts"]["replace_field"]
        == 2
    )
    assert result["bundle"]["candidate_mix"]["accepted_field_replacement_counts"] == {
        "DATE": 1,
        "TIME": 1,
    }
    contract = result["bundle"]["merchant_synthesis_contracts"][0]
    assert contract["merchant_name"] == "Date Time Mart"
    assert contract["operation_contracts"]["replace_field"]["ready"] is True
    assert contract["operation_contracts"]["replace_field"]["candidate_count"] == 2
    assert (
        contract["operation_contracts"]["replace_field"]["fields"]["DATE"][
            "stable_format"
        ]
        == "MM/DD/YYYY"
    )
    assert (
        contract["operation_contracts"]["replace_field"]["fields"]["TIME"][
            "stable_format"
        ]
        == "HH:MM"
    )
    assert contract["quality_gates"]["validation_policy"] == "real_receipts_only"

    written_bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    assert written_bundle["candidate_mix"]["field_replacement_counts"] == {
        "DATE": 1,
        "TIME": 1,
    }
    assert written_bundle["candidate_mix"]["accepted_field_replacement_counts"] == {
        "DATE": 1,
        "TIME": 1,
    }
    assert written_bundle["candidate_mix"]["merchants"][0][
        "accepted_field_replacement_counts"
    ] == {"DATE": 1, "TIME": 1}
    written_contract = written_bundle["merchant_synthesis_contracts"][0]
    assert "replace_field" in written_contract["supported_operations"]
    assert (
        written_contract["operation_contracts"]["replace_field"]["fields"]["DATE"][
            "observed_count"
        ]
        == 2
    )
    replacements = [
        row
        for row in written_bundle["synthetic_training_examples"]
        if row["metadata"]["operation"] == "replace_field"
    ]
    assert [row["metadata"]["field_replacement"] for row in replacements] == [
        {
            "label": "DATE",
            "old_text": "05/13/2026",
            "new_text": "05/14/2026",
            "format": "MM/DD/YYYY",
        },
        {
            "label": "TIME",
            "old_text": "15:07",
            "new_text": "15:24",
            "format": "HH:MM",
        },
    ]
    assert "05/14/2026" in replacements[0]["tokens"]
    assert "15:24" in replacements[1]["tokens"]


def test_local_pipeline_cli_writes_without_deployment_lookup(
    tmp_path,
    monkeypatch,
    capsys,
):
    module = _load_module()
    receipt_path = tmp_path / "receipts.json"
    artifact_dir = tmp_path / "artifacts"
    bundle_path = tmp_path / "bundle.json"
    receipt_path.write_text(json.dumps(_receipt_group_payload()), encoding="utf-8")
    monkeypatch.setattr(
        module,
        "load_outputs",
        lambda _env: (_ for _ in ()).throw(AssertionError("should not call AWS")),
    )
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "verify_synthetic_replay.py",
            "local-pipeline",
            "--receipt-file",
            str(receipt_path),
            "--artifact-output-dir",
            str(artifact_dir),
            "--bundle-output",
            str(bundle_path),
            "--min-ready-share",
            "0.0",
            "--min-avg-readiness-score",
            "0.0",
            "--min-grounded-candidate-share",
            "0.0",
        ],
    )

    assert module.main() == 0
    output = json.loads(capsys.readouterr().out)
    assert output["ready"] is True
    assert output["artifact_output_dir"] == str(artifact_dir)
    assert output["bundle_output"] == str(bundle_path)
    assert output["preflight"]["llm_execution"]["api_call_allowed_count"] == 0
    assert output["preflight"]["merchant_gap_summary"]["merchant_gap_count"] == 1
    assert output["bundle"]["selection"]["candidates_accepted"] > 0
    assert output["bundle"]["candidate_mix"]["accepted_structure_components"]


def test_bundle_cli_writes_local_artifact_without_deployment_lookup(
    tmp_path,
    monkeypatch,
    capsys,
):
    module = _load_module()
    artifact_path = tmp_path / "merchant.json"
    output_path = tmp_path / "bundle.json"
    artifact_path.write_text(json.dumps(_artifact()), encoding="utf-8")
    monkeypatch.setattr(
        module,
        "load_outputs",
        lambda _env: (_ for _ in ()).throw(AssertionError("should not call AWS")),
    )
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "verify_synthetic_replay.py",
            "bundle",
            "--artifact-dir",
            str(tmp_path),
            "--output",
            str(output_path),
            "--min-grounded-candidate-share",
            "0.4",
        ],
    )

    assert module.main() == 0
    output = json.loads(capsys.readouterr().out)
    assert output["ready"] is True
    assert output["output"] == str(output_path)
    assert output["preflight"]["llm_execution"] == {
        "mode_counts": {"deterministic_fallback": 1},
        "paid_llm_disabled_count": 1,
        "api_call_allowed_count": 0,
        "configured_models": ["openai/gpt-5.5"],
        "latest_model_sources": [
            "https://developers.openai.com/api/docs/guides/latest-model"
        ],
        "latest_model_verified_at": "2026-06-23",
    }
    assert output["candidate_mix"] == {
        "merchant_count": 1,
        "accepted_merchant_count": 1,
        "rejected_count": 0,
        "rejection_reasons": {},
        "accepted_operation_counts": {
            "add_line_item": 1,
            "hard_negative": 1,
        },
        "accepted_category_counts": {"PRODUCE": 1},
        "accepted_field_replacement_counts": {},
        "accepted_structure_similarity": {
            "count": 2,
            "avg": 0.9,
            "min": 0.88,
            "max": 0.92,
        },
        "accepted_structure_components": {
            "category_sequence": {
                "count": 2,
                "avg": 0.67,
                "min": 0.67,
                "max": 0.67,
            },
            "category_set": {"count": 2, "avg": 0.5, "min": 0.5, "max": 0.5},
            "item_count": {"count": 2, "avg": 0.415, "min": 0.33, "max": 0.5},
            "line_step": {"count": 2, "avg": 0.6, "min": 0.55, "max": 0.65},
            "price_column": {"count": 2, "avg": 1.0, "min": 1.0, "max": 1.0},
            "token_count": {"count": 2, "avg": 0.47, "min": 0.47, "max": 0.47},
        },
        "accepted_candidate_quality": {
            "count": 2,
            "avg": 0.915,
            "min": 0.9,
            "max": 0.93,
        },
        "accepted_candidate_quality_components": {
            "arithmetic_reconciliation": {
                "count": 1,
                "avg": 0.9,
                "min": 0.9,
                "max": 0.9,
            },
            "category_alignment": {
                "count": 1,
                "avg": 1.0,
                "min": 1.0,
                "max": 1.0,
            },
            "cross_receipt_grounding": {
                "count": 1,
                "avg": 1.0,
                "min": 1.0,
                "max": 1.0,
            },
            "structure_similarity": {
                "count": 2,
                "avg": 0.9,
                "min": 0.88,
                "max": 0.92,
            },
            "token_budget": {
                "count": 2,
                "avg": 1.0,
                "min": 1.0,
                "max": 1.0,
            },
        },
        "accepted_source_lineage": {
            "schema_version": "accepted-source-lineage-v1",
            "coverage_status": "complete",
            "authoritative": True,
            "candidate_count": 2,
            "observed_candidate_count": 2,
            "expected_candidate_count": 2,
            "with_base_receipt_count": 0,
            "with_cross_receipt_item_count": 1,
            "with_category_evidence_count": 1,
            "with_nearest_real_structure_count": 0,
            "with_layout_integrity_count": 0,
            "with_arithmetic_reconciliation_count": 1,
            "with_selection_evidence_count": 0,
            "source_receipt_key_count": 2,
            "source_receipt_keys": ["base#00001", "source#00001"],
            "source_receipt_keys_truncated": False,
        },
    }
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["selection"]["candidates_accepted"] == 2
    assert written["candidate_mix"]["accepted_merchant_count"] == 1

    from receipt_layoutlm.data_loader import (
        _load_synthetic_training_examples_with_summary,
    )

    loaded = _load_synthetic_training_examples_with_summary(str(output_path))
    assert loaded.candidates_seen == 2
    assert loaded.candidates_accepted == 2


def test_report_cli_reads_local_bundle_without_deployment_lookup(
    tmp_path,
    monkeypatch,
    capsys,
):
    module = _load_module()
    bundle_path = tmp_path / "bundle.json"
    artifact = _artifact()
    bundle = module.build_local_synthetic_training_bundle(
        [artifact],
        min_grounded_candidate_share=0.4,
    )
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
    monkeypatch.setattr(
        module,
        "load_outputs",
        lambda _env: (_ for _ in ()).throw(AssertionError("should not call AWS")),
    )
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "verify_synthetic_replay.py",
            "report",
            "--bundle-file",
            str(bundle_path),
        ],
    )

    assert module.main() == 0
    output = json.loads(capsys.readouterr().out)
    assert output == bundle["synthesis_quality_report"]
    assert output["ready"] is True
    assert output["schema_version"] == "local-synthesis-quality-report-v1"
    assert output["summary"]["accepted_count"] == 2
    assert output["summary"]["ready_contract_count"] == 1
    assert output["summary"]["llm_execution"]["mode_counts"] == {
        "deterministic_fallback": 1
    }
    assert output["quality_gates"]["validation_policy"] == "real_receipts_only"
    assert output["merchants"][0]["merchant_name"] == "Market Mart"
    assert output["merchants"][0]["accepted_count"] == 2
    assert output["merchants"][0]["accepted_examples"][0]["candidate_id"] == (
        "grounded-add-item"
    )
